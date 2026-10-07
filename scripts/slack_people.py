"""Persistent display names and pictures of the people who asked a Tag for work.

Activity shows who asked, so the bridge looks each requester up once with the
bot token (``users.info``) and keeps one private, atomic record per person.
Reads never contact Slack. Cached names are labels only and must never be used
to grant access to anything.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

try:
    from . import tag_config
except ImportError:
    import tag_config


VERSION = 1
# Names and pictures change rarely; refresh them about once a day.
FRESH_SECONDS = 24 * 60 * 60
USER_RE = re.compile(r"[UW][A-Z0-9]+")


def _directory(home: Path, team: str) -> Path | None:
    return home / "state/slack-people" / team if re.fullmatch(r"T[A-Z0-9]+", team) else None


def _valid_name(name: object) -> bool:
    return (isinstance(name, str) and 0 < len(name) <= 80 and name == name.strip()
            and not any(ord(c) < 32 or ord(c) == 127 for c in name))


def _valid_avatar(url: object) -> bool:
    return isinstance(url, str) and len(url) <= 500 and url.startswith("https://") and not any(
        c.isspace() or c in "\"'<>" for c in url)


def _person(record: object) -> dict[str, str] | None:
    if not isinstance(record, dict) or record.get("version") != VERSION or not _valid_name(record.get("name")):
        return None
    person = {"name": record["name"]}
    if _valid_avatar(record.get("avatar")):
        person["avatar"] = record["avatar"]
    return person


def read(home: Path, team: str) -> dict[str, dict[str, str]]:
    """Every cached person in one workspace: ``{user: {"name", "avatar"?}}``."""
    directory = _directory(home, team)
    if directory is None:
        return {}
    people = {}
    for path in directory.glob("*.json"):
        if not USER_RE.fullmatch(path.stem):
            continue
        try:
            person = _person(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if person:
            people[path.stem] = person
    return people


def fresh(home: Path, team: str, user: str) -> bool:
    directory = _directory(home, team)
    if directory is None or not USER_RE.fullmatch(user):
        return True  # Nothing to look up.
    try:
        return time.time() - (directory / f"{user}.json").stat().st_mtime < FRESH_SECONDS
    except OSError:
        return False


def from_profile(payload: object, user: str) -> dict[str, str] | None:
    """The display name and picture from a ``users.info`` response."""
    info = payload.get("user") if isinstance(payload, dict) and payload.get("ok") else None
    if not isinstance(info, dict) or info.get("id") != user:
        return None
    profile = info.get("profile") if isinstance(info.get("profile"), dict) else {}
    name = next((value.strip() for value in (profile.get("display_name"), profile.get("real_name"),
                                              info.get("real_name"), info.get("name"))
                 if isinstance(value, str) and value.strip()), None)
    if not _valid_name(name):
        return None
    person = {"name": name}
    avatar = next((profile.get(key) for key in ("image_72", "image_48", "image_192")
                   if _valid_avatar(profile.get(key))), None)
    if avatar:
        person["avatar"] = avatar
    return person


def remember(home: Path, team: str, user: str, person: dict[str, str]) -> None:
    directory = _directory(home, team)
    if directory is None or not USER_RE.fullmatch(user) or not _valid_name(person.get("name")):
        return
    record = {"version": VERSION, "name": person["name"]}
    if _valid_avatar(person.get("avatar")):
        record["avatar"] = person["avatar"]
    tag_config.save_config(directory / f"{user}.json", record)


def look_up(home: Path, team: str, user: str, users_info) -> bool:
    """Refresh one requester when their record is missing or stale; return whether it is cached.

    ``users_info(user)`` returns Slack's response. Failures keep any saved record.
    """
    if fresh(home, team, user):
        return True
    try:
        person = from_profile(users_info(user), user)
    except Exception:  # noqa: BLE001 - a label must never interrupt a request
        person = None
    if person:
        remember(home, team, user, person)
    return person is not None


def migrate(home: Path, values: dict[str, str], *, api=None) -> bool:
    """Name the requesters of retained activity before services start.

    Older Tags kept only Slack user IDs. Successful lookups survive a partial
    failure, and unresolved people are retried at the next start. users:read is
    already granted for caller verification, so no new permission is needed.
    """
    try:
        from . import slack_channels, tag_activity
    except ImportError:
        import slack_channels
        import tag_activity
    team = values.get("SLACK_TEAM_ID", "")
    token = values.get("SLACK_BOT_TOKEN")
    if _directory(home, team) is None:
        return False
    store = tag_activity.ActivityStore(home / "state/activity")
    wanted = set()
    for path in store.root.glob("*.json"):
        record = store.get(path.stem)
        if record and record["team"] == team and USER_RE.fullmatch(record["requester"]):
            wanted.add(record["requester"])
    for user in sorted(wanted - read(home, team).keys()):
        if not token:
            break
        look_up(home, team, user, lambda user: (api or slack_channels.slack_api)(token, "users.info", {"user": user}))
    if not wanted <= read(home, team).keys():
        return False
    marker = home / "state/migrations/slack-people-v1.json"
    expected = {"version": VERSION, "team": team}
    try:
        if json.loads(marker.read_text(encoding="utf-8")) == expected:
            return True
    except (OSError, ValueError):
        pass
    tag_config.save_config(marker, expected)
    return True
