#!/usr/bin/env python3
"""Lead: the one Tag that routes work and creates new Tags.

Lead is a dispatcher, not an orchestrator. Each request has exactly
one owner Tag, which handles it end to end and replies in the thread itself.
Lead routes new, unclear or cross-lane requests to one owner in the
same thread, so the owner sees the whole thread. It never rewords the owner's
answer. Specialists can ask Lead for a judgment call, and nobody else.

Installation state, in ``<TAG_HOME>/state``:

- ``lead.json``: which Tag holds the role, and which Tags it created.
  One file names one Tag, so only one Tag can hold the role.
- ``tag-roster/<id>.json``: each Tag's public card (name, description, Slack
  member ID, channels), written by its bridge at startup. The agent's helper
  reads these and never another Tag's settings.
- ``tag-proposals/p-*.json``: proposed new Tags waiting for a person's approval.

The agent records what it wants in the run's request file (see
``tag_handoff``). The bridge checks the role again before it acts, so an agent
that writes to the file by itself can never create a Tag.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

try:
    from tag_memory import scope_lock, write_document
except ImportError:
    from scripts.tag_memory import scope_lock, write_document

SCHEMA_VERSION = 1
ROLE_NAME = "Lead"
MIGRATION_NAME = "lead"
MIGRATION_VERSION = 1
DEFAULT_MAX_TAGS = 10  # Free Slack workspaces allow 10 apps in total.
APPROVAL_MINUTES = 60
PROPOSAL_ID = r"p-[0-9a-f]{10}"
CHANNEL_ID = r"[CG][A-Z0-9]{2,20}"
MEMBER_ID = r"[UW][A-Z0-9]{2,20}"
TAG_ID = r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?"
NAME_LIMIT = 35
DESCRIPTION_LIMIT = 140
# A new Tag only for a real boundary; otherwise a skill or a subagent will do.
BOUNDARIES = {
    "credentials": "different credentials or permissions",
    "memory": "its own long-lived memory and routines",
    "parallel": "long-running parallel work",
    "identity": "a distinct Slack identity people should @mention directly",
    "owner": "a different owner, audience or runtime",
}
# States in which a proposal still holds the single creation slot.
OPEN_STATES = frozenset({"pending", "approved", "creating", "waiting_on_slack"})
FINAL_STATES = frozenset({"rejected", "expired", "created", "failed", "refused"})


class LeadRefused(Exception):
    """A refused Lead action; the message is safe to show the user."""


# Designation ----------------------------------------------------------------------


def _state(root: Path) -> Path:
    return root.expanduser().absolute() / "state"


def record_path(root: Path) -> Path:
    return _state(root) / "lead.json"


def _valid_id(value: object) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(TAG_ID, value) else None


def read_record(root: Path) -> dict[str, Any]:
    try:
        record = json.loads(record_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
        return {}
    return record


def lead_id(root: Path) -> str | None:
    """The Tag ID that holds the role, or None when none is designated."""
    return _valid_id(read_record(root).get("id"))


def created_by_lead(root: Path) -> list[str]:
    created = read_record(root).get("created")
    return [item for item in created if _valid_id(item)] if isinstance(created, list) else []


def _write_record(root: Path, change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    path = record_path(root)
    with scope_lock(path):
        record = read_record(root) or {"schema_version": SCHEMA_VERSION, "id": None, "created": []}
        change(record)
        write_document(path, record)
    return record


def set_lead(root: Path, tag_id: str) -> None:
    """Move the role to ``tag_id``. The previous holder loses it in the same write."""
    if not _valid_id(tag_id):
        raise ValueError("Choose a Tag by its ID or nickname")

    def change(record: dict[str, Any]) -> None:
        record.update(id=tag_id, set_at=time.time())
    _write_record(root, change)
    if lead_id(root) != tag_id:
        raise RuntimeError("Tag couldn't save Lead. Nothing else changed; try again.")


def record_created(root: Path, tag_id: str) -> None:
    def change(record: dict[str, Any]) -> None:
        created = [item for item in record.get("created") or [] if item != tag_id]
        record["created"] = [*created, tag_id]
    _write_record(root, change)


def renamed(root: Path, old: str, new: str) -> None:
    """Keep the role and the created list on a Tag whose ID changed."""
    record = read_record(root)
    if not record or (record.get("id") != old and old not in (record.get("created") or [])):
        return

    def change(item: dict[str, Any]) -> None:
        if item.get("id") == old:
            item["id"] = new
        item["created"] = [new if value == old else value for value in item.get("created") or []]
    _write_record(root, change)
    roster = roster_path(root, old)
    if roster.exists():
        try:
            card = json.loads(roster.read_text(encoding="utf-8"))
            card["id"] = new
            write_document(roster_path(root, new), card)
        except (OSError, ValueError):
            pass
        roster.unlink(missing_ok=True)


def _migration_marker(root: Path) -> Path:
    return _state(root) / "migrations" / f"{MIGRATION_NAME}-v{MIGRATION_VERSION}.json"


def migrate(root: Path, tags: list[dict[str, Any]], main: str | None) -> str | None:
    """Migration v1: make sure one existing Tag holds the role.

    ``tags`` are ``tag_instances.discover`` rows. The saved Lead is
    kept while it exists. Otherwise the main Tag gets the role, or the first
    Tag with a Slack app, or the first Tag. The migration never renames a Slack
    app or changes a Tag's settings. It is idempotent and checks itself on every
    run, so it also repairs a role left on a removed Tag. Completion is recorded
    only after the saved role reads back.
    """
    existing = [str(item["id"]) for item in tags
                if item.get("valid") and Path(str(item.get("home", ""))).exists()]
    current = lead_id(root)
    chosen = current if current in existing else None
    if chosen is None and existing:
        with_app = [item for item in existing if _has_app(tags, item)]
        chosen = main if main in existing else (with_app or existing)[0]
        set_lead(root, chosen)
    if chosen is not None and lead_id(root) == chosen:
        marker = _migration_marker(root)
        if not marker.exists():
            write_document(marker, {"version": MIGRATION_VERSION, "completed_at": time.time(), "lead": chosen})
    return chosen


def _has_app(tags: list[dict[str, Any]], tag_id: str) -> bool:
    for item in tags:
        if item.get("id") == tag_id:
            settings = Path(str(item.get("home", ""))) / "config/settings.json"
            try:
                return bool(json.loads(settings.read_text(encoding="utf-8")).get("SLACK_APP_ID"))
            except (OSError, ValueError, AttributeError):
                return False
    return False


# Roster: each Tag's public card ------------------------------------------------------


def roster_path(root: Path, tag_id: str) -> Path:
    if not _valid_id(tag_id):
        raise ValueError("invalid Tag id")
    return _state(root) / "tag-roster" / f"{tag_id}.json"


def save_card(root: Path, tag_id: str, *, user_id: str, team: str, name: str, description: str = "",
              channels: list[dict[str, str | None]] | None = None, model: str = "") -> None:
    """Publish this Tag's public card. Never put a token or a private setting here."""
    if not re.fullmatch(MEMBER_ID, user_id or ""):
        return
    write_document(roster_path(root, tag_id), {
        "schema_version": SCHEMA_VERSION, "id": tag_id, "user_id": user_id, "team": team,
        "name": name[:80], "description": description[:DESCRIPTION_LIMIT],
        "channels": [{"id": item["id"], "name": item.get("name")} for item in channels or []
                     if isinstance(item.get("id"), str) and re.fullmatch(CHANNEL_ID, item["id"])],
        "model": model[:80], "updated_at": time.time(),
    })


