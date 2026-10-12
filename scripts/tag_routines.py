# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""Routines: work a Tag does on a schedule, without anyone prompting it.

A routine is a frozen prompt that runs at set times in one Slack channel. This
module owns everything that is not Slack or a backend: the definition schema,
schedule maths, the approval hash, limits, and the files that hold definitions
and run state. The bridge's scheduler and the CLI both use it, and so can any
other component that creates routines (for example a Tag that sets up another
Tag), through ``validate_routine`` and ``RoutineStore.add``.

Definitions live in ``.tag/routines.json``. Run state, history, and run-now
requests live under ``.tag/state/routines/``, written only by the bridge,
except requests, which the CLI writes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import DAILY, MONTHLY, WEEKLY, rrule, weekday as rrule_weekday
from dateutil.rrule import FR, MO, SA, SU, TH, TU, WE

try:
    from .tag_locks import acquire_all
    from .tag_paths import restrict_windows_acl
except ImportError:
    from tag_locks import acquire_all
    from tag_paths import restrict_windows_acl


SCHEMA_VERSION = 1
STATE_SCHEMA_VERSION = 1
DEFINITIONS = "routines.json"
STATE_DIR = "state/routines"
REQUESTS_DIR = "state/routines/requests"
LOCK = "state/routines.lock"

MIN_INTERVAL_SECONDS = 3600
MAX_ROUTINES_PER_TAG = 10
MAX_ROUTINES_PER_CHANNEL = 3
MAX_NAME_CHARS = 80
MAX_PROMPT_CHARS = 8_000
MAX_HISTORY = 100
# After a sleep or a stopped Tag, only the latest missed slot is worth running,
# and only if it is recent (the same look-back as Claude Desktop's tasks).
CATCH_UP_LOOKBACK = timedelta(days=7)
# A slot that starts later than this counts as a catch-up run.
LATE_AFTER = timedelta(minutes=10)
AUTO_PAUSE_FAILURES = 5
MAX_CONCURRENT_RUNS = 2
NO_REPLY = "NO_REPLY"

DEFAULT_LIMITS = {"max_turns": 40, "max_budget_usd": 0.50, "timeout_seconds": 1200, "max_tokens": 2_000_000}
LIMIT_BOUNDS = {
    "max_turns": (1, 200),
    "max_budget_usd": (0.01, 20.0),
    "timeout_seconds": (60, 3600),
    "max_tokens": (10_000, 20_000_000),
}
CATCH_UP = ("latest", "skip")
OUTCOMES = frozenset({
    "posted", "no_reply", "failed", "budget_exceeded", "timeout",
    "skipped", "missed", "interrupted", "blocked",
})
TRIGGERS = frozenset({"schedule", "catch-up", "run-now"})
ROUTINE_ID_RE = re.compile(r"^r-[0-9a-f]{10}$")
CHANNEL_RE = re.compile(r"^[CG][A-Z0-9]{8,}$")
MEMBER_RE = re.compile(r"^[UW][A-Z0-9]{2,20}$")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

DAYS = {"mon": MO, "tue": TU, "wed": WE, "thu": TH, "fri": FR, "sat": SA, "sun": SU}
DAY_ORDER = tuple(DAYS)
DAY_GROUPS = {
    "daily": DAY_ORDER,
    "weekdays": DAY_ORDER[:5],
    "weekends": DAY_ORDER[5:],
}


class RoutineError(ValueError):
    """An invalid routine or request; the message is safe to show."""


class RoutineSchemaError(RuntimeError):
    """Routines were saved by a newer Tag; refuse to overwrite them."""


# --- Schedules -------------------------------------------------------------

