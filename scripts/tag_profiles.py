#!/usr/bin/env python3
"""Tag profiles: Tags that answer through Chief-of-Tag's one Slack app (ADR 0012).

A profile is a Tag instance without its own Slack app. It keeps its own name,
picture, description, working folder, backend settings, memory and sessions,
but no Slack tokens. People call it by name in a message to the one app
("@Tag designer: make a banner"), and it replies with its own name and
picture through ``chat:write.customize``.

Chief-of-Tag's bridge holds the only Socket Mode connection. It decides which
profile owns each request and drops the Slack payload in that profile's inbox;
the profile's own bridge process picks it up and handles it exactly as if
Slack had delivered it, with the profile's own settings.

State:

- ``<Tag home>/instance.json`` ``kind``: ``"profile"``; absent means an app Tag.
- ``<Tag home>/state/inbox/*.json``: payloads waiting for the profile's bridge.
- ``<TAG_HOME>/state/profiles/threads/*.json``: the profile that owns each thread.
- ``<TAG_HOME>/state/profiles/messages/<channel>/<ts>.json``: which profile
  posted each message, because every profile shares one bot user.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Iterator
import urllib.parse
import uuid

try:
    from tag_memory import write_document
except ImportError:
    from scripts.tag_memory import write_document


SCHEMA_VERSION = 1
KIND_APP = "app"
KIND_PROFILE = "profile"
ICON_SETTING = "OPENTAG_PROFILE_ICON"
# Built-in characters, served from a commit of this public repository so a
# later change to the files never changes a picture already in Slack.
ICON_COMMIT = "f2685e30f06d3a7f650b9c4feb4824d229e7e444"
ICON_BASE = f"https://raw.githubusercontent.com/klovr-co/hover-tag/{ICON_COMMIT}/assets/characters/"
CHARACTERS = {
    "maya": "tag-maya-pixel.png", "jules": "tag-jules-pixel.png", "iris": "tag-iris-pixel.png",
    "rowan": "tag-rowan-pixel.png", "zara": "tag-zara-pixel.png",
    "water": "tag-avatar-water-pixel.png", "soil": "tag-avatar-soil-pixel.png",
    "metal": "tag-avatar-metal-pixel.png", "wood": "tag-avatar-wood-pixel.png",
    "fire": "tag-avatar-fire-pixel.png",
}
DEFAULT_CHARACTER = "maya"
# Only the bot token and the IDs that name the app; the app-level token stays with Chief-of-Tag.
SHARED_SLACK_KEYS = ("SLACK_BOT_TOKEN", "SLACK_TEAM_ID", "SLACK_ENTERPRISE_ID", "SLACK_APP_ID")
READY_SECONDS = 15
INBOX_MAX_AGE_SECONDS = 600
MESSAGE_DAYS = 30


# Kind ------------------------------------------------------------------------


def kind(home: Path) -> str:
    try:
        record = json.loads((home / "instance.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return KIND_APP
    return KIND_PROFILE if isinstance(record, dict) and record.get("kind") == KIND_PROFILE else KIND_APP


def is_profile(home: Path) -> bool:
    return kind(home) == KIND_PROFILE


# Pictures --------------------------------------------------------------------


def icon_error(value: str) -> str | None:
    """Why a picture setting is unusable, or None. A character name or a public HTTPS URL."""
    value = value.strip()
    if not value or value.casefold() in CHARACTERS:
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return "Use a character name or a public https:// URL"
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not host:
        return "Use a character name (" + ", ".join(CHARACTERS) + ") or a public https:// URL"
    if parsed.username or parsed.password:
        return "The picture URL must not contain a user name or password"
    if host == "localhost" or host.endswith((".local", ".internal")) or re.fullmatch(r"[\d.]+|\[?[0-9a-f:]+\]?", host):
        return "Slack fetches the picture itself, so use a public host name"
    if len(value) > 500:
        return "Keep the picture URL under 500 characters"
    return None


def icon_url(value: str | None) -> str:
    """The URL Slack shows for a profile; an unusable setting falls back to the default character."""
    value = (value or "").strip()
    if value and icon_error(value) is None and value.casefold() not in CHARACTERS:
        return value
    return ICON_BASE + CHARACTERS.get(value.casefold(), CHARACTERS[DEFAULT_CHARACTER])


@dataclass(frozen=True)
class Identity:
    """How one profile appears in Slack."""
    id: str
    name: str
    icon_url: str

    def message_fields(self) -> dict[str, str]:
        return {"username": self.name[:80], "icon_url": self.icon_url}


# Discovery -------------------------------------------------------------------


@dataclass(frozen=True)
class Profile:
    id: str
    home: Path
    name: str
    description: str
    nickname: str | None
    icon: str

    @property
    def identity(self) -> Identity:
        return Identity(self.id, self.name, icon_url(self.icon))

    def names(self) -> list[str]:
        return [name for name in dict.fromkeys([self.name, self.id, self.nickname or ""]) if name]


def _settings(home: Path) -> dict[str, str]:
    try:
        document = json.loads((home / "config/settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: value for key, value in document.items() if isinstance(value, str)} if isinstance(document, dict) else {}


def load(root: Path, tag_id: str) -> Profile | None:
    try:
        import tag_instances
    except ImportError:
        from scripts import tag_instances
    try:
        context = tag_instances.resolve(root, tag_id)
    except ValueError:
        return None
    if not is_profile(context.home):
        return None
    values = _settings(context.home)
    return Profile(tag_id, context.home, values.get("OPENTAG_BOT_NAME") or tag_id,
                   values.get("OPENTAG_BOT_DESCRIPTION", ""), tag_instances.nickname(context.home),
                   values.get(ICON_SETTING, ""))


def profiles(root: Path) -> list[Profile]:
    try:
        import tag_instances
    except ImportError:
        from scripts import tag_instances
    found = []
    for item in tag_instances.discover(root):
        if item.get("valid") and Path(str(item.get("home", ""))).exists():
            profile = load(root, str(item["id"]))
            if profile is not None:
                found.append(profile)
    return found


def shared_slack_values(root: Path) -> dict[str, str]:
    """What a profile's bridge needs from Chief-of-Tag's app. Read at start; never saved in the profile."""
    try:
        import tag_chief, tag_instances
    except ImportError:
        from scripts import tag_chief, tag_instances
    chief = tag_chief.chief_id(root)
    if chief is None:
        raise ValueError("Profiles answer through Chief-of-Tag's Slack app, and no Tag is Chief-of-Tag")
    home = tag_instances.resolve(root, chief).home
    if is_profile(home):
        raise ValueError("Chief-of-Tag must be a Tag with its own Slack app")
    values = _settings(home)
    if not values.get("SLACK_BOT_TOKEN"):
        raise ValueError(f"Chief-of-Tag ({chief}) isn't connected to Slack yet; run tag {chief} setup")
    return {key: values[key] for key in SHARED_SLACK_KEYS if values.get(key)}


def running(home: Path, maximum_age: float = READY_SECONDS) -> bool:
    """Whether the profile's bridge is reading its inbox, from its heartbeat file."""
    try:
        _instance, _pid, heartbeat = (home / "state/slack.ready").read_text(encoding="utf-8").split()
        return 0 <= time.time() - float(heartbeat) <= maximum_age
    except (OSError, ValueError):
        return False


