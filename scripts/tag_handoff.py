#!/usr/bin/env python3
"""Ask other Tags for help and continue when they have all replied.

The agent only records a request with ``ask``. After the run, the Slack bridge
posts one channel message that mentions every requested Tag, saves a wait
record, and keeps a status line in the original thread. Each Tag replies in
the request thread and mentions the sender. When every Tag has replied, or the
deadline passes, the sender's bridge starts one run with all the replies.

Slack carries every message between Tags, so Tags owned by different people on
different machines can work together and people can follow the exchange. Wait
states follow A2A task vocabulary (submitted, completed, failed) so another
transport can replace Slack later without changing the record.

When an installation has a Chief of Staff (see ``tag_chief``), Tags work as a
dispatcher and lanes instead:

- Chief of Staff uses ``route`` to give a request to exactly one owner Tag. It
  posts in the same thread, so the owner sees the whole thread. The owner then
  replies to the person itself. Nothing is combined or reworded.
- Any other Tag may ``ask`` only Chief of Staff, for a judgment call. It still
  writes the answer itself.
- A request makes at most MAX_TAG_HOPS Tag-to-Tag hops: route, then one
  escalation. A Tag answering another Tag can neither route nor ask.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from tag_memory import scope_lock, write_document
except ImportError:
    from scripts.tag_memory import scope_lock, write_document

SCHEMA_VERSION = 1
DEFAULT_WAIT_MINUTES = 30
MAX_WAIT_MINUTES = 120
MAX_TARGETS = 5
MAX_TASK_CHARS = 3_000
# A Tag that is answering another Tag, or combining replies, cannot ask again.
MAX_DEPTH = 1
# Route (Chief of Staff to the owner), then at most one escalation (owner to Chief of Staff).
MAX_TAG_HOPS = 2
MAX_REASON_CHARS = 300
MEMBER_ID = r"[UW][A-Z0-9]{2,20}"
HANDOFF_ID = r"h-[0-9a-f]{10}"
ROUTE_ID = r"r-[0-9a-f]{10}"
REQUEST_RE = re.compile(rf"_Handoff ({HANDOFF_ID}) for <@({MEMBER_ID})>\. Reply in this thread\._")
ROUTE_RE = re.compile(rf"_Routed ({ROUTE_ID}) for <@({MEMBER_ID})>\. You own this request\._")
RESULT_RE = re.compile(rf"_Handoff ({HANDOFF_ID}) result: (completed|failed)\._")
FINISHED = frozenset({"completed", "failed"})


class HandoffRefused(Exception):
    """A refused handoff; the message is safe to show the user."""


@dataclass(frozen=True)
class Peer:
    name: str
    user_id: str


def parse_peers(raw: str) -> list[Peer]:
    """Parse ``Name=UMEMBERID`` pairs from ``OPENTAG_PEER_TAGS``."""
    peers: list[Peer] = []
    for item in raw.split(","):
        if not item.strip():
            continue
        name, separator, user_id = item.rpartition("=")
        name, user_id = name.strip(), user_id.strip()
        if not separator or not name or not re.fullmatch(MEMBER_ID, user_id):
            raise ValueError("Use comma-separated Name=MEMBERID pairs, for example Research Tag=U0123ABCD")
        peers.append(Peer(name, user_id))
    if len({peer.user_id for peer in peers}) != len(peers) or len({p.name.casefold() for p in peers}) != len(peers):
        raise ValueError("Each peer Tag name and member ID must be listed once")
    return peers


def configured_peers() -> list[Peer]:
    try:
        return parse_peers(os.getenv("OPENTAG_PEER_TAGS", ""))
    except ValueError:
        return []


@dataclass(frozen=True)
class Role:
    """This Tag's place among the installation's Tags, as the agent's helper sees it.

    ``chief`` is None when the installation has no Chief of Staff; Tags then ask
    each other as before. The bridge checks the role again before acting.
    """
    is_chief: bool
    chief: Peer | None
    lanes: list[Peer]


def current_role() -> Role:
    home, own = os.getenv("TAG_HOME", ""), os.getenv("TAG_ID", "")
    if not home or not own:
        return Role(False, None, [])
    try:
        from tag_chief import chief_card, chief_of_staff, roster
    except ImportError:
        from scripts.tag_chief import chief_card, chief_of_staff, roster
    root = Path(home)
    team = os.getenv("SLACK_TEAM_ID", "")
    if chief_of_staff(root) == own:
        return Role(True, None, [Peer(card.name, card.user_id) for card in roster(root, own, team)])
    card = chief_card(root, own)
    return Role(False, Peer(card.name, card.user_id) if card else None, [])


def append_request(payload: dict[str, Any]) -> None:
    """Record the one Tag-to-Tag action this reply may take, for the bridge to carry out."""
    destination = os.getenv("OPENTAG_HANDOFF_REQUESTS", "")
    if not destination:
        raise HandoffRefused("Working with other Tags is unavailable in this conversation.")
    path = Path(destination)
    if path.exists() and path.read_text(encoding="utf-8").strip():
        raise HandoffRefused("This reply already asks, routes or proposes; each reply takes one Tag action. "
                             "Include every Tag in one request.")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def resolve_peer(value: str, peers: list[Peer]) -> Peer:
    wanted = value.strip().removeprefix("@").strip()
    mention = re.fullmatch(rf"<@({MEMBER_ID})>", wanted)
    if mention:
        wanted = mention.group(1)
    for peer in peers:
        if wanted == peer.user_id or wanted.casefold() == peer.name.casefold():
            return peer
    names = ", ".join(peer.name for peer in peers) or "none are configured"
    raise HandoffRefused(f"{value!r} is not a Tag this Tag may ask. Available Tags: {names}.")


def new_handoff_id() -> str:
    return f"h-{uuid.uuid4().hex[:10]}"


def request_text(
    handoff_id: str, targets: list[dict[str, str]], task: str, requester: str, origin_link: str | None = None,
) -> str:
    mentions = " ".join(f"<@{target['user_id']}>" for target in targets)
    text = f"{mentions} {task}\n\n_Handoff {handoff_id} for <@{requester}>. Reply in this thread._"
    # After the marker, so task_from_request never passes the link to a peer.
    return f"{text}\n<{origin_link}|Asked from this thread>" if origin_link else text


WORKING_TEXT = "Working on it…"


def closing_text(record: dict[str, Any]) -> str:
    """Last note in the request thread. It must never mention a Tag."""
    done = "Done. The final answer is in" if record.get("state") == "completed" else "Tag couldn't write the final answer. See"
    link = record.get("origin_link")
    return f"{done} <{link}|the original thread>." if link else f"{done} the original thread."


def result_prefix(handoff_id: str, sender: str, outcome: str) -> str:
    return f"<@{sender}> _Handoff {handoff_id} result: {outcome}._"


def task_from_request(text: str) -> str:
    """The delegated task without mentions or the handoff marker."""
    body = REQUEST_RE.split(text, maxsplit=1)[0]
    return re.sub(r"<@[^>]+>", "", body).strip()


def mentioned_tags(text: str) -> list[str]:
    """Member IDs the request message was sent to, in order."""
    head = REQUEST_RE.split(text, maxsplit=1)[0]
    return list(dict.fromkeys(re.findall(rf"<@({MEMBER_ID})(?:\|[^>]*)?>", head)))


def peer_brief(own_name: str, sender_name: str, other_names: list[str], task: str) -> str:
    """The question a peer runs: who it is, who asked, and which part is its own."""
    together = f", together with {', '.join(other_names)}," if other_names else ""
    return (
        f"You are {own_name}. {sender_name} asked you{together} to help with the request below for the person "
        f"named in it. The same message went to every Tag it names. Answer only the part meant for {own_name}; "
        "if no part is named, answer all of it. You cannot contact other Tags. If you need something, say what "
        f"in your reply.\n\nRequest from {sender_name}:\n{task}"
    )


# Agent side: record the request for the bridge -----------------------------------


def record_request(targets: list[str], task: str, wait_minutes: int) -> str:
    if int(os.getenv("OPENTAG_HANDOFF_DEPTH", "0") or 0) >= MAX_DEPTH:
        raise HandoffRefused("This run is already part of a handoff, so it cannot ask other Tags. "
                             "Answer with what you have.")
    if not os.getenv("OPENTAG_HANDOFF_REQUESTS", ""):
        raise HandoffRefused("Asking other Tags is unavailable in this conversation.")
    task = task.strip()
    if not task:
        raise HandoffRefused("Describe what the other Tags should do.")
    if len(task) > MAX_TASK_CHARS:
        raise HandoffRefused(f"Keep the request under {MAX_TASK_CHARS:,} characters.")
    if not 1 <= wait_minutes <= MAX_WAIT_MINUTES:
        raise HandoffRefused(f"Wait between 1 and {MAX_WAIT_MINUTES} minutes.")
    role = current_role()
    if role.is_chief:
        raise HandoffRefused("Chief of Staff doesn't collect answers from other Tags. Route the request to "
                             "one owner with route, or answer it yourself.")
    peers = [role.chief] if role.chief else configured_peers()
    chosen: list[Peer] = []
    for value in targets:
        peer = resolve_peer(value, peers)
        if peer not in chosen:
            chosen.append(peer)
    if not chosen or len(chosen) > MAX_TARGETS:
        raise HandoffRefused(f"Ask between 1 and {MAX_TARGETS} Tags at once.")
    append_request({"kind": "ask", "targets": [{"name": peer.name, "user_id": peer.user_id} for peer in chosen],
                    "task": task, "wait_minutes": wait_minutes})
    names = " and ".join(peer.name for peer in chosen)
    return (f"After this reply, Tag will post the request to {names} in this channel and continue "
            f"in this thread when they answer (up to {wait_minutes} minutes).")


def record_route(target: str, reason: str) -> str:
    """Chief of Staff gives this request to one owner Tag, which answers in this thread."""
    if int(os.getenv("OPENTAG_HANDOFF_DEPTH", "0") or 0) >= MAX_DEPTH:
        raise HandoffRefused("This run is answering another Tag, so it cannot route. Answer with what you have.")
    if not os.getenv("OPENTAG_HANDOFF_REQUESTS", ""):
        raise HandoffRefused("Routing is unavailable in this conversation.")
    role = current_role()
    if not role.is_chief:
        raise HandoffRefused("Only Chief of Staff routes requests. Answer it yourself, or ask Chief of Staff.")
    reason = " ".join(reason.split())
    if not reason:
        raise HandoffRefused("Say in one sentence why this Tag should own the request.")
    if len(reason) > MAX_REASON_CHARS:
        raise HandoffRefused(f"Keep the reason under {MAX_REASON_CHARS} characters.")
    peer = resolve_peer(target, role.lanes)
    append_request({"kind": "route", "target": {"name": peer.name, "user_id": peer.user_id}, "reason": reason})
    return (f"After this reply, Tag will hand this thread to {peer.name}, which will answer here itself. "
            "Don't answer the request yourself; say who will and why in one or two sentences.")


def read_requests(path: Path) -> list[dict[str, Any]]:
    """The Tag actions a run recorded; a line without ``kind`` is an ``ask`` from an older release."""
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []
    found = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            item.setdefault("kind", "ask")
            found.append(item)
    return found


def read_request(path: Path) -> dict[str, Any] | None:
    requests = read_requests(path)
    request = requests[0] if requests else None
    if not isinstance(request, dict) or request.get("kind") != "ask" or not request.get("targets") or not request.get("task"):
        return None
    return request


def read_route(path: Path) -> dict[str, Any] | None:
    requests = read_requests(path)
    request = requests[0] if requests else None
    target = request.get("target") if isinstance(request, dict) and request.get("kind") == "route" else None
    if (not isinstance(target, dict) or not re.fullmatch(MEMBER_ID, str(target.get("user_id", "")))
            or not str(request.get("reason") or "").strip()):
        return None
    return {"target": {"name": str(target.get("name") or "a Tag"), "user_id": target["user_id"]},
            "reason": " ".join(str(request["reason"]).split())[:MAX_REASON_CHARS]}


def read_proposal(path: Path) -> dict[str, Any] | None:
    requests = read_requests(path)
    request = requests[0] if requests else None
    return request if isinstance(request, dict) and request.get("kind") == "propose" else None


def new_route_id() -> str:
    return f"r-{uuid.uuid4().hex[:10]}"


def route_text(route_id: str, target: dict[str, str], reason: str, requester: str) -> str:
    """The only message that wakes the owner. Posted in the requester's own thread."""
    return (f"<@{target['user_id']}> {reason}\n\n"
            f"_Routed {route_id} for <@{requester}>. You own this request._")