def parse_schedule(text: str) -> dict[str, Any]:
    """Parse the small schedule form into a stored, structured schedule.

    ``every 2h`` · ``every 90m`` · ``every 1d`` · ``daily 09:00`` ·
    ``weekdays 09:00,13:30`` · ``weekends 10:00`` · ``mon,wed,fri 17:30`` ·
    ``monthly 1 09:00`` · ``monthly last 17:00``. Add ``exact`` to keep a time
    on the hour instead of moving it a few minutes off.
    """
    if not isinstance(text, str):
        raise RoutineError("Give a schedule, for example: weekdays 09:00")
    words = text.strip().lower().split()
    exact = bool(words) and words[-1] == "exact"
    if exact:
        words = words[:-1]
    if not words:
        raise RoutineError("Give a schedule, for example: weekdays 09:00")
    canonical = " ".join(words + (["exact"] if exact else []))
    head = words[0]
    if head == "every":
        if len(words) != 2:
            raise RoutineError("Write intervals like: every 2h, every 90m or every 1d")
        match = re.fullmatch(r"(\d{1,4})([mhd])", words[1])
        if not match:
            raise RoutineError("Write intervals like: every 2h, every 90m or every 1d")
        seconds = int(match.group(1)) * {"m": 60, "h": 3600, "d": 86400}[match.group(2)]
        if seconds < MIN_INTERVAL_SECONDS:
            raise RoutineError("Routines run at most once an hour")
        if seconds > 31 * 86400:
            raise RoutineError("Intervals can be at most 31 days; use monthly for longer")
        return {"text": canonical, "kind": "interval", "every_seconds": seconds, "exact": exact}
    if head == "monthly":
        if len(words) != 3:
            raise RoutineError("Write monthly schedules like: monthly 1 09:00 or monthly last 17:00")
        if words[1] == "last":
            day = -1
        elif words[1].isdigit() and 1 <= int(words[1]) <= 28:
            day = int(words[1])
        else:
            raise RoutineError("Use a day from 1 to 28, or last, so every month has it")
        return {"text": canonical, "kind": "monthly", "day": day, "times": _times(words[2]), "exact": exact}
    if len(words) != 2:
        raise RoutineError("Write a day and time, for example: weekdays 09:00 or mon,fri 17:30")
    if head in DAY_GROUPS:
        days = list(DAY_GROUPS[head])
    else:
        days = []
        for part in head.split(","):
            if part not in DAYS:
                raise RoutineError(f"Unknown day {part!r}. Use mon, tue, wed, thu, fri, sat or sun")
            if part not in days:
                days.append(part)
        days.sort(key=DAY_ORDER.index)
    return {"text": canonical, "kind": "weekly", "days": days, "times": _times(words[1]), "exact": exact}


def _times(raw: str) -> list[str]:
    times: list[str] = []
    for part in raw.split(","):
        match = TIME_RE.fullmatch(part)
        if not match:
            raise RoutineError(f"Write times as HH:MM in 24-hour time, not {part!r}")
        value = f"{int(match.group(1)):02d}:{match.group(2)}"
        if value not in times:
            times.append(value)
    if len(times) > 6:
        raise RoutineError("Use at most six times a day")
    times.sort()
    # Several times a day must still be at least an hour apart.
    minutes = [int(t[:2]) * 60 + int(t[3:]) for t in times]
    gaps = [b - a for a, b in zip(minutes, minutes[1:])] + [minutes[0] + 1440 - minutes[-1]]
    if min(gaps) < MIN_INTERVAL_SECONDS // 60:
        raise RoutineError("Routines run at most once an hour")
    return times


def describe_schedule(schedule: dict[str, Any]) -> str:
    return str(schedule.get("text", ""))


def stagger_minutes(routine_id: str, schedule: dict[str, Any]) -> int:
    """A stable 1–5 minute offset for times on the hour, so routines don't all start at :00."""
    if schedule.get("exact"):
        return 0
    return 1 + int(hashlib.sha256(routine_id.encode()).hexdigest(), 16) % 5


def _resolve_local(naive: datetime, zone: ZoneInfo) -> datetime:
    """The UTC instant for a local wall time.

    A time skipped by a daylight-saving change runs when the gap ends (02:30
    becomes 03:00), as GitHub Actions does. A repeated time runs once, at its
    first occurrence. Libraries give a time inside the gap the old offset,
    which would run an hour late, so the gap is found explicitly here.
    """
    first = naive.replace(tzinfo=zone, fold=0)
    utc = first.astimezone(timezone.utc)
    if utc.astimezone(zone).replace(tzinfo=None) == naive:
        return utc
    # In a gap: fold=1 is before the transition and fold=0 after it, in UTC.
    low = naive.replace(tzinfo=zone, fold=1).astimezone(timezone.utc)
    high = utc
    if low > high:
        low, high = high, low
    while (high - low) > timedelta(seconds=1):
        middle = low + (high - low) / 2
        if middle.astimezone(zone).utcoffset() == low.astimezone(zone).utcoffset():
            low = middle
        else:
            high = middle
    return high.replace(microsecond=0)


