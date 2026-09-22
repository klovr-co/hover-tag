"""Idempotently reconcile versioned Tag requirements into a linked Slack app."""
from __future__ import annotations

import json
import yaml
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable

try:
    import slack_app_create
    import slack_credentials
    import tag_config as settings
except ImportError:
    from scripts import slack_app_create, slack_credentials, tag_config as settings


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_VERSION = 3
DM_SCOPE = "im:history"
REQUIRED_MANIFEST = yaml.safe_load((ROOT / "slack-app-manifest.yaml").read_text())
REQUIRED_BOT_SCOPES = tuple(REQUIRED_MANIFEST["oauth_config"]["scopes"]["bot"])
DM_EVENT = "message.im"
AGENT_DESCRIPTION = "Run approved Codex or Claude tasks from Slack."


def migrate_manifest(remote: dict) -> tuple[dict, bool]:
    """Reconcile the release manifest without replacing operator-owned values."""
    migrated = json.loads(json.dumps(remote))
    try:
        app_home = migrated.setdefault("features", {}).setdefault("app_home", {})
        scopes = migrated.setdefault("oauth_config", {}).setdefault("scopes", {}).setdefault("bot", [])
        events = migrated.setdefault("settings", {}).setdefault("event_subscriptions", {}).setdefault("bot_events", [])
    except AttributeError as exc:
        raise RuntimeError("Slack returned a malformed app manifest; no settings were changed") from exc
    if not isinstance(app_home, dict) or not isinstance(scopes, list) or not isinstance(events, list):
        raise RuntimeError("Slack returned a malformed app manifest; no settings were changed")
    app_home.update(REQUIRED_MANIFEST["features"]["app_home"])
    app_home["messages_tab_enabled"] = True
    app_home["messages_tab_read_only_enabled"] = False
    for scope in REQUIRED_BOT_SCOPES:
        if scope not in scopes:
            scopes.append(scope)
    for event in REQUIRED_MANIFEST["settings"]["event_subscriptions"]["bot_events"]:
        if event not in events:
            events.append(event)
    migrated["settings"]["socket_mode_enabled"] = True
    interactivity = migrated["settings"].setdefault("interactivity", {})
    if not isinstance(interactivity, dict):
        raise RuntimeError("Slack returned malformed interactivity; no settings were changed")
    interactivity["is_enabled"] = True
    if "assistant_view" in migrated["features"]:
        raise RuntimeError(
            "This app needs an irreversible Assistant-to-Agent conversion. "
            "Run `tag setup --review` and approve Agent messaging, then retry `tag start`."
        )
    migrated, _, _ = migrate_agent_view(migrated)
    migrated["features"]["agent_view"].setdefault("agent_description", AGENT_DESCRIPTION)
    return migrated, migrated != remote


def migrate_agent_view(remote: dict) -> tuple[dict, bool, bool]:
    """Enable Agent messaging while preserving compatible legacy presentation."""
    migrated = json.loads(json.dumps(remote))
    try:
        features = migrated.setdefault("features", {})
    except AttributeError as exc:
        raise RuntimeError("Slack returned a malformed app manifest; no settings were changed") from exc
    if not isinstance(features, dict):
        raise RuntimeError("Slack returned a malformed app manifest; no settings were changed")
    current = features.get("agent_view")
    if current is not None:
        if not isinstance(current, dict):
            raise RuntimeError("Slack returned a malformed agent_view; no settings were changed")
        if "assistant_view" in features:
            raise RuntimeError("Slack returned conflicting messaging experiences; no settings were changed")
        return migrated, False, False
    legacy = features.get("assistant_view")
    if legacy is not None and not isinstance(legacy, dict):
        raise RuntimeError("Slack returned a malformed assistant_view; no settings were changed")
    agent_view = {"agent_description": AGENT_DESCRIPTION}
    if legacy is not None:
        description = legacy.get("assistant_description")
        if isinstance(description, str) and description.strip():
            agent_view["agent_description"] = description
        for key in ("actions", "suggested_prompts"):
            if key in legacy:
                agent_view[key] = legacy[key]
        del features["assistant_view"]
    features["agent_view"] = agent_view
    return migrated, True, legacy is not None


def _json_output(output: str) -> dict:
    start = output.find("{")
    if start < 0:
        raise RuntimeError("Slack CLI did not return an app manifest")
    try:
        value = json.loads(output[start:])
    except json.JSONDecodeError as exc:
        raise RuntimeError("Slack CLI returned an unreadable app manifest") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Slack CLI returned an unreadable app manifest")
    return value


def _run(command: list[str], *, cwd: Path, capture: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=False,
            text=True,
            capture_output=capture,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Slack CLI did not complete; check its installation and retry") from exc


def remote_manifest(slack: str, project: Path, app_id: str) -> dict:
    result = _run([
        slack, "manifest", "info", "--source", "remote", "--app", app_id,
        "--skip-update", "--no-color",
    ], cwd=project)
    if result.returncode:
        raise RuntimeError("Slack app settings could not be inspected; run `slack login`, then retry `tag start`")
    return _json_output(result.stdout)


def granted_bot_scopes(token: str) -> set[str]:
    request = urllib.request.Request(
        "https://slack.com/api/auth.test",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read())
            header = response.headers.get("x-oauth-scopes", "")
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError) as exc:
        raise RuntimeError("Slack permissions could not be verified; check the connection and retry") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError("Slack rejected the bot token; reconnect it with `tag setup`")
    return {item.strip() for item in header.split(",") if item.strip()}


