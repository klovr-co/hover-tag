"""Approved Slack CLI app creation, with durable duplicate-prevention checkpoints."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

try:
    import setup_ui as ui
    import tag_config as settings
except ImportError:
    from scripts import setup_ui as ui, tag_config as settings

ROOT = Path(__file__).resolve().parents[1]


def read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise RuntimeError(f"Setup metadata is unreadable: {path.name}. Existing files were kept.") from None


def saved_app_ids(project: Path, team_id: str) -> set[str]:
    """Read only Slack's non-secret identity metadata, never CLI credentials."""
    ids = set()
    for name in ("apps.json", "apps.dev.json"):
        path = project / ".slack" / name
        if not path.exists():
            continue
        document = read_object(path)
        # apps.json wraps deployed records in "apps"; apps.dev.json is a flat
        # team map. Older apps.json files can also contain a "dev" wrapper.
        containers = [document]
        if name == "apps.json":
            for key in ("apps", "dev"):
                if key in document:
                    if not isinstance(document[key], dict):
                        raise RuntimeError("Slack's saved app metadata has an invalid wrapper. Existing links were kept.")
                    containers.append(document[key])
        for records in containers:
            record = records.get(team_id)
            if record is None:
                continue
            if (not isinstance(record, dict) or record.get("team_id") != team_id
                    or not re.fullmatch(r"A[A-Z0-9]+", str(record.get("app_id", "")))):
                raise RuntimeError("Slack's saved app identity is inconsistent. Existing links were kept.")
            ids.add(record["app_id"])
    return ids


def linked_app(project: Path, team_id: str) -> str:
    ids = saved_app_ids(project, team_id)
    if len(ids) > 1:
        raise RuntimeError("More than one app is linked to this workspace. Choose an existing App ID; no app was created.")
    return next(iter(ids), "")