@dataclass(frozen=True)
class Card:
    id: str
    user_id: str
    team: str
    name: str
    description: str
    channels: tuple[tuple[str, str | None], ...]
    model: str

    def in_channel(self, channel: str) -> bool:
        return any(item[0] == channel for item in self.channels)


def cards(root: Path) -> list[Card]:
    found = []
    folder = _state(root) / "tag-roster"
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # One damaged card must not hide the others.
        if (not isinstance(card, dict) or card.get("schema_version") != SCHEMA_VERSION
                or not _valid_id(card.get("id")) or card["id"] != path.stem
                or not re.fullmatch(MEMBER_ID, str(card.get("user_id", "")))):
            continue
        channels = tuple((str(item["id"]), item.get("name") if isinstance(item.get("name"), str) else None)
                         for item in card.get("channels") or [] if isinstance(item, dict) and item.get("id"))
        found.append(Card(card["id"], card["user_id"], str(card.get("team", "")), str(card.get("name") or card["id"]),
                          str(card.get("description") or ""), channels, str(card.get("model") or "")))
    return found


def roster(root: Path, own_id: str, team: str = "") -> list[Card]:
    """The Tags Lead may route to: every other Tag in the same Slack team. Flat by design."""
    return [card for card in cards(root) if card.id != own_id and (not team or not card.team or card.team == team)]