def _sync_command(slack: str, project: Path, app_id: str, team_id: str) -> list[str]:
    help_result = _run([
        slack, "manifest", "sync", "--help", "--skip-update", "--no-color",
    ], cwd=project)
    base = [slack, "manifest", "sync", "--app", app_id, "--team", team_id,
            "--skip-update", "--no-color"]
    if "--manifest-source" in help_result.stdout:
        return [*base, "--manifest-source", "local"]
    experiment = _run([
        slack, "manifest", "sync", "--help", "--experiment", "manifest-sync",
        "--skip-update", "--no-color",
    ], cwd=project)
    if experiment.returncode == 0 and "--force-remote" in experiment.stdout:
        return [*base, "--experiment", "manifest-sync", "--force"]
    raise RuntimeError("Slack CLI 4.8 or newer is required to migrate app settings; run `slack upgrade`")


def _migration_project(project: Path, manifest: dict, team_id: str, app_id: str, directory: Path) -> Path:
    linked_ids = slack_app_create.saved_app_ids(project, team_id)
    if app_id not in linked_ids:
        raise RuntimeError("The configured Slack app is not linked; run `tag setup` before starting")
    temporary = directory / "slack-manifest-migration"
    (temporary / ".slack").mkdir(parents=True)
    helper = ROOT / "scripts/slack_manifest_hook.py"
    command = [str(Path(sys.executable).resolve()), str(helper)]
    hook = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
    # This disposable project is the migration transaction's local source.
    # The operator's persistent Slack CLI project remains remote-managed.
    settings.save_config(temporary / ".slack/config.json", {"manifest": {"source": "local"}})
    settings.save_config(temporary / ".slack/hooks.json", {"hooks": {"get-manifest": hook}})
    settings.save_config(temporary / ".slack/apps.json", {
        "apps": {team_id: {"team_id": team_id, "app_id": app_id}}
    })
    settings.save_config(temporary / "manifest.json", manifest)
    return temporary


def _marker_matches(marker: Path, team_id: str, app_id: str) -> bool:
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return value == {"version": MIGRATION_VERSION, "team_id": team_id, "app_id": app_id}


def enable_agent_view(
    project: Path,
    app_id: str,
    team_id: str,
    *,
    approve_legacy: Callable[[], bool],
) -> bool:
    """Targetedly enable Agent messaging and verify the remote manifest."""
    slack = shutil.which("slack")
    if not slack:
        raise RuntimeError("Slack CLI is required to enable Agent messaging")
    remote = remote_manifest(slack, project, app_id)
    migrated, changed, replaces_legacy = migrate_agent_view(remote)
    if not changed:
        return False
    if replaces_legacy and not approve_legacy():
        return False
    with tempfile.TemporaryDirectory(prefix="tag-agent-view-") as directory:
        migration_project = _migration_project(
            project, migrated, team_id, app_id, Path(directory)
        )
        result = _run(
            _sync_command(slack, migration_project, app_id, team_id),
            cwd=migration_project,
        )
        if result.returncode:
            raise RuntimeError(
                "Slack CLI could not enable Agent messaging; open app settings or retry after `slack login`"
            )
    verified = remote_manifest(slack, project, app_id)
    features = verified.get("features", {})
    if not isinstance(features, dict) or not isinstance(features.get("agent_view"), dict):
        raise RuntimeError("Slack did not save Agent messaging; no local success was recorded")
    if "assistant_view" in features:
        raise RuntimeError("Slack still reports the legacy Assistant messaging experience")
    return True


def reconcile(home: Path, config_path: Path, values: dict[str, str]) -> bool:
    """Apply release requirements using existing CLI authorization, without prompts."""
    team_id, app_id = values["SLACK_TEAM_ID"], values["SLACK_APP_ID"]
    marker = home / "state/slack-manifest-migrations.json"
    if _marker_matches(marker, team_id, app_id):
        return False
    slack = shutil.which("slack")
    if not slack:
        raise RuntimeError("Slack CLI is required to migrate app settings; install it, then retry `tag start`")
    project = home / "integrations/slack-cli"
    remote = remote_manifest(slack, project, app_id)
    migrated, changed = migrate_manifest(remote)
    scopes = granted_bot_scopes(values["SLACK_BOT_TOKEN"])
    if not changed and set(REQUIRED_BOT_SCOPES).issubset(scopes):
        settings.save_config(marker, {"version": MIGRATION_VERSION, "team_id": team_id, "app_id": app_id})
        return False
    with tempfile.TemporaryDirectory(prefix="tag-slack-migration-") as directory:
        migration_project = _migration_project(project, migrated, team_id, app_id, Path(directory))
        if changed:
            result = _run(_sync_command(slack, migration_project, app_id, team_id), cwd=migration_project)
            if result.returncode:
                raise RuntimeError("Slack app settings migration failed; run `slack login`, then retry `tag start`")
    verified = remote_manifest(slack, project, app_id)
    if migrate_manifest(verified)[1]:
        raise RuntimeError("Slack did not save the required app settings; retry `tag start`")
    # The credential handoff refreshes the installation itself. It captures
    # output and closes stdin, so background upgrades never wait for input.
    credentials = slack_credentials.receive(project, team_id, app_id)
    settings.update_config(config_path, credentials)
    missing = set(REQUIRED_BOT_SCOPES) - granted_bot_scopes(credentials["SLACK_BOT_TOKEN"])
    if missing:
        raise RuntimeError(
            "Slack reinstalled the app without " + ", ".join(sorted(missing))
            + "; approve those permissions in Slack and retry `tag start`"
        )
    settings.save_config(marker, {"version": MIGRATION_VERSION, "team_id": team_id, "app_id": app_id})
    return True