def route_brief(own_name: str, chief_name: str, reason: str) -> str:
    return (
        f"You are {own_name}. {chief_name} routed the request in this thread to you because: {reason}\n"
        "You own it end to end. Read the whole thread above, then answer the person who asked, here in this "
        "thread. Don't wait for other Tags and don't hand it back. If you need a judgment call only "
        f"{chief_name} can make, you may ask {chief_name} once; you still write the answer."
    )


def routed_note(owner_name: str) -> str:
    """Context for Chief of Staff when it is mentioned again in a thread it already routed."""
    return (f"Earlier in this thread you routed the request to {owner_name}, which owns it. "
            f"If this message continues that work, tell the person to mention {owner_name} directly. "
            "Route again only if the work has clearly moved to another lane, and say so.")


# Bridge side: durable wait records ---------------------------------------------


class HandoffStore:
    """One JSON record per wait, replaced atomically under its own lock."""

    def __init__(self, root: Path):
        self.root = root

    def path(self, handoff_id: str) -> Path:
        if not re.fullmatch(HANDOFF_ID, handoff_id):
            raise ValueError("invalid handoff id")
        return self.root / f"{handoff_id}.json"

    def get(self, handoff_id: str) -> dict[str, Any] | None:
        for attempt in range(20):
            try:
                record = json.loads(self.path(handoff_id).read_text(encoding="utf-8"))
                break
            except PermissionError:
                # Windows refuses to read a file while another thread replaces it; that is not "missing".
                if os.name != "nt" or attempt == 19:
                    return None
                time.sleep(0.05)
            except (OSError, ValueError):
                return None
        return record if isinstance(record, dict) and record.get("schema") == SCHEMA_VERSION else None

    def create(self, *, handoff_id: str, team: str, requester: str, origin_channel: str,
               origin_thread_ts: str, question: str, task: str, targets: list[dict[str, str]],
               request_ts: str, wait_minutes: int, now: float | None = None) -> dict[str, Any]:
        created = time.time() if now is None else now
        record = {
            "schema": SCHEMA_VERSION, "id": handoff_id, "state": "waiting", "team": team,
            "requester": requester, "origin_channel": origin_channel, "origin_thread_ts": origin_thread_ts,
            "question": question, "task": task, "request_ts": request_ts, "status_ts": None,
            "created_at": created, "deadline": created + wait_minutes * 60,
            "targets": {target["user_id"]: {"name": target["name"], "state": "submitted", "reply_ts": None}
                        for target in targets},
        }
        path = self.path(handoff_id)
        with scope_lock(path):
            write_document(path, record)
        return record

    def _update(self, handoff_id: str, change: Any) -> tuple[dict[str, Any] | None, Any]:
        path = self.path(handoff_id)
        with scope_lock(path):
            record = self.get(handoff_id)
            if record is None:
                return None, None
            result = change(record)
            write_document(path, record)
            return record, result

    def set_field(self, handoff_id: str, name: str, value: str) -> None:
        if name not in {"status_ts", "status_mode", "request_ts", "request_link", "origin_link", "link_shown", "status_closed"}:
            raise ValueError(f"cannot set {name}")
        self._update(handoff_id, lambda record: record.__setitem__(name, value))

    def record_reply(self, handoff_id: str, sender: str, outcome: str, reply_ts: str) -> tuple[dict[str, Any] | None, bool]:
        """Record a Tag's reply; return True exactly once, when every Tag has replied."""
        def change(record: dict[str, Any]) -> bool:
            target = record["targets"].get(sender)
            if record["state"] != "waiting" or target is None or target["state"] in FINISHED:
                return False
            target.update(state=outcome, reply_ts=reply_ts)
            if all(item["state"] in FINISHED for item in record["targets"].values()):
                record["state"] = "combining"
                return True
            return False
        record, ready = self._update(handoff_id, change)
        return record, bool(ready)

    def claim_expired(self, now: float | None = None) -> list[dict[str, Any]]:
        """Move overdue waits to combining; each is returned to exactly one caller."""
        moment = time.time() if now is None else now
        claimed = []
        for path in sorted(self.root.glob("h-*.json")):
            if not re.fullmatch(HANDOFF_ID, path.stem):
                continue  # A stray file must not stop the other waits.

            def change(record: dict[str, Any]) -> bool:
                deadline = record.get("deadline")
                targets = record.get("targets")
                answered = isinstance(targets, dict) and bool(targets) and all(
                    isinstance(item, dict) and item.get("state") in FINISHED for item in targets.values()
                )
                overdue = isinstance(deadline, (int, float)) and deadline <= moment
                if record.get("state") == "waiting" and (overdue or answered):
                    record["state"] = "combining"
                    return True
                return False
            try:
                record, ready = self._update(path.stem, change)
            except (OSError, ValueError):
                continue  # One unwritable file must not stop the other waits.
            if ready and record is not None:
                claimed.append(record)
        return claimed

    def finish(self, handoff_id: str, outcome: str) -> None:
        self._update(handoff_id, lambda record: record.__setitem__("state", outcome))

    def fail_if_combining(self, handoff_id: str) -> bool:
        """End a combine run that stopped without a result; return True if it changed."""
        def change(record: dict[str, Any]) -> bool:
            if record.get("state") == "combining":
                record["state"] = "failed"
                return True
            return False
        return bool(self._update(handoff_id, change)[1])

    def requeue_combining(self) -> list[str]:
        """Return interrupted combine runs to waiting; claim_expired runs them again."""
        requeued = []
        for path in sorted(self.root.glob("h-*.json")):
            if not re.fullmatch(HANDOFF_ID, path.stem):
                continue

            def change(record: dict[str, Any]) -> bool:
                if record.get("state") == "combining":
                    record["state"] = "waiting"
                    return True
                return False
            try:
                if self._update(path.stem, change)[1]:
                    requeued.append(path.stem)
            except (OSError, ValueError):
                continue  # A damaged file must not stop startup.
        return requeued


