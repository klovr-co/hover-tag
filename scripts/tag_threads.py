# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""Thread transcripts: each Slack thread a Tag works in, as one conversation.

The Slack bridge appends one JSON line per event to
``.tag/state/threads/<channel>-<thread_ts>.jsonl`` while it works: people's
Slack messages, the Tag's full Markdown answers, step names and statuses,
files, memory changes, and the questions the Tag asked with who answered them.
Tag.app reads these through ``tag NAME threads --json`` and
``tag NAME thread CHANNEL THREAD_TS --json``.

Only the bridge writes transcripts. The CLI asks the running bridge to reply or
to answer a question by writing a request under ``state/threads/requests/``,
like routine run-now requests.

Every text is redacted with the same credential patterns as the Activity
store (``tag_error_reporting.redact_secrets``), keeping its Markdown layout.
Raw tool output, prompts, and reasoning are never stored: a step is only its
public label and status. A thread is deleted ``retention_days`` (90 by default)
after its last entry; the owner changes this with ``tag NAME threads retention``.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    from .tag_activity import PUBLIC_LABELS, ActivityStore, artifact_records, reply_preview
    from .tag_error_reporting import redact_secrets
    from .tag_links import thread_link
    from .tag_paths import instance_home, restrict_windows_acl
except ImportError:  # Direct script execution does not create a package context.
    from tag_activity import PUBLIC_LABELS, ActivityStore, artifact_records, reply_preview
    from tag_error_reporting import redact_secrets
    from tag_links import thread_link
    from tag_paths import instance_home, restrict_windows_acl


SCHEMA_VERSION = 1
THREADS_DIR = "state/threads"
REQUESTS_DIR = "state/threads/requests"
SETTINGS_FILE = "state/threads/settings.json"
DEFAULT_RETENTION_DAYS = 90
MAX_RETENTION_DAYS = 3650
MAX_ANSWER_CHARS = 40_000
MAX_MESSAGE_CHARS = 8_000
MAX_PROMPT_CHARS = 1_000
MAX_LABEL_CHARS = 80
MAX_NAME_CHARS = 80
MAX_TITLE_CHARS = 80
MAX_CHOICES = 12
MAX_REPLY_CHARS = 4_000
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 500
# A reply or answer asked for this long ago was made while the Tag was stopped; it is dropped.
REQUEST_MAX_AGE = timedelta(minutes=10)
# A run still "running" this long after its last entry was cut off by a stopped bridge.
STALE_RUN = timedelta(hours=2)

CHANNEL_RE = re.compile(r"^[CDG][A-Z0-9]{1,30}$")
THREAD_TS_RE = re.compile(r"^\d{1,12}\.\d{1,8}$")
MEMBER_RE = re.compile(r"^[UWB][A-Z0-9]{2,30}$")
PROMPT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CHOICE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
KIND_RE = re.compile(r"^[a-z][a-z_]{0,31}$")
RUN_ID_RE = re.compile(r"^[a-f0-9]{32}$")
TEAM_RE = re.compile(r"^[TE][A-Z0-9]{2,30}$")

RUN_STATUSES = frozenset({"running", "completed", "failed", "interrupted"})
STEP_STATUSES = frozenset({"running", "completed", "failed", "declined", "interrupted", "unknown"})
ANSWER_OUTCOMES = frozenset({"completed", "failed", "stopped"})
MEMORY_ACTIONS = frozenset({"saved", "changed", "forgot"})
VIA = frozenset({"slack", "app", "expired"})
# Kinds Tag itself opens. Other components (such as the Slack rounds) add their own.
PROMPT_KINDS = ("run_request", "approval", "queue_steer", "sign_in")
# Prompt fields the store controls; any other JSON scalar a caller adds passes through.
_PROMPT_FIELDS = frozenset({"type", "at", "prompt_id", "kind", "text", "choices", "requester", "url",
                            "expires_at", "run_id", "slack_message"})
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class PromptExpired(Exception):
    """An answer arrived after the question stopped waiting for it."""


class ThreadError(Exception):
    """A request about a thread can't be made; the message is for people."""


# --- Redaction ----------------------------------------------------------------

def redact(value: Any, limit: int) -> str:
    """Credential-redacted text that keeps its Markdown layout, bounded to ``limit``."""
    if not isinstance(value, str):
        return ""
    text = _CONTROL_RE.sub("", value.replace("\r\n", "\n").replace("\r", "\n"))
    text = redact_secrets(text[: limit * 2]).strip()
    return text if len(text) <= limit else text[: limit - 14].rstrip() + "… [truncated]"


def _name(value: Any) -> str | None:
    clean = " ".join(redact(value, MAX_NAME_CHARS).split()) if isinstance(value, str) else ""
    return clean or None


