"""Stage guided Settings changes without overwriting the active installation."""
from __future__ import annotations

import os
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

try:
    from . import tag_config as settings, tag_cli as lifecycle, setup_ui as ui
    from .tag_paths import initialize, runtime_environment
except ImportError:
    import tag_config as settings
    import tag_cli as lifecycle
    import setup_ui as ui
    from tag_paths import initialize, runtime_environment


def commit(home: Path, draft: Path, original: dict[str, str]) -> None:
    """Serialize with starts/settings writes; restore the old link on failure."""
    config = settings.config_path(home)
    start_lock = home / "state/start.lock"
    config_lock = config.with_name(config.name + ".lock")
    try:
        start_lock.mkdir()
    except FileExistsError:
        raise RuntimeError("Another start or reset is in progress. Draft kept; retry afterward.") from None
    locked = False
    project = home / "integrations/slack-cli"
    previous = draft / "previous-slack-cli"
    moved = False
    installed = False
    try:
        try:
            descriptor = os.open(config_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise RuntimeError("Another settings update is in progress. Draft kept; retry afterward.") from None
        os.close(descriptor)
        locked = True
        if settings.load_config(config) != original:
            raise RuntimeError("Settings changed while editing. Active settings were kept; reopen Settings.")
        if any(lifecycle.process_for(home / "state" / f"{name}.json") for name in ("slack", "mfs")):
            raise RuntimeError("Tag started while editing. Stop Tag before applying changes.")
        values = settings.read_config(draft / "config/settings.json")
        if settings.config_errors(values):
            raise RuntimeError("Draft settings are incomplete. Active settings were kept.")
        source = Path(values["MFS_SLACK_CONNECTOR_CONFIG"])
        if not source.resolve().is_relative_to(draft.resolve()):
            raise RuntimeError("Draft connector must belong to the draft home.")
        destination = home / "integrations/mfs/connectors" / f"{draft.name}.toml"
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(source, destination)
        destination.chmod(0o600)
        values["MFS_SLACK_CONNECTOR_CONFIG"] = str(destination)
        settings.save_config(draft / "previous-settings.json", original)
        if project.is_symlink():
            raise RuntimeError("Refusing to replace a symlinked Slack project.")
        if project.exists():
            shutil.move(str(project), str(previous))
            moved = True
        installed = True
        shutil.copytree(draft / "integrations/slack-cli", project)
        settings.save_config(config, values)
    except BaseException:
        # Only our newly copied project is moved; nothing is recursively deleted.
        if project.exists() and (installed or moved):
            shutil.move(str(project), str(draft / "unapplied-slack-cli"))
        if moved:
            shutil.move(str(previous), str(project))
        raise
    finally:
        if locked:
            config_lock.unlink(missing_ok=True)
        start_lock.rmdir()


def edit(home: Path, kind: str) -> None:
    if any(lifecycle.process_for(home / "state" / f"{name}.json") for name in ("slack", "mfs")):
        ui.notice("Stop Tag before changing Slack or memory", "Run tag stop, then reopen tag settings. Your current setup is unchanged.")
        return
    path = settings.config_path(home)
    original = settings.load_config(path)
    if not original:
        ui.message("Run tag setup first.")
        return
    root = home / "integrations/setup-drafts"
    pointer = home / "state/settings-draft.json"
    resumable = None
    if pointer.is_file():
        try:
            pending = json.loads(pointer.read_text())
            draft = Path(pending["path"])
            if (pending.get("kind") == kind and draft.parent.resolve() == root.resolve()
                    and not draft.is_symlink() and draft.is_dir()
                    and settings.read_config(draft / "active-settings.json") == original):
                resumable = draft
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if resumable:
        choice = ui.choose("Continue saved Settings changes?", ["Resume draft", "Start another draft", "Cancel"], default=0)
        if choice == 2:
            return
        if choice == 0:
            run_draft(home, resumable, kind, original)
            return
    values = dict(original)
    values.setdefault("SLACK_CHANNEL_POLICY", "selected" if original.get("MFS_SLACK_CONNECTOR_CONFIG") else "invited")
    values.pop("SLACK_CHANNEL_ID", None)
    if kind == "policy":
        choice = ui.choose("Channel memory policy", ["Selected channels only", "Follow every invitation", "Cancel"], default=2)
        if choice == 2:
            return
        values["SLACK_CHANNEL_POLICY"] = ("selected", "invited")[choice]
    if kind == "history":
        days = ("7", "30", "90")
        choice = ui.choose("Slack history window", [f"Last {day} days" for day in days] + ["Cancel"])
        if choice == 3:
            return
        values["MFS_SLACK_HISTORY_DAYS"] = days[choice]
    if ui.choose("Prepare guided changes?", ["Continue", "Cancel"], default=1) != 0:
        return
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    draft = Path(tempfile.mkdtemp(prefix="settings-", dir=root))
    initialize(draft)
    project = home / "integrations/slack-cli"
    if kind == "app":
        for key in ("SLACK_APP_ID", "SLACK_TEAM_ID", "SLACK_APP_TOKEN", "SLACK_BOT_TOKEN",
                    "SLACK_ALLOWED_USER_IDS", "SLACK_CHANNEL_ID", "SLACK_CHANNEL_IDS", "MFS_SLACK_TOKEN",
                    "MFS_SLACK_CONNECTOR_CONFIG", "MFS_SLACK_CONNECTOR_URI"):
            values.pop(key, None)
        prefix = f"slack://tag-{original.get('SLACK_TEAM_ID', '').lower()}"
        values["MFS_ALLOWED_SCOPES"] = ",".join(scope for scope in values.get("MFS_ALLOWED_SCOPES", "").split(",")
                                                if scope.strip() != prefix and not scope.strip().startswith(prefix + "/"))
        settings.save_config(draft / "integrations/slack-cli/tag-kept-app.json", {
            "app_id": original.get("SLACK_APP_ID", ""), "team_id": original.get("SLACK_TEAM_ID", ""),
            "saved_bot_name": original.get("OPENTAG_BOT_NAME", ""),
        })
    else:
        if project.exists():
            if project.is_symlink():
                raise RuntimeError("Refusing to copy a symlinked Slack project.")
            shutil.copytree(project, draft / "integrations/slack-cli")
        else:
            settings.save_config(draft / "integrations/slack-cli/.slack/config.json", {"manifest": {"source": "remote"}})
            settings.save_config(draft / "integrations/slack-cli/.slack/hooks.json", {"hooks": {}})
        connector = Path(values.get("MFS_SLACK_CONNECTOR_CONFIG", ""))
        if connector.is_file():
            target = draft / "integrations/mfs/connectors/draft.toml"
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(connector, target)
            target.chmod(0o600)
            values["MFS_SLACK_CONNECTOR_CONFIG"] = str(target)
        if kind == "credentials":
            for key in ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "MFS_SLACK_TOKEN"):
                values.pop(key, None)
        # Rebuild this workspace's scopes from the newly approved channel set.
        prefix = f"slack://tag-{values.get('SLACK_TEAM_ID', '').lower()}"
        values["MFS_ALLOWED_SCOPES"] = ",".join(scope for scope in values.get("MFS_ALLOWED_SCOPES", "").split(",")
                                                if scope.strip() != prefix and not scope.strip().startswith(prefix + "/"))
    draft_config = draft / "config/settings.json"
    settings.save_config(draft_config, values)
    settings.save_config(draft / "active-settings.json", original)
    settings.save_config(pointer, {"path": str(draft), "kind": kind})
    run_draft(home, draft, kind, original)


