"""Idempotently reconcile versioned Tag requirements into a linked Slack app."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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
    import slack_identity
    import slack_channels
    import slack_credentials
    import tag_config as settings
except ImportError:
    from scripts import slack_identity, slack_channels, slack_app_create, slack_credentials, tag_config as settings


ROOT = Path(__file__).resolve().parents[1]
# 4: team:read, so Tag.app can show the Slack workspace icon.
MIGRATION_VERSION = 4
DM_SCOPE = "im:history"
REQUIRED_MANIFEST = yaml.safe_load((ROOT / "slack-app-manifest.yaml").read_text(encoding="utf-8"))
REQUIRED_BOT_SCOPES = tuple(REQUIRED_MANIFEST["oauth_config"]["scopes"]["bot"])
# Requested like the others, but never worth stopping a start for: team:read only
# shows the workspace icon. If Slack (or an admin) hasn't granted it yet, Tag starts
# anyway and asks again at most once a day.
OPTIONAL_BOT_SCOPES = frozenset({"team:read"})
NEEDED_BOT_SCOPES = tuple(scope for scope in REQUIRED_BOT_SCOPES if scope not in OPTIONAL_BOT_SCOPES)
OPTIONAL_RETRY = timedelta(hours=24)
OPTIONAL_KEYS = {"optional_pending", "optional_checked_at", "optional_error"}
DM_EVENT = "message.im"
AGENT_DESCRIPTION = "Run approved Codex or Claude tasks from Slack."


def migrate_manifest(remote: dict, *, enterprise: bool = False, include_optional: bool = True) -> tuple[dict, bool]:
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
        if scope not in scopes and (include_optional or scope not in OPTIONAL_BOT_SCOPES):
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
    if enterprise:
        migrated["settings"]["org_deploy_enabled"] = True
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _marker_state(marker: Path, receipt: dict) -> dict | None:
    """The saved receipt when it matches this release and app; None when the migration must run."""
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or {k: v for k, v in value.items() if k not in OPTIONAL_KEYS} != receipt:
        return None
    return value


def _optional_due(state: dict) -> bool:
    if not state.get("optional_pending"):
        return False
    try:
        checked = datetime.fromisoformat(str(state.get("optional_checked_at")))
    except ValueError:
        return True
    return _now() - checked >= OPTIONAL_RETRY


def _save_receipt(marker: Path, receipt: dict, pending: set[str] | frozenset[str] = frozenset(), error: str = "") -> None:
    value = dict(receipt)
    if pending:
        value.update(optional_pending=sorted(pending), optional_checked_at=_now().isoformat(),
                     **({"optional_error": error} if error else {}))
    settings.save_config(marker, value)


def optional_pending(home: Path) -> list[str]:
    """Optional permissions Slack hasn't granted yet, for status lines."""
    try:
        value = json.loads((home / "state/slack-manifest-migrations.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    pending = value.get("optional_pending") if isinstance(value, dict) else None
    return [str(scope) for scope in pending] if isinstance(pending, list) else []


class _RequiredMissing(RuntimeError):
    """A required permission is still missing after a reinstall; this always stops the start."""


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


def _named(manifest: dict, name: str) -> tuple[dict, bool]:
    renamed = json.loads(json.dumps(manifest))
    information = renamed.setdefault("display_information", {})
    bot_user = renamed.setdefault("features", {}).setdefault("bot_user", {})
    changed = information.get("name") != name or bot_user.get("display_name") != name
    information["name"] = name
    bot_user["display_name"] = name
    return renamed, changed


def set_display_name(home: Path, values: dict[str, str], name: str, *, retry: str) -> bool:
    """Rename the Tag's Slack app and bot user, then verify Slack kept it.

    Uses existing Slack CLI authorization without prompts. ``retry`` is the
    command to repeat after the operator resolves a Slack requirement.
    """
    team_id, app_id = values.get("SLACK_TEAM_ID", ""), values.get("SLACK_APP_ID", "")
    if not team_id or not app_id:
        raise RuntimeError("This Tag has no Slack app yet; finish its setup first")
    slack = shutil.which("slack")
    if not slack:
        raise RuntimeError(f"Slack CLI is required to rename the app; install it, then retry `{retry}`")
    project = home / "integrations/slack-cli"
    renamed, changed = _named(remote_manifest(slack, project, app_id), name)
    if not changed:
        return False
    with tempfile.TemporaryDirectory(prefix="tag-slack-rename-") as directory:
        migration_project = _migration_project(project, renamed, team_id, app_id, Path(directory))
        result = _run(_sync_command(slack, migration_project, app_id, team_id), cwd=migration_project)
        if result.returncode:
            raise RuntimeError(f"Slack could not rename the app; run `slack login`, then retry `{retry}`")
    if _named(remote_manifest(slack, project, app_id), name)[1]:
        raise RuntimeError(f"Slack did not save the new name; retry `{retry}`")
    return True


def reconcile(home: Path, config_path: Path, values: dict[str, str]) -> bool:
    """Apply release requirements using existing CLI authorization, without prompts.

    Returns True when credentials were refreshed. Required changes are verified
    before the receipt is saved and stop the start until they succeed. Optional
    permissions (OPTIONAL_BOT_SCOPES) are requested too, but when only they are
    missing the receipt records them as pending and the start continues; Tag asks
    again at most once every OPTIONAL_RETRY.
    """
    team_id, app_id = values["SLACK_TEAM_ID"], values["SLACK_APP_ID"]
    enterprise_id = values.get("SLACK_ENTERPRISE_ID", "")
    receipt = {"version": MIGRATION_VERSION, "team_id": team_id, "app_id": app_id}
    if enterprise_id:
        receipt.update(enterprise_id=enterprise_id, organization_version=1)
    marker = home / "state/slack-manifest-migrations.json"
    state = _marker_state(marker, receipt)
    if state is not None and not _optional_due(state):
        return False
    # optional_only: every required change is already done, so failures only defer optional ones.
    progress = {"optional_only": state is not None, "refreshed": False, "granted": set()}
    try:
        return _reconcile(home, config_path, values, receipt, marker, progress)
    except _RequiredMissing:
        raise
    except RuntimeError as exc:
        if not progress["optional_only"]:
            raise
        _save_receipt(marker, receipt, OPTIONAL_BOT_SCOPES - progress["granted"], str(exc))
        return progress["refreshed"]


def _reconcile(home: Path, config_path: Path, values: dict[str, str], receipt: dict, marker: Path, progress: dict) -> bool:
    team_id, app_id = values["SLACK_TEAM_ID"], values["SLACK_APP_ID"]
    enterprise_id = values.get("SLACK_ENTERPRISE_ID", "")
    authorization_id = enterprise_id or team_id
    slack = shutil.which("slack")
    if not slack:
        raise RuntimeError("Slack CLI is required to migrate app settings; install it, then retry `tag start`")
    project = home / "integrations/slack-cli"
    remote = remote_manifest(slack, project, app_id)
    migrated, changed = migrate_manifest(remote, enterprise=bool(enterprise_id))
    scopes = granted_bot_scopes(values["SLACK_BOT_TOKEN"])
    if enterprise_id:
        slack_identity.validate(values["SLACK_BOT_TOKEN"], team_id=team_id, app_id=app_id,
                                enterprise_id=enterprise_id, label="Bot token", api=slack_channels.slack_api)
    if not changed and set(REQUIRED_BOT_SCOPES).issubset(scopes):
        _save_receipt(marker, receipt)
        return False
    if not migrate_manifest(remote, enterprise=bool(enterprise_id), include_optional=False)[1] \
            and set(NEEDED_BOT_SCOPES).issubset(scopes):
        progress["optional_only"] = True
        progress["granted"] = scopes
    with tempfile.TemporaryDirectory(prefix="tag-slack-migration-") as directory:
        migration_project = _migration_project(project, migrated, authorization_id, app_id, Path(directory))
        if changed:
            result = _run(_sync_command(slack, migration_project, app_id, authorization_id), cwd=migration_project)
            if result.returncode:
                raise RuntimeError("Slack app settings migration failed; run `slack login`, then retry `tag start`")
    verified = remote_manifest(slack, project, app_id)
    if migrate_manifest(verified, enterprise=bool(enterprise_id))[1]:
        raise RuntimeError("Slack did not save the required app settings; retry `tag start`")
    # The credential handoff refreshes the installation itself. It captures
    # output and closes stdin, so background upgrades never wait for input.
    credentials = slack_credentials.receive(project, team_id, app_id,
        **({"enterprise_id": enterprise_id} if enterprise_id else {}))
    if enterprise_id:
        slack_identity.validate(credentials["SLACK_BOT_TOKEN"], team_id=team_id, app_id=app_id,
                                enterprise_id=enterprise_id, label="Bot token", api=slack_channels.slack_api)
    settings.update_config(config_path, credentials)
    progress["refreshed"] = True
    granted = granted_bot_scopes(credentials["SLACK_BOT_TOKEN"])
    progress["granted"] = granted
    missing = set(NEEDED_BOT_SCOPES) - granted
    if missing:
        raise _RequiredMissing(
            "Slack reinstalled the app without " + ", ".join(sorted(missing))
            + "; approve those permissions in Slack and retry `tag start`"
        )
    pending = OPTIONAL_BOT_SCOPES - granted
    _save_receipt(marker, receipt, pending,
                  ("Slack reinstalled the app without " + ", ".join(sorted(pending))) if pending else "")
    return True


def enable_org_deployment(project: Path, app_id: str, enterprise_id: str) -> None:
    """Retryable setup prerequisite; preserve all unrelated remote settings."""
    slack = shutil.which("slack") or "slack"
    remote = remote_manifest(slack, project, app_id)
    if remote.get("settings", {}).get("org_deploy_enabled") is True:
        return
    migrated = json.loads(json.dumps(remote))
    migrated.setdefault("settings", {})["org_deploy_enabled"] = True
    with tempfile.TemporaryDirectory(prefix="tag-slack-org-") as directory:
        target = _migration_project(project, migrated, enterprise_id, app_id, Path(directory))
        result = _run(_sync_command(slack, target, app_id, enterprise_id), cwd=target)
        if result.returncode:
            raise RuntimeError("Slack could not enable organization deployment. Ask an organization admin to approve the app, then retry setup.")
    if remote_manifest(slack, project, app_id).get("settings", {}).get("org_deploy_enabled") is not True:
        raise RuntimeError("Organization deployment is not yet enabled; retry setup after Slack approval.")
