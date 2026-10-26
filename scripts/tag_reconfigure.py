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
    from .tag_paths import initialize_instance, runtime_environment, tag_home
except ImportError:
    import tag_config as settings
    import tag_cli as lifecycle
    import setup_ui as ui
    from tag_paths import initialize_instance, runtime_environment, tag_home


try:
    from .tag_locks import LifecycleLock
except ImportError:
    from tag_locks import LifecycleLock


def commit(home: Path, draft: Path, original: dict[str, str]) -> None:
    """Serialize with starts/settings writes; restore the old link on failure."""
    config = settings.config_path(home)
    start_lock = home / "state/start.lock"
    config_lock = config.with_name(config.name + ".lock")
    lifecycle_lock = LifecycleLock(start_lock).acquire()
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
        if lifecycle.process_for(home / "state/slack.json"):
            raise RuntimeError("Tag started while editing. Stop Tag before applying changes.")
        values = settings.read_config(draft / "config/settings.json")
        if settings.config_errors(values):
            raise RuntimeError("Draft settings are incomplete. Active settings were kept.")
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
        lifecycle_lock.release()


def edit(home: Path, kind: str) -> None:
    tag_id = os.getenv("TAG_ID", "default")
    target = "" if tag_id == "default" else f"{tag_id} "
    if lifecycle.process_for(home / "state/slack.json"):
        ui.notice("Stop Tag before changing its Slack settings",
                  f"Run tag {target}stop, then reopen tag {target}settings. Your current setup is unchanged.")
        return
    path = settings.config_path(home)
    original = settings.load_config(path)
    if not original:
        ui.message(f"Run tag {target}setup first.")
        return
    ui.message("Target: " + ui.display.target_detail(
        os.getenv("TAG_ID", "default"),
        original.get("SLACK_TEAM_ID", ""),
        original.get("SLACK_APP_ID", ""),
        original.get("OPENTAG_BOT_NAME", ""),
    ))
    root = home / "integrations/setup-drafts"
    pointer = home / "state/settings-draft.json"
    resumable = None
    if pointer.is_file():
        try:
            pending = json.loads(pointer.read_text(encoding="utf-8"))
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
    values.setdefault("SLACK_CHANNEL_POLICY", "selected" if original.get("SLACK_CHANNEL_IDS") else "invited")
    values.pop("SLACK_CHANNEL_ID", None)
    if kind == "policy":
        choice = ui.choose("Channel policy", ["Selected channels only", "Follow every invitation", "Cancel"], default=2)
        if choice == 2:
            return
        values["SLACK_CHANNEL_POLICY"] = ("selected", "invited")[choice]
    if ui.choose("Prepare guided changes?", ["Continue", "Cancel"], default=1) != 0:
        return
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    draft = Path(tempfile.mkdtemp(prefix="settings-", dir=root))
    initialize_instance(draft)
    project = home / "integrations/slack-cli"
    if kind == "app":
        for key in ("SLACK_APP_ID", "SLACK_TEAM_ID", "SLACK_APP_TOKEN", "SLACK_BOT_TOKEN",
                    "SLACK_ALLOWED_USER_IDS", "SLACK_CHANNEL_ID", "SLACK_CHANNEL_IDS"):
            values.pop(key, None)
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
        if kind == "credentials":
            for key in ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN"):
                values.pop(key, None)
    draft_config = draft / "config/settings.json"
    settings.save_config(draft_config, values)
    settings.save_config(draft / "active-settings.json", original)
    settings.save_config(pointer, {"path": str(draft), "kind": kind})
    run_draft(home, draft, kind, original)


def run_draft(home: Path, draft: Path, kind: str, original: dict[str, str]) -> None:
    draft_config = draft / "config/settings.json"
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("SLACK_", "MFS_", "OPENTAG_"))}
    # A setup draft keeps instance data private while installation-wide state,
    # such as telemetry preference, remains rooted at the installed Tag home.
    environment.update(
        runtime_environment(draft, installation_root=tag_home()),
        OPENTAG_ENV_FILE=str(draft_config),
    )
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
    if pointer.is_file() and json.loads(pointer.read_text(encoding="utf-8")).get("path") == str(draft):
        pointer.unlink()
    ui.message(f"Changes saved. Previous settings and app links are kept in: {draft}")
    tag_id = os.getenv("TAG_ID", "default")
    target = "" if tag_id == "default" else f"{tag_id} "
    ui.message(f"Run tag {target}start to connect and apply the approved changes.")