def is_tag_manifest_hook(hooks: dict) -> bool:
    """Recognize our generated command independently of a temporary Python path."""
    if set(hooks) != {"hooks"} or not isinstance(hooks["hooks"], dict):
        return False
    if set(hooks["hooks"]) != {"get-manifest"}:
        return False
    command = hooks["hooks"]["get-manifest"]
    if not isinstance(command, str):
        return False
    try:
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return False
    if os.name == "nt":
        parts = [part.strip('"') for part in parts]
    if len(parts) != 2:
        return False
    interpreter, helper = map(Path, parts)
    return (
        interpreter.is_absolute()
        and bool(re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?", interpreter.name, re.IGNORECASE))
        and helper == ROOT / "scripts/slack_manifest_hook.py"
    )


def prepare_project(project: Path, name: str) -> dict:
    import yaml

    manifest = yaml.safe_load((ROOT / "slack-app-manifest.yaml").read_text(encoding="utf-8"))
    manifest["display_information"]["name"] = name
    manifest["features"]["bot_user"]["display_name"] = name
    # Remote source prevents subsequent installations from overwriting app settings.
    config = read_object(project / ".slack/config.json")
    if config.get("manifest", {}).get("source") != "remote":
        raise RuntimeError("This Slack project uses a different manifest source. Existing configuration was kept.")
    # The helper uses only stdlib: resolve uv's temporary interpreter symlink.
    command = [str(Path(sys.executable).resolve()), str(ROOT / "scripts/slack_manifest_hook.py")]
    hook = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
    hook_path = project / ".slack/hooks.json"
    hooks = read_object(hook_path)
    expected = {"hooks": {"get-manifest": hook}}
    if hooks != {"hooks": {}} and not is_tag_manifest_hook(hooks):
        raise RuntimeError("This Slack project has custom hooks. They were kept; use a separate Tag home for a new app.")
    manifest_path = project / "manifest.json"
    if manifest_path.exists() and read_object(manifest_path) != manifest:
        raise RuntimeError("This Slack project already has a different manifest. It was not overwritten.")
    settings.save_config(manifest_path, manifest)
    settings.save_config(hook_path, expected)
    return manifest


def is_installed(project: Path, team_id: str, app_id: str) -> bool:
    try:
        result = subprocess.run(
            [shutil.which("slack") or "slack", "app", "list", "--team", team_id,
             "--app", app_id, "--skip-update", "--no-color"],
            cwd=project, check=False, text=True, capture_output=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode:
        return False
    # Bind all three fields to the same app block; another app's status cannot pass.
    blocks = re.split(r"App\s+ID:\s*", result.stdout)
    return any(
        re.match(re.escape(app_id) + r"\s", block)
        and re.search(r"Team\s+ID:\s*" + re.escape(team_id) + r"\s", block)
        and re.search(r"Status:\s*Installed\b", block)
        for block in blocks[1:]
    )


def create_app(project: Path, team_id: str, config_path: Path, run_cli) -> str:
    """Create/install only after approval; never blindly repeat an ambiguous creation."""
    marker = project / "tag-create.json"
    state = read_object(marker) if marker.exists() else {}
    values = settings.load_config(config_path)
    app_id = linked_app(project, team_id)
    if state:
        if state.get("team_id") != team_id:
            raise RuntimeError("Saved app creation belongs to another workspace. No app was changed.")
        if state.get("app_id") and state["app_id"] != app_id:
            raise RuntimeError("The saved creation and Slack app link disagree. No app was changed.")
        if values.get("SLACK_APP_ID") and values["SLACK_APP_ID"] != app_id:
            raise RuntimeError("The selected app differs from the pending creation. No app was changed.")
        if not app_id:
            raise RuntimeError(
                "The previous creation outcome is unknown. Check Slack apps before retrying. "
                "Link the created app with tag config set SLACK_APP_ID <App ID>; "
                "Tag will not automatically create a duplicate."
            )
    elif app_id:
        raise RuntimeError("An app is already linked to this workspace. Link its App ID instead of creating another.")
    else:
        manifest = prepare_project(project, values.get("OPENTAG_BOT_NAME", "Tag"))
        print()
        ui.message(f"Create {manifest['display_information']['name']} in workspace {team_id}?")
        ui.message("Slack CLI will create and install the app. Tag still runs on this computer.")
        ui.message("Requested bot permissions: " + ", ".join(manifest["oauth_config"]["scopes"]["bot"]))
        ui.message("Channel indexing and service startup are approved separately.")
        if ui.choose("Create this app?", ["Create and install app", "Save and exit"], default=1) == 1:
            raise ui.Paused()
        state = {"team_id": team_id, "status": "attempting"}
        # Exclusive checkpoint prevents two setup processes from creating two apps.
        try:
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise RuntimeError("Another setup started app creation. Exit and resume after it finishes.") from None
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            run_cli(["app", "install", "--team", team_id, "--environment", "deployed"],
                    cwd=project, interactive=True)
        finally:
            # CLI saves identity before installation. Recover it even on Ctrl-C/error.
            app_id = linked_app(project, team_id)
            if app_id:
                state.update(app_id=app_id, status="created")
                settings.save_config(marker, state)
                settings.update_config(config_path, {"SLACK_APP_ID": app_id})
        if not app_id:
            raise RuntimeError("Slack did not save an App ID. Creation is unverified; rerun setup for recovery guidance.")

    settings.update_config(config_path, {"SLACK_APP_ID": app_id})
    while not is_installed(project, team_id, app_id):
        print()
        ui.message(f"App {app_id} is saved; installation is not confirmed.")
        ui.message("Slack may need administrator approval. You can leave and resume later.")
        action = ui.choose("Finish app installation", ["Check approval again", "Continue installation", "Save and exit"], default=2)
        if action == 2:
            raise ui.Paused()
        if action == 1:
            # An explicit App ID prevents a new creation. Remote source preserves settings.
            if read_object(project / ".slack/config.json").get("manifest", {}).get("source") != "remote":
                raise RuntimeError("Manifest source changed. Installation stopped to preserve app settings.")
            if linked_app(project, team_id) != app_id:
                raise RuntimeError("The saved Slack link changed. Installation stopped.")
            run_cli(["app", "install", "--team", team_id, "--app", app_id], cwd=project, interactive=True)
    state.update(app_id=app_id, status="installed")
    settings.save_config(marker, state)
    ui.message("✓ App installed · connection checks are next")
    return app_id
