"""Versioned, retryable Slack avatar cache shared by the CLI and Tag.app.

Version 1 backfills existing installations from the authenticated bot's profile.
Only a verified download commits the migration record. The setup upload remains
untouched; the remote profile is authoritative once it has been cached.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

try:
    from . import slack_channels, slack_identity, slack_workspace_icon, tag_config
    from .tag_locks import LifecycleLock
except ImportError:
    import slack_channels, slack_identity, slack_workspace_icon, tag_config
    from tag_locks import LifecycleLock


MIGRATION_VERSION = 1
RECORD = "state/slack-avatar.json"
REFRESH_SECONDS = 3600
RETRY_SECONDS = 300
SIZES = ("image_192", "image_512", "image_1024", "image_72", "image_48", "image_32", "image_24")


def _record(home: Path) -> dict[str, Any]:
    try:
        value = json.loads((home / RECORD).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def path(home: Path, *, team_id: str = "", app_id: str = "") -> Path | None:
    record = _record(home)
    name = record.get("file")
    if record.get("version") != MIGRATION_VERSION or not isinstance(name, str) \
            or not re.fullmatch(r"slack-avatar-[a-f0-9]{64}\.(png|jpg|gif|webp)", name):
        return None
    if team_id and record.get("team_id") != team_id or app_id and record.get("app_id") != app_id:
        return None
    candidate = home / "state" / name
    return candidate if candidate.is_file() and not candidate.is_symlink() else None


def _validate(data: bytes, suffix: str) -> None:
    signatures = {
        ".png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": data.startswith(b"\xff\xd8\xff"),
        ".gif": data[:6] in {b"GIF87a", b"GIF89a"},
        ".webp": data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    }
    if not signatures.get(suffix) or len(data) > slack_workspace_icon.MAX_BYTES:
        raise RuntimeError("Slack returned an invalid profile image")


def refresh(home: Path, values: dict[str, str], *, api=None, fetch=None) -> str:
    """Migrate or refresh the cache without changing Slack or operator settings.

    Repeated runs reuse identical bytes. Failed or interrupted downloads leave
    the previous record intact and will be retried on the next run.
    """
    token = values.get("SLACK_BOT_TOKEN", "")
    if not token:
        return "skipped"
    api = api or slack_channels.slack_api
    fetch = fetch or slack_workspace_icon.download
    team_id, app_id = values.get("SLACK_TEAM_ID", ""), values.get("SLACK_APP_ID", "")
    with LifecycleLock(home / "state/slack-avatar.lock"):
        auth = slack_identity.validate(token, team_id=team_id, app_id=app_id,
                                       enterprise_id=values.get("SLACK_ENTERPRISE_ID", ""),
                                       label="Slack avatar token", api=api)
        user_id = auth.get("user_id")
        if not isinstance(user_id, str) or not user_id or not auth.get("bot_id"):
            raise RuntimeError("Slack didn't identify the bot's profile")
        payload = api(token, "users.info", {"user": user_id, **({"team_id": team_id} if team_id else {})})
        user = payload.get("user")
        if not isinstance(user, dict) or user.get("id") != user_id or not user.get("is_bot"):
            raise RuntimeError("Slack didn't return the bot's profile")
        profile = user.get("profile")
        if not isinstance(profile, dict):
            raise RuntimeError("Slack didn't include the bot's picture")
        if app_id and profile.get("api_app_id") and profile["api_app_id"] != app_id:
            raise RuntimeError("Slack returned another app's profile")
        url = next((profile[size] for size in SIZES if isinstance(profile.get(size), str) and profile[size]), "")
        if not url:
            raise RuntimeError("Slack didn't include the bot's picture")
        record = _record(home)
        previous = path(home, team_id=team_id, app_id=app_id)
        if record.get("url") == url and record.get("user_id") == user_id and previous:
            # Verify the checkpoint's file before declaring the migration complete.
            data = previous.read_bytes()
            if hashlib.sha256(data).hexdigest() in previous.name:
                _validate(data, previous.suffix)
                return "current"
        try:
            data, suffix = fetch(url)
        except RuntimeError as exc:
            raise RuntimeError("Slack's profile picture couldn't be downloaded") from exc
        _validate(data, suffix)
        # Content-addressed paths also invalidate the desktop webview's image cache.
        digest = hashlib.sha256(data).hexdigest()
        target = home / "state" / f"slack-avatar-{digest}{suffix}"
        slack_workspace_icon._write(target, data)
        if target.read_bytes() != data:
            raise RuntimeError("The saved Slack picture couldn't be verified")
        slack_workspace_icon._write(home / RECORD, (json.dumps({
            "version": MIGRATION_VERSION, "team_id": team_id, "app_id": app_id,
            "user_id": user_id, "url": url, "file": target.name,
        }) + "\n").encode())
        for stale in (home / "state").glob("slack-avatar-*.*"):
            if stale != target and re.fullmatch(r"slack-avatar-[a-f0-9]{64}\.(png|jpg|gif|webp)", stale.name):
                stale.unlink(missing_ok=True)
        return "saved"


def refresh_safely(home: Path, values: dict[str, str]) -> str:
    """Cosmetic failures never prevent setup, readiness, or Slack requests."""
    try:
        return refresh(home, values)
    except slack_channels.MissingScope:
        return "needs_permission"
    except (OSError, ValueError, RuntimeError):
        return "unavailable"


def watch(home: Path, shutdown: threading.Event) -> None:
    """Refresh outside the socket heartbeat loop, using credentials reloaded each time."""
    while not shutdown.is_set():
        try:
            values = tag_config.read_config(home / "config/settings.json")
            result = refresh_safely(home, values)
        except (OSError, ValueError, RuntimeError):
            result = "unavailable"
        if shutdown.wait(REFRESH_SECONDS if result in {"saved", "current", "skipped"} else RETRY_SECONDS):
            return