def _calendar_rule(schedule: dict[str, Any], start: datetime, offset: int) -> rrule:
    hours, minutes = [], []
    for value in schedule["times"]:
        hour, minute = int(value[:2]), int(value[3:])
        if minute == 0:
            minute = offset
        hours.append(hour)
        minutes.append(minute)
    common: dict[str, Any] = {"dtstart": start, "byhour": sorted(set(hours)),
                              "byminute": sorted(set(minutes)), "bysecond": 0}
    if schedule["kind"] == "monthly":
        return rrule(MONTHLY, bymonthday=schedule["day"], **common)
    days: list[rrule_weekday] = [DAYS[day] for day in schedule["days"]]
    return rrule(WEEKLY if len(days) < 7 else DAILY, byweekday=days, **common)


def _calendar_times(schedule: dict[str, Any], zone: ZoneInfo, after: datetime, offset: int) -> Iterator[datetime]:
    """UTC run times after ``after`` for weekly and monthly schedules, in order."""
    local_start = (after.astimezone(zone) - timedelta(days=2)).replace(tzinfo=None, second=0, microsecond=0)
    # rrule crosses byhour with byminute, so each time of day gets its own rule.
    rules: list[rrule] = []
    for value in schedule["times"]:
        rules.append(_calendar_rule({**schedule, "times": [value]}, local_start, offset))
    iterators = [iter(rule) for rule in rules]
    heads: list[datetime | None] = [next(it, None) for it in iterators]
    last: datetime | None = None
    while any(head is not None for head in heads):
        index = min((i for i, head in enumerate(heads) if head is not None), key=lambda i: heads[i])
        naive = heads[index]
        heads[index] = next(iterators[index], None)
        assert naive is not None
        moment = _resolve_local(naive, zone)
        if moment > after and moment != last:
            last = moment
            yield moment


def next_run(routine: dict[str, Any], after: datetime) -> datetime:
    """The first scheduled time strictly after ``after`` (an aware datetime), in UTC."""
    after = after.astimezone(timezone.utc)
    schedule = routine["schedule"]
    if schedule["kind"] == "interval":
        anchor = datetime.fromisoformat(routine["anchor"]).astimezone(timezone.utc)
        step = timedelta(seconds=schedule["every_seconds"])
        if after < anchor:
            return anchor
        count = int((after - anchor) / step) + 1
        return anchor + step * count
    zone = ZoneInfo(routine["timezone"])
    offset = stagger_minutes(routine["id"], schedule)
    return next(_calendar_times(schedule, zone, after, offset))


def upcoming(routine: dict[str, Any], after: datetime, count: int = 5) -> list[datetime]:
    times: list[datetime] = []
    moment = after
    for _ in range(count):
        moment = next_run(routine, moment)
        times.append(moment)
    return times


def interval_anchor(routine_id: str, schedule: dict[str, Any], now: datetime) -> str:
    """Intervals count from the next whole minute after creation, moved off :00."""
    start = (now.astimezone(timezone.utc) + timedelta(minutes=1)).replace(second=0, microsecond=0)
    if start.minute == 0:
        start += timedelta(minutes=stagger_minutes(routine_id, schedule))
    return start.isoformat()


# --- Definitions -----------------------------------------------------------

def new_routine_id() -> str:
    return "r-" + secrets.token_hex(5)


