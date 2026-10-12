# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""Decide when routines run. The Slack bridge calls ``tick`` every few seconds.

Everything is decided from the wall clock and the saved next-due time, so
restarts, sleep and clock changes all reduce to "is it due yet?". After a gap,
missed slots collapse into one history entry and at most one catch-up run.
A routine never overlaps itself, a run that was cut off by a crash is marked
interrupted and never replayed, and a routine that keeps failing pauses itself.

The scheduler knows nothing about Slack or backends: ``start_run`` does the
work and returns a run record (see ``tag_routines.run_record``).
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from . import tag_routines as routines_lib
except ImportError:
    import tag_routines as routines_lib

StartRun = Callable[[dict[str, Any], str, "datetime | None", "str | None"], dict[str, Any]]
MAX_SLOTS = 20_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _spawn(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="tag-routine-run", daemon=True).start()


def schedule_key(routine: dict[str, Any]) -> str:
    """What next-due depends on; when it changes, next-due is computed again."""
    return "|".join((routine["schedule"]["text"], routine["timezone"], routine.get("anchor") or ""))


class RoutineScheduler:
    def __init__(self, home: Path, start_run: StartRun, *, clock: Callable[[], datetime] = _now,
                 spawn: Callable[[Callable[[], None]], None] = _spawn,
                 on_auto_pause: Callable[[dict[str, Any], int], None] | None = None,
                 logger: Any = None) -> None:
        self.home = home
        self.store = routines_lib.RoutineStore(home)
        self.states = routines_lib.RoutineStateStore(home)
        self.start_run = start_run
        self.clock = clock
        self.spawn = spawn
        self.on_auto_pause = on_auto_pause
        self.logger = logger
        self.running: dict[str, str] = {}
        self.lock = threading.Lock()

    def _warn(self, message: str, *args: Any) -> None:
        if self.logger is not None:
            self.logger.warning(message, *args)

    def recover(self) -> None:
        """At startup, close runs that a previous bridge left unfinished."""
        try:
            routines = self.store.load()
        except (routines_lib.RoutineError, routines_lib.RoutineSchemaError) as exc:
            self._warn("Could not read routines: %s", exc)
            return
        for routine in routines:
            state = self.states.load(routine["id"])
            running = state.get("running")
            if not isinstance(running, dict):
                continue
            state["running"] = None
            self.states.save(routine["id"], state)
            slot = _parse(running.get("scheduled_for"))
            self.states.record(routine["id"], routines_lib.run_record(
                routine, trigger=running.get("trigger") if running.get("trigger") in routines_lib.TRIGGERS else "schedule",
                slot=slot, outcome="interrupted", started=_parse(running.get("started")), ended=self.clock(),
                diagnostic="Tag stopped before this run finished. It was not run again, so nothing posts twice."))

    def tick(self) -> None:
        now = self.clock()
        try:
            routines = self.store.load()
        except (routines_lib.RoutineError, routines_lib.RoutineSchemaError) as exc:
            self._warn("Could not read routines: %s", exc)
            return
        for routine in routines:
            try:
                self._check_due(routine, now)
            except Exception as exc:  # noqa: BLE001 - one routine must not stop the others
                self._warn("Could not schedule routine %s: %s", routine["id"], exc)
        by_id = {routine["id"]: routine for routine in routines}
        for request in routines_lib.take_requests(self.home):
            routine = by_id.get(request["routine_id"])
            if routine is not None:
                self._launch(routine, "run-now", None, now)

    def _check_due(self, routine: dict[str, Any], now: datetime) -> None:
        state = self.states.load(routine["id"])
        key = schedule_key(routine)
        if not routine["enabled"]:
            # A paused routine doesn't catch up; resuming starts from the next slot.
            if state["next_due"] is not None:
                state["next_due"] = None
                self.states.save(routine["id"], state)
            return
        due = _parse(state.get("next_due"))
        if due is None or state.get("schedule_key") != key:
            state["next_due"] = routines_lib.next_run(routine, now).isoformat()
            state["schedule_key"] = key
            self.states.save(routine["id"], state)
            return
        if now < due:
            return
        slots = [due]
        while len(slots) < MAX_SLOTS:
            following = routines_lib.next_run(routine, slots[-1])
            if following > now:
                break
            slots.append(following)
        latest = slots[-1]
        state["next_due"] = routines_lib.next_run(routine, now).isoformat()
        self.states.save(routine["id"], state)
        late = now - latest > routines_lib.LATE_AFTER
        run_latest = not late or (routine["catch_up"] == "latest"
                                  and now - latest <= routines_lib.CATCH_UP_LOOKBACK)
        # Every slot that won't run becomes one history entry, however many there were.
        missed = slots[:-1] if run_latest else slots
        if missed:
            count = len(missed)
            reason = "" if run_latest else (" This routine skips late runs." if routine["catch_up"] == "skip"
                                            else " The latest was too long ago to catch up.")
            self.states.record(routine["id"], routines_lib.run_record(
                routine, trigger="schedule", slot=missed[-1], outcome="missed", count=count,
                diagnostic=f"Missed {count} run{'s' if count != 1 else ''} while Tag was stopped "
                           f"or this computer was asleep.{reason}"))
        if run_latest:
            self._launch(routine, "catch-up" if late else "schedule", latest, now)

    def _launch(self, routine: dict[str, Any], trigger: str, slot: datetime | None, now: datetime) -> None:
        routine_id = routine["id"]

        def refuse(outcome: str, diagnostic: str) -> None:
            self.states.record(routine_id, routines_lib.run_record(
                routine, trigger=trigger, slot=slot, outcome=outcome, started=now, ended=now,
                diagnostic=diagnostic))

        with self.lock:
            if routine_id in self.running:
                return refuse("skipped", "The previous run was still going.")
            if len(self.running) >= routines_lib.MAX_CONCURRENT_RUNS:
                return refuse("skipped", "Other routines were already running.")
            if not routines_lib.approved(routine):
                return refuse("blocked", "This routine changed after it was approved. Approve it again "
                                         "with tag routine edit or in Tag.app.")
            self.running[routine_id] = trigger
        state = self.states.load(routine_id)
        state["running"] = {"trigger": trigger, "started": now.isoformat(timespec="seconds"),
                            "scheduled_for": slot.isoformat(timespec="seconds") if slot else None}
        self.states.save(routine_id, state)
        last_success = state.get("last_success")
        self.spawn(lambda: self._run(routine, trigger, slot, last_success))

    def _run(self, routine: dict[str, Any], trigger: str, slot: datetime | None, last_success: str | None) -> None:
        routine_id = routine["id"]
        started = self.clock()
        try:
            record = self.start_run(routine, trigger, slot, last_success)
        except Exception as exc:  # noqa: BLE001 - a crashed run is a failed run, not a dead scheduler
            self._warn("Routine %s failed: %s", routine_id, exc)
            record = routines_lib.run_record(
                routine, trigger=trigger, slot=slot, outcome="failed", started=started, ended=self.clock(),
                diagnostic=f"Tag hit an internal error ({type(exc).__name__}).")
        finally:
            with self.lock:
                self.running.pop(routine_id, None)
            state = self.states.load(routine_id)
            state["running"] = None
            self.states.save(routine_id, state)
        state = self.states.record(routine_id, record)
        failures = state["consecutive_failures"]
        if failures >= routines_lib.AUTO_PAUSE_FAILURES and routine["enabled"]:
            try:
                paused = self.store.set_enabled(
                    routine_id, False, reason=f"Paused after {failures} failed runs in a row.")
            except routines_lib.RoutineError as exc:
                self._warn("Could not pause routine %s: %s", routine_id, exc)
                return
            if self.on_auto_pause is not None:
                try:
                    self.on_auto_pause(paused, failures)
                except Exception as exc:  # noqa: BLE001 - the pause itself already happened
                    self._warn("Could not announce the paused routine %s: %s", routine_id, exc)


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