def status_text(record: dict[str, Any]) -> str:
    """Progress line for the original thread. It must never mention a Tag."""
    labels = {"submitted": "waiting", "completed": "replied ✓", "failed": "couldn't help"}
    parts = [f"{target['name']}: {labels[target['state']]}" for target in record["targets"].values()]
    if record["state"] == "waiting":
        deadline = time.strftime("%H:%M UTC", time.gmtime(record["deadline"]))
        tail = f"Continuing here when all reply, or at {deadline}."
    elif record["state"] == "combining":
        tail = "Writing the final answer…"
    else:
        tail = "Done."
    link = record.get("request_link")
    where = f"<{link}|a new message>" if link else "a new message"
    return f"Asked other Tags in {where} in this channel. {' · '.join(parts)}. {tail}"


PLAN_TITLE = "Asking other Tags"


def plan_chunks(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Slack plan steps for the original thread: one per Tag, then the final answer.

    Every call returns every step, so a late or repeated update cannot leave a step behind.
    Titles never mention a Tag.
    """
    labels = {
        "submitted": ("Waiting for {name}", "in_progress"),
        "completed": ("{name} replied", "complete"),
        "failed": ("{name} couldn't help", "error"),
    }
    chunks = []
    for user_id, target in record["targets"].items():
        title, status = labels[target["state"]]
        if record["state"] != "waiting" and target["state"] == "submitted":
            title, status = "{name} didn't reply in time", "error"
        chunks.append({
            "type": "task_update",
            "id": hashlib.sha256(f"{record['id']}:{user_id}".encode()).hexdigest()[:32],
            "title": html.escape(title.format(name=target["name"])[:200], quote=False),
            "status": status,
        })
    final = {
        "waiting": "pending", "combining": "in_progress", "completed": "complete", "failed": "error",
    }.get(record["state"], "pending")
    chunks.append({
        "type": "task_update",
        "id": hashlib.sha256(f"{record['id']}:final".encode()).hexdigest()[:32],
        "title": "Write the final answer" if record["state"] != "failed" else "Couldn't write the final answer",
        "status": final,
    })
    return chunks


def plan_link_text(record: dict[str, Any]) -> str:
    link = record.get("request_link")
    return f"Their replies are in [a new message]({link})." if link else "Their replies are in a new message in this channel."


def combine_question(record: dict[str, Any]) -> str:
    missing = [target["name"] for target in record["targets"].values() if target["state"] != "completed"]
    gap = ""
    if missing:
        gap = (f" {', '.join(missing)} did not provide a result before the deadline or could not help; "
               "say so in the answer.")
    return (
        f"The other Tags have replied to handoff {record['id']}. Using their replies in the handoff thread "
        "below, finish the original request for the person who asked. Do not ask other Tags again."
        f"{gap}\n\nOriginal request: {record['question']}\n\nWhat the other Tags were asked: {record['task']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask other Tags for help from the current Slack channel.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("peers", help="List the Tags this Tag may ask or route to.")
    route = commands.add_parser("route", help="Chief of Staff only: give this request to one owner Tag.")
    route.add_argument("--to", required=True, help="The owner Tag's name.")
    route.add_argument("--reason", required=True, help="One sentence: why this Tag owns the request.")
    ask = commands.add_parser("ask", help="Ask one or more Tags; Tag continues when all reply.")
    ask.add_argument("--to", action="append", required=True, help="Tag name; repeat for each Tag.")
    ask.add_argument("--task", required=True, help="What the Tags should do, with the needed context.")
    ask.add_argument("--wait-minutes", type=int, default=DEFAULT_WAIT_MINUTES)
    args = parser.parse_args(argv)
    try:
        if args.command == "peers":
            role = current_role()
            peers = role.lanes if role.is_chief else [role.chief] if role.chief else configured_peers()
            print("\n".join(peer.name for peer in peers) or "No other Tags are configured.")
        elif args.command == "route":
            print(record_route(args.to, args.reason))
        else:
            print(record_request(args.to, args.task, args.wait_minutes))
    except HandoffRefused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