def _limits(raw: Any) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - set(DEFAULT_LIMITS):
        raise RoutineError("Limits can set only " + ", ".join(DEFAULT_LIMITS))
    limits = dict(DEFAULT_LIMITS)
    for key, value in raw.items():
        low, high = LIMIT_BOUNDS[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise RoutineError(f"{key} must be between {low} and {high}")
        limits[key] = round(float(value), 2) if key == "max_budget_usd" else int(value)
    return limits


def validate_routine(raw: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Check a routine from any source and return its normalized definition.

    Accepts a schedule as text or as a stored schedule. Fills in an id, interval
    anchor, default limits, and catch-up policy. Approval is separate: see
    ``approve``. Raises RoutineError with a message that is safe to show.
    """
    if not isinstance(raw, dict):
        raise RoutineError("A routine must be a JSON object")
    known = {"id", "name", "schedule", "timezone", "channel", "prompt", "owner", "enabled",
             "limits", "catch_up", "anchor", "created", "approval", "paused_reason"}
    if unknown := set(raw) - known:
        raise RoutineError("Unknown routine fields: " + ", ".join(sorted(unknown)))
    now = now or datetime.now(timezone.utc)
    routine_id = raw.get("id") or new_routine_id()
    if not isinstance(routine_id, str) or not ROUTINE_ID_RE.fullmatch(routine_id):
        raise RoutineError("Invalid routine id")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_NAME_CHARS or "\n" in name:
        raise RoutineError(f"Give the routine a one-line name of at most {MAX_NAME_CHARS} characters")
    schedule_raw = raw.get("schedule")
    schedule = parse_schedule(schedule_raw["text"] if isinstance(schedule_raw, dict) else schedule_raw)
    zone_name = raw.get("timezone")
    if not isinstance(zone_name, str) or not zone_name:
        raise RoutineError("Give a timezone, for example Europe/London")
    try:
        ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise RoutineError(f"Unknown timezone {zone_name!r}; use an IANA name such as Europe/London") from None
    channel = raw.get("channel")
    if not isinstance(channel, str) or not CHANNEL_RE.fullmatch(channel):
        raise RoutineError("Give a Slack channel ID such as C0123ABCD; routines don't post to direct messages")
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
        raise RoutineError(f"Give a prompt of at most {MAX_PROMPT_CHARS} characters")
    owner = raw.get("owner")
    if not isinstance(owner, str) or not MEMBER_RE.fullmatch(owner):
        raise RoutineError("Give the owner's Slack member ID, such as U0123ABCD")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise RoutineError("enabled must be true or false")
    catch_up = raw.get("catch_up", "latest")
    if catch_up not in CATCH_UP:
        raise RoutineError("catch_up must be latest or skip")
    routine: dict[str, Any] = {
        "id": routine_id,
        "name": name.strip(),
        "schedule": schedule,
        "timezone": zone_name,
        "channel": channel,
        "prompt": prompt.strip(),
        "owner": owner,
        "enabled": enabled,
        "limits": _limits(raw.get("limits")),
        "catch_up": catch_up,
    }
    if schedule["kind"] == "interval":
        anchor = raw.get("anchor")
        if isinstance(anchor, str):
            try:
                datetime.fromisoformat(anchor)
            except ValueError:
                anchor = None
        routine["anchor"] = anchor if isinstance(anchor, str) else interval_anchor(routine_id, schedule, now)
    created = raw.get("created")
    routine["created"] = created if _created_valid(created) else {
        "at": now.isoformat(timespec="seconds"), "by": "unknown"}
    if isinstance(raw.get("approval"), dict):
        routine["approval"] = raw["approval"]
    if isinstance(raw.get("paused_reason"), str) and not enabled:
        routine["paused_reason"] = raw["paused_reason"][:200]
    return routine


def _created_valid(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("at"), str) and isinstance(value.get("by"), str)


FROZEN_FIELDS = ("name", "schedule", "timezone", "channel", "prompt", "owner", "limits", "catch_up")


def approval_hash(routine: dict[str, Any]) -> str:
    """Hash of everything a person approved. Pausing and resuming don't change it."""
    frozen = {key: routine.get(key) for key in FROZEN_FIELDS}
    canonical = json.dumps(frozen, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def prompt_hash(routine: dict[str, Any]) -> str:
    return hashlib.sha256(routine["prompt"].encode("utf-8")).hexdigest()


def approve(routine: dict[str, Any], *, by: str, via: str, now: datetime | None = None) -> dict[str, Any]:
    """Record who approved this exact definition.

    ``by`` is a Slack member ID, or ``local-operator`` for someone at this
    computer using the CLI or Tag.app. This makes edits evident, not
    impossible: the coding agent runs with the user's permissions (ADR 0001).
    """
    now = now or datetime.now(timezone.utc)
    routine["approval"] = {"by": by, "via": via, "at": now.isoformat(timespec="seconds"),
                           "hash": approval_hash(routine)}
    return routine


def approved(routine: dict[str, Any]) -> bool:
    approval = routine.get("approval")
    return isinstance(approval, dict) and approval.get("hash") == approval_hash(routine)


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class RoutineStore:
    """The Tag's routine definitions, in ``.tag/routines.json``."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.path = home / DEFINITIONS

    def _lock(self):
        held, _ = acquire_all([self.home / LOCK], wait=10)
        return held

    def load(self) -> list[dict[str, Any]]:
        """Valid routines. A missing file means none; a newer schema is refused."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError, UnicodeError) as exc:
            raise RoutineError(f"Can't read {self.path.name}: {exc}") from None
        if not isinstance(data, dict) or not isinstance(data.get("schema_version"), int):
            raise RoutineError(f"{self.path.name} isn't a routines file")
        if data["schema_version"] > SCHEMA_VERSION:
            raise RoutineSchemaError("These routines were saved by a newer Tag. Upgrade Tag to change them.")
        data = migrate(data)
        routines = []
        for raw in data.get("routines") or []:
            try:
                routines.append(validate_routine(raw))
            except RoutineError:
                continue  # One damaged entry must not hide the others.
        return routines

    def invalid_count(self) -> int:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            raw = data.get("routines") or []
        except (OSError, ValueError, AttributeError):
            return 0
        return max(len(raw) - len(self.load()), 0)

    def save(self, routines: list[dict[str, Any]]) -> None:
        _atomic_write(self.path, {"schema_version": SCHEMA_VERSION, "routines": routines})
        restrict_windows_acl(self.home)

    def get(self, reference: str) -> dict[str, Any]:
        return find(self.load(), reference)

    def add(self, raw: dict[str, Any], *, by: str, via: str, now: datetime | None = None) -> dict[str, Any]:
        """Validate, approve, check limits, and save one new routine."""
        locks = self._lock()
        try:
            routines = self.load()
            routine = validate_routine({key: value for key, value in raw.items() if key != "approval"}, now=now)
            routine["created"] = {"at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "by": via}
            check_limits(routines, routine)
            approve(routine, by=by, via=via, now=now)
            routines.append(routine)
            self.save(routines)
            return routine
        finally:
            for lock in reversed(locks):
                lock.release()

    def update(self, reference: str, changes: dict[str, Any], *, by: str, via: str,
               now: datetime | None = None) -> dict[str, Any]:
        """Edit a routine. Changing anything approved records a new approval by ``by``."""
        locks = self._lock()
        try:
            routines = self.load()
            current = find(routines, reference)
            merged = {**current, **changes}
            if "schedule" in changes and isinstance(changes["schedule"], str):
                merged.pop("anchor", None)
            routine = validate_routine(merged, now=now)
            check_limits([r for r in routines if r["id"] != routine["id"]], routine)
            if approval_hash(routine) != approval_hash(current) or not approved(current):
                approve(routine, by=by, via=via, now=now)
            routines = [routine if r["id"] == routine["id"] else r for r in routines]
            self.save(routines)
            return routine
        finally:
            for lock in reversed(locks):
                lock.release()

    def set_enabled(self, reference: str, enabled: bool, *, reason: str | None = None) -> dict[str, Any]:
        locks = self._lock()
        try:
            routines = self.load()
            routine = find(routines, reference)
            if enabled:
                check_limits([r for r in routines if r["id"] != routine["id"]], {**routine, "enabled": True})
            routine["enabled"] = enabled
            routine.pop("paused_reason", None)
            if not enabled and reason:
                routine["paused_reason"] = reason[:200]
            self.save(routines)
            return routine
        finally:
            for lock in reversed(locks):
                lock.release()

    def remove(self, reference: str) -> dict[str, Any]:
        locks = self._lock()
        try:
            routines = self.load()
            routine = find(routines, reference)
            self.save([r for r in routines if r["id"] != routine["id"]])
            return routine
        finally:
            for lock in reversed(locks):
                lock.release()


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Bring an older routines file up to SCHEMA_VERSION. Version 1 is the first."""
    if data.get("schema_version") == SCHEMA_VERSION:
        return data
    raise RoutineError(f"Unsupported routines schema {data.get('schema_version')!r}")


def find(routines: list[dict[str, Any]], reference: str) -> dict[str, Any]:
    """A routine by id, or by a name that matches exactly one routine (ignoring case)."""
    reference = (reference or "").strip()
    for routine in routines:
        if routine["id"] == reference:
            return routine
    matches = [r for r in routines if r["name"].casefold() == reference.casefold()]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise RoutineError(f"More than one routine is named {reference!r}; use its id")
    raise RoutineError(f"No routine named {reference!r}. See tag routine list")


def check_limits(others: list[dict[str, Any]], routine: dict[str, Any]) -> None:
    if any(r["name"].casefold() == routine["name"].casefold() for r in others if r["id"] != routine["id"]):
        raise RoutineError(f"A routine named {routine['name']!r} already exists")
    if not routine.get("enabled", True):
        return
    active = [r for r in others if r.get("enabled") and r["id"] != routine["id"]]
    if len(active) >= MAX_ROUTINES_PER_TAG:
        raise RoutineError(f"A Tag can have at most {MAX_ROUTINES_PER_TAG} active routines. Pause or remove one first")
    if sum(1 for r in active if r["channel"] == routine["channel"]) >= MAX_ROUTINES_PER_CHANNEL:
        raise RoutineError(f"A channel can have at most {MAX_ROUTINES_PER_CHANNEL} active routines")


# --- Run state -------------------------------------------------------------

def empty_state() -> dict[str, Any]:
    return {"schema_version": STATE_SCHEMA_VERSION, "next_due": None, "schedule_key": None, "running": None,
            "consecutive_failures": 0, "last_success": None, "history": []}


_STATE_LOCKS: dict[Path, threading.RLock] = {}
_STATE_LOCKS_GUARD = threading.Lock()


def _state_lock(path: Path) -> threading.RLock:
    with _STATE_LOCKS_GUARD:
        return _STATE_LOCKS.setdefault(path, threading.RLock())


class RoutineStateStore:
    """Per-routine run state and history, written only by the bridge.

    The scheduler's tick and finishing runs change the same file from different
    threads, so every change goes through ``update``, which holds a per-routine
    lock across the read, the change and the write. Writes are atomic, so the
    CLI can read at any time.
    """

    def __init__(self, home: Path) -> None:
        self.home = home
        self.root = home / STATE_DIR

    def _path(self, routine_id: str) -> Path:
        if not ROUTINE_ID_RE.fullmatch(routine_id):
            raise RoutineError("Invalid routine id")
        return self.root / f"{routine_id}.json"

    def load(self, routine_id: str) -> dict[str, Any]:
        try:
            data = json.loads(self._path(routine_id).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return empty_state()
        if not isinstance(data, dict) or data.get("schema_version") != STATE_SCHEMA_VERSION:
            return empty_state()
        state = empty_state()
        state.update({key: data[key] for key in state if key in data})
        if not isinstance(state["history"], list):
            state["history"] = []
        if not isinstance(state["consecutive_failures"], int):
            state["consecutive_failures"] = 0
        return state

    def save(self, routine_id: str, state: dict[str, Any]) -> None:
        with _state_lock(self._path(routine_id)):
            state["history"] = state["history"][-MAX_HISTORY:]
            _atomic_write(self._path(routine_id), state)

    def update(self, routine_id: str, change: Any) -> dict[str, Any]:
        """Read, change and write one routine's state as one step; returns the new state.

        ``change`` edits the state in place and may return False to skip writing.
        """
        path = self._path(routine_id)
        with _state_lock(path):
            state = self.load(routine_id)
            if change(state) is not False:
                self.save(routine_id, state)
            return state

    def remove(self, routine_id: str) -> None:
        path = self._path(routine_id)
        with _state_lock(path):
            path.unlink(missing_ok=True)

    def record(self, routine_id: str, run: dict[str, Any]) -> dict[str, Any]:
        """Add one run record and update failure counting. Returns the new state."""
        return self.update(routine_id, lambda state: add_run(state, run))


def add_run(state: dict[str, Any], run: dict[str, Any]) -> None:
    """Append a run to history and keep the failure count and last success current."""
    state["history"].append(run)
    outcome = run.get("outcome")
    if outcome in {"posted", "no_reply"}:
        state["consecutive_failures"] = 0
        state["last_success"] = run.get("ended") or run.get("started")
    elif outcome in {"failed", "budget_exceeded", "timeout"}:
        state["consecutive_failures"] += 1


def run_record(routine: dict[str, Any], *, trigger: str, slot: datetime | None, outcome: str,
               started: datetime | None = None, ended: datetime | None = None,
               diagnostic: str = "", **extra: Any) -> dict[str, Any]:
    """One entry in a routine's run history."""
    assert outcome in OUTCOMES, outcome
    record: dict[str, Any] = {
        "run_id": uuid.uuid4().hex,
        "trigger": trigger,
        "scheduled_for": slot.isoformat(timespec="seconds") if slot else None,
        "started": started.isoformat(timespec="seconds") if started else None,
        "ended": ended.isoformat(timespec="seconds") if ended else None,
        "outcome": outcome,
        "prompt_sha256": prompt_hash(routine),
        "approval_hash": (routine.get("approval") or {}).get("hash"),
    }
    if diagnostic:
        record["diagnostic"] = diagnostic[:300]
    for key, value in extra.items():
        if value is not None:
            record[key] = value
    return record


# --- Run-now requests -------------------------------------------------------

def request_run(home: Path, routine_id: str, *, by: str = "local-operator") -> Path:
    """Ask the running bridge to run a routine now (the CLI's run-now)."""
    if not ROUTINE_ID_RE.fullmatch(routine_id):
        raise RoutineError("Invalid routine id")
    path = home / REQUESTS_DIR / f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.json"
    _atomic_write(path, {"routine_id": routine_id, "by": by,
                         "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return path


def take_requests(home: Path) -> list[dict[str, Any]]:
    """Claim pending run-now requests, oldest first."""
    root = home / REQUESTS_DIR
    try:
        paths = sorted(root.glob("*.json"))
    except OSError:
        return []
    requests = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        path.unlink(missing_ok=True)
        if isinstance(data, dict) and isinstance(data.get("routine_id"), str):
            requests.append(data)
    return requests


# --- Prompt ------------------------------------------------------------------

def run_prompt(routine: dict[str, Any], *, trigger: str, slot: datetime | None,
               last_success: str | None) -> str:
    """The routine's frozen prompt, preceded by a short, fixed preamble."""
    lines = [
        f'This is a scheduled run of the routine "{routine["name"]}". Nobody is waiting in a '
        "conversation: work on your own, and write your final answer as the message to post in "
        "the channel. Each run starts fresh and remembers nothing from earlier runs.",
    ]
    if trigger == "catch-up" and slot is not None:
        lines.append(f"This run is late: it was due at {slot.isoformat(timespec='minutes')} (UTC), "
                     "while this computer was asleep or Tag was stopped.")
    if trigger == "run-now":
        lines.append("Someone started this run by hand.")
    lines.append(f"The last successful run finished at {last_success} (UTC)." if last_success
                 else "This is the routine's first successful run.")
    lines.append(f"If there is nothing new or worth posting, reply with exactly {NO_REPLY} and nothing else.")
    lines.append("Actions that need approval still ask a person in Slack; this prompt is not approval.")
    return "\n".join(lines) + "\n\nRoutine instructions:\n" + routine["prompt"]


def is_no_reply(answer: str) -> bool:
    return answer.strip().strip("`*_ ").upper() == NO_REPLY