def _person(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    member = value.get("id")
    person: dict[str, Any] = {"id": member if isinstance(member, str) and MEMBER_RE.fullmatch(member) else None,
                              "name": _name(value.get("name"))}
    if value.get("bot") is True:
        person["bot"] = True
    if value.get("self") is True:
        person["self"] = True
    return person if person["id"] or person["name"] else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def valid_thread(channel: Any, thread_ts: Any) -> bool:
    return (isinstance(channel, str) and bool(CHANNEL_RE.fullmatch(channel))
            and isinstance(thread_ts, str) and bool(THREAD_TS_RE.fullmatch(thread_ts)))


def file_name(channel: str, thread_ts: str) -> str:
    if not valid_thread(channel, thread_ts):
        raise ThreadError("That isn't a Slack thread.")
    return f"{channel}-{thread_ts}.jsonl"


_MENTION_RE = re.compile(r"<@([UW][A-Z0-9]{2,30})(?:\|([^>]*))?>")
_CHANNEL_REF_RE = re.compile(r"<#([CG][A-Z0-9]{2,30})(?:\|([^>]*))?>")
_LINK_RE = re.compile(r"<((?:https?|mailto):[^>|\s]+)(?:\|([^>]*))?>")
_SPECIAL_RE = re.compile(r"<!(here|channel|everyone)(?:\|[^>]*)?>")


def slack_to_markdown(text: Any, name_of: Callable[[str], str | None] = lambda _id: None) -> str:
    """A Slack message's text as Markdown: names for mentions, links as links."""
    if not isinstance(text, str):
        return ""

    def mention(match: re.Match[str]) -> str:
        name = match.group(2) or name_of(match.group(1))
        return f"@{name}" if name else "@someone"

    text = _MENTION_RE.sub(mention, text)
    text = _CHANNEL_REF_RE.sub(lambda m: f"#{m.group(2) or m.group(1)}", text)
    text = _LINK_RE.sub(lambda m: f"[{m.group(2)}]({m.group(1)})" if m.group(2) else m.group(1), text)
    text = _SPECIAL_RE.sub(lambda m: f"@{m.group(1)}", text)
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


# --- Retention ----------------------------------------------------------------

def retention_days(home: Path) -> int:
    try:
        value = json.loads((home / SETTINGS_FILE).read_text(encoding="utf-8")).get("retention_days")
    except (OSError, ValueError, AttributeError):
        return DEFAULT_RETENTION_DAYS
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_RETENTION_DAYS \
        else DEFAULT_RETENTION_DAYS


def set_retention_days(home: Path, days: int) -> int:
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_RETENTION_DAYS:
        raise ThreadError(f"Keep threads for 1 to {MAX_RETENTION_DAYS} days.")
    _atomic_write(home / SETTINGS_FILE, {"schema_version": SCHEMA_VERSION, "retention_days": days})
    return days


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, separators=(",", ":"))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


# --- The store ----------------------------------------------------------------

_LOCK = threading.RLock()


