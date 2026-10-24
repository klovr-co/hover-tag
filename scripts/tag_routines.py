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

Run times come from cronsim, which follows Debian cron's daylight-saving
rules. The scheduling rules themselves follow Temporal schedules, Kubernetes
CronJobs, Hermes Agent and OpenClaw, and the tests port their scenarios.
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

from cronsim import CronSim

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
HEALTH = "state/routines/scheduler.json"

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
# A run-now request older than this was made while Tag was stopped; it doesn't run later.
RUN_NOW_MAX_AGE = timedelta(minutes=10)
# A rate-limited run is tried once more after this.
RETRY_AFTER = timedelta(minutes=1)
# A run still going this long after its time limit is treated as stuck.
STUCK_AFTER_LIMIT = timedelta(minutes=10)
# A time moved by a daylight-saving gap is dropped when another of the routine's
# times follows within this, so a clock change never posts twice in a row.
DST_DUPLICATE_WINDOW = timedelta(minutes=30)
NO_REPLY = "NO_REPLY"
# Accepted alone or on their own first or last line, as Hermes Agent does for cron runs.
SILENCE_MARKERS = frozenset({"NO_REPLY", "NO REPLY", "[NO_REPLY]", "[NO REPLY]", "[SILENT]", "SILENT"})

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
    "skipped", "missed", "interrupted", "blocked", "cancelled",
})
# Outcomes that count towards pausing a routine that keeps failing. A run cut off
# by a stopped Tag counts too, so a routine that crashes Tag every time is paused.
FAILURES = frozenset({"failed", "budget_exceeded", "timeout", "interrupted"})
TRIGGERS = frozenset({"schedule", "catch-up", "run-now", "retry"})
ROUTINE_ID_RE = re.compile(r"^r-[0-9a-f]{10}$")
CHANNEL_RE = re.compile(r"^[CG][A-Z0-9]{8,}$")
MEMBER_RE = re.compile(r"^[UW][A-Z0-9]{2,20}$")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

DAY_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAYS = frozenset(DAY_ORDER)
DAY_GROUPS = {
    "daily": DAY_ORDER,
    "weekdays": DAY_ORDER[:5],
    "weekends": DAY_ORDER[5:],
}


class RoutineError(ValueError):
    """An invalid routine or request; the message is safe to show."""


class RoutineSchemaError(RuntimeError):
    """Routines were saved by a newer Tag; refuse to overwrite them."""


class RoutineStateDamaged(RoutineError):
    """A routine's run state can't be read."""


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


def _cron_expression(schedule: dict[str, Any], value: str, offset: int) -> str:
    hour, minute = int(value[:2]), int(value[3:])
    if minute == 0:
        minute = offset
    if schedule["kind"] == "monthly":
        return f"{minute} {hour} {'L' if schedule['day'] == -1 else schedule['day']} * *"
    days = "*" if len(schedule["days"]) == 7 else ",".join(schedule["days"])
    return f"{minute} {hour} * * {days}"


def _displaced(local: datetime, value: str, expressions: dict[str, str], offset: int) -> bool:
    """Whether a time moved by a daylight-saving gap lands just before another of the routine's times."""
    minute = int(value[3:]) or offset
    if (local.hour, local.minute) == (int(value[:2]), minute):
        return False
    moment = local.astimezone(timezone.utc)
    for other, expression in expressions.items():
        if other == value:
            continue
        following = next(CronSim(expression, local - timedelta(seconds=1)), None)
        if following is not None and following.astimezone(timezone.utc) - moment < DST_DUPLICATE_WINDOW:
            return True
    return False


def _calendar_times(schedule: dict[str, Any], zone: ZoneInfo, after: datetime, offset: int) -> Iterator[datetime]:
    """UTC run times after ``after`` for weekly and monthly schedules, in order.

    cronsim works in local wall time and follows Debian cron: a time skipped by a
    daylight-saving change runs when the gap ends (02:30 becomes 03:00), as GitHub
    Actions does, and a repeated time runs once, at its first occurrence. A moved
    time that lands within half an hour of another of the routine's times is
    dropped, so the change doesn't post twice.
    """
    expressions = {value: _cron_expression(schedule, value, offset) for value in schedule["times"]}
    iterators = {value: CronSim(expression, after.astimezone(zone)) for value, expression in expressions.items()}
    heads = {value: head for value, iterator in iterators.items() if (head := next(iterator, None)) is not None}
    last: datetime | None = None
    while heads:
        # Compare instants: aware datetimes in one zone compare by wall time.
        value = min(heads, key=lambda key: heads[key].astimezone(timezone.utc))
        local = heads[value]
        following = next(iterators[value], None)
        if following is None:
            del heads[value]
        else:
            heads[value] = following
        moment = local.astimezone(timezone.utc)
        if moment <= after or moment == last or _displaced(local, value, expressions, offset):
            continue
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
    moment = next(_calendar_times(schedule, zone, after, offset), None)
    if moment is None:
        raise RoutineError("This schedule has no next run")
    return moment