# Calling a profile by name -------------------------------------------------------


LEADING_MENTIONS = re.compile(r"^(?:\s*<@[UW][A-Z0-9]+(?:\|[^>]*)?>)+\s*")


@dataclass(frozen=True)
class Call:
    profile: Profile
    text: str


def parse_call(text: str, candidates: list[Profile]) -> Call | None:
    """``designer: …``, ``designer, …`` or ``ask designer (to) …`` right after the app mention.

    The name must come first; a name elsewhere in the text is ordinary words.
    """
    rest = LEADING_MENTIONS.sub("", text or "", count=1)
    names = sorted(((name, profile) for profile in candidates for name in profile.names()),
                   key=lambda item: -len(item[0]))
    for name, profile in names:
        quoted = re.escape(name)
        match = (re.match(rf"\s*@?{quoted}\s*[:,]\s*(?P<rest>.*)\Z", rest, re.IGNORECASE | re.DOTALL)
                 or re.match(rf"\s*ask\s+@?{quoted}(?![\w-])\s*(?:to\s+)?(?P<rest>.*)\Z", rest,
                             re.IGNORECASE | re.DOTALL))
        if match and match.group("rest").strip():
            return Call(profile, match.group("rest").strip())
    return None


# Thread owners -------------------------------------------------------------------