def lead_card(root: Path, own_id: str) -> Card | None:
    """Lead's card, for a specialist that may escalate to it."""
    lead = lead_id(root)
    if lead is None or lead == own_id:
        return None
    return next((card for card in cards(root) if card.id == lead), None)


def roster_text(entries: list[Card], channel: str) -> str:
    lines = []
    for card in entries:
        where = "in this channel" if card.in_channel(channel) else "not in this channel"
        about = f": {card.description}" if card.description else ""
        lines.append(f"- {card.name}{about} ({where})")
    return "\n".join(lines) or "- No other Tags yet."


# Agent side: record a proposal for the bridge ---------------------------------------


def _parse_routine(value: str) -> dict[str, str]:
    schedule, separator, prompt = value.partition("::")
    if not separator or not schedule.strip() or not prompt.strip():
        raise LeadRefused('Write each routine as "SCHEDULE::WHAT TO DO", for example "weekdays 09:00::Post the open bugs".')
    return {"schedule": schedule.strip()[:80], "prompt": prompt.strip()[:1000]}


def validate_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    """Check a proposal's shape. Limits that depend on the installation are the bridge's job."""
    name = str(proposal.get("name") or "").strip()
    description = " ".join(str(proposal.get("description") or "").split())
    if not name or len(name) > NAME_LIMIT or "\n" in name:
        raise LeadRefused(f"Give the new Tag a one-line name of up to {NAME_LIMIT} characters.")
    if not description or len(description) > DESCRIPTION_LIMIT:
        raise LeadRefused(f"Describe the new Tag's lane in up to {DESCRIPTION_LIMIT} characters.")
    boundary = str(proposal.get("boundary") or "")
    if boundary not in BOUNDARIES:
        raise LeadRefused("A new Tag needs a real boundary: " + ", ".join(
            f"{key} ({text})" for key, text in BOUNDARIES.items()) + ". Otherwise use a skill or a subagent.")
    why = " ".join(str(proposal.get("why_not_skill") or "").split())
    if len(why) < 10:
        raise LeadRefused("Say why a skill or an in-process subagent isn't enough.")
    channels = list(dict.fromkeys(str(item) for item in proposal.get("channels") or []))
    if any(not re.fullmatch(CHANNEL_ID, item) for item in channels):
        raise LeadRefused("Name channels by their ID, such as C0123ABCD.")
    model = str(proposal.get("model") or "").strip()
    if model and not re.fullmatch(r"(codex|claude)(:[A-Za-z0-9._\-\[\]]+)?", model):
        raise LeadRefused("Choose a model such as codex:gpt-5.5 or claude:claude-sonnet-5-5, or leave it out.")
    routines = proposal.get("routines") or []
    if not isinstance(routines, list) or len(routines) > 5:
        raise LeadRefused("Propose up to 5 starting routines.")
    routines = [_parse_routine(item) if isinstance(item, str) else _parse_routine(
        f"{item.get('schedule', '')}::{item.get('prompt', '')}" if isinstance(item, dict) else "") for item in routines]
    return {"name": name, "description": description, "boundary": boundary, "why_not_skill": why[:500],
            "channels": channels[:10], "model": model, "routines": routines,
            "reason": " ".join(str(proposal.get("reason") or "").split())[:300]}


