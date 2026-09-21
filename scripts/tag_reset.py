"""Recoverable local reset with separately confirmed remote app deletion."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import shutil
import tempfile
import subprocess
import sys
from datetime import datetime, timezone

try:
    import setup_ui as ui
    import tag_config as settings
    import slack_app_create
except ImportError:
    from scripts import setup_ui as ui, tag_config as settings, slack_app_create


def selected_app(home: Path) -> dict | None:
    """Use explicit saved identities, never a guessed app or CLI default."""
    try:
        values = settings.load_config(settings.config_path(home))
    except (OSError, ValueError):
        return None
    app_id, team_id = values.get("SLACK_APP_ID", ""), values.get("SLACK_TEAM_ID", "")
    if not re.fullmatch(r"A[A-Z0-9]+", app_id) or not re.fullmatch(r"T[A-Z0-9]+", team_id):
        return None
    name = values.get("OPENTAG_BOT_NAME", "Not saved")
    name = "".join(char for char in name if char.isprintable())[:100]
    return {"app_id": app_id, "team_id": team_id, "saved_bot_name": name}


def check_app_link(project: Path, app: dict) -> None:
    if not project.is_dir() or project.is_symlink():
        raise RuntimeError("Slack app-link metadata is unavailable. Nothing was reset or deleted.")
    ids = slack_app_create.saved_app_ids(project, app["team_id"])
    if ids != {app["app_id"]}:
        raise RuntimeError("Saved settings and Slack app links do not identify the same single app. Nothing was reset or deleted.")


def delete_slack_app(home: Path, backup: Path, app: dict, executable: str) -> bool:
    """Delete only the confirmed ID, retaining the original backup metadata."""
    record = dict(app, status="attempting")
    journal = backup / "app-deletion.json"
    journal.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    try:
        with tempfile.TemporaryDirectory(prefix="delete-app-", dir=home / "tmp") as temporary:
            project = Path(temporary) / "slack-cli"
            shutil.copytree(backup / "slack-cli", project)
            check_app_link(project, app)
            result = subprocess.run(
                [executable, "app", "delete", "--app", app["app_id"], "--team", app["team_id"],
                 "--force", "--skip-update", "--no-color"],
                cwd=project, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90,
            )
        confirmed = result.returncode == 0
        record["status"] = "deleted" if confirmed else "unverified"
    except (OSError, RuntimeError, subprocess.TimeoutExpired, KeyboardInterrupt):
        confirmed = False
        record["status"] = "unverified"
    journal.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if not confirmed:
        ui.message(f"App deletion was not confirmed. Check app {app['app_id']} in Slack before continuing.")
        ui.message(f"Local setup is backed up at: {backup}")
        ui.message("Setup was not restarted. Once the app state is checked, run tag setup.")
    return confirmed


def archive_setup(home: Path, lifecycle, *, expected_app: dict | None = None) -> Path:
    """Serialize with starts/settings writes and move exact setup targets to backup."""
    lifecycle.initialize(home)
    config = settings.config_path(home).absolute()
    targets = (
        (config, "settings.json"),
        (config.with_name("setup-progress.json"), "setup-progress.json"),
        (home / "integrations/slack-cli", "slack-cli"),
        (home / "state/slack-memory.json", "slack-memory.json"),
    )
    for source, name in targets:
        if source.is_symlink():
            raise RuntimeError(f"Cannot reset a symlinked setup path: {source}")
        if source.exists() and not (source.is_dir() if name == "slack-cli" else source.is_file()):
            raise RuntimeError(f"Unexpected setup path type; nothing was reset: {source}")
    start_lock = home / "state/start.lock"
    try:
        start_lock.mkdir()
    except FileExistsError:
        raise RuntimeError("Another start or reset is in progress; retry after it finishes") from None
    config_lock = config.with_name(config.name + ".lock")
    locked = False
    try:
        config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(config_lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise RuntimeError("Another settings update is in progress; retry after it finishes") from None
        os.close(descriptor)
        locked = True
        if expected_app is not None:
            if selected_app(home) != expected_app:
                raise RuntimeError("The selected Slack app changed during confirmation. Nothing was reset or deleted.")
            check_app_link(home / "integrations/slack-cli", expected_app)
        # Use the same identity-checked lifecycle as `tag stop`. A failure leaves
        # saved answers intact and never starts a second onboarding flow.
        lifecycle.stop_process(home, "slack")
        backup_root = home / "config/backups"
        backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = Path(tempfile.mkdtemp(prefix=f"setup-{stamp}-", dir=backup_root))
        existing = [(source, backup / name) for source, name in targets if source.exists()]
        manifest = {destination.name: str(source) for source, destination in existing}
        (backup / "restore-paths.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        moved = []
        try:
            for source, destination in existing:
                shutil.move(str(source), str(destination))
                moved.append((source, destination))
        except (OSError, shutil.Error) as exc:
            try:
                for source, destination in reversed(moved):
                    shutil.move(str(destination), str(source))
            except (OSError, shutil.Error) as rollback_error:
                raise RuntimeError(f"Reset failed; recover saved files from {backup}. Services remain stopped.") from rollback_error
            raise RuntimeError("Reset failed; saved setup was restored. Services remain stopped.") from exc
        return backup
    finally:
        if locked:
            config_lock.unlink(missing_ok=True)
        start_lock.rmdir()


def reset_and_setup(home: Path, lifecycle) -> int:
    ui.notice("Reset Tag setup?",
              "Tag will stop its managed services and back up saved answers and local Slack app-link checkpoints. "
              "Then setup will start from the beginning.\n"
              "Your workspace, skills, indexed memory, and CLI sign-ins are kept. "
              "Deleting the Slack app is optional and requires a separate confirmation.",
              footer=f"Settings: {settings.config_path(home)}")
    try:
        if ui.choose("Continue?", ["Cancel", "Reset and redo setup"], default=0) != 1:
            ui.message("Reset cancelled. Nothing changed.")
            return 0
        app = selected_app(home)
        delete_app = False
        executable = None
        if app:
            ui.notice("Which Slack app will this affect?",
                      f"Saved bot name: {app['saved_bot_name']}\n"
                      f"App ID: {app['app_id']}\nWorkspace Team ID: {app['team_id']}",
                      footer="These are the saved identifiers for this Tag installation.")
            action = ui.choose("Delete this Slack app too?",
                               ["Keep the Slack app", "Permanently delete this Slack app", "Cancel reset"], default=0)
            if action == 2:
                raise ui.Paused()
            delete_app = action == 1
            if delete_app:
                check_app_link(home / "integrations/slack-cli", app)
                search_path = str(home / "integrations/bin") + os.pathsep + os.environ.get("PATH", "")
                executable = shutil.which("slack", path=search_path)
                if not executable:
                    raise RuntimeError("Slack CLI is unavailable. Nothing was reset or deleted. Keep the app or install Slack CLI first.")
                ui.notice("Permanently delete Slack app?",
                          f"App {app['app_id']} in workspace {app['team_id']} will be uninstalled and permanently deleted. "
                          "Its Slack app data will be deleted too. The local backup cannot restore the remote app.")
                if input(f"Type {app['app_id']} to delete it (Enter cancels reset): ").strip() != app["app_id"]:
                    raise ui.Paused()
        else:
            ui.message("No reliable saved App ID and Team ID were found. No Slack app will be deleted.")
    except (ui.Paused, KeyboardInterrupt, EOFError):
        print()
        ui.message("Reset cancelled. Nothing changed.")
        return 0
    backup = archive_setup(home, lifecycle, expected_app=app if delete_app else None)
    print()
    ui.message(f"Setup reset. Previous setup is backed up at: {backup}")
    if delete_app:
        if not delete_slack_app(home, backup, app, executable):
            return 1
        ui.message(f"Slack app {app['app_id']} was deleted from workspace {app['team_id']}. This cannot be undone.")
    else:
        # Re-read the archived identity so the suggestion reflects what was
        # actually reset, not potentially stale answers from confirmation.
        try:
            saved = settings.read_config(backup / "settings.json") if (backup / "settings.json").exists() else {}
        except (OSError, ValueError):
            saved = {}
        app_id, team_id = saved.get("SLACK_APP_ID", ""), saved.get("SLACK_TEAM_ID", "")
        if re.fullmatch(r"A[A-Z0-9]+", app_id) and re.fullmatch(r"T[A-Z0-9]+", team_id):
            settings.save_config(home / "integrations/slack-cli/tag-kept-app.json", {
                "app_id": app_id, "team_id": team_id,
                "saved_bot_name": saved.get("OPENTAG_BOT_NAME", ""),
            })
        ui.message("Existing Slack apps are kept. Choose Use an existing app and paste its App ID to reconnect.")
    ui.message("If setup pauses, run tag setup to continue.")
    print()
    target = [] if os.getenv("TAG_ID", "default") == "default" else ["--tag", os.environ["TAG_ID"]]
    return subprocess.call([sys.executable, str(lifecycle.ROOT / "scripts/tag_cli.py"), "setup", *target])
