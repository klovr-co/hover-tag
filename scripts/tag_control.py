"""Shared inspection and small interactive views over Tag's public commands."""
from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import tag_config as settings
    import slack_channels
    import setup_ui as ui
except ImportError:
    from scripts import slack_channels, tag_config as settings, setup_ui as ui


def target_detail(values: dict[str, str], tag_id: str = "default", *, suffix: str = "") -> str:
    return ui.display.target_detail(
        tag_id,
        values.get("SLACK_TEAM_ID", ""),
        values.get("SLACK_APP_ID", ""),
        values.get("OPENTAG_BOT_NAME", ""),
        suffix=suffix,
    )


def tag_command(tag_id: str, action: str) -> str:
    target = "" if tag_id == "default" else f"{tag_id} "
    return f"tag {target}{action}"


def inspect(home: Path, lifecycle, *, offline: bool = False, tag_id: str = "default") -> dict:
    path = settings.config_path(home)
    values, error = {}, None
    try:
        values = settings.load_config(path)
    except (OSError, ValueError):
        error = "Cannot read settings as a JSON object of string values; repair the file before continuing"
    errors = settings.config_errors(values)
    backend = values.get("OPENTAG_BACKEND", "codex")
    search_path = str(home / "integrations/bin") + os.pathsep + os.environ.get("PATH", "")
    installed = backend in {"codex", "claude"} and shutil.which(backend, path=search_path) is not None
    mfs_installed = lifecycle.mfs_server_executable() is not None
    services = {"mfs": None, "slack": None}
    managed = False
    dependency_error = None
    if not offline:
        if not settings.validation_error("MFS_URL", values.get("MFS_URL", settings.DEFAULTS["MFS_URL"])):
            services["mfs"] = lifecycle.healthy(values.get("MFS_URL", settings.DEFAULTS["MFS_URL"]))
        try:
            services["slack"] = lifecycle.slack_ready(home)
            managed = lifecycle.process_for(home / "state/slack.json") is not None
        except ImportError:
            dependency_error = "Runtime dependencies missing; rerun the Tag installer"
    if error:
        state, action = "invalid_configuration", "config show"
    elif not path.exists():
        state, action = "not_configured", "setup"
    elif errors:
        state, action = "setup_incomplete", "setup"
    elif dependency_error or not installed or (not offline and not mfs_installed and not services["mfs"]):
        state, action = "needs_attention", "doctor"
    elif offline:
        state, action = "configured", "inspect"
    elif all(services.values()):
        state, action = "running", "status"
    elif managed:
        state, action = "needs_attention", "doctor"
    else:
        state, action = "stopped", "start"
    memory_sync = {"policy": values.get("SLACK_CHANNEL_POLICY", "selected"), "state": "not_checked"}
    if memory_sync["policy"] == "invited":
        try:
            sync = json.loads((home / "state/slack-memory.json").read_text())
            allowed = {"syncing", "sync_requested", "no_joined_channels", "settings_changed", "needs_attention"}
            memory_sync["state"] = sync["state"] if sync["state"] in allowed and 0 <= time.time() - sync["checked_at"] < 600 else "stale"
            if sync.get("check") in {"membership", "history_access", "index_submission", "mfs_slack_connector"}:
                memory_sync["check"] = sync["check"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if memory_sync["state"] in {"needs_attention", "settings_changed", "stale"} and state == "running":
            state, action = "needs_attention", "status"
    return {
        "schema_version": 1, "tag": tag_id, "state": state,
        "next_command": tag_command(tag_id, action),
        "configuration": {"path": str(path), "exists": path.exists(), "error": error,
                          "complete": not error and not errors, "fields": errors},
        "workspace": os.getenv("OPENTAG_WORKDIR", str(home / "workspace")),
        "backend": {"selected": backend if backend in {"codex", "claude"} else None,
                    "experimental": backend == "claude", "executable_found": installed,
                    "authentication": "not_checked", "task_execution": "not_checked"},
        "services": services, "managed_process_running": managed,
        "runtime": {"mfs_executable_found": mfs_installed, "error": dependency_error},
        "first_reply": "not_verified",
        "memory_sync": memory_sync,
        "slack_workspace": values.get("SLACK_TEAM_ID") or None,
        "slack_app": values.get("SLACK_APP_ID") or None,
        "slack_app_name": values.get("OPENTAG_BOT_NAME") or None,
    }


def status_report(home: Path, lifecycle, *, tag_id: str = "default") -> dict:
    report = inspect(home, lifecycle, tag_id=tag_id)
    if report["configuration"]["exists"]:
        message, ready = ui.display.backend_status(report["backend"]["selected"],
            search_path=str(home / "integrations/bin") + os.pathsep + os.environ.get("PATH", ""))
        report["backend"].update(status=message, ready=ready,
                                 authentication="signed_in" if ready else "unverified")
        if not ready and report["state"] == "running":
            report.update(state="needs_attention", next_command=tag_command(tag_id, "doctor"))
    return report


def show_status(report: dict) -> None:
    services = report["services"]
    backend = report["backend"]
    ui.display.summary(report["state"], report["next_command"],
                       slack=services.get("slack"), memory=services.get("mfs"),
                       backend=backend["selected"] if report["configuration"]["exists"] else None,
                       agent=(backend["status"], backend["ready"]) if "status" in backend else None)
    print("  Target: " + ui.display.target_detail(
        report.get("tag", "default"),
        report.get("slack_workspace") or "",
        report.get("slack_app") or "",
        report.get("slack_app_name") or "",
    ))
    if report.get("memory_sync", {}).get("policy") == "invited":
        print("  Invitation memory: " + report["memory_sync"]["state"].replace("_", " "))
    print("  First reply: not verified by this status check.")


def show_inspection(report: dict) -> None:
    ui.display.header(
        "Inspect",
        ui.display.target_detail(
            report.get("tag", "default"),
            report.get("slack_workspace") or "",
            report.get("slack_app") or "",
            report.get("slack_app_name") or "",
            suffix="No changes are made",
        ),
    )
    ui.display.section("Installation")
    ui.display.info_row("Tag", report.get("tag", "default"))
    ui.display.info_row("Slack workspace", report.get("slack_workspace") or "Not configured")
    ui.display.info_row("Slack app", report.get("slack_app") or "Not configured")
    ui.display.info_row("State", report["state"].replace("_", " "))
    ui.display.info_row("Workspace", ui.display.short_path(report["workspace"]))
    if report["configuration"]["error"]:
        ui.display.info_row("Settings", report["configuration"]["error"], good=False)
    for key, problem in report["configuration"]["fields"].items():
        ui.display.info_row(settings.LABELS.get(key, key), problem.lower(), good=False)
    ui.display.section("Runtime")
    if report["runtime"]["error"]:
        ui.display.info_row("Dependencies", report["runtime"]["error"], good=False)
    if not report["runtime"]["mfs_executable_found"] and not report["services"]["mfs"]:
        ui.display.info_row("Memory", "Runtime not found beside Tag's Python", good=False)
    backend = report["backend"]
    ui.display.info_row(
        "Agent",
        f"{backend['selected'] or 'not configured'} · "
        f"{'executable found' if backend['executable_found'] else 'not found'} · sign-in not checked",
        good=backend["executable_found"],
    )
    if report.get("memory_sync", {}).get("policy") == "invited":
        ui.display.info_row("Invitations", report["memory_sync"]["state"].replace("_", " "))
    ui.display.next_action("Recommended next step", report["next_command"])


def config_command(home: Path, words: list[str], *, json_output: bool, stdin: bool,
                   tag_id: str = "default") -> int:
    path = settings.config_path(home)
    action = words[0] if words else "show"
    if stdin and action != "set":
        raise ValueError("--stdin is only supported for config set")
    try:
        identity = settings.load_config(path)
    except (OSError, ValueError):
        identity = {}
    if action == "init" and len(words) == 1:
        settings.update_config(path, settings.DEFAULTS, only_missing=True)
        result = {"schema_version": 1, "tag": tag_id,
                  "next_command": tag_command(tag_id, "inspect --json"),
                  "note": "Missing defaults saved. Existing settings preserved."}
    elif action == "keys" and len(words) == 1:
        result = {"schema_version": 1, "tag": tag_id, "editable": sorted(settings.EDITABLE),
                  "secret_input": "Use tag config set KEY --stdin; values are never returned"}
    elif action == "show" and len(words) <= 1:
        values = settings.load_config(path)
        result = {"schema_version": 1, "tag": tag_id, "path": str(path), "settings": settings.public_config(values),
                  "fields": settings.config_errors(values)}
    elif action == "set" and ((stdin and len(words) == 2) or (not stdin and len(words) == 3)):
        key = words[1]
        if key not in settings.PUBLIC and not stdin:
            raise ValueError("Use --stdin for secret settings so they do not enter shell history")
        value = sys.stdin.read().rstrip("\r\n") if stdin else words[2]
        settings.update_config(path, {key: value})
        result = {"schema_version": 1, "tag": tag_id, "updated": [key],
                  "next_command": tag_command(tag_id, "inspect --json"),
                  "note": "Changes apply on next start. If running, use "
                          f"{tag_command(tag_id, 'stop')} then {tag_command(tag_id, 'start')}."}
    else:
        raise ValueError("Use tag config init, tag config show, tag config keys, or tag config set KEY VALUE (secrets: --stdin)")
    if not json_output:
        try:
            identity = settings.load_config(path)
        except (OSError, ValueError):
            identity = {}
    if json_output:
        print(json.dumps(result, indent=2))
    elif action == "show":
        ui.display.header("Config", target_detail(identity, tag_id, suffix="Secret values are hidden"))
        ui.display.info_row("File", ui.display.short_path(path))
        ui.display.section("Settings")
        for key, value in result["settings"].items():
            if key == "SLACK_CHANNEL_ID" and value and values.get("SLACK_BOT_TOKEN"):
                value = f"{slack_channels.channel_label(values['SLACK_BOT_TOKEN'], value)} ({value})"
            ui.display.info_row(key, value or "not set")
        for key, problem in result["fields"].items():
            ui.display.info_row(key, problem, good=False)
        ui.display.next_action("Edit settings interactively", tag_command(tag_id, "settings"))
    elif action == "init":
        ui.display.header("Config", target_detail(identity, tag_id, suffix="Existing values are preserved"))
        ui.display.completion("Defaults saved", result["note"], next_label="Inspect configuration", next_command=result["next_command"])
    elif action == "keys":
        ui.display.header("Config keys", target_detail(identity, tag_id))
        ui.display.section("Editable")
        for key in result["editable"]:
            ui.display.info_row("", key)
        ui.display.next_action("Set a secret without shell history", "tag config set KEY --stdin", detail=result["secret_input"])
    else:
        ui.display.header("Config", target_detail(identity, tag_id, suffix="Secret values are hidden"))
        ui.display.completion(f"Updated {words[1]}", result["note"], next_label="Inspect configuration", next_command=result["next_command"])
    return 0


def run_command(lifecycle, *words: str) -> int:
    return subprocess.call([sys.executable, str(lifecycle.ROOT / "scripts/tag_cli.py"), *words])


def settings_menu(home: Path) -> None:
    try:
        _settings_menu(home)
    except (ui.Paused, KeyboardInterrupt, EOFError):
        print()
        ui.message("Settings closed. Saved changes are kept.")
        print()


def _settings_menu(home: Path) -> None:
    groups = (
        ("Slack connection and access", ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "SLACK_ALLOWED_USER_IDS", "SLACK_CHANNEL_IDS", "OPENTAG_BOT_NAME", "SLACK_CHANNEL_POLICY", "change_app", "reconnect")),
        ("Workspace and memory", ("MFS_SLACK_HISTORY_DAYS", "MFS_ALLOWED_SCOPES", "MFS_URL", "MFS_TOKEN")),
        ("Agent", ("OPENTAG_BACKEND",)),
        ("Advanced", ("OPENTAG_TIMEOUT_SECONDS", "OPENTAG_MAX_TIMEOUT_SECONDS", "OPENTAG_BACKEND_ATTEMPTS", "OPENTAG_SLACK_STREAMING",
                      "OPENTAG_SLACK_DM_ENABLED",
                      "OPENTAG_JEV_AUTO_INVOKE", "OPENTAG_TYPESAFE_API_KEY",
                      "OPENTAG_JEV_MODEL", "OPENTAG_JEV_THRESHOLD",
                      "OPENTAG_JEV_TIMEOUT_SECONDS",
                      "OPENTAG_CODEX_TRANSPORT")),
    )
    while True:
        tag_id = os.getenv("TAG_ID", "default")
        try:
            identity = settings.load_config(settings.config_path(home))
        except (OSError, ValueError):
            identity = {}
        ui.display.header(
            "Settings",
            target_detail(identity, tag_id, suffix="Changes apply on next start"),
        )
        if ui.keyboard_available():
            choice = ui.choose("What would you like to manage?", [name for name, _ in groups] + ["Back"])
            selection = str(choice + 1) if choice < len(groups) else "0"
        else:
            for i, (name, _) in enumerate(groups, 1):
                print(f"  {i}. {name}")
            print("  0. Back")
            selection = input("Choose: ").strip()
        if selection in {"0", "", "back"}:
            return
        if selection not in {"1", "2", "3", "4"}:
            continue
        name, keys = groups[int(selection) - 1]
        raw_values = settings.load_config(settings.config_path(home))
        values = settings.public_config(raw_values)
        channel_id = raw_values.get("SLACK_CHANNEL_ID", "")
        if channel_id and raw_values.get("SLACK_BOT_TOKEN"):
            values["SLACK_CHANNEL_ID"] = (
                f"{slack_channels.channel_label(raw_values['SLACK_BOT_TOKEN'], channel_id)} ({channel_id})"
            )
        ui.display.header("Settings / " + name, target_detail(raw_values, tag_id))
        if selection == "2":
            ui.message(f"Workspace: {home / 'workspace'} (managed by Tag)")
            ui.message("Memory uses sources already indexed in MFS; changing scopes does not index a source.")
        if selection == "3":
            ui.message("codex: recommended. claude: experimental. Sign in with the chosen CLI first.")
        actions = {"change_app": "Change Slack app or workspace", "reconnect": "Reconnect credentials with Slack CLI"}
        labels = [actions.get(key, f"{settings.LABELS.get(key, key)}: {values.get(key, 'not set')}") for key in keys]
        if ui.keyboard_available():
            choice = ui.choose("Choose a setting", labels + ["Back"])
            selection = str(choice + 1) if choice < len(keys) else ""
        else:
            for i, label in enumerate(labels, 1):
                print(f"  {i}. {label}")
            selection = input("Setting number (Enter to go back): ").strip()
        if not selection.isdigit() or not 1 <= int(selection) <= len(keys):
            continue
        key = keys[int(selection) - 1]
        try:
            if key in {"change_app", "reconnect", "SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "SLACK_CHANNEL_IDS", "MFS_SLACK_HISTORY_DAYS", "SLACK_CHANNEL_POLICY"}:
                try:
                    from . import tag_reconfigure
                except ImportError:
                    import tag_reconfigure
                kind = {"change_app": "app", "reconnect": "credentials", "SLACK_APP_TOKEN": "credentials", "SLACK_BOT_TOKEN": "credentials", "SLACK_CHANNEL_IDS": "channels",
                        "MFS_SLACK_HISTORY_DAYS": "history", "SLACK_CHANNEL_POLICY": "policy"}[key]
                tag_reconfigure.edit(home, kind)
                continue
            elif key == "OPENTAG_BACKEND" and ui.keyboard_available():
                choice = ui.choose("Choose your agent", ["Codex · recommended", "Claude · experimental", "Cancel"],
                                   default=int(raw_values.get(key) == "claude"))
                if choice == 2:
                    continue
                value = ("codex", "claude")[choice]
            else:
                reader = input if key in settings.PUBLIC else getpass.getpass
                value = reader("New value (Enter to cancel; /clear to empty an optional setting): ").strip()
                if not value:
                    continue
            settings.update_config(settings.config_path(home), {key: "" if value == "/clear" else value})
            ui.message("Saved. Changes apply on next start; restart Tag if it is running.")
            if key == "OPENTAG_BACKEND":
                message, _ = ui.display.backend_status(value)
                ui.message(f"{value.capitalize()}: {message}")
        except (ValueError, RuntimeError, slack_channels.SlackChannelError) as exc:
            ui.message(str(exc))