def upcoming(routine: dict[str, Any], after: datetime, count: int = 5) -> list[datetime]:
    times: list[datetime] = []
    moment = after
    for _ in range(count):
        moment = next_run(routine, moment)
        times.append(moment)
    return times


def interval_anchor(routine_id: str, schedule: dict[str, Any], now: datetime) -> str:
    """Intervals count from a minute or few after creation, different per routine.

    Routines saved together (by Chief of Staff, say) then don't all come due at once.
    """
    start = (now.astimezone(timezone.utc) + timedelta(minutes=1)).replace(second=0, microsecond=0)
    if not schedule.get("exact"):
        start += timedelta(minutes=stagger_minutes(routine_id, schedule) - 1)
    if start.minute == 0:
        start += timedelta(minutes=1)
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
    schedule = parse_schedule(schedule_raw.get("text") if isinstance(schedule_raw, dict) else schedule_raw)
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
    created = raw.get("created")
    if schedule["kind"] == "interval":
        routine["anchor"] = _anchor(raw, routine_id, schedule, now)
    routine["created"] = created if _created_valid(created) else {
        "at": now.isoformat(timespec="seconds"), "by": "unknown"}
    if isinstance(raw.get("approval"), dict):
        routine["approval"] = raw["approval"]
    if isinstance(raw.get("paused_reason"), str) and not enabled:
        routine["paused_reason"] = raw["paused_reason"][:200]
    return routine


def _anchor(raw: dict[str, Any], routine_id: str, schedule: dict[str, Any], now: datetime) -> str:
    """The saved interval anchor. A time without a zone is UTC, as everywhere else in Tag.

    A new routine counts from now. A saved one whose anchor was lost counts from when it
    was created, or a fixed date, so its timing doesn't move each time it is read.
    """
    anchor = raw.get("anchor")
    if isinstance(anchor, str):
        try:
            parsed = datetime.fromisoformat(anchor)
        except ValueError:
            parsed = None
        if parsed is not None:
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).isoformat()
    if raw.get("id") is None:
        return interval_anchor(routine_id, schedule, now)
    created = raw.get("created")
    try:
        base = datetime.fromisoformat(created["at"]) if _created_valid(created) else ANCHOR_FALLBACK
    except ValueError:
        base = ANCHOR_FALLBACK
    return interval_anchor(routine_id, schedule, base if base.tzinfo else base.replace(tzinfo=timezone.utc))


ANCHOR_FALLBACK = datetime(2026, 1, 1, tzinfo=timezone.utc)


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


def _atomic_write(path: Path, payload: Any, *, durable: bool = True) -> None:
    """Replace a file in one step. ``durable`` also flushes it to disk first, so a power
    cut leaves the old file or the new one, never an empty one."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            if durable:
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(temporary, path)
        if durable:
            _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return  # Windows can't open a directory for this; NTFS orders the rename itself.
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


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
            if (routine := _valid_or_none(raw)) is not None:
                routines.append(routine)
        return routines

    def _damaged(self) -> list[Any]:
        """Entries that don't validate, kept as they are so saving the others never deletes them."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return []
        raw = data.get("routines") if isinstance(data, dict) else None
        return [entry for entry in raw or [] if _valid_or_none(entry) is None] if isinstance(raw, list) else []

    def invalid_count(self) -> int:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            raw = data.get("routines") or []
        except (OSError, ValueError, AttributeError):
            return 0
        return max(len(raw) - len(self.load()), 0)

    def save(self, routines: list[dict[str, Any]]) -> None:
        ids = {routine["id"] for routine in routines}
        kept = [entry for entry in self._damaged() if not (isinstance(entry, dict) and entry.get("id") in ids)]
        _atomic_write(self.path, {"schema_version": SCHEMA_VERSION, "routines": routines + kept})
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
            if isinstance(changes.get("schedule"), str):
                schedule = parse_schedule(changes["schedule"])
                # Saving the same schedule again keeps an interval's timing; a new one starts from now.
                if schedule["text"] != current["schedule"]["text"] and schedule["kind"] == "interval":
                    merged["anchor"] = interval_anchor(current["id"], schedule, now or datetime.now(timezone.utc))
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
            if enabled and not approved(routine):
                raise RoutineError("This routine changed after it was approved. Edit and save it to approve it, "
                                   "then resume it.")
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


