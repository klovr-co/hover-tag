"""Retryable migration that names a Tag after its Slack IDs.

A Tag is created before setup knows its Slack app: earlier releases used
``default`` for the first Tag, and ``tag add`` uses a provisional ``new-tag``
name. Once setup has created or linked the Tag's Slack app, this migration
renames its folder in one step to ``TEAM_ID-APP_ID`` in lowercase (for example
``~/Tag/default`` to ``~/Tag/t0abc123-a0xyz789``), rewrites saved paths inside
Tag's own data, and keeps plain ``tag start`` pointing at the main Tag.

Every Tag has its own Slack app, so the name is unique even when several Tags
share a workspace or a Slack display name. Lowercase keeps names distinct on
case-insensitive file systems.

A plan file written before the rename makes every step retryable: an
interrupted run resumes from whichever side of the rename it reached and never
chooses a different name. Working files are moved, never copied or rewritten.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile

try:
    from . import tag_config, tag_instances
    from .tag_layout import _rewrite
    from .tag_locks import LifecycleLock
    from .tag_paths import data_home
except ImportError:
    import tag_config
    import tag_instances
    from tag_layout import _rewrite
    from tag_locks import LifecycleLock
    from tag_paths import data_home

VERSION = 1
SKIPPED_DIRECTORIES = {"tmp", "workspace"}
TOKEN_LINE = re.compile(r'(?m)^(token\s*=\s*)("(?:[^"\\]|\\.)*")')


def id_name(team_id: str, app_id: str) -> str:
    """The Tag's permanent name: its workspace and app IDs, lowercase."""
    if not re.fullmatch(r"T[A-Z0-9]+", team_id) or not re.fullmatch(r"A[A-Z0-9]+", app_id):
        raise ValueError("A Tag is named after valid Slack team and app IDs")
    return tag_instances.validate_name(f"{team_id}-{app_id}".lower(), allow_default=False)


def _plan_path(root: Path, tag_id: str) -> Path:
    return root / f"state/rename-{tag_id}.json"


def _read_plan(root: Path, tag_id: str) -> dict | None:
    path = _plan_path(root, tag_id)
    if not path.is_file():
        return None
    plan = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or plan.get("version") != VERSION:
        raise RuntimeError(f"Cannot read the Tag rename plan at {path}; it was preserved")
    tag_instances.validate_name(plan.get("to", ""), allow_default=False)
    return plan


def _folder(home: Path) -> Path:
    """The directory that moves: the user folder for ~/Tag/NAME/.tag homes."""
    return home.parent if home.name == ".tag" else home


def lookup_workspace_name(home: Path, values: dict[str, str]) -> str | None:
    """Find the Slack workspace's display name without asking anyone."""
    if name := tag_instances.workspace_name(home):
        return name
    token, team_id = values.get("SLACK_BOT_TOKEN", ""), values.get("SLACK_TEAM_ID", "")
    if not token or not team_id:
        return None
    try:
        import slack_channels
    except ImportError:
        from scripts import slack_channels
    try:
        payload = slack_channels.slack_api(token, "auth.test", {})
    except (slack_channels.SlackChannelError, RuntimeError, OSError, ValueError):
        return None
    team = payload.get("team")
    return team if payload.get("team_id") == team_id and isinstance(team, str) else None


