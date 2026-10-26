# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""Retire the local Slack search index (MFS) after the move to live Slack search.

Earlier Tags copied Slack history into a shared MFS server (a local Milvus index
with an embedding model) and searched that copy. Tags now search Slack live
(``slack_search``) and keep nothing, so on upgrade:

1. Each Tag drops its MFS settings and its private Slack-history credential
   (``retire_tag``).
2. Once per installation (``retire_installation``), while the old Tag-managed
   MFS server still answers, Tag removes every Tag's Slack connector, which
   deletes the Slack messages it indexed, then stops the server. The server's
   folder (``~/.mfs`` unless ``MFS_HOME`` says otherwise) may hold other
   people's data, so Tag never deletes it on its own:
   ``tag memory remove-old-index --yes`` does, when the person asks.

Both steps are idempotent and record completion only after checking the
result, so an interrupted run simply runs again.
"""
from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    import tag_config
    import tag_instances
except ImportError:
    from scripts import tag_config, tag_instances

VERSION = 1
MARKER = "state/migrations/live-search-v1.json"
DEFAULT_URL = "http://127.0.0.1:13619"
TAG_FILES = ("config/credentials/mfs-slack-token", "state/slack-memory.json")
INDEX_FILES = ("milvus.db", "metadata.db", "metadata.db-shm", "metadata.db-wal", "transformation_cache.db",
               "transformation_cache.db-shm", "transformation_cache.db-wal", "cache", "onnx-cache", "server.token",
               "server.toml")


def _done(path: Path) -> bool:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("version") == VERSION
    except (OSError, ValueError, AttributeError):
        return False


def retire_tag(home: Path) -> bool:
    """Drop one Tag's MFS settings and Slack-history credential. Returns whether it changed anything."""
    marker = home / MARKER
    if _done(marker):
        return False
    config = home / "config/settings.json"
    values = tag_config.load_config(config) if config.is_file() else {}
    retired = [key for key in values if key.startswith("MFS_")]
    connector = values.get("MFS_SLACK_CONNECTOR_CONFIG", "")
    if retired:
        tag_config.save_config(config, {k: v for k, v in values.items() if k not in retired})
    changed = bool(retired)
    for name in TAG_FILES:
        path = home / name
        if path.exists():
            path.unlink()
            changed = True
    if connector:
        path = Path(connector).expanduser()
        # Only a connector file Tag wrote inside this Tag's own folder.
        if path.suffix == ".toml" and path.is_file() and home.resolve() in path.resolve().parents:
            path.unlink()
            changed = True
    remaining = tag_config.load_config(config) if config.is_file() else {}
    if any(key.startswith("MFS_") for key in remaining) or (home / TAG_FILES[0]).exists():
        raise RuntimeError("Could not remove the old Slack index settings; retry tag start.")
    tag_config.save_config(marker, {"version": VERSION})
    return changed


def connector_uris(installation_root: Path) -> list[str]:
    """Every Slack connector a Tag here registered with the old index."""
    uris: list[str] = []
    for item in tag_instances.discover(installation_root):
        config = Path(str(item["home"])) / "config/settings.json"
        try:
            values = tag_config.load_config(config) if config.is_file() else {}
        except (OSError, ValueError):
            continue
        uri = values.get("MFS_SLACK_CONNECTOR_URI", "").strip()
        if uri.startswith("slack://"):
            uris.append(uri)
        team = values.get("SLACK_TEAM_ID", "").strip()
        if team:
            uris.append(f"slack://tag-{team.lower()}")
            if values.get("SLACK_APP_ID"):
                uris.append(f"slack://tag-{team.lower()}-{values['SLACK_APP_ID'].lower()}")
    return list(dict.fromkeys(uris))


def index_home() -> Path:
    return Path(os.getenv("MFS_HOME") or Path.home() / ".mfs").expanduser()


def _token() -> str:
    token = os.getenv("MFS_TOKEN", "").strip()
    if token:
        return token
    try:
        return (index_home() / "server.token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _remove_connector(url: str, uri: str) -> bool:
    """Ask a running MFS to drop one connector and its indexed records."""
    request = urllib.request.Request(
        f"{url.rstrip('/')}/v1/connectors?{urllib.parse.urlencode({'target': uri})}",
        method="DELETE", headers={"Authorization": f"Bearer {_token()}"} if _token() else {})
    try:
        with urllib.request.urlopen(request, timeout=30):
            return True
    except urllib.error.HTTPError as exc:
        return exc.code in {400, 404}  # Never registered (or already gone).
    except (OSError, ValueError):
        return False


def _healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=2) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def retire_installation(installation_root: Path, *, stop: Callable[[], None],
                        running: Callable[[], bool], url: str = DEFAULT_URL) -> dict[str, Any]:
    """Remove Tag's Slack connectors from a still-running old index, then stop it.

    ``stop`` stops the Tag-managed MFS process (identity-checked); ``running``
    says whether it still runs. Returns what happened, for the start output.
    """
    marker = installation_root / MARKER
    if _done(marker):
        return {"state": "done"}
    result: dict[str, Any] = {"state": "done", "removed": 0, "leftover": None}
    uris = connector_uris(installation_root)
    if running() and _healthy(url):
        removed = [uri for uri in uris if _remove_connector(url, uri)]
        result["removed"] = len(removed)
        if len(removed) < len(uris):
            result["state"] = "retry"  # Keep the server so the next start can finish removing.
            return result
    if running():
        stop()
        if running():
            result["state"] = "retry"
            return result
    home = index_home()
    if any((home / name).exists() for name in INDEX_FILES):
        result["leftover"] = str(home)
    tag_config.save_config(marker, {"version": VERSION, "leftover": result["leftover"]})
    shared = installation_root / "shared/mfs"
    for name in ("slack-cooldowns-v1", "slack-runtime-migration-v1.json", "slack-runtime-ready-v1.json"):
        path = shared / name
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    return result


def leftover(installation_root: Path) -> str | None:
    """The old index folder that may still hold Slack copies, if Tag left one."""
    try:
        record = json.loads((installation_root / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    folder = record.get("leftover") if isinstance(record, dict) else None
    if not isinstance(folder, str) or not any((Path(folder) / name).exists() for name in INDEX_FILES):
        return None
    return folder


def remove_old_index(installation_root: Path) -> list[str]:
    """Delete the old index's data files. Only on the person's explicit request."""
    folder = leftover(installation_root) or (str(index_home()) if any((index_home() / n).exists() for n in INDEX_FILES) else None)
    if folder is None:
        return []
    removed = []
    for name in INDEX_FILES:
        path = Path(folder) / name
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
            removed.append(str(path))
        elif path.exists() or path.is_symlink():
            path.unlink()
            removed.append(str(path))
    marker = installation_root / MARKER
    if marker.exists():
        tag_config.save_config(marker, {"version": VERSION, "leftover": None})
    return removed
