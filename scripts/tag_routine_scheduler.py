# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""Decide when routines run. The Slack bridge calls ``tick`` every few seconds.

Everything is decided from the wall clock and the saved next-due time, so
restarts, sleep and clock changes all reduce to "is it due yet?". After a gap,
missed slots collapse into one history entry and at most one catch-up run, of
the latest slot, as Kubernetes CronJobs do. A routine never overlaps itself; at
most one run waits for a free slot, and a newer one replaces it. A run that was
cut off by a crash is marked interrupted and never replayed, and a routine that
keeps failing pauses itself. The scenarios in the tests come from the test
suites of Hermes Agent, OpenClaw, Temporal, Kubernetes, nanobot, Agno,
APScheduler and DBOS.

The scheduler knows nothing about Slack or backends: ``start_run`` does the
work and returns a run record (see ``tag_routines.run_record``).
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from . import tag_routines as routines_lib
except ImportError:
    import tag_routines as routines_lib

StartRun = Callable[[dict[str, Any], str, "datetime | None", "str | None"], dict[str, Any]]
MAX_SLOTS = 20_000
# Wall time and monotonic time drifting apart by more than this between checks means
# the computer slept or its clock was changed.
CLOCK_JUMP = timedelta(minutes=2)
SCHEDULED = frozenset({"schedule", "catch-up"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _spawn(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="tag-routine-run", daemon=True).start()


def schedule_key(routine: dict[str, Any]) -> str:
    """What next-due depends on; when it changes, next-due is computed again."""
    return "|".join((routine["schedule"]["text"], routine["timezone"], routine.get("anchor") or ""))


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat(timespec="seconds") if moment else None


class RoutineScheduler:
    def __init__(self, home: Path, start_run: StartRun, *, clock: Callable[[], datetime] = _now,
                 spawn: Callable[[Callable[[], None]], None] = _spawn,
                 on_auto_pause: Callable[[dict[str, Any]], None] | None = None,
                 on_crash: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
                 monotonic: Callable[[], float] | None = None, logger: Any = None) -> None:
        self.home = home
        self.store = routines_lib.RoutineStore(home)
        self.states = routines_lib.RoutineStateStore(home)
        self.start_run = start_run
        self.clock = clock
        self.spawn = spawn
        self.on_auto_pause = on_auto_pause
        self.on_crash = on_crash
        # Run ages are measured on this clock, which doesn't count sleep or clock changes, so
        # a run that was busy when the laptop slept isn't mistaken for a stuck one.
        self.monotonic = monotonic or (lambda: time.monotonic())
        self.logger = logger
        self.running: dict[str, str] = {}
        # When each run started (wall and monotonic), how long it may take, its trigger and slot:
        # to notice a run that never returns.
        self.started: dict[str, tuple[datetime, float, int, str, datetime | None]] = {}
        # At most one run per routine waits for a free slot; a newer one replaces it.
        self.waiting: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.recovered = False
        self.health = routines_lib.scheduler_health(home)
        self.last_seen: tuple[float, datetime] | None = None

    def _warn(self, message: str, *args: Any) -> None:
        if self.logger is not None:
            self.logger.warning(message, *args)

    # --- Startup -----------------------------------------------------------------

    def recover(self) -> None:
        """Close runs that a previous bridge left unfinished. Retried by ``tick`` until it works."""
        try:
            routines = self.store.load()
        except (routines_lib.RoutineError, routines_lib.RoutineSchemaError) as exc:
            self._warn("Could not read routines: %s", exc)
            return
        started: set[str] = set()
        for routine in routines:
            try:
                self._recover_one(routine, started)
            except Exception as exc:  # noqa: BLE001 - one routine must not stop the others
                self._warn("Could not recover routine %s: %s", routine["id"], exc)
        routines_lib.reclaim_requests(self.home, started=started)
        self.recovered = True

    def _recover_one(self, routine: dict[str, Any], started: set[str]) -> None:
        if routine["id"] in self.running:
            return
        now = self.clock()
        closed: dict[str, int] = {}

        def close(state: dict[str, Any]) -> bool:
            changed = False
            running = state.get("running")
            if isinstance(running, dict):
                state["running"] = None
                if isinstance(running.get("request"), str):
                    started.add(running["request"])
                trigger = running.get("trigger")
                routines_lib.add_run(state, routines_lib.run_record(
                    routine, trigger=trigger if trigger in routines_lib.TRIGGERS else "schedule",
                    slot=_parse(running.get("scheduled_for")), outcome="interrupted",
                    started=_parse(running.get("started")), ended=now,
                    diagnostic="Tag stopped before this run finished, so it may not have posted. "
                               "It wasn't run again, so nothing posts twice."))
                closed["failures"] = state["consecutive_failures"]
                changed = True
            pending = state.get("pending")
            if isinstance(pending, dict):
                # Chosen to run just before Tag stopped, but never started: it waits for a slot.
                state["pending"] = None
                if not isinstance(state.get("waiting"), dict):
                    state["waiting"] = {**pending, "queued": _iso(now)}
                changed = True
            return changed

        state = self.states.update(routine["id"], close)
        waiting = state.get("waiting")
        if isinstance(waiting, dict) and waiting.get("trigger") in routines_lib.TRIGGERS:
            with self.lock:
                # Only restored runs keep when they were queued: they may have waited while Tag was stopped.
                self.waiting[routine["id"]] = {"trigger": waiting["trigger"],
                                               "slot": _parse(waiting.get("scheduled_for")),
                                               "key": waiting.get("key"), "queued": _parse(waiting.get("queued"))}
        if closed.get("failures", 0) >= routines_lib.AUTO_PAUSE_FAILURES:
            self._pause(routine, f"Paused after {closed['failures']} failed runs in a row.")

    # --- Each check --------------------------------------------------------------

    def tick(self) -> None:
        now = self.clock()
        self._note_clock(now)
        if not self.recovered:
            self.recover()
        try:
            routines = self.store.load()
        except (routines_lib.RoutineError, routines_lib.RoutineSchemaError) as exc:
            self._warn("Could not read routines: %s", exc)
            self._save_health(now, error=str(exc))
            return
        errors: dict[str, str] = {}
        for routine in routines:
            try:
                self._check_due(routine, now)
            except Exception as exc:  # noqa: BLE001 - one routine must not stop the others
                self._warn("Could not schedule routine %s: %s", routine["id"], exc)
                errors[routine["id"]] = str(exc)[:200] or type(exc).__name__
        by_id = {routine["id"]: routine for routine in routines}
        self._release_stuck(now, by_id)
        for request in routines_lib.take_requests(self.home):
            routine = by_id.get(request["routine_id"])
            if routine is not None:
                try:
                    self._run_now(routine, request, now)
                except Exception as exc:  # noqa: BLE001 - keep checking the others
                    self._warn("Could not start routine %s: %s", routine["id"], exc)
            # Only now: a crash before this leaves the request to be picked up again.
            routines_lib.finish_request(request)
        self._drain(now, by_id)
        self._save_health(now, error=None, routine_errors=errors)

    def _note_clock(self, now: datetime) -> None:
        """Note sleeps and clock changes: wall time moving differently from monotonic time."""
        monotonic = self.monotonic()
        if self.last_seen is not None:
            previous_monotonic, previous_wall = self.last_seen
            drift = (now - previous_wall) - timedelta(seconds=monotonic - previous_monotonic)
            if abs(drift) > CLOCK_JUMP:
                minutes = round(drift.total_seconds() / 60)
                note = (f"The clock jumped {'forward' if minutes > 0 else 'back'} {abs(minutes)} minutes "
                        "between checks: the computer slept or its clock was changed.")
                self._warn("%s", note)
                self.health["last_clock_jump"] = {"at": _iso(now), "note": note}
        self.last_seen = (monotonic, now)

    def _save_health(self, now: datetime, *, error: str | None,
                     routine_errors: dict[str, str] | None = None) -> None:
        self.health.update(last_tick=_iso(now), last_error=error)
        if routine_errors is not None:
            self.health["routine_errors"] = routine_errors
        try:
            routines_lib.write_scheduler_health(self.home, self.health)
        except OSError as exc:
            self._warn("Could not save scheduler health: %s", exc)

    def _run_now(self, routine: dict[str, Any], request: dict[str, Any], now: datetime) -> None:
        asked = _parse(request.get("at"))
        if asked is not None and now - asked > routines_lib.RUN_NOW_MAX_AGE:
            minutes = int(routines_lib.RUN_NOW_MAX_AGE.total_seconds() // 60)
            self.states.record(routine["id"], routines_lib.run_record(
                routine, trigger="run-now", slot=None, outcome="skipped", started=now, ended=now,
                diagnostic=f"Asked for more than {minutes} minutes before Tag could start it, "
                           "so it didn't run. Run it again if it's still wanted."))
            return
        self._launch(routine, "run-now", None, now, request=request["name"])

    def _check_due(self, routine: dict[str, Any], now: datetime) -> None:
        key = schedule_key(routine)
        decision: dict[str, Any] = {}

        def plan(state: dict[str, Any]) -> bool:
            if not routine["enabled"]:
                # A paused routine doesn't catch up; resuming starts from the next slot.
                if state["next_due"] is None and state["retry"] is None:
                    return False
                state["next_due"] = state["retry"] = None
                return True
            due = _parse(state.get("next_due"))
            if due is None or state.get("schedule_key") != key:
                if due is None:
                    # First check, or resumed after a pause: failures start again from zero.
                    state["consecutive_failures"] = 0
                state["next_due"] = routines_lib.next_run(routine, now).isoformat()
                state["schedule_key"] = key
                state["retry"] = None
                return True
            if now < due:
                if due > routines_lib.upcoming(routine, now, 2)[-1]:
                    # Saved while the clock was ahead; slots that already ran won't run again.
                    self._warn("Routine %s was due at %s, past its next runs; working it out again",
                               routine["id"], due.isoformat())
                    state["next_due"] = routines_lib.next_run(routine, now).isoformat()
                    return True
                retry = state.get("retry")
                if isinstance(retry, dict) and (at := _parse(retry.get("at"))) is not None and now >= at:
                    state["retry"] = None
                    decision["launch"] = ("retry", _parse(retry.get("scheduled_for")))
                    state["pending"] = {"trigger": "retry", "scheduled_for": retry.get("scheduled_for"), "key": key}
                    return True
                return False
            slots = [due]
            while len(slots) < MAX_SLOTS:
                following = routines_lib.next_run(routine, slots[-1])
                if following > now:
                    break
                slots.append(following)
            latest = slots[-1]
            state["next_due"] = routines_lib.next_run(routine, now).isoformat()
            state["retry"] = None
            done = {entry.get("scheduled_for") for entry in state["history"] if entry.get("outcome") != "missed"}
            late = now - latest > routines_lib.LATE_AFTER
            run_latest = _iso(latest) not in done and (
                not late or (routine["catch_up"] == "latest" and now - latest <= routines_lib.CATCH_UP_LOOKBACK))
            # Every slot that won't run becomes one history entry, however many there were. Slots up to
            # the newest one in history were already handled: if the clock stepped back, they come round
            # again, and one missed entry can stand for several of them.
            handled = max(filter(None, (_parse(entry.get("scheduled_for")) for entry in state["history"])),
                          default=None)
            missed = [slot for slot in (slots[:-1] if run_latest or _iso(latest) in done else slots)
                      if _iso(slot) not in done and (handled is None or slot > handled)]
            if missed:
                count = len(missed)
                reason = "" if run_latest else (" This routine skips late runs." if routine["catch_up"] == "skip"
                                                else " The latest was too long ago to catch up.")
                state["history"].append(routines_lib.run_record(
                    routine, trigger="schedule", slot=missed[-1], outcome="missed", count=count,
                    diagnostic=f"Missed {count} run{'s' if count != 1 else ''} while Tag was stopped "
                               f"or this computer was asleep.{reason}"))
            if run_latest:
                trigger = "catch-up" if late else "schedule"
                decision["launch"] = (trigger, latest)
                state["pending"] = {"trigger": trigger, "scheduled_for": _iso(latest), "key": key}
            elif _iso(latest) in done:
                self._warn("Routine %s already ran its %s slot; not running it again", routine["id"], _iso(latest))
            return True

        self.states.update(routine["id"], plan)
        if "launch" in decision:
            trigger, slot = decision["launch"]
            self._launch(routine, trigger, slot, now, key=key)

    def _drain(self, now: datetime, by_id: dict[str, dict[str, Any]] | None = None) -> None:
        """Start waiting runs while there are free slots."""
        while True:
            with self.lock:
                if len(self.running) >= routines_lib.MAX_CONCURRENT_RUNS:
                    return
                ready = next((rid for rid in self.waiting if rid not in self.running), None)
                if ready is None:
                    return
                entry = self.waiting.pop(ready)
            if by_id is None:
                try:
                    by_id = {r["id"]: r for r in self.store.load()}
                except (routines_lib.RoutineError, routines_lib.RoutineSchemaError) as exc:
                    self._warn("Could not read routines: %s", exc)
                    return
            routine = by_id.get(ready)
            if routine is None:
                continue  # Removed while it waited.
            trigger, slot = entry["trigger"], entry["slot"]
            outcome, diagnostic = self._still_wanted(routine, trigger, slot, entry.get("key"), now,
                                                     queued=entry.get("queued"))
            if outcome:
                self.states.update(ready, lambda state, o=outcome, d=diagnostic, t=trigger, s=slot: (
                    state.update(waiting=None),
                    routines_lib.add_run(state, routines_lib.run_record(
                        routine, trigger=t, slot=s, outcome=o, started=now, ended=now, diagnostic=d))))
                continue
            if trigger == "schedule" and slot is not None and now - slot > routines_lib.LATE_AFTER:
                trigger = "catch-up"
            try:
                self._launch(routine, trigger, slot, now, queued=True, key=entry.get("key"))
            except Exception as exc:  # noqa: BLE001 - keep draining the others
                self._warn("Could not start routine %s: %s", ready, exc)

    def _still_wanted(self, routine: dict[str, Any], trigger: str, slot: datetime | None, key: str | None,
                      now: datetime, *, queued: datetime | None = None) -> tuple[str, str]:
        """Why a run that waited for a free slot shouldn't run any more: (outcome, diagnostic), or empty."""
        if trigger == "run-now":
            if queued is not None and now - queued > routines_lib.RUN_NOW_MAX_AGE:
                minutes = int(routines_lib.RUN_NOW_MAX_AGE.total_seconds() // 60)
                return "skipped", (f"Asked for more than {minutes} minutes before Tag could start it, "
                                   "so it didn't run. Run it again if it's still wanted.")
            return "", ""
        if not routine["enabled"]:
            return "skipped", "Paused while it waited for a free slot."
        if trigger in SCHEDULED and key is not None and key != schedule_key(routine):
            return "skipped", "The schedule changed while it waited for a free slot."
        if trigger in SCHEDULED and slot is not None:
            if routines_lib.next_run(routine, slot) <= now:
                return "skipped", "A newer scheduled run came due while it waited for a free slot."
            late = now - slot > routines_lib.LATE_AFTER
            if late and (routine["catch_up"] == "skip" or now - slot > routines_lib.CATCH_UP_LOOKBACK):
                return "missed", ("Waited too long for a free slot, while Tag was busy or stopped."
                                  + (" This routine skips late runs." if routine["catch_up"] == "skip" else ""))
        return "", ""

    # --- Starting and finishing runs --------------------------------------------

    def _launch(self, routine: dict[str, Any], trigger: str, slot: datetime | None, now: datetime,
                *, queued: bool = False, key: str | None = None, request: str | None = None) -> None:
        routine_id = routine["id"]

        def refuse(outcome: str, diagnostic: str, waiting: dict[str, Any] | None = None,
                   replaced: dict[str, Any] | None = None) -> None:
            def change(state: dict[str, Any]) -> None:
                state["pending"] = None
                if waiting is not None:
                    state["waiting"] = {"trigger": waiting["trigger"], "queued": _iso(now),
                                        "scheduled_for": _iso(waiting["slot"]), "key": waiting["key"]}
                if outcome:
                    routines_lib.add_run(state, routines_lib.run_record(
                        routine, trigger=(replaced or {}).get("trigger", trigger),
                        slot=(replaced or {}).get("slot", slot) if replaced else slot, outcome=outcome,
                        started=now, ended=now, diagnostic=diagnostic))
            self.states.update(routine_id, change)

        token = uuid.uuid4().hex
        replaced: dict[str, Any] | None = None
        entry = {"trigger": trigger, "slot": slot, "key": key}
        with self.lock:
            if routine_id in self.running:
                decision = "overlap"
            elif not routines_lib.approved(routine):
                decision = "blocked"
            elif len(self.running) >= routines_lib.MAX_CONCURRENT_RUNS:
                decision = "wait"
                replaced = self.waiting.pop(routine_id, None)
                self.waiting[routine_id] = entry
            else:
                decision = "start"
                self.running[routine_id] = token
                self.started[routine_id] = (now, self.monotonic(), int(routine["limits"]["timeout_seconds"]),
                                            trigger, slot)
                # Starting supersedes a run that was still waiting, so it can't run afterwards.
                replaced = self.waiting.pop(routine_id, None)
        if decision == "overlap":
            return refuse("skipped", "The previous run was still going.")
        if decision == "blocked":
            return refuse("blocked", "This routine changed after it was approved. Approve it again "
                                     "with tag routine edit or in Tag.app.")
        if decision == "wait":
            # Both slots are busy: wait for one. A newer run replaces an older waiting one.
            if replaced is not None:
                return refuse("skipped", "A newer run replaced it while it waited for a free slot.",
                              waiting=entry, replaced=replaced)
            return refuse("", "", waiting=entry)

        def mark(state: dict[str, Any]) -> None:
            state["waiting"] = state["pending"] = None
            if replaced is not None:
                routines_lib.add_run(state, routines_lib.run_record(
                    routine, trigger=replaced["trigger"], slot=replaced["slot"], outcome="skipped",
                    started=now, ended=now, diagnostic="A newer run started while it waited for a free slot."))
            state["running"] = {"token": token, "trigger": trigger, "started": _iso(now),
                                "scheduled_for": _iso(slot), "request": request}
        try:
            state = self.states.update(routine_id, mark)
        except BaseException:
            self._release(routine_id, token)
            raise
        last_success = state.get("last_success")
        try:
            self.spawn(lambda: self._run(routine, token, trigger, slot, last_success))
        except Exception as exc:  # noqa: BLE001 - record it instead of leaving the routine marked running
            self._release(routine_id, token)
            self._warn("Could not start routine %s: %s", routine_id, exc)
            self._finish(routine_id, token, routines_lib.run_record(
                routine, trigger=trigger, slot=slot, outcome="failed", started=now, ended=now,
                diagnostic=f"Tag couldn't start this run ({type(exc).__name__})."))
        except BaseException:
            self._release(routine_id, token)
            raise

    def _release(self, routine_id: str, token: str) -> None:
        with self.lock:
            if self.running.get(routine_id) == token:
                self.running.pop(routine_id)
                self.started.pop(routine_id, None)

    def _release_stuck(self, now: datetime, by_id: dict[str, dict[str, Any]]) -> None:
        """Stop waiting for a run that is long past its time limit, so later runs aren't skipped forever."""
        awake = self.monotonic()
        allowance = routines_lib.STUCK_AFTER_LIMIT.total_seconds()
        with self.lock:
            stuck = [(rid, token, self.started[rid]) for rid, token in self.running.items()
                     if rid in self.started and awake - self.started[rid][1] > self.started[rid][2] + allowance]
        for routine_id, token, (started, _, limit, trigger, slot) in stuck:
            routine = by_id.get(routine_id)
            self._release(routine_id, token)
            self._warn("Routine %s has been running since %s; no longer waiting for it", routine_id, started)
            if routine is None:
                continue
            self._finish(routine_id, token, routines_lib.run_record(
                routine, trigger=trigger, slot=slot, outcome="timeout", started=started, ended=now,
                diagnostic=f"Still going {int(routines_lib.STUCK_AFTER_LIMIT.total_seconds() // 60)} minutes "
                           f"after its {limit // 60}-minute limit, so Tag stopped waiting for it. "
                           "If it finishes, its reply may still post."))

    def _run(self, routine: dict[str, Any], token: str, trigger: str, slot: datetime | None,
             last_success: str | None) -> None:
        routine_id = routine["id"]
        started = self.clock()
        record: dict[str, Any] | None = None
        crashed = False
        error = "an internal error"
        state: dict[str, Any] | None = None
        try:
            record = self.start_run(routine, trigger, slot, last_success)
        except Exception as exc:  # noqa: BLE001 - a crashed run is a failed run, not a dead scheduler
            self._warn("Routine %s failed: %s", routine_id, exc)
            error = f"an internal error ({type(exc).__name__})"
            crashed = True
        finally:
            # Also on BaseException, such as shutdown, so the running mark never outlives the run.
            if record is None:
                record = routines_lib.run_record(
                    routine, trigger=trigger, slot=slot, outcome="failed", started=started, ended=self.clock(),
                    diagnostic=f"Tag hit {error}.")
            try:
                state = self._finish(routine_id, token, record)
            except Exception as exc:  # noqa: BLE001 - never leave the routine marked running
                self._warn("Could not record routine %s's run: %s", routine_id, exc)
            finally:
                self._release(routine_id, token)
        if crashed and self.on_crash is not None:
            try:
                self.on_crash(routine, record)
            except Exception as exc:  # noqa: BLE001 - the run is already recorded
                self._warn("Could not post routine %s's failure: %s", routine_id, exc)
        try:
            self._drain(self.clock())
        except Exception as exc:  # noqa: BLE001 - the next tick drains again
            self._warn("Could not start waiting routines: %s", exc)
        if not self._exists(routine_id):
            # Removed while it ran: don't leave its state behind.
            self.states.remove(routine_id)
            return
        if state is None:
            return
        if record.get("rate_limited") and trigger != "retry":
            retry_at = self.clock() + routines_lib.RETRY_AFTER
            self.states.update(routine_id, lambda s: s.update(retry={"at": _iso(retry_at),
                                                                      "scheduled_for": _iso(slot)}))
            return
        failures = state["consecutive_failures"]
        if isinstance(record.get("pause_reason"), str):
            self._pause(routine, record["pause_reason"])
        elif failures >= routines_lib.AUTO_PAUSE_FAILURES:
            self._pause(routine, f"Paused after {failures} failed runs in a row.")

    def _pause(self, routine: dict[str, Any], reason: str) -> None:
        """Pause a routine that can't run, unless someone already paused it, and say so."""
        try:
            if not self.store.get(routine["id"])["enabled"]:
                return
            paused = self.store.set_enabled(routine["id"], False, reason=reason)
        except Exception as exc:  # noqa: BLE001 - the next failure tries again
            self._warn("Could not pause routine %s: %s", routine["id"], exc)
            return
        # Once resumed, it gets a full set of tries again; history keeps the failures.
        self.states.update(routine["id"], lambda state: state.update(consecutive_failures=0))
        if self.on_auto_pause is not None:
            try:
                self.on_auto_pause(paused)
            except Exception as exc:  # noqa: BLE001 - the pause itself already happened
                self._warn("Could not announce the paused routine %s: %s", routine["id"], exc)

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