def pending(root: Path, tag_id: str) -> bool:
    """Whether this Tag still carries a name chosen before setup finished."""
    if _read_plan(root, tag_id):
        return True
    if tag_id == tag_instances.DEFAULT_TAG:
        return True
    try:
        home = tag_instances.resolve(root, tag_id).home
        record = json.loads((home / "instance.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(record.get("provisional"))


def _rewrite_file(path: Path, mappings: list[tuple[Path, Path]]) -> None:
    original = path.read_bytes()
    if path.suffix == ".json":
        try:
            value = json.loads(original)
        except (ValueError, UnicodeError):
            return  # Leave unreadable files exactly as they were.
        updated = _rewrite(value, mappings)
        if updated == value:
            return
        data = (json.dumps(updated, indent=2, sort_keys=True) + "\n").encode()
    else:
        content = original.decode("utf-8")
        data = TOKEN_LINE.sub(
            lambda match: match[1] + json.dumps(_rewrite(json.loads(match[2]), mappings)), content
        ).encode()
        if data == original:
            return
    descriptor, temporary = tempfile.mkstemp(prefix=".rename-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _rewrite_tree(home: Path, mappings: list[tuple[Path, Path]]) -> None:
    for path in sorted(home.rglob("*")):
        relative = path.relative_to(home)
        if relative.parts[0] in SKIPPED_DIRECTORIES or path.is_symlink() or not path.is_file():
            continue
        if path.suffix in {".json", ".toml"} and not path.name.endswith((".lock", ".guard")):
            _rewrite_file(path, mappings)


def _finish_identity(home: Path, name: str, workspace: str | None) -> None:
    path = home / "instance.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    updated = {**record, "id": name}
    updated.pop("provisional", None)
    if workspace:
        updated["workspace_name"] = workspace
    if updated != record:
        tag_config.save_config(path, updated)


def migrate(root: Path, lifecycle, tag_id: str = tag_instances.DEFAULT_TAG) -> str | None:
    """Rename a provisionally named Tag after its Slack name. Returns the new name.

    Returns None when there is nothing to do yet: the Tag has a final name, its
    Slack app is not set up, or its data still awaits the layout migration.
    """
    root = root.expanduser().absolute()
    plan = _read_plan(root, tag_id)
    old_home = data_home(root, tag_id)
    old_folder = _folder(old_home)
    if plan is None:
        if not pending(root, tag_id):
            return None
        try:
            context = tag_instances.resolve(root, tag_id)
        except ValueError:
            return None
        # Legacy homes move to the current layout first; rename only after that.
        if context.home != old_home or not (old_home / "instance.json").is_file():
            return None
        settings = old_home / "config/settings.json"
        values = tag_config.load_config(settings) if settings.is_file() else {}
        team_id, app_id = values.get("SLACK_TEAM_ID", ""), values.get("SLACK_APP_ID", "")
        if not team_id or not app_id:
            return None
        plan = {"version": VERSION, "to": id_name(team_id, app_id),
                # Display only; the name itself never depends on it.
                "workspace_name": lookup_workspace_name(old_home, values)}
    name = plan["to"]
    new_home = data_home(root, name)
    new_folder = _folder(new_home)
    with LifecycleLock(root / "state/layout.lock"):
        if old_folder.exists() and new_folder.exists():
            raise RuntimeError(
                f"Cannot rename Tag '{tag_id}' to '{name}': {new_folder} already exists. Both were preserved."
            )
        if old_folder.exists():
            start_lock = LifecycleLock(old_home / "state/start.lock").acquire()
            try:
                tag_config.save_config(_plan_path(root, tag_id), plan)
                # Identity-checked stop never signals an unrelated process.
                lifecycle.stop_process(old_home, "slack")
                new_folder.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.rename(old_folder, new_folder)
                # The held lock moved with its folder; release it there.
                start_lock.path = new_home / "state/start.lock"
            finally:
                start_lock.release()
        elif not new_folder.exists():
            raise RuntimeError(f"Tag '{tag_id}' disappeared during its rename to '{name}'. Nothing else was changed.")
        with LifecycleLock(new_home / "state/start.lock"):
            _rewrite_tree(new_home, [(old_folder, new_folder)])
            _finish_identity(new_home, name, plan.get("workspace_name"))
            main = tag_instances.main_tag(root)
            others = [item for item in tag_instances.discover(root)
                      if item["id"] not in {name, tag_id} and Path(str(item["home"])).exists()]
            # Keep plain `tag start` on the Tag it already selected.
            if main == tag_id or (main is None and (tag_id == tag_instances.DEFAULT_TAG or not others)):
                tag_instances.set_main_tag(root, name)
            # Verify the renamed Tag resolves before declaring the work done.
            tag_instances.resolve(root, name)
            _plan_path(root, tag_id).unlink(missing_ok=True)
    return name
