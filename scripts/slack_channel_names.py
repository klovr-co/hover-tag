"""Persistent display names, independent of Slack memory and authorization.

One private, atomic record per workspace/channel avoids lost updates between
startup and the membership worker. Reads never contact Slack. Cached names
are labels only and must never be used to grant access to a conversation.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

try:
    from . import tag_config
except ImportError:
    import tag_config


VERSION = 1


def _directory(home: Path, team: str) -> Path | None:
    return home / "state/slack-channel-names" / team if re.fullmatch(r"T[A-Z0-9]+", team) else None


def _valid(channel: str, name: object) -> bool:
    return (bool(re.fullmatch(r"[CG][A-Z0-9]+", channel)) and isinstance(name, str)
            and 0 < len(name) <= 80 and name == name.strip()
            and not any(ord(c) < 32 or ord(c) == 127 for c in name))


def read(home: Path, team: str) -> dict[str, str]:
    directory = _directory(home, team)
    if directory is None:
        return {}
    names = {}
    for path in directory.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if (isinstance(record, dict) and record.get("version") == VERSION
                    and _valid(path.stem, record.get("name"))):
                names[path.stem] = record["name"]
        except (OSError, ValueError):
            continue
    return names


def remember(home: Path, team: str, names: dict[str, str]) -> None:
    directory = _directory(home, team)
    if directory is None:
        return
    existing = read(home, team)
    for channel, name in names.items():
        if _valid(channel, name) and existing.get(channel) != name:
            tag_config.save_config(directory / f"{channel}.json", {"version": VERSION, "name": name})


def migrate(home: Path, values: dict[str, str], *, api=None) -> bool:
    """Backfill configured channels and retained runs before services start.

    Successful lookups survive a partial failure; unresolved names are retried
    at the next start. Even after migration, newly configured channels are
    filled in. No fresh sign-in or additional permission is required.
    """
    try:
        from . import slack_channels, tag_activity
    except ImportError:
        import slack_channels
        import tag_activity
    team = values.get("SLACK_TEAM_ID", "")
    if _directory(home, team) is None:
        return False
    names = read(home, team)
    saved = tag_activity.channel_names(values.get("MFS_ALLOWED_SCOPES", ""))
    wanted = set(slack_channels.parse_channel_ids(
        values.get("SLACK_CHANNEL_IDS") or values.get("SLACK_CHANNEL_ID", "")))
    store = tag_activity.ActivityStore(home / "state/activity")
    for path in store.root.glob("*.json"):
        record = store.get(path.stem)
        if record and record["team"] == team:
            wanted.add(record["channel"])
    wanted = {channel for channel in wanted if re.fullmatch(r"[CG][A-Z0-9]+", channel)}
    remember(home, team, {channel: saved[channel] for channel in wanted - names.keys() if channel in saved})
    names = read(home, team)
    token = values.get("SLACK_BOT_TOKEN")
    for channel in sorted(wanted - names.keys()):
        if not token:
            break
        try:
            payload = (api or slack_channels.slack_api)(token, "conversations.info", {"channel": channel})
            info = payload.get("channel", {}) if isinstance(payload, dict) and payload.get("ok") else {}
            name = info.get("name") if isinstance(info, dict) and info.get("id") == channel else None
            if _valid(channel, name):
                remember(home, team, {channel: name})
        except (OSError, ValueError, RuntimeError):
            # Leave this entry missing, so a later start can recover it.
            continue
    if not wanted <= read(home, team).keys():
        return False
    marker = home / "state/migrations/slack-channel-names-v1.json"
    expected = {"version": VERSION, "team": team}
    try:
        if json.loads(marker.read_text(encoding="utf-8")) == expected:
            return True
    except (OSError, ValueError):
        pass
    tag_config.save_config(marker, expected)
    return True
