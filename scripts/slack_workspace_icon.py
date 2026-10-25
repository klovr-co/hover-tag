"""Keep a local copy of each Tag's Slack workspace icon for Tag.app and `tag list`.

The icon is cosmetic. Every failure keeps the last good copy, and nothing here
may stop a Tag from starting. Apps load the local file, so they never fetch
from Slack themselves.
"""
from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    import slack_channels
except ImportError:
    from scripts import slack_channels


RECORD = "state/workspace-icon.json"
# Big enough for a crisp 2x header icon; Slack offers these sizes in team.info.
SIZES = ("image_132", "image_102", "image_88", "image_230", "image_68", "image_44", "image_34")
TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
MAX_BYTES = 1_000_000


def _record(home: Path) -> dict[str, Any]:
    try:
        value = json.loads((home / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def path(home: Path) -> Path | None:
    """The saved icon, or None when the workspace uses Slack's default icon or none was saved yet."""
    name = _record(home).get("file")
    # Only a file this module wrote; the record is never trusted to point elsewhere.
    if not isinstance(name, str) or Path(name).suffix not in TYPES.values() or name != f"workspace-icon{Path(name).suffix}":
        return None
    candidate = home / "state" / name
    return candidate if candidate.is_file() else None


def _clear(home: Path) -> None:
    for stale in (home / "state").glob("workspace-icon.*"):
        stale.unlink(missing_ok=True)


def download(url: str) -> tuple[bytes, str]:
    """Fetch one icon over HTTPS; returns its bytes and file extension."""
    if urllib.parse.urlparse(url).scheme != "https":
        raise RuntimeError("Slack returned an icon address that isn't HTTPS")
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=15) as response:
            kind = response.headers.get_content_type()
            data = response.read(MAX_BYTES + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError("the workspace icon couldn't be downloaded") from exc
    if kind not in TYPES:
        raise RuntimeError("Slack returned something other than an image")
    if len(data) > MAX_BYTES:
        raise RuntimeError("the workspace icon is unexpectedly large")
    return data, TYPES[kind]


def refresh(
    home: Path,
    token: str,
    team_id: str,
    *,
    api: Callable[[str, str, dict[str, str]], dict[str, Any]] = slack_channels.slack_api,
    fetch: Callable[[str], tuple[bytes, str]] = download,
) -> str:
    """Save the workspace's current icon.

    Returns "saved" after a new icon is stored, "current" when the saved icon
    still matches Slack, "default" when the workspace uses Slack's default
    icon, or "needs_permission" when Slack hasn't granted team:read yet (an
    optional permission; see slack_manifest_migrations). Raises RuntimeError,
    keeping any saved icon, when Slack can't say.
    """
    try:
        payload = api(token, "team.info", {"team": team_id} if team_id else {})
    except slack_channels.MissingScope:
        return "needs_permission"
    except RuntimeError as exc:
        raise RuntimeError("Slack didn't share the workspace details") from exc
    team = payload.get("team") if isinstance(payload, dict) else None
    icon = team.get("icon") if isinstance(team, dict) else None
    if not isinstance(icon, dict):
        raise RuntimeError("Slack didn't include the workspace icon")
    url = next((icon[size] for size in SIZES if isinstance(icon.get(size), str) and icon[size]), "")
    if icon.get("image_default") is True or not url:
        _clear(home)
        (home / RECORD).unlink(missing_ok=True)
        return "default"
    if _record(home).get("url") == url and path(home) is not None:
        return "current"
    data, suffix = fetch(url)
    target = home / "state" / f"workspace-icon{suffix}"
    _write(target, data)
    _write(home / RECORD, (json.dumps({"url": url, "file": target.name, "team_id": team_id}, indent=2) + "\n").encode())
    # Drop an older icon saved in another format only after the new one is recorded.
    for stale in (home / "state").glob("workspace-icon.*"):
        if stale.name not in {target.name, Path(RECORD).name}:
            stale.unlink(missing_ok=True)
    return "saved"
