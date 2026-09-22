"""Retryable migrations from legacy Tag homes to per-instance user workspaces."""
from __future__ import annotations

import json
import filecmp
import os
from pathlib import Path
import shutil
import tempfile

try:
    from . import tag_config
    from .tag_locks import LifecycleLock
except ImportError:
    import tag_config
    from tag_locks import LifecycleLock

VERSION = 1
INSTALLATION_STATE = {"mfs.json", "mfs.log", "mfs.migrating", "install.lock", "update-check.json", "legacy-command.json"}


def _rewrite(value, mappings):
    if isinstance(value, dict):
        return {k: _rewrite(v, mappings) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite(v, mappings) for v in value]
    if isinstance(value, str):
        for old, new in mappings:
            if value == str(old) or value.startswith(str(old) + os.sep):
                return str(new) + value[len(str(old)):]
    return value


def _copy(source: Path, destination: Path, mappings=()):
    if source.is_symlink():
        target = os.readlink(source)
        if destination.is_symlink() and os.readlink(destination) == target:
            return
        if destination.exists() or destination.is_symlink():
            raise RuntimeError(f"Migration conflict at {destination}; both originals were preserved")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.symlink_to(target, target_is_directory=source.is_dir())
        return
    if destination.is_symlink():
        raise RuntimeError(f"Migration destination is a symlink: {destination}")
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        for child in source.iterdir():
            if mappings and child.name.endswith((".lock", ".guard")):
                continue
            _copy(child, destination / child.name, mappings)
        return
    data = None
    if mappings and source.suffix == ".json":
        value = json.loads(source.read_bytes())
        data = (json.dumps(_rewrite(value, mappings), indent=2, sort_keys=True) + "\n").encode()
    if destination.exists():
        if data is None and filecmp.cmp(source, destination, shallow=False):
            return
        if data is not None:
            try:
                if json.loads(destination.read_bytes()) == json.loads(data):
                    return
            except (ValueError, UnicodeError):
                pass  # Preserve malformed destination files as conflicts too.
        existing = destination.read_bytes() if destination.name == "config.toml" else b""
        # Workspace initialization may have created only this empty template.
        template = (destination.name == "config.toml" and destination.parent.name == ".codex"
                    and existing == (
                        '# TAG-only Codex defaults and MCP servers go here.\n'
                        '# model = "gpt-example"\n# model_reasoning_effort = "high"\n'
                        '# service_tier = "default"\n# [mcp_servers.NAME]\n'
                    ).encode())
        if not template:
            raise RuntimeError(f"Migration conflict at {destination}; both originals were preserved")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".migrate-", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            if data is None:
                with source.open("rb") as original:
                    shutil.copyfileobj(original, stream)
            else:
                stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        shutil.copystat(source, temporary)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def migrate(context, lifecycle) -> bool:
    """Preserve originals; only copy while the affected managed bridge is stopped."""
    root, home, workspace = context.installation_root, context.home, context.workspace
    marker = home / "state/layout-migrations.json"
    if marker.is_file() and json.loads(marker.read_text()).get("version") == VERSION:
        return False
    legacy = context.is_default and (root / "config/settings.json").is_file()
    sources = [(home / "workspace", workspace)] if home / "workspace" != workspace else []
    if context.is_default:
        sources.insert(0, (root / "workspace", workspace))
    sources = [(old, new) for old, new in sources if old.exists()]
    if not legacy and not sources:
        return False
    with LifecycleLock(root / "state/layout.lock"):
        with LifecycleLock(home / "state/start.lock"):
            if marker.is_file() and json.loads(marker.read_text()).get("version") == VERSION:
                return False
            # Identity-checked stop never signals an unrelated reused PID.
            if legacy:
                lifecycle.stop_process(root, "slack")
            lifecycle.stop_process(home, "slack")
            mappings = [*sources, (root / "integrations", home / "integrations"),
                        (root / "config", home / "config"), (root / "state", home / "state")]
            if legacy:
                _copy(root / "config", home / "config", mappings)
                if (root / "integrations").is_dir():
                    _copy(root / "integrations", home / "integrations")
                for entry in (root / "state").iterdir():
                    if entry.name in INSTALLATION_STATE or entry.name.endswith((".lock", ".guard")):
                        continue
                    _copy(entry, home / "state" / entry.name, mappings)
            for old, new in sources:
                _copy(old, new)
            tag_config.save_config(marker, {"version": VERSION})
    return True