def run_draft(home: Path, draft: Path, kind: str, original: dict[str, str]) -> None:
    draft_config = draft / "config/settings.json"
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("SLACK_", "MFS_", "OPENTAG_"))}
    environment.update(runtime_environment(draft), OPENTAG_ENV_FILE=str(draft_config))
    receipt = draft / "state/setup-approved.json"
    receipt.unlink(missing_ok=True)
    command = [sys.executable, str(lifecycle.ROOT / "scripts/opentag_setup.py"), "--config", str(draft_config),
               "--review", "--no-start", "--completion-file", str(receipt)]
    if kind in {"channels", "app"}:
        command.append("--review-channels")
    ui.message("Preparing a separate draft. Slack actions still require their normal approvals.")
    result = subprocess.call(command, env=environment)
    if result != 0 or not receipt.is_file():
        ui.message(f"Active settings unchanged. Draft saved at: {draft}")
        return
    if ui.choose("Apply validated changes?", ["Apply changes", "Keep current settings"], default=1) != 0:
        ui.message("Active settings unchanged.")
        return
    commit(home, draft, original)
    pointer = home / "state/settings-draft.json"
    if pointer.is_file() and json.loads(pointer.read_text()).get("path") == str(draft):
        pointer.unlink()
    ui.message(f"Changes saved. Previous settings and app links are kept in: {draft}")
    ui.message("Run tag start to connect and apply the approved memory configuration.")