class ThreadStore:
    """Append-only transcripts, one file per Slack thread.

    Writes take one process-wide lock and go out as single ``O_APPEND`` writes,
    so readers in other processes see whole lines; a reader skips a torn last
    line. Every public method is safe to call from any bridge thread.
    """

    def __init__(self, home: Path | None = None) -> None:
        self.home = home or instance_home()
        self.root = self.home / THREADS_DIR

    def path(self, channel: str, thread_ts: str) -> Path:
        return self.root / file_name(channel, thread_ts)

    def exists(self, channel: str, thread_ts: str) -> bool:
        return valid_thread(channel, thread_ts) and self.path(channel, thread_ts).is_file()

    # Writing ---------------------------------------------------------------

    def append(self, channel: str, thread_ts: str, entry: dict[str, Any], *, team: str = "") -> None:
        """Append one entry, starting the file with its thread header when it is new."""
        path = self.path(channel, thread_ts)
        with _LOCK:
            lines = []
            if not path.exists():
                self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
                restrict_windows_acl(self.root)
                lines.append({"type": "thread", "v": SCHEMA_VERSION, "at": _now(), "channel": channel,
                              "thread_ts": thread_ts, "team": team if TEAM_RE.fullmatch(team or "") else None,
                              "source": "slack"})
            lines.append({"at": _now(), **entry})
            data = "".join(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n" for line in lines)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(descriptor, data.encode("utf-8"))
            finally:
                os.close(descriptor)

    def message(self, channel: str, thread_ts: str, *, ts: str | None, author: dict[str, Any] | None,
                text: str, via: str = "slack", team: str = "", posted_to_slack: bool | None = None) -> None:
        entry: dict[str, Any] = {"type": "message", "ts": ts if isinstance(ts, str) and THREAD_TS_RE.fullmatch(ts) else None,
                                 "author": _person(author), "text": redact(text, MAX_MESSAGE_CHARS),
                                 "via": via if via in VIA else "slack"}
        if posted_to_slack is not None:
            entry["posted_to_slack"] = bool(posted_to_slack)
        self.append(channel, thread_ts, entry, team=team)

    def open_prompt(self, *, team: str = "", channel: str, thread_ts: str, kind: str, text: str,
                    choices: Iterable[dict[str, Any]], requester: dict[str, Any] | None = None,
                    prompt_id: str | None = None, url: str | None = None, expires_at: datetime | None = None,
                    run_id: str | None = None, slack_message: dict[str, Any] | None = None,
                    **extra: Any) -> str:
        """Record a question that waits for a person; returns its prompt ID.

        The thread shows "Needs you" in Tag.app until ``close_prompt`` or ``expires_at``.
        ``slack_message`` is the private Slack message holding its buttons, when it can be edited.
        """
        prompt_id = prompt_id or uuid.uuid4().hex[:16]
        if not PROMPT_ID_RE.fullmatch(prompt_id) or not isinstance(kind, str) or not KIND_RE.fullmatch(kind):
            raise ValueError("invalid prompt")
        clean_choices = []
        for choice in list(choices)[:MAX_CHOICES]:
            if not isinstance(choice, dict) or not isinstance(choice.get("id"), str) \
                    or not CHOICE_ID_RE.fullmatch(choice["id"]):
                continue
            item: dict[str, Any] = {"id": choice["id"], "label": " ".join(redact(choice.get("label"), MAX_LABEL_CHARS).split())
                                    or choice["id"]}
            if choice.get("primary"):
                item["primary"] = True
            if choice.get("style") in ("primary", "danger"):
                item["style"] = choice["style"]
            clean_choices.append(item)
        if not clean_choices:
            raise ValueError("a prompt needs at least one choice")
        entry: dict[str, Any] = {"type": "prompt", "prompt_id": prompt_id, "kind": kind,
                                 "text": redact(text, MAX_PROMPT_CHARS), "choices": clean_choices,
                                 "requester": _person(requester)}
        if isinstance(url, str) and re.fullmatch(r"https://[^\s<>\"]{1,2000}", url):
            entry["url"] = url
        if expires_at is not None:
            entry["expires_at"] = expires_at.astimezone(timezone.utc).isoformat(timespec="seconds")
        if isinstance(run_id, str) and RUN_ID_RE.fullmatch(run_id):
            entry["run_id"] = run_id
        if isinstance(slack_message, dict) and valid_thread(slack_message.get("channel"), slack_message.get("ts")):
            entry["slack_message"] = {"channel": slack_message["channel"], "ts": slack_message["ts"]}
        for key, value in extra.items():
            if key in _PROMPT_FIELDS or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", key):
                continue
            if isinstance(value, bool) or (isinstance(value, int) and abs(value) < 2**53):
                entry[key] = value
            elif isinstance(value, str):
                entry[key] = redact(value, 200)
        self.append(channel, thread_ts, entry, team=team)
        return prompt_id

    def close_prompt(self, channel: str, thread_ts: str, prompt_id: str, *, choice: str | None,
                     by: dict[str, Any] | None = None, via: str = "slack") -> bool:
        """Record who answered a question, once. False when it isn't waiting any more."""
        with _LOCK:
            prompt = self.pending_prompt(channel, thread_ts, prompt_id, include_expired=via == "expired")
            if prompt is None:
                return False
            label = next((c["label"] for c in prompt["choices"] if c["id"] == choice), None)
            self.append(channel, thread_ts, {
                "type": "prompt_closed", "prompt_id": prompt_id,
                "choice": choice if isinstance(choice, str) and CHOICE_ID_RE.fullmatch(choice) else None,
                "choice_label": label, "by": _person(by), "via": via if via in VIA else "slack"})
            return True

    def expire_prompts(self, channel: str, thread_ts: str, *, run_id: str | None = None,
                       kinds: Iterable[str] | None = None) -> int:
        """Close every waiting prompt of a run (or of these kinds) that can no longer be answered."""
        wanted = set(kinds) if kinds is not None else None
        closed = 0
        with _LOCK:
            if not self.exists(channel, thread_ts):
                return 0
            for prompt in self.read(channel, thread_ts)["prompts"]:
                if prompt["status"] != "pending":
                    continue
                if run_id is not None and prompt.get("run_id") != run_id:
                    continue
                if wanted is not None and prompt["kind"] not in wanted:
                    continue
                closed += self.close_prompt(channel, thread_ts, prompt["prompt_id"], choice=None, via="expired")
        return closed

    def notice(self, channel: str, thread_ts: str, text: str, *, prompt_id: str | None = None) -> None:
        entry: dict[str, Any] = {"type": "notice", "text": redact(text, 300)}
        if prompt_id and PROMPT_ID_RE.fullmatch(prompt_id):
            entry["prompt_id"] = prompt_id
        self.append(channel, thread_ts, entry)

    # Reading ---------------------------------------------------------------

    def entries(self, channel: str, thread_ts: str) -> list[dict[str, Any]]:
        try:
            raw = self.path(channel, thread_ts).read_text(encoding="utf-8")
        except (OSError, UnicodeError, ThreadError):
            return []
        result = []
        for line in raw.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue  # A torn last line, or damage: skip it.
            if isinstance(item, dict) and isinstance(item.get("type"), str):
                result.append(item)
        return result

    def pending_prompt(self, channel: str, thread_ts: str, prompt_id: str, *,
                       include_expired: bool = False) -> dict[str, Any] | None:
        if not self.exists(channel, thread_ts):
            return None
        for prompt in self.read(channel, thread_ts)["prompts"]:
            if prompt["prompt_id"] == prompt_id:
                if prompt["status"] == "pending" or (include_expired and prompt["status"] == "expired"
                                                     and prompt.get("answer") is None):
                    return prompt
                return None
        return None

    def read(self, channel: str, thread_ts: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Fold a transcript into the conversation Tag.app shows."""
        return fold(self.entries(channel, thread_ts), channel, thread_ts, now=now)

    def seen_slack_messages(self, channel: str, thread_ts: str) -> set[str]:
        return {entry["ts"] for entry in self.entries(channel, thread_ts)
                if entry.get("type") in ("message", "answer") and isinstance(entry.get("ts"), str)}

    # Expiry ----------------------------------------------------------------

    def prune(self, *, now: datetime | None = None) -> int:
        """Delete threads whose last entry is older than the retention period."""
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days(self.home))
        removed = 0
        try:
            paths = list(self.root.glob("*.jsonl"))
        except OSError:
            return 0
        for path in paths:
            with _LOCK:
                if _last_entry_time(path) < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1
        return removed


def _last_entry_time(path: Path) -> datetime:
    """When a transcript last changed: its last entry's time, else the file's."""
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - 65_536))
            tail = stream.read().decode("utf-8", "replace").splitlines()
        for line in reversed(tail):
            try:
                at = _parse(json.loads(line).get("at"))
            except (ValueError, AttributeError):
                continue
            if at is not None:
                return at
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return datetime.max.replace(tzinfo=timezone.utc)


def expired(path: Path, home: Path, now: datetime | None = None) -> bool:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days(home))
    return _last_entry_time(path) < cutoff


# --- Folding ------------------------------------------------------------------

def fold(entries: list[dict[str, Any]], channel: str, thread_ts: str, *,
         now: datetime | None = None) -> dict[str, Any]:
    """Entries in order, with each run's steps folded into one "work" item.

    Messages and answers interleave as they happened. An answer carries its
    run's files and memory changes; a prompt carries its answer.
    """
    now = now or datetime.now(timezone.utc)
    header = next((e for e in entries if e.get("type") == "thread"), {})
    items: list[dict[str, Any]] = []
    runs: dict[str, dict[str, Any]] = {}
    answers: dict[str, list[dict[str, Any]]] = {}
    prompts: dict[str, dict[str, Any]] = {}
    files: list[dict[str, Any]] = []
    memory: list[dict[str, Any]] = []
    people: dict[str, str] = {}
    first_question = ""
    last_at = _parse(header.get("at"))
    for entry in entries:
        kind = entry.get("type")
        at = entry.get("at") if isinstance(entry.get("at"), str) else None
        if (parsed := _parse(at)) is not None and (last_at is None or parsed > last_at):
            last_at = parsed
        if kind == "message":
            author = _person(entry.get("author"))
            text = entry.get("text") if isinstance(entry.get("text"), str) else ""
            item = {"type": "message", "at": at, "ts": entry.get("ts"), "author": author, "text": text,
                    "via": entry.get("via") if entry.get("via") in VIA else "slack"}
            if isinstance(entry.get("posted_to_slack"), bool):
                item["posted_to_slack"] = entry["posted_to_slack"]
            items.append(item)
            if author and not author.get("bot") and author.get("name"):
                people.setdefault(author.get("id") or author["name"], author["name"])
            if not first_question and author and not author.get("bot") and text.strip():
                first_question = text
        elif kind == "run" and isinstance(entry.get("run_id"), str):
            run = runs.get(entry["run_id"])
            if run is None:
                run = {"type": "work", "run_id": entry["run_id"], "status": "running", "started_at": at,
                       "finished_at": None, "steps": [], "via": entry.get("via") if entry.get("via") in VIA else "slack"}
                if (requester := _person(entry.get("requester"))) is not None:
                    run["requester"] = requester
                runs[entry["run_id"]] = run
                items.append(run)
            status = entry.get("status")
            if status in RUN_STATUSES and status != "running":
                run["status"] = status
                run["finished_at"] = at
        elif kind == "step" and isinstance(entry.get("run_id"), str):
            run = runs.get(entry["run_id"])
            if run is None or not isinstance(entry.get("step"), str):
                continue
            step = next((s for s in run["steps"] if s["id"] == entry["step"]), None)
            status = entry.get("status") if entry.get("status") in STEP_STATUSES else "unknown"
            if step is None:
                run["steps"].append({"id": entry["step"], "label": entry.get("label") if isinstance(entry.get("label"), str)
                                     else "Working…", "status": status})
            else:
                step["status"] = status
        elif kind == "answer":
            item = {"type": "answer", "at": at, "run_id": entry.get("run_id"),
                    "text": entry.get("text") if isinstance(entry.get("text"), str) else "",
                    "outcome": entry.get("outcome") if entry.get("outcome") in ANSWER_OUTCOMES else "completed",
                    "posted_to": entry.get("posted_to") if entry.get("posted_to") in ("slack", "app") else "slack",
                    "files": [], "memory": []}
            if isinstance(entry.get("ts"), str):
                item["ts"] = entry["ts"]
            items.append(item)
            answers.setdefault(str(entry.get("run_id")), []).append(item)
        elif kind == "file":
            record = artifact_records([entry.get("file")])
            if record:
                files.append({**record[0], "run_id": entry.get("run_id"), "at": at})
        elif kind == "memory":
            change = {key: entry.get(key) for key in ("action", "scope", "key", "version", "summary", "run_id")}
            if change["action"] in MEMORY_ACTIONS and isinstance(change["key"], str):
                memory.append({**change, "at": at})
        elif kind == "prompt" and isinstance(entry.get("prompt_id"), str):
            prompt = {key: value for key, value in entry.items() if key not in ("type", "at")}
            prompt.update(type="prompt", at=at, status="pending", answer=None)
            if entry["prompt_id"] not in prompts:
                prompts[entry["prompt_id"]] = prompt
                items.append(prompt)
        elif kind == "prompt_closed" and entry.get("prompt_id") in prompts:
            prompt = prompts[entry["prompt_id"]]
            if prompt["status"] == "pending":
                prompt["status"] = "expired" if entry.get("via") == "expired" else "answered"
                if entry.get("via") != "expired":
                    prompt["answer"] = {"choice": entry.get("choice"), "label": entry.get("choice_label"),
                                        "by": _person(entry.get("by")), "via": entry.get("via"), "at": at}
        elif kind == "notice":
            items.append({"type": "notice", "at": at, "text": entry.get("text") if isinstance(entry.get("text"), str) else "",
                          **({"prompt_id": entry["prompt_id"]} if isinstance(entry.get("prompt_id"), str) else {})})
    for change in files:
        for answer in answers.get(str(change["run_id"]), [])[-1:]:
            answer["files"].append(change)
    for change in memory:
        for answer in answers.get(str(change["run_id"]), [])[-1:]:
            answer["memory"].append(change)
    for prompt in prompts.values():
        expires = _parse(prompt.get("expires_at"))
        if prompt["status"] == "pending" and expires is not None and expires <= now:
            prompt["status"] = "expired"
    for run in runs.values():
        run["step_count"] = len(run["steps"])
        started, finished = _parse(run["started_at"]), _parse(run["finished_at"])
        if run["status"] == "running" and last_at is not None and now - last_at > STALE_RUN:
            run["status"] = "interrupted"  # The bridge stopped before the run could finish.
        if started and finished:
            run["duration_seconds"] = max(0, int((finished - started).total_seconds()))
    pending = [p for p in prompts.values() if p["status"] == "pending"]
    ordered_runs = [item for item in items if item["type"] == "work"]
    last_answer = next((item for item in reversed(items) if item["type"] == "answer"), None)
    if pending:
        status = "needs_you"
    elif ordered_runs and ordered_runs[-1]["status"] == "running":
        status = "working"
    elif ordered_runs and ordered_runs[-1]["status"] == "failed":
        status = "failed"
    elif ordered_runs and ordered_runs[-1]["status"] == "interrupted":
        status = "stopped"
    else:
        status = "done"
    title = _title(first_question)
    return {
        "channel": channel, "thread_ts": thread_ts,
        "team": header.get("team") if isinstance(header.get("team"), str) else None,
        "source": "slack", "title": title, "status": status, "preview_only": False,
        "started_at": header.get("at") or (items[0]["at"] if items else None),
        "updated_at": last_at.isoformat(timespec="seconds") if last_at else None,
        "people": list(people.values())[:8],
        "message_count": sum(1 for item in items if item["type"] in ("message", "answer")),
        "file_count": len(files), "memory_count": len(memory), "pending_prompts": len(pending),
        "last_answer_preview": answer_preview(last_answer["text"]) if last_answer else None,
        "items": items, "files": files, "memory": memory, "prompts": list(prompts.values()),
    }


def answer_preview(text: str) -> str:
    """A short excerpt of an answer for the thread list, without tables."""
    return reply_preview("\n".join(line for line in text.splitlines() if not line.lstrip().startswith("|")))


def _title(text: str) -> str | None:
    """A thread's title: its first question, without the mention, on one line."""
    text = re.sub(r"\[([^\]]*)\]\([^)\s]*\)", r"\1", text)
    line = " ".join(re.sub(r"^\s*(?:@\S+\s+)+", "", text).split())
    if not line:
        return None
    line = line[0].upper() + line[1:]
    return line if len(line) <= MAX_TITLE_CHARS else line[: MAX_TITLE_CHARS - 1].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


SUMMARY_FIELDS = ("channel", "thread_ts", "team", "source", "title", "status", "preview_only", "started_at",
                  "updated_at", "people", "message_count", "file_count", "memory_count", "pending_prompts",
                  "last_answer_preview")


# --- Runs, as the bridge records them ---------------------------------------

class RunTranscript:
    """What one bridge run adds to its thread. Never raises: a transcript must not stop a reply."""

    def __init__(self, store: ThreadStore, *, team: str, channel: str, thread_ts: str, run_id: str | None,
                 requester: dict[str, Any] | None, via: str = "slack", logger: Any = None) -> None:
        self.store = store
        self.team, self.channel, self.thread_ts = team, channel, thread_ts
        self.run_id = run_id if isinstance(run_id, str) and RUN_ID_RE.fullmatch(run_id) else uuid.uuid4().hex
        self.requester = requester
        self.via = via
        self.logger = logger
        self.enabled = valid_thread(channel, thread_ts)
        self.steps: set[str] = set()
        self.finished = False

    def _safely(self, action: Callable[[], Any]) -> Any:
        if not self.enabled:
            return None
        try:
            return action()
        except (OSError, ValueError, ThreadError) as exc:
            if self.logger is not None:
                self.logger.warning("Could not save the thread transcript: %s", exc)
            return None

    def _append(self, entry: dict[str, Any]) -> None:
        self._safely(lambda: self.store.append(self.channel, self.thread_ts, entry, team=self.team))

    def start(self) -> None:
        self._append({"type": "run", "run_id": self.run_id, "status": "running",
                      "requester": _person(self.requester), "via": self.via})

    def slack_messages(self, messages: list[dict[str, Any]], *, own_user: str | None,
                       name_of: Callable[[str], str | None]) -> None:
        """People's Slack messages not yet in the transcript, oldest first.

        The Tag's own messages are its answers, saved in full by ``answer``; they are
        copied from Slack only when the transcript starts mid-thread.
        """
        def record() -> None:
            new_file = not self.store.exists(self.channel, self.thread_ts)
            seen = set() if new_file else self.store.seen_slack_messages(self.channel, self.thread_ts)
            for message in sorted(messages, key=lambda m: float(m.get("ts") or 0)):
                ts = message.get("ts")
                if not isinstance(ts, str) or ts in seen or message.get("tag_app"):
                    continue
                user = message.get("user") if isinstance(message.get("user"), str) else None
                own = bool(own_user and user == own_user)
                if own and not new_file:
                    continue
                bot = own or bool(message.get("bot_id"))
                name = (message.get("username") if bot and isinstance(message.get("username"), str) else None) \
                    or (name_of(user) if user else None)
                text = slack_to_markdown(message.get("text"), name_of)
                if not text.strip() and not message.get("files"):
                    continue
                if message.get("files"):
                    names = [f.get("name") for f in message["files"] if isinstance(f, dict) and isinstance(f.get("name"), str)]
                    if names:
                        text = (text + "\n\n" if text.strip() else "") + " ".join(f"📎 {n}" for n in names[:10])
                self.store.message(self.channel, self.thread_ts, ts=ts, team=self.team, via="slack", text=text,
                                   author={"id": user, "name": name, "bot": bot, "self": own})
                seen.add(ts)
        self._safely(record)

    def step(self, event: dict[str, Any]) -> None:
        """A step's public label and status, from a normalized activity event; never its details."""
        kind = event.get("type")
        item = event.get("activity_id")
        if kind not in ("activity_start", "activity_complete") or not isinstance(item, str) or not item:
            return
        step = hashlib.sha256(item.encode("utf-8")).hexdigest()[:16]
        if kind == "activity_start":
            if step in self.steps:
                return
            self.steps.add(step)
            label = event.get("label")
            self._append({"type": "step", "run_id": self.run_id, "step": step, "status": "running",
                          "label": label if isinstance(label, str) and label in PUBLIC_LABELS else "Using a connected tool…"})
        elif step in self.steps:
            status = event.get("status")
            self._append({"type": "step", "run_id": self.run_id, "step": step,
                          "status": status if status in STEP_STATUSES - {"running"} else "unknown"})

    def answer(self, text: str, *, outcome: str, posted_to: str = "slack", ts: str | None = None) -> None:
        entry: dict[str, Any] = {"type": "answer", "run_id": self.run_id, "text": redact(text, MAX_ANSWER_CHARS),
                                 "outcome": outcome if outcome in ANSWER_OUTCOMES else "completed",
                                 "posted_to": posted_to if posted_to in ("slack", "app") else "slack"}
        if isinstance(ts, str) and THREAD_TS_RE.fullmatch(ts):
            entry["ts"] = ts
        self._append(entry)

    def files(self, artifacts: list[dict[str, Any]]) -> None:
        for record in artifact_records(artifacts):
            self._append({"type": "file", "run_id": self.run_id, "file": record})

    def memory(self, receipts: list[dict[str, Any]]) -> None:
        for receipt in receipts[:50]:
            if not isinstance(receipt, dict) or receipt.get("action") not in MEMORY_ACTIONS:
                continue
            key, scope, version = receipt.get("key"), receipt.get("scope"), receipt.get("version")
            if not isinstance(key, str) or not key:
                continue
            self._append({
                "type": "memory", "run_id": self.run_id, "action": receipt["action"],
                "scope": scope if isinstance(scope, str) and (scope == "global" or CHANNEL_RE.fullmatch(scope)) else None,
                "key": " ".join(redact(key, MAX_LABEL_CHARS).split()),
                "version": version if isinstance(version, str) and re.fullmatch(r"[0-9a-f]{6,64}", version) else None,
                "summary": " ".join(redact(receipt.get("text"), 200).split()) or None,
            })

    def finish(self, outcome: str) -> None:
        if self.finished:
            return
        self.finished = True
        self._append({"type": "run", "run_id": self.run_id,
                      "status": outcome if outcome in RUN_STATUSES - {"running"} else "failed"})
        # A run's approvals can't be answered once it ends.
        self._safely(lambda: self.store.expire_prompts(self.channel, self.thread_ts, run_id=self.run_id))


# --- Requests from the CLI ----------------------------------------------------

_REQUEST_ORDER = itertools.count()


def _request(home: Path, payload: dict[str, Any]) -> str:
    request_id = uuid.uuid4().hex
    # Names sort in the order requests were made, even within one clock tick.
    path = home / REQUESTS_DIR / f"{time.time_ns():020d}-{next(_REQUEST_ORDER):06d}-{request_id[:8]}.json"
    _atomic_write(path, {**payload, "request_id": request_id, "at": _now()})
    return request_id


def request_reply(home: Path, channel: str, thread_ts: str, text: str, *, post_to_slack: bool,
                  by: str | None) -> str:
    """Ask the running bridge to send the owner's reply to the Tag (and to Slack when asked)."""
    file_name(channel, thread_ts)
    clean = text.strip() if isinstance(text, str) else ""
    if not clean:
        raise ThreadError("Write a reply first.")
    if len(clean) > MAX_REPLY_CHARS:
        raise ThreadError(f"Keep a reply under {MAX_REPLY_CHARS} characters.")
    return _request(home, {"kind": "reply", "channel": channel, "thread_ts": thread_ts, "text": clean,
                           "post_to_slack": bool(post_to_slack), "by": by})


def request_answer(home: Path, channel: str, thread_ts: str, prompt_id: str, choice: str, *,
                   by: str | None) -> str:
    """Ask the running bridge to answer a waiting question as the owner, from Tag.app."""
    prompt = ThreadStore(home).pending_prompt(channel, thread_ts, prompt_id)
    if prompt is None:
        raise ThreadError("This question isn't waiting for an answer any more.")
    if choice not in {c["id"] for c in prompt["choices"]}:
        raise ThreadError(f"Choose one of: {', '.join(c['id'] for c in prompt['choices'])}.")
    return _request(home, {"kind": "answer", "channel": channel, "thread_ts": thread_ts,
                           "prompt_id": prompt_id, "choice": choice, "by": by})


def take_requests(home: Path, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Claim pending app requests, oldest first, dropping ones made while the Tag was stopped."""
    root = home / REQUESTS_DIR
    try:
        paths = sorted(root.glob("*.json"))
    except OSError:
        return []
    moment = now or datetime.now(timezone.utc)
    requests = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        path.unlink(missing_ok=True)
        if not isinstance(data, dict) or data.get("kind") not in ("reply", "answer") \
                or not valid_thread(data.get("channel"), data.get("thread_ts")):
            continue
        asked = _parse(data.get("at"))
        data["stale"] = asked is None or moment - asked > REQUEST_MAX_AGE
        requests.append(data)
    return requests


# --- What the CLI shows -------------------------------------------------------

def _summary(thread: dict[str, Any], tag_id: str, names: dict[str, str]) -> dict[str, Any]:
    row = {key: thread.get(key) for key in SUMMARY_FIELDS}
    channel = row["channel"]
    row["channel_name"] = None if channel.startswith("D") else names.get(channel)
    row["dm"] = channel.startswith("D")
    row["link"] = thread_link(tag_id, channel, row["thread_ts"])
    return row


def _legacy_threads(home: Path, channel: str | None) -> dict[tuple[str, str], dict[str, Any]]:
    """Threads that exist only in Activity, from before transcripts: a preview of each run."""
    store = ActivityStore(home / "state/activity")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    try:
        paths = list(store.root.glob("*.json"))
    except OSError:
        return {}
    for path in paths:
        record = store.get(path.stem)
        if record is None or not valid_thread(record["channel"], record["thread_ts"]):
            continue
        key = (record["channel"], record["thread_ts"])
        if channel is not None and key[0] != channel:
            continue
        grouped.setdefault(key, []).append(record)
    result = {}
    for key, records in grouped.items():
        records.sort(key=lambda r: r.get("finished_at") or r["started_at"])
        result[key] = _legacy_fold(key, records)
    return result


_LEGACY_STATUS = {"running": "running", "completed": "completed", "failed": "failed", "interrupted": "interrupted"}


def _legacy_fold(key: tuple[str, str], records: list[dict[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for record in records:
        at = record.get("finished_at") or record["started_at"]
        items.append({"type": "work", "run_id": record["run_id"], "status": _LEGACY_STATUS[record["outcome"]],
                      "started_at": record["started_at"], "finished_at": record.get("finished_at"),
                      "steps": [], "step_count": len(record["events"]) + record["omitted"], "via": "slack",
                      "preview_only": True})
        text = record.get("reply_summary") or record.get("reply_preview")
        if record["outcome"] == "completed" and isinstance(text, str) and text:
            items.append({"type": "answer", "at": at, "run_id": record["run_id"], "text": reply_preview(text),
                          "outcome": "completed", "posted_to": "slack", "preview_only": True,
                          "files": [{**a, "run_id": record["run_id"], "at": at} for a in artifact_records(record.get("artifacts"))],
                          "memory": []})
    last = records[-1]
    status = {"running": "working", "failed": "failed", "interrupted": "stopped"}.get(last["outcome"], "done")
    preview = next((i["text"] for i in reversed(items) if i["type"] == "answer"), None)
    return {
        "channel": key[0], "thread_ts": key[1], "team": records[0]["team"], "source": "slack",
        "title": preview, "status": status, "preview_only": True,
        "started_at": records[0]["started_at"], "updated_at": last.get("finished_at") or last["started_at"],
        "people": [], "message_count": sum(1 for i in items if i["type"] == "answer"),
        "file_count": sum(len(i.get("files", [])) for i in items), "memory_count": 0, "pending_prompts": 0,
        "last_answer_preview": preview, "items": items,
        "files": [f for i in items for f in i.get("files", [])], "memory": [], "prompts": [],
    }


def list_threads(home: Path, tag_id: str, *, channel: str | None = None, limit: int = DEFAULT_LIST_LIMIT,
                 names: dict[str, str] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Every thread on this computer, newest first; reading never changes a file."""
    if channel is not None and not CHANNEL_RE.fullmatch(channel):
        raise ThreadError("That isn't a Slack channel ID.")
    store = ThreadStore(home)
    found: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        paths = list(store.root.glob("*.jsonl"))
    except OSError:
        paths = []
    for path in paths:
        stem_channel, _, stem_ts = path.stem.partition("-")
        if not valid_thread(stem_channel, stem_ts) or (channel is not None and stem_channel != channel):
            continue
        if expired(path, home, now):
            continue
        thread = store.read(stem_channel, stem_ts, now=now)
        if thread["items"]:
            found[(stem_channel, stem_ts)] = thread
    for key, legacy in _legacy_threads(home, channel).items():
        if key not in found:
            found[key] = legacy
    rows = sorted(found.values(), key=lambda t: t.get("updated_at") or "", reverse=True)
    # "Needs you" is always shown, wherever it would fall in the limit.
    waiting = [t for t in rows if t["status"] == "needs_you"]
    others = [t for t in rows if t["status"] != "needs_you"]
    shown = (waiting + others)[:max(limit, len(waiting))]
    shown.sort(key=lambda t: t.get("updated_at") or "", reverse=True)
    return {"retention_days": retention_days(home), "has_more": len(rows) > len(shown),
            "threads": [_summary(t, tag_id, names or {}) for t in shown]}


def thread_detail(home: Path, tag_id: str, channel: str, thread_ts: str, *,
                  names: dict[str, str] | None = None, now: datetime | None = None) -> dict[str, Any] | None:
    """One conversation, or None when this computer doesn't have it."""
    if not valid_thread(channel, thread_ts):
        return None
    store = ThreadStore(home)
    thread = None
    if store.exists(channel, thread_ts) and not expired(store.path(channel, thread_ts), home, now):
        thread = store.read(channel, thread_ts, now=now)
        if not thread["items"]:
            thread = None
    legacy = _legacy_threads(home, channel).get((channel, thread_ts))
    if thread is None:
        thread = legacy
    elif legacy is not None:
        # Runs from before the transcript started keep their preview, in front.
        known = {item.get("run_id") for item in thread["items"]}
        earlier = [item for item in legacy["items"] if item.get("run_id") not in known
                   and (item.get("at") or item.get("started_at") or "") < (thread["started_at"] or "")]
        thread["items"] = earlier + thread["items"]
    if thread is None:
        return None
    return {**_summary(thread, tag_id, names or {}), "items": thread["items"], "files": thread["files"],
            "memory": thread["memory"], "prompts": thread["prompts"]}