def _state(root: Path) -> Path:
    return root.expanduser().absolute() / "state" / "profiles"


def _part(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9.]{1,40}", value or ""):
        raise ValueError("invalid Slack identifier")
    return value


def _owner_path(root: Path, team: str, channel: str, thread_ts: str) -> Path:
    return _state(root) / "threads" / f"{_part(team or 'T')}-{_part(channel)}-{_part(thread_ts)}.json"


def thread_owner(root: Path, team: str, channel: str, thread_ts: str) -> str | None:
    path = _owner_path(root, team, channel, thread_ts)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    owner = record.get("owner") if isinstance(record, dict) else None
    return owner if isinstance(owner, str) and owner else None


def set_thread_owner(root: Path, team: str, channel: str, thread_ts: str, owner: str, *, by: str) -> None:
    """Only Chief-of-Tag's bridge writes owners, so one writer needs no lock."""
    write_document(_owner_path(root, team, channel, thread_ts), {
        "schema_version": SCHEMA_VERSION, "owner": owner, "set_by": by, "updated_at": time.time(),
    })


# Who posted which message ----------------------------------------------------------


def _message_path(root: Path, channel: str, ts: str) -> Path:
    return _state(root) / "messages" / _part(channel) / f"{_part(ts)}.json"


def record_message(root: Path, channel: str, ts: str, profile: str) -> None:
    """One small file per message, so profiles posting at once never contend."""
    if not channel or not ts:
        return
    write_document(_message_path(root, channel, ts), {"schema_version": SCHEMA_VERSION, "profile": profile})


def message_profile(root: Path, channel: str, ts: str) -> str | None:
    try:
        record = json.loads(_message_path(root, channel, ts).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    profile = record.get("profile") if isinstance(record, dict) else None
    return profile if isinstance(profile, str) else None


def prune_messages(root: Path, days: int = MESSAGE_DAYS) -> int:
    cutoff = time.time() - days * 86_400
    removed = 0
    folder = _state(root) / "messages"
    for path in folder.glob("*/*.json") if folder.is_dir() else []:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


# Inbox -----------------------------------------------------------------------------


def inbox_path(home: Path) -> Path:
    return home / "state" / "inbox"


def deliver(home: Path, envelope: dict[str, Any]) -> Path:
    """Queue a payload for the profile whose home this is. Written whole, then renamed."""
    folder = inbox_path(home)
    name = f"{time.time_ns():020d}-{uuid.uuid4().hex[:8]}.json"
    write_document(folder / name, {"schema_version": SCHEMA_VERSION, "created_at": time.time(), **envelope})
    return folder / name


def claim(home: Path, max_age: float = INBOX_MAX_AGE_SECONDS) -> Iterator[dict[str, Any]]:
    """Take queued payloads oldest first. Each is removed before it is handled, so a crash
    never runs a request twice; payloads older than ``max_age`` are dropped unhandled."""
    folder = inbox_path(home)
    for path in sorted(folder.glob("[0-9]*.json")) if folder.is_dir() else []:
        claimed = path.with_name(f".claimed-{path.name}")
        try:
            os.replace(path, claimed)
        except OSError:
            continue  # Another reader took it.
        try:
            envelope = json.loads(claimed.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            envelope = None
        finally:
            claimed.unlink(missing_ok=True)
        if (isinstance(envelope, dict) and envelope.get("schema_version") == SCHEMA_VERSION
                and time.time() - float(envelope.get("created_at") or 0) <= max_age):
            yield envelope