def record_proposal(proposal: dict[str, Any]) -> str:
    try:
        from tag_handoff import append_request, current_role
    except ImportError:
        from scripts.tag_handoff import append_request, current_role
    if not current_role().is_lead:
        raise LeadRefused(f"Only {ROLE_NAME} can create Tags. Ask {ROLE_NAME} for a new Tag instead.")
    if int(os.getenv("OPENTAG_HANDOFF_DEPTH", "0") or 0) >= 1:
        raise LeadRefused("This run is answering another Tag, so it cannot create Tags.")
    clean = validate_proposal(proposal)
    append_request({"kind": "propose", **clean})
    return (f"After this reply, Tag will post the proposal for {clean['name']} in this thread. "
            "Nothing is created until the person who asked approves it.")


# Bridge side: durable proposals ---------------------------------------------------


def new_proposal_id() -> str:
    return f"p-{uuid.uuid4().hex[:10]}"


class ProposalStore:
    """One JSON record per proposed Tag, replaced atomically under its own lock."""

    def __init__(self, root: Path):
        self.root = root

    def path(self, proposal_id: str) -> Path:
        if not re.fullmatch(PROPOSAL_ID, proposal_id or ""):
            raise ValueError("invalid proposal id")
        return self.root / f"{proposal_id}.json"

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        try:
            record = json.loads(self.path(proposal_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) and record.get("schema") == SCHEMA_VERSION else None

    def all(self) -> list[dict[str, Any]]:
        records = []
        for path in sorted(self.root.glob("p-*.json")) if self.root.is_dir() else []:
            if re.fullmatch(PROPOSAL_ID, path.stem) and (record := self.get(path.stem)):
                records.append(record)
        return records

    def unfinished(self) -> list[dict[str, Any]]:
        return [record for record in self.all() if record.get("state") in OPEN_STATES]

    def create(self, *, proposal: dict[str, Any], lead: str, team: str, channel: str, thread_ts: str,
               requester: str, now: float | None = None) -> dict[str, Any]:
        created = time.time() if now is None else now
        proposal_id = new_proposal_id()
        record = {"schema": SCHEMA_VERSION, "id": proposal_id, "state": "pending", "lead": lead, "team": team,
                  "channel": channel, "thread_ts": thread_ts, "requester": requester, "created_at": created,
                  "expires_at": created + APPROVAL_MINUTES * 60, "proposal": proposal, "tag": None,
                  "card_ts": None, "step": None, "note": None}
        path = self.path(proposal_id)
        with scope_lock(path):
            write_document(path, record)
        return record

    def update(self, proposal_id: str, change: Callable[[dict[str, Any]], Any]) -> tuple[dict[str, Any] | None, Any]:
        path = self.path(proposal_id)
        with scope_lock(path):
            record = self.get(proposal_id)
            if record is None:
                return None, None
            result = change(record)
            write_document(path, record)
            return record, result

    def transition(self, proposal_id: str, allowed: set[str] | frozenset[str], state: str,
                   **fields: Any) -> dict[str, Any] | None:
        """Move to ``state`` only from ``allowed``; return the record exactly once, to one caller."""
        def change(record: dict[str, Any]) -> bool:
            if record.get("state") not in allowed:
                return False
            record.update(state=state, **fields)
            return True
        record, moved = self.update(proposal_id, change)
        return record if moved else None

    def set_fields(self, proposal_id: str, **fields: Any) -> None:
        names = {"tag", "card_ts", "step", "note"}
        if set(fields) - names:
            raise ValueError("cannot set " + ", ".join(sorted(set(fields) - names)))
        self.update(proposal_id, lambda record: record.update(fields))

    def claim_expired(self, now: float | None = None) -> list[dict[str, Any]]:
        moment = time.time() if now is None else now
        expired = []
        for record in self.all():
            if record.get("state") == "pending" and float(record.get("expires_at") or 0) <= moment:
                if moved := self.transition(record["id"], {"pending"}, "expired"):
                    expired.append(moved)
        return expired

    def interrupted(self) -> list[dict[str, Any]]:
        """Creations a restart cut short. They wait for a person to press Check again."""
        found = []
        for record in self.all():
            if record.get("state") in {"approved", "creating"}:
                if moved := self.transition(record["id"], {"approved", "creating"}, "waiting_on_slack",
                                            step="interrupted"):
                    found.append(moved)
        return found


def proposal_store(root: Path) -> ProposalStore:
    return ProposalStore(_state(root) / "tag-proposals")


def check_limits(root: Path, proposal: dict[str, Any], *, lead: str, channel: str, allowed_channels: set[str],
                 existing_names: list[str], tag_count: int, max_tags: int) -> None:
    """Installation limits for a new Tag. Raises LeadRefused with what to do instead."""
    if lead_id(root) != lead:
        raise LeadRefused(f"Only {ROLE_NAME} can create Tags. Ask {ROLE_NAME} instead.")
    if tag_count >= max_tags:
        raise LeadRefused(f"This installation already has {tag_count} Tags, the most {ROLE_NAME} may create "
                           f"(OPENTAG_LEAD_MAX_TAGS={max_tags}). Free Slack workspaces allow 10 apps in total.")
    if proposal_store(root).unfinished():
        raise LeadRefused("Another new Tag is still waiting for approval or being created. "
                           "Finish that one first.")
    if proposal["name"].casefold() in {name.casefold() for name in existing_names}:
        raise LeadRefused(f"A Tag named {proposal['name']} already exists. Route the work to it instead.")
    outside = [item for item in proposal["channels"] if item not in allowed_channels | {channel}]
    if outside:
        raise LeadRefused(f"A new Tag can only join channels {ROLE_NAME} is in. Not allowed: "
                           + ", ".join(f"<#{item}>" for item in outside) + ".")


def approval_text(record: dict[str, Any]) -> str:
    proposal = record["proposal"]
    channels = ", ".join(f"<#{item}>" for item in proposal["channels"]) or "none yet; invite it later"
    routines = "\n".join(f"• {item['schedule']}: {item['prompt']}" for item in proposal.get("routines") or [])
    return "\n".join(filter(None, [
        f"*{ROLE_NAME} proposes a new Tag: {proposal['name']}*",
        proposal["description"],
        f"*Why a separate Tag:* {BOUNDARIES[proposal['boundary']]}. {proposal['why_not_skill']}",
        f"*Model:* {proposal['model'] or 'same as ' + ROLE_NAME}",
        f"*Channels:* {channels}",
        f"*Starting routines:*\n{routines}" if routines else "",
        f"<@{record['requester']}>, approving creates a Slack app with this name and picture. "
        f"This request expires in {APPROVAL_MINUTES} minutes.",
    ]))


def main(argv: list[str] | None = None) -> int:
    try:
        from tag_handoff import HandoffRefused
    except ImportError:
        from scripts.tag_handoff import HandoffRefused
    parser = argparse.ArgumentParser(description="Lead's tools: see the Tags, and propose a new one.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("roster", help="List the Tags Lead can route to.")
    propose = commands.add_parser("propose", help="Propose a new Tag; a person approves it in Slack.")
    propose.add_argument("--name", required=True)
    propose.add_argument("--description", required=True, help="Its lane, in up to 140 characters.")
    propose.add_argument("--boundary", required=True, choices=sorted(BOUNDARIES))
    propose.add_argument("--why-not-skill", required=True, help="Why a skill or a subagent isn't enough.")
    propose.add_argument("--channel", action="append", default=[], help="Channel ID; repeat for each.")
    propose.add_argument("--model", default="")
    propose.add_argument("--routine", action="append", default=[], help='"SCHEDULE::WHAT TO DO"; repeat for each.')
    propose.add_argument("--reason", default="")
    args = parser.parse_args(argv)
    root = Path(os.getenv("TAG_HOME", "")) if os.getenv("TAG_HOME") else None
    try:
        if args.command == "roster":
            if root is None:
                raise LeadRefused("The roster is unavailable in this conversation.")
            entries = roster(root, os.getenv("TAG_ID", ""))
            print(roster_text(entries, os.getenv("OPENTAG_CURRENT_CHANNEL_ID", "")))
        else:
            print(record_proposal({"name": args.name, "description": args.description, "boundary": args.boundary,
                                   "why_not_skill": args.why_not_skill, "channels": args.channel,
                                   "model": args.model, "routines": args.routine, "reason": args.reason}))
    except (LeadRefused, HandoffRefused) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# Bridge side: one Tag's live view of the role ----------------------------------------


class LeadBridge:
    """What a bridge needs from the role. Every check reads the saved state again,

    so moving Lead takes effect at once, without a restart.
    """

    def __init__(self, root: Path, own_id: str, home: Path, *,
                 create: Callable[..., Any] | None = None):
        self.root, self.own_id, self.home = root, own_id, home
        self.proposals = proposal_store(root)
        self.routes_root = home / "state" / "routes"
        self._create = create

    @classmethod
    def from_environment(cls) -> "LeadBridge | None":
        root, own, home = os.getenv("TAG_HOME", ""), os.getenv("TAG_ID", ""), os.getenv("TAG_INSTANCE_HOME", "")
        if not root or not _valid_id(own) or not home:
            return None
        return cls(Path(root), own, Path(home))

    def is_lead(self) -> bool:
        return lead_id(self.root) == self.own_id

    def lanes(self) -> list[Card]:
        return roster(self.root, self.own_id, os.getenv("SLACK_TEAM_ID", ""))

    def lead_card(self) -> Card | None:
        return lead_card(self.root, self.own_id)

    def settings(self) -> dict[str, str]:
        """This Tag's saved settings, read fresh, so a later sign-in is seen without a restart."""
        try:
            document = json.loads((self.home / "config/settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            document = {}
        values = {key: value for key, value in os.environ.items() if key.startswith(("OPENTAG_", "SLACK_"))}
        if isinstance(document, dict):
            values.update({key: value for key, value in document.items() if isinstance(value, str)})
        return values

    def tag_count(self) -> int:
        try:
            import tag_instances
        except ImportError:
            from scripts import tag_instances
        return sum(1 for item in tag_instances.discover(self.root)
                   if item.get("valid") and Path(str(item.get("home", ""))).exists())

    def max_tags(self) -> int:
        value = self.settings().get("OPENTAG_LEAD_MAX_TAGS", "")
        return int(value) if value.isdigit() and 1 <= int(value) <= 50 else DEFAULT_MAX_TAGS

    def check(self, proposal: dict[str, Any], channel: str) -> dict[str, Any]:
        """Validate a proposal against the shape rules and this installation's limits."""
        clean = validate_proposal(proposal)
        values = self.settings()
        allowed = {item.strip() for item in values.get("SLACK_CHANNEL_IDS", "").split(",") if item.strip()}
        names = [card.name for card in cards(self.root)] + [values.get("OPENTAG_BOT_NAME", "")]
        check_limits(self.root, clean, lead=self.own_id, channel=channel, allowed_channels=allowed,
                     existing_names=[name for name in names if name], tag_count=self.tag_count(),
                     max_tags=self.max_tags())
        return clean

    def create(self, record: dict[str, Any], *, on_tag: Callable[[str], None]) -> Any:
        if self._create is not None:
            return self._create(record, on_tag=on_tag)
        try:
            import tag_creator
        except ImportError:
            from scripts import tag_creator
        return tag_creator.create(self.root, record, lead_values=self.settings(), on_tag=on_tag)

    # Routes: which owner holds a thread Lead routed.

    def save_route(self, route_id: str, *, channel: str, thread_ts: str, owner: Card, requester: str) -> None:
        write_document(self.routes_root / f"{route_id}.json", {
            "schema_version": SCHEMA_VERSION, "id": route_id, "channel": channel, "thread_ts": thread_ts,
            "owner": owner.id, "owner_name": owner.name, "owner_user": owner.user_id, "requester": requester,
            "created_at": time.time(),
        })

    def route_for_thread(self, channel: str, thread_ts: str) -> dict[str, Any] | None:
        latest = None
        for path in sorted(self.routes_root.glob("r-*.json")) if self.routes_root.is_dir() else []:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(record, dict) and record.get("channel") == channel and record.get("thread_ts") == thread_ts:
                if latest is None or float(record.get("created_at") or 0) >= float(latest.get("created_at") or 0):
                    latest = record
        return latest
