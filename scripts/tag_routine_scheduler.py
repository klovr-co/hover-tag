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
import uuid
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
        # At most one run per routine waits for a free slot, oldest first (like Temporal's BufferOne).
        self.waiting: dict[str, tuple[str, datetime | None]] = {}
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
            def close(state: dict[str, Any]) -> bool:
                running = state.get("running")
                if not isinstance(running, dict):
                    return False
                state["running"] = None
                trigger = running.get("trigger")
                state["history"].append(routines_lib.run_record(
                    routine, trigger=trigger if trigger in routines_lib.TRIGGERS else "schedule",
                    slot=_parse(running.get("scheduled_for")), outcome="interrupted",
                    started=_parse(running.get("started")), ended=self.clock(),
                    diagnostic="Tag stopped before this run finished. It was not run again, so nothing posts twice."))
                return True
            self.states.update(routine["id"], close)
            waiting = self.states.load(routine["id"]).get("waiting")
            if isinstance(waiting, dict) and waiting.get("trigger") in routines_lib.TRIGGERS:
                # A run that was waiting for a slot when Tag stopped still waits.
                with self.lock:
                    self.waiting[routine["id"]] = (waiting["trigger"], _parse(waiting.get("scheduled_for")))

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
                try:
                    self._launch(routine, "run-now", None, now)
                except Exception as exc:  # noqa: BLE001 - keep checking the others
                    self._warn("Could not start routine %s: %s", routine["id"], exc)
        self._drain(now, by_id)

    def _drain(self, now: datetime, by_id: dict[str, dict[str, Any]] | None = None) -> None:
        """Start waiting runs, oldest first, while there are free slots."""
        while True:
            with self.lock:
                if len(self.running) >= routines_lib.MAX_CONCURRENT_RUNS:
                    return
                ready = next((rid for rid in self.waiting if rid not in self.running), None)
                if ready is None:
                    return
                trigger, slot = self.waiting.pop(ready)
            if by_id is None:
                try:
                    by_id = {r["id"]: r for r in self.store.load()}
                except (routines_lib.RoutineError, routines_lib.RoutineSchemaError) as exc:
                    self._warn("Could not read routines: %s", exc)
                    return
            routine = by_id.get(ready)
            if routine is None:
                continue  # Removed while it waited.
            if trigger != "run-now" and not routine["enabled"]:
                self.states.update(ready, lambda state: (state.update(waiting=None), routines_lib.add_run(
                    state, routines_lib.run_record(routine, trigger=trigger, slot=slot, outcome="skipped",
                                                   started=now, ended=now,
                                                   diagnostic="Paused while it waited for a free slot."))))
                continue
            try:
                self._launch(routine, trigger, slot, now, queued=True)
            except Exception as exc:  # noqa: BLE001 - keep draining the others
                self._warn("Could not start routine %s: %s", ready, exc)

    def _check_due(self, routine: dict[str, Any], now: datetime) -> None:
        key = schedule_key(routine)
        decision: dict[str, Any] = {}

        def plan(state: dict[str, Any]) -> bool:
            if not routine["enabled"]:
                # A paused routine doesn't catch up; resuming starts from the next slot.
                if state["next_due"] is None:
                    return False
                state["next_due"] = None
                return True
            due = _parse(state.get("next_due"))
            if due is None or state.get("schedule_key") != key:
                state["next_due"] = routines_lib.next_run(routine, now).isoformat()
                state["schedule_key"] = key
                return True
            if now < due:
                return False
            slots = [due]
            while len(slots) < MAX_SLOTS:
                following = routines_lib.next_run(routine, slots[-1])
                if following > now:
                    break
                slots.append(following)
            latest = slots[-1]
            state["next_due"] = routines_lib.next_run(routine, now).isoformat()
            late = now - latest > routines_lib.LATE_AFTER
            run_latest = not late or (routine["catch_up"] == "latest"
                                      and now - latest <= routines_lib.CATCH_UP_LOOKBACK)
            # Every slot that won't run becomes one history entry, however many there were.
            missed = slots[:-1] if run_latest else slots
            if missed:
                count = len(missed)
                reason = "" if run_latest else (" This routine skips late runs." if routine["catch_up"] == "skip"
                                                else " The latest was too long ago to catch up.")
                state["history"].append(routines_lib.run_record(
                    routine, trigger="schedule", slot=missed[-1], outcome="missed", count=count,
                    diagnostic=f"Missed {count} run{'s' if count != 1 else ''} while Tag was stopped "
                               f"or this computer was asleep.{reason}"))
            if run_latest:
                decision["launch"] = ("catch-up" if late else "schedule", latest)
            return True

        self.states.update(routine["id"], plan)
        if "launch" in decision:
            trigger, slot = decision["launch"]
            self._launch(routine, trigger, slot, now)

    def _launch(self, routine: dict[str, Any], trigger: str, slot: datetime | None, now: datetime,
                *, queued: bool = False) -> None:
        routine_id = routine["id"]

        def refuse(outcome: str, diagnostic: str, waiting: tuple[str, datetime | None] | None = None) -> None:
            def change(state: dict[str, Any]) -> None:
                if waiting is not None:
                    state["waiting"] = {"trigger": waiting[0], "queued": now.isoformat(timespec="seconds"),
                                        "scheduled_for": waiting[1].isoformat(timespec="seconds") if waiting[1] else None}
                if outcome:
                    routines_lib.add_run(state, routines_lib.run_record(
                        routine, trigger=trigger if waiting is None else replaced[0],
                        slot=slot if waiting is None else replaced[1], outcome=outcome, started=now, ended=now,
                        diagnostic=diagnostic))
            self.states.update(routine_id, change)

        token = uuid.uuid4().hex
        replaced: tuple[str, datetime | None] | None = None
        with self.lock:
            if routine_id in self.running:
                decision = "overlap"
            elif not routines_lib.approved(routine):
                decision = "blocked"
            elif len(self.running) >= routines_lib.MAX_CONCURRENT_RUNS:
                decision = "wait"
                replaced = self.waiting.pop(routine_id, None)
                self.waiting[routine_id] = (trigger, slot)
            else:
                decision = "start"
                self.running[routine_id] = token
        if decision == "overlap":
            return refuse("skipped", "The previous run was still going.")
        if decision == "blocked":
            return refuse("blocked", "This routine changed after it was approved. Approve it again "
                                     "with tag routine edit or in Tag.app.")
        if decision == "wait":
            # Both slots are busy: wait for one. A newer run replaces an older waiting one.
            if replaced is not None:
                return refuse("skipped", "A newer run replaced it while it waited for a free slot.",
                              waiting=(trigger, slot))
            return refuse("", "", waiting=(trigger, slot))

        def mark(state: dict[str, Any]) -> None:
            state["waiting"] = None
            state["running"] = {"token": token, "trigger": trigger, "started": now.isoformat(timespec="seconds"),
                                "scheduled_for": slot.isoformat(timespec="seconds") if slot else None}
        try:
            state = self.states.update(routine_id, mark)
            last_success = state.get("last_success")
            self.spawn(lambda: self._run(routine, token, trigger, slot, last_success))
        except BaseException:
            with self.lock:
                if self.running.get(routine_id) == token:
                    self.running.pop(routine_id)
            raise

    def _run(self, routine: dict[str, Any], token: str, trigger: str, slot: datetime | None,
             last_success: str | None) -> None:
        routine_id = routine["id"]
        started = self.clock()
        record: dict[str, Any] | None = None
        error = "an internal error"
        try:
            record = self.start_run(routine, trigger, slot, last_success)
        except Exception as exc:  # noqa: BLE001 - a crashed run is a failed run, not a dead scheduler
            self._warn("Routine %s failed: %s", routine_id, exc)
            error = f"an internal error ({type(exc).__name__})"
        finally:
            # Also on BaseException, such as shutdown, so the running mark never outlives the run.
            if record is None:
                record = routines_lib.run_record(
                    routine, trigger=trigger, slot=slot, outcome="failed", started=started, ended=self.clock(),
                    diagnostic=f"Tag hit {error}.")
            state = self._finish(routine_id, token, record)
            with self.lock:
                if self.running.get(routine_id) == token:
                    self.running.pop(routine_id)
        try:
            self._drain(self.clock())
        except Exception as exc:  # noqa: BLE001 - the next tick drains again
            self._warn("Could not start waiting routines: %s", exc)
        if not self._exists(routine_id):
            # Removed while it ran: don't leave its state behind.
            self.states.remove(routine_id)
            return
        failures = state["consecutive_failures"]
        if failures >= routines_lib.AUTO_PAUSE_FAILURES and routine["enabled"]:
            try:
                paused = self.store.set_enabled(
                    routine_id, False, reason=f"Paused after {failures} failed runs in a row.")
            except Exception as exc:  # noqa: BLE001 - the next failure tries again
                self._warn("Could not pause routine %s: %s", routine_id, exc)
                return
            if self.on_auto_pause is not None:
                try:
                    self.on_auto_pause(paused, failures)
                except Exception as exc:  # noqa: BLE001 - the pause itself already happened
                    self._warn("Could not announce the paused routine %s: %s", routine_id, exc)

    def _finish(self, routine_id: str, token: str, record: dict[str, Any]) -> dict[str, Any]:
        """Record the run and clear its running mark in one step, never another run's mark."""
        def finish(state: dict[str, Any]) -> None:
            running = state.get("running")
            if isinstance(running, dict) and running.get("token") == token:
                state["running"] = None
            routines_lib.add_run(state, record)
        return self.states.update(routine_id, finish)

    def _exists(self, routine_id: str) -> bool:
        try:
            return any(r["id"] == routine_id for r in self.store.load())
        except (routines_lib.RoutineError, routines_lib.RoutineSchemaError):
            return True


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
