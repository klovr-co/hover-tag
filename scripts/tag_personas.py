# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""Tag personas: named faces over one Tag's single agent loop (ADR 0012).

A persona is a name, a picture, a one-line description, the skills it reaches
for first, an optional memory scope, a short voice and how often it speaks up.
It has no Slack tokens, sessions, backend credentials or process of its own:
every persona is the same Tag, with the same permissions (ADR 0001). The Tag
itself, under its own name, is the default persona; on Lead's Tag that is Lead.

State, in the Tag's own folder:

- ``config/personas.json``: the personas, written only by ``tag NAME persona``.
- ``state/personas/threads/*.json``: the persona each thread is with.
- ``state/personas/messages/<channel>/<ts>.json``: which persona posted each
  message, because every persona shares one bot user.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from tag_memory import write_document
except ImportError:
    from scripts.tag_memory import write_document

SCHEMA_VERSION = 1
MAX_PERSONAS = 12
NAME_LIMIT = 35
DESCRIPTION_LIMIT = 140
VOICE_LIMIT = 200
MAX_SKILLS = 20
MESSAGE_DAYS = 30
# How often a persona speaks up; each level includes the ones before it.
SPEAKS_UP = ("only-when-asked", "when-addressed", "chimes-in")
DEFAULT_SPEAKS_UP = SPEAKS_UP[0]
# Names that already mean something when people call the Tag.
RESERVED_NAMES = frozenset({"lead", "tag", "everyone", "here", "channel"})
NAME = re.compile(r"[A-Za-z][A-Za-z0-9' -]*[A-Za-z0-9]|[A-Za-z]")
SKILL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
CHANNEL = re.compile(r"[CG][A-Z0-9]{2,20}")
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


class PersonaError(ValueError):
    """A refused persona change; the message is safe to show."""


# Pictures --------------------------------------------------------------------


def picture_error(value: str) -> str | None:
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


def picture_url(value: str | None) -> str:
    """The URL Slack shows; an unusable setting falls back to the default character."""
    value = (value or "").strip()
    if value and picture_error(value) is None and value.casefold() not in CHARACTERS:
        return value
    return ICON_BASE + CHARACTERS.get(value.casefold(), CHARACTERS[DEFAULT_CHARACTER])


# The persona record ------------------------------------------------------------


@dataclass(frozen=True)
class Persona:
    name: str
    description: str
    picture: str = ""
    skills: tuple[str, ...] = ()
    memory_scopes: tuple[str, ...] = ()
    voice: str = ""
    speaks_up: str = DEFAULT_SPEAKS_UP
    channels: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.name.casefold()

    def speaks_up_in(self, channel: str) -> str:
        return self.channels.get(channel, self.speaks_up)

    def message_fields(self) -> dict[str, str]:
        """``chat:write.customize`` fields for a post as this persona."""
        return {"username": self.name[:80], "icon_url": picture_url(self.picture)}

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "picture": self.picture,
                "skills": list(self.skills), "memory_scopes": list(self.memory_scopes), "voice": self.voice,
                "speaks_up": self.speaks_up, "channels": dict(sorted(self.channels.items()))}


def _one_line(value: object) -> str:
    return " ".join(str(value or "").split())


def _list(value: object, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PersonaError(f"{label} must be a list of names")
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def validate(record: dict[str, Any], *, allowed_scopes: list[str] | None = None) -> Persona:
    """Check one persona's own fields. Rules across personas are in ``check_all``."""
    name = _one_line(record.get("name"))
    if not name or len(name) > NAME_LIMIT or not NAME.fullmatch(name):
        raise PersonaError(f"Give the persona a name of up to {NAME_LIMIT} letters, digits, spaces, "
                           "hyphens or apostrophes, starting with a letter")
    if name.casefold() in RESERVED_NAMES:
        raise PersonaError(f"'{name}' already means something to Tag; choose another name")
    description = _one_line(record.get("description"))
    if not description or len(description) > DESCRIPTION_LIMIT:
        raise PersonaError(f"Describe what {name} does in up to {DESCRIPTION_LIMIT} characters")
    picture = str(record.get("picture") or "").strip()
    if error := picture_error(picture):
        raise PersonaError(error)
    skills = _list(record.get("skills"), "Skills")
    if len(skills) > MAX_SKILLS or any(not SKILL.fullmatch(skill) for skill in skills):
        raise PersonaError(f"Name up to {MAX_SKILLS} skills by their folder name, such as month-end-close")
    scopes = _list(record.get("memory_scopes"), "Memory scopes")
    if scopes and allowed_scopes is not None:
        outside = [scope for scope in scopes if scope not in allowed_scopes]
        if outside:
            raise PersonaError("A persona's memory scopes must be among this Tag's allowed memory sources; "
                               f"not allowed: {', '.join(outside)}")
    voice = _one_line(record.get("voice"))
    if len(voice) > VOICE_LIMIT:
        raise PersonaError(f"Keep the voice to {VOICE_LIMIT} characters: tone and length only. "
                           "Rules and procedures belong in skills")
    speaks_up = str(record.get("speaks_up") or DEFAULT_SPEAKS_UP)
    if speaks_up not in SPEAKS_UP:
        raise PersonaError("Speaks up must be one of " + ", ".join(SPEAKS_UP))
    channels = record.get("channels") or {}
    if not isinstance(channels, dict):
        raise PersonaError("Channel settings must map a channel ID to a level")
    for channel, level in channels.items():
        if not isinstance(channel, str) or not CHANNEL.fullmatch(channel):
            raise PersonaError("Name channels by their ID, such as C0123ABCD")
        if level not in SPEAKS_UP:
            raise PersonaError(f"Speaks up in {channel} must be one of " + ", ".join(SPEAKS_UP))
    return Persona(name, description, picture, tuple(skills), tuple(scopes), voice, speaks_up,
                   {key: channels[key] for key in sorted(channels)})


def check_all(personas: list[Persona], *, tag_name: str = "", tag_channels: list[str] = ()) -> None:
    """Rules across personas: unique names, a limit, and one persona that chimes in per channel."""
    if len(personas) > MAX_PERSONAS:
        raise PersonaError(f"A Tag can have up to {MAX_PERSONAS} personas")
    seen: set[str] = set()
    for persona in personas:
        if persona.key in seen:
            raise PersonaError(f"There is already a persona named {persona.name}")
        if tag_name and persona.key == tag_name.casefold():
            raise PersonaError(f"{persona.name} is this Tag's own name, which is already its default persona")
        seen.add(persona.key)
    channels = set(tag_channels) | {channel for persona in personas for channel in persona.channels}
    for channel in sorted(channels):
        chiming = [persona.name for persona in personas if persona.speaks_up_in(channel) == "chimes-in"]
        if len(chiming) > 1:
            raise PersonaError(f"Only one persona can chime in per channel; in {channel}: {', '.join(chiming)}. "
                               f"Set the others to when-addressed there with --channel {channel}=when-addressed")
    # A default of chimes-in applies to every channel, including ones joined later.
    defaults = [persona.name for persona in personas if persona.speaks_up == "chimes-in"]
    if len(defaults) > 1:
        raise PersonaError("Only one persona can chime in by default; set the others per channel: "
                           + ", ".join(defaults))


# Storage ---------------------------------------------------------------------


def path(home: Path) -> Path:
    return home / "config" / "personas.json"


def load(home: Path) -> list[Persona]:
    """Saved personas. One that no longer validates is skipped, never guessed at."""
    try:
        document = json.loads(path(home).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise PersonaError(f"personas.json can't be read: {exc}") from None
    items = document.get("personas") if isinstance(document, dict) else None
    found: list[Persona] = []
    for item in items if isinstance(items, list) else []:
        try:
            found.append(validate(item if isinstance(item, dict) else {}))
        except PersonaError:
            continue
    return found


def save(home: Path, personas: list[Persona]) -> None:
    write_document(path(home), {"schema_version": SCHEMA_VERSION, "updated_at": time.time(),
                                "personas": [persona.to_json() for persona in personas]})


def find(personas: list[Persona], name: str) -> Persona | None:
    wanted = _one_line(name).casefold()
    return next((persona for persona in personas if persona.key == wanted), None)


# Calling a persona by name -------------------------------------------------------


LEADING_MENTIONS = re.compile(r"^(?:\s*<@[UW][A-Z0-9]+(?:\|[^>]*)?>)+\s*")


@dataclass(frozen=True)
class Call:
    persona: Persona
    text: str


def parse_call(text: str, personas: list[Persona]) -> Call | None:
    """``Finance: …``, ``Finance, …`` or ``ask Finance (to) …`` right after the app mention.

    The name must come first, and the match ignores case. A name elsewhere in the
    text is ordinary words, so "ask the finance team whether…" is not a call.
    """
    rest = LEADING_MENTIONS.sub("", text or "", count=1)
    for persona in sorted(personas, key=lambda item: -len(item.name)):
        quoted = re.escape(persona.name)
        match = (re.match(rf"\s*@?{quoted}\s*[:,]\s*(?P<rest>.*)\Z", rest, re.IGNORECASE | re.DOTALL)
                 or re.match(rf"\s*ask\s+@?{quoted}(?![\w'-])\s*(?:to\s+)?(?P<rest>.*)\Z", rest,
                             re.IGNORECASE | re.DOTALL))
        if match and match.group("rest").strip():
            return Call(persona, match.group("rest").strip())
    return None


# One run's personas -------------------------------------------------------------------
# The bridge writes this file for a run; the agent's helper records a handover in it.


HANDOVER_TEXT = "That's {name}'s area, handing you over."


def brief(persona: Persona) -> dict[str, Any]:
    """What a run needs to answer as a persona; memory scopes and channels stay with the bridge."""
    return {"name": persona.name, "description": persona.description, "voice": persona.voice,
            "skills": list(persona.skills)}


def write_run_file(destination: Path, *, active: Persona | None, personas: list[Persona]) -> None:
    write_document(destination, {"schema_version": SCHEMA_VERSION,
                                 "active": brief(active) if active else None,
                                 "personas": [] if active else [brief(item) for item in personas],
                                 "handover": None, "proposal": None})


def read_run_file(source: Path) -> dict[str, Any]:
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def record_handover(source: Path, name: str) -> dict[str, Any]:
    """The agent's handover, once per run, to a persona the bridge listed for this run."""
    document = read_run_file(source)
    if not document:
        raise PersonaError("Handing over is unavailable in this conversation.")
    if document.get("active"):
        raise PersonaError(f"You are already answering as {document['active'].get('name')}; answer here yourself.")
    if document.get("handover"):
        raise PersonaError(f"This reply already hands over to {document['handover']}; answer as that persona.")
    wanted = _one_line(name).removeprefix("@").casefold()
    target = next((item for item in document.get("personas") or []
                   if isinstance(item, dict) and str(item.get("name", "")).casefold() == wanted), None)
    if target is None:
        names = ", ".join(str(item.get("name")) for item in document.get("personas") or [] if isinstance(item, dict))
        raise PersonaError(f"There is no persona named {name}. The personas are: {names or 'none'}.")
    document["handover"] = target["name"]
    write_document(source, document)
    return target


def record_proposal(source: Path, proposal: dict[str, Any]) -> None:
    """One persona or skill proposal per run, made under the Tag's own name; the bridge checks it again."""
    document = read_run_file(source)
    if not document:
        raise PersonaError("Proposing is unavailable in this conversation.")
    if document.get("active"):
        raise PersonaError(f"Only the Tag under its own name proposes; you are answering as "
                           f"{document['active'].get('name')}.")
    if document.get("proposal"):
        raise PersonaError("This reply already proposes something; each reply makes one proposal.")
    document["proposal"] = proposal
    write_document(source, document)


def read_proposal(source: Path) -> dict[str, Any] | None:
    value = read_run_file(source).get("proposal")
    return value if isinstance(value, dict) else None


def read_handover(source: Path) -> str | None:
    value = read_run_file(source).get("handover")
    return value if isinstance(value, str) and value else None


def brief_text(item: dict[str, Any]) -> str:
    parts = [f"{item.get('name')}: {item.get('description')}"]
    if item.get("voice"):
        parts.append(f"Voice: {item['voice']}.")
    if item.get("skills"):
        parts.append("Reach for these skills first: " + ", ".join(item["skills"]) + ".")
    return " ".join(parts)


# Thread personas and who posted what ------------------------------------------------


def _state(home: Path) -> Path:
    return home / "state" / "personas"


def _part(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9.]{1,40}", value or ""):
        raise ValueError("invalid Slack identifier")
    return value


def _thread_path(home: Path, channel: str, thread_ts: str) -> Path:
    return _state(home) / "threads" / f"{_part(channel)}-{_part(thread_ts)}.json"


def thread_persona(home: Path, channel: str, thread_ts: str) -> str | None:
    """The persona a thread is with, or None for the Tag's own (default) persona."""
    try:
        record = json.loads(_thread_path(home, channel, thread_ts).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    persona = record.get("persona") if isinstance(record, dict) else None
    return persona if isinstance(persona, str) and persona else None


def set_thread_persona(home: Path, channel: str, thread_ts: str, persona: str | None, *, by: str) -> None:
    """``by`` is ``named`` (a person called it) or ``handover`` (the default persona handed over)."""
    write_document(_thread_path(home, channel, thread_ts), {
        "schema_version": SCHEMA_VERSION, "persona": persona or "", "by": by, "updated_at": time.time(),
    })


def _message_path(home: Path, channel: str, ts: str) -> Path:
    return _state(home) / "messages" / _part(channel) / f"{_part(ts)}.json"


def record_message(home: Path, channel: str, ts: str, persona: str) -> None:
    """One small file per message, so replies posting at once never contend."""
    if channel and ts:
        write_document(_message_path(home, channel, ts), {"schema_version": SCHEMA_VERSION, "persona": persona})


def message_persona(home: Path, channel: str, ts: str) -> str | None:
    try:
        record = json.loads(_message_path(home, channel, ts).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    persona = record.get("persona") if isinstance(record, dict) else None
    return persona if isinstance(persona, str) else None


def prune_messages(home: Path, days: int = MESSAGE_DAYS) -> int:
    cutoff = time.time() - days * 86_400
    removed = 0
    folder = _state(home) / "messages"
    for item in folder.glob("*/*.json") if folder.is_dir() else []:
        try:
            if item.stat().st_mtime < cutoff:
                item.unlink()
                removed += 1
        except OSError:
            continue
    return removed