def _valid_or_none(raw: Any) -> dict[str, Any] | None:
    try:
        return validate_routine(raw)
    except (RoutineError, KeyError, TypeError, AttributeError, ValueError):
        return None  # One damaged entry must not hide the others.


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
    # ``pending`` is a slot chosen to run but not started yet: it is saved in the same write
    # that moves ``next_due``, so a crash in between can't lose the slot (as Hermes Agent does).
    return {"schema_version": STATE_SCHEMA_VERSION, "next_due": None, "schedule_key": None, "running": None,
            "waiting": None, "pending": None, "retry": None, "consecutive_failures": 0, "last_success": None,
            "history": []}


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
        """One routine's state. Raises RoutineStateDamaged or RoutineSchemaError rather than guess."""
        path = self._path(routine_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return empty_state()
        except (OSError, ValueError, UnicodeError) as exc:
            raise RoutineStateDamaged(f"Can't read {path.name}: {exc}") from None
        if not isinstance(data, dict) or not isinstance(data.get("schema_version"), int):
            raise RoutineStateDamaged(f"{path.name} isn't a routine state file")
        if data["schema_version"] > STATE_SCHEMA_VERSION:
            raise RoutineSchemaError("This routine's history was saved by a newer Tag. Upgrade Tag to run it.")
        if data["schema_version"] != STATE_SCHEMA_VERSION:
            raise RoutineStateDamaged(f"{path.name} has an unknown schema")
        state = empty_state()
        state.update({key: data[key] for key in state if key in data})
        history = state["history"] if isinstance(state["history"], list) else []
        state["history"] = [entry for entry in history
                            if isinstance(entry, dict) and isinstance(entry.get("outcome"), str)]
        if not isinstance(state["consecutive_failures"], int):
            state["consecutive_failures"] = 0
        return state

    def peek(self, routine_id: str) -> dict[str, Any]:
        """State for display: empty when it can't be read, never an error."""
        try:
            return self.load(routine_id)
        except (RoutineError, RoutineSchemaError):
            return empty_state()

    def _set_aside(self, routine_id: str) -> dict[str, Any]:
        """Keep a damaged state file for inspection and start again with a note in history."""
        path = self._path(routine_id)
        now = datetime.now(timezone.utc)
        kept = path.with_name(f"{routine_id}.corrupt-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}.json")
        os.replace(path, kept)
        state = empty_state()
        state["history"].append({
            "run_id": uuid.uuid4().hex, "trigger": "schedule", "scheduled_for": None,
            "started": now.isoformat(timespec="seconds"), "ended": now.isoformat(timespec="seconds"),
            "outcome": "failed",
            "diagnostic": f"This routine's run history couldn't be read, so it was kept as {kept.name} "
                          "and started again. Missed runs from before then aren't listed.",
        })
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
            try:
                state = self.load(routine_id)
            except RoutineStateDamaged:
                state = self._set_aside(routine_id)
                self.save(routine_id, state)
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
    """Append a run to history and keep the failure count and last success current.

    A rate-limited run that will be tried again doesn't count, and nor does one a person stopped.
    """
    ids = {entry.get("run_id") for entry in state["history"]}
    if run.get("run_id") in ids:
        # A stuck run that finally returns, after its slot was already recorded.
        run["run_id"] = f"{run['run_id']}:{sum(1 for i in ids if str(i).startswith(run['run_id']))}"
    state["history"].append(run)
    outcome = run.get("outcome")
    if outcome in {"posted", "no_reply"}:
        state["consecutive_failures"] = 0
        state["last_success"] = run.get("ended") or run.get("started")
    elif outcome in FAILURES and not run.get("rate_limited"):
        state["consecutive_failures"] += 1


def run_record(routine: dict[str, Any], *, trigger: str, slot: datetime | None, outcome: str,
               started: datetime | None = None, ended: datetime | None = None,
               diagnostic: str = "", **extra: Any) -> dict[str, Any]:
    """One entry in a routine's run history."""
    assert outcome in OUTCOMES, outcome
    # One id per routine and scheduled time, so a slot is recognised however it ran (as OpenClaw does).
    run_id = (f"{routine['id']}:{slot.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
              + (":retry" if trigger == "retry" else "") if slot else uuid.uuid4().hex)
    record: dict[str, Any] = {
        "run_id": run_id,
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

def request_run(home: Path, routine_id: str, *, by: str = "local-operator", at: datetime | None = None) -> Path:
    """Ask the running bridge to run a routine now (the CLI's run-now)."""
    if not ROUTINE_ID_RE.fullmatch(routine_id):
        raise RoutineError("Invalid routine id")
    path = home / REQUESTS_DIR / f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.json"
    _atomic_write(path, {"routine_id": routine_id, "by": by,
                         "at": (at or datetime.now(timezone.utc)).isoformat(timespec="seconds")})
    return path


def take_requests(home: Path) -> list[dict[str, Any]]:
    """Claim pending run-now requests, oldest first.

    A claimed request is renamed, not deleted: ``finish_request`` deletes it once it is
    started or recorded, and ``reclaim_requests`` brings back any a crash left claimed.
    """
    root = home / REQUESTS_DIR
    try:
        paths = sorted(root.glob("*.json"))
    except OSError:
        return []
    requests = []
    for path in paths:
        taken = path.with_suffix(".taken")
        try:
            os.replace(path, taken)
            data = json.loads(taken.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict) and isinstance(data.get("routine_id"), str):
            requests.append({**data, "name": taken.stem, "path": str(taken)})
        else:
            taken.unlink(missing_ok=True)
    return requests


def finish_request(request: dict[str, Any]) -> None:
    Path(request["path"]).unlink(missing_ok=True)


def reclaim_requests(home: Path, *, started: set[str]) -> None:
    """After a crash: drop claimed requests whose run started, and make the rest pending again."""
    try:
        paths = list((home / REQUESTS_DIR).glob("*.taken"))
    except OSError:
        return
    for path in paths:
        if path.stem in started:
            path.unlink(missing_ok=True)
        else:
            try:
                os.replace(path, path.with_suffix(".json"))
            except OSError:
                pass


# --- Scheduler health ----------------------------------------------------------

def scheduler_health(home: Path) -> dict[str, Any]:
    """What the bridge's scheduler last saw, for ``tag routine list`` and Tag.app."""
    health: dict[str, Any] = {"last_tick": None, "last_error": None, "routine_errors": {}, "last_clock_jump": None}
    try:
        data = json.loads((home / HEALTH).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return health
    if isinstance(data, dict):
        health.update({key: data[key] for key in health if key in data})
    if not isinstance(health["routine_errors"], dict):
        health["routine_errors"] = {}
    return health


def write_scheduler_health(home: Path, health: dict[str, Any]) -> None:
    _atomic_write(home / HEALTH, health, durable=False)


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
    lines.append(f"If there is nothing new or worth posting, reply with exactly {NO_REPLY} and nothing else. "
                 f"{NO_REPLY} is a fixed code: don't translate it or add it to a real answer.")
    lines.append("This run is the routine itself: do the task now. Don't add or change routines, even if the "
                 'instructions mention times such as "every Monday".')
    lines.append("Actions that need approval still ask a person in Slack; this prompt is not approval.")
    return "\n".join(lines) + "\n\nRoutine instructions:\n" + routine["prompt"]


def _silence_marker(text: str) -> bool:
    stripped = text.strip()
    if not 0 < len(stripped) <= 64:
        return False
    return any(" ".join(form.upper().split()) in SILENCE_MARKERS
               for form in (stripped, stripped.strip("`*_ .,:;!\"'")))


def is_no_reply(answer: str) -> bool:
    """Whether a run chose to post nothing.

    The marker counts alone, or on its own first or last line next to a short note
    (``NO_REPLY`` then "nothing merged today"), or opening the answer in brackets.
    Inside a sentence it is real content. An empty answer is a failure, not silence.
    This follows Hermes Agent's matcher for scheduled runs.
    """
    stripped = answer.strip() if isinstance(answer, str) else ""
    if not stripped:
        return False
    lines = [line for line in stripped.splitlines() if line.strip()]
    return (stripped.upper().startswith(("[NO_REPLY]", "[SILENT]"))
            or any(_silence_marker(candidate) for candidate in (stripped, lines[0], lines[-1])))
