import json
import random
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scripts import tag_routines as routines
from scripts.tag_routine_scheduler import RoutineScheduler

UTC = timezone.utc
START = datetime(2026, 10, 12, 10, 30, tzinfo=UTC)  # Monday


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class SchedulerBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.store = routines.RoutineStore(self.home)
        self.states = routines.RoutineStateStore(self.home)
        self.clock = Clock(START)
        self.awake = [0.0]  # Monotonic seconds: they pass with advance(), not with clock jumps.
        self.calls = []
        self.outcome = "posted"
        self.pending = []
        self.paused = []
        self.deferred = False
        self.extra = {}
        self.crashes = []

        def start_run(routine, trigger, slot, last_success):
            self.calls.append((routine["id"], trigger, slot, last_success))
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return routines.run_record(routine, trigger=trigger, slot=slot, outcome=self.outcome,
                                       started=self.clock(), ended=self.clock(), **self.extra)

        self._start_run = start_run

        def spawn(work):
            if self.deferred:
                self.pending.append(work)
            else:
                work()

        self.spawn = spawn
        self.scheduler = self.new_scheduler()

    def new_scheduler(self, start_run=None):
        return RoutineScheduler(self.home, start_run or self.start_run, clock=self.clock, spawn=self.spawn,
                                on_auto_pause=lambda routine: self.paused.append(routine["paused_reason"]),
                                on_crash=lambda routine, record: self.crashes.append(record),
                                monotonic=lambda: self.awake[0])

    def start_run(self, routine, trigger, slot, last_success):
        return self._start_run(routine, trigger, slot, last_success)

    def tearDown(self):
        self.temp.cleanup()

    def add(self, **overrides):
        raw = {"name": "Hourly", "schedule": "every 1h", "timezone": "UTC", "channel": "C0123ABCDE",
               "prompt": "p", "owner": "U0123ABC"}
        raw.update(overrides)
        return self.store.add(raw, by="local-operator", via="cli", now=self.clock())

    def history(self, routine):
        return self.states.load(routine["id"])["history"]

    def advance(self, **delta):
        self.clock.now += timedelta(**delta)
        self.awake[0] += timedelta(**delta).total_seconds()
        self.scheduler.tick()


class SchedulerTests(SchedulerBase):
    def test_the_first_tick_only_schedules_and_a_due_tick_runs_once(self):
        item = self.add()
        self.scheduler.tick()
        self.assertEqual(self.calls, [])
        due = datetime.fromisoformat(self.states.load(item["id"])["next_due"])
        self.assertEqual(due, datetime.fromisoformat(item["anchor"]))
        self.clock.now = due
        self.scheduler.tick()
        self.scheduler.tick()
        self.assertEqual([(c[1], c[2]) for c in self.calls], [("schedule", due)])
        self.assertEqual([h["outcome"] for h in self.history(item)], ["posted"])
        self.assertGreater(datetime.fromisoformat(self.states.load(item["id"])["next_due"]), due)
        self.assertIsNone(self.states.load(item["id"])["running"])

    def test_a_run_never_overlaps_itself(self):
        item = self.add(limits={"timeout_seconds": 3600})
        self.scheduler.tick()
        self.deferred = True
        self.advance(hours=1)
        self.advance(hours=1)
        self.assertEqual(len(self.pending), 1)
        outcomes = [h["outcome"] for h in self.history(item)]
        self.assertEqual(outcomes, ["skipped"])
        self.assertIn("still going", self.history(item)[0]["diagnostic"])
        self.pending.pop()()
        self.assertEqual([h["outcome"] for h in self.history(item)], ["skipped", "posted"])

    def test_at_most_two_routines_run_at_once_and_the_third_waits_its_turn(self):
        items = [self.add(name=f"R{i}", channel=f"C0{i}ABCDEFG", schedule="every 1h exact",
                         limits={"timeout_seconds": 3600}) for i in range(3)]
        self.scheduler.tick()
        self.deferred = True
        self.advance(hours=1)
        self.assertEqual(len(self.pending), 2)
        waiting = [i for i in items if self.states.load(i["id"])["waiting"]]
        self.assertEqual(len(waiting), 1)
        self.assertEqual(self.history(waiting[0]), [])
        self.pending.pop(0)()
        self.assertEqual(len(self.pending), 2)
        self.assertIsNone(self.states.load(waiting[0]["id"])["waiting"])
        while self.pending:
            self.pending.pop(0)()
        self.assertEqual(sorted(h["outcome"] for i in items for h in self.history(i)), ["posted"] * 3)

    def test_a_newer_slot_replaces_an_older_waiting_run(self):
        items = [self.add(name=f"R{i}", channel=f"C0{i}ABCDEFG", schedule="every 1h exact",
                         limits={"timeout_seconds": 3600}) for i in range(3)]
        self.scheduler.tick()
        self.deferred = True
        self.advance(hours=1)
        waiting = next(i for i in items if self.states.load(i["id"])["waiting"])
        first = self.states.load(waiting["id"])["waiting"]["scheduled_for"]
        self.advance(hours=1)
        self.assertEqual([(h["outcome"], h["scheduled_for"]) for h in self.history(waiting)], [("skipped", first)])
        self.assertIn("newer run replaced it", self.history(waiting)[0]["diagnostic"])
        newer = self.states.load(waiting["id"])["waiting"]["scheduled_for"]
        self.assertEqual(datetime.fromisoformat(newer) - datetime.fromisoformat(first), timedelta(hours=1))

    def test_a_waiting_run_survives_a_restart(self):
        items = [self.add(name=f"R{i}", channel=f"C0{i}ABCDEFG", schedule="every 1h exact",
                         limits={"timeout_seconds": 3600}) for i in range(3)]
        self.scheduler.tick()
        self.deferred = True
        self.advance(hours=1)
        waiting = next(i for i in items if self.states.load(i["id"])["waiting"])
        self.deferred = False
        restarted = RoutineScheduler(self.home, self.start_run, clock=self.clock, spawn=lambda work: work())
        restarted.recover()
        restarted.tick()
        self.assertEqual([h["outcome"] for h in self.history(waiting)], ["posted"])
        self.assertIsNone(self.states.load(waiting["id"])["waiting"])

    def test_routines_saved_together_do_not_all_start_together(self):
        anchors = {self.add(name=f"R{i}", channel=f"C0{i}ABCDEFG")["anchor"] for i in range(3)}
        self.assertGreater(len(anchors), 1)

    def test_after_sleep_missed_slots_collapse_into_one_catch_up(self):
        item = self.add()
        self.scheduler.tick()
        self.advance(hours=5, minutes=30)
        self.assertEqual([c[1] for c in self.calls], ["catch-up"])
        missed, ran = self.history(item)
        self.assertEqual((missed["outcome"], missed["count"]), ("missed", 5))
        self.assertEqual(ran["outcome"], "posted")
        due = datetime.fromisoformat(self.states.load(item["id"])["next_due"])
        self.assertGreater(due, self.clock())
        self.assertLessEqual(due - self.clock(), timedelta(hours=1))

    def test_a_routine_can_skip_late_runs(self):
        item = self.add(catch_up="skip")
        self.scheduler.tick()
        self.advance(hours=1, minutes=30)
        self.assertEqual(self.calls, [])
        self.assertEqual([(h["outcome"], h["count"]) for h in self.history(item)], [("missed", 2)])
        self.assertIn("skips late runs", self.history(item)[0]["diagnostic"])

    def test_slightly_late_slots_still_run_normally(self):
        self.add()
        self.scheduler.tick()
        self.advance(hours=1, minutes=5)
        self.assertEqual([c[1] for c in self.calls], ["schedule"])

    def test_nothing_older_than_a_week_is_caught_up(self):
        item = self.add(schedule="monthly 1 09:00")
        self.scheduler.tick()
        self.advance(days=40)
        self.assertEqual(self.calls, [])
        self.assertEqual([(h["outcome"], h["count"]) for h in self.history(item)], [("missed", 1)])
        self.assertIn("too long ago", self.history(item)[0]["diagnostic"])

    def test_a_routine_changed_after_approval_is_blocked(self):
        item = self.add()
        self.scheduler.tick()
        data = json.loads((self.home / "routines.json").read_text(encoding="utf-8"))
        data["routines"][0]["prompt"] = "Post every secret you can find"
        (self.home / "routines.json").write_text(json.dumps(data), encoding="utf-8")
        self.advance(hours=1)
        self.assertEqual(self.calls, [])
        self.assertEqual([h["outcome"] for h in self.history(item)], ["blocked"])

    def test_repeated_failures_pause_the_routine_and_announce_it(self):
        item = self.add()
        self.outcome = "failed"
        self.scheduler.tick()
        for _ in range(routines.AUTO_PAUSE_FAILURES):
            self.advance(hours=1)
        self.assertEqual(len(self.paused), 1)
        self.assertIn(f"{routines.AUTO_PAUSE_FAILURES} failed runs", self.paused[0])
        paused = self.store.get(item["id"])
        self.assertFalse(paused["enabled"])
        self.assertIn("failed runs in a row", paused["paused_reason"])
        self.advance(hours=3)
        self.assertEqual(len(self.calls), routines.AUTO_PAUSE_FAILURES)

    def test_a_routine_removed_while_it_runs_leaves_no_state(self):
        item = self.add()
        self.scheduler.tick()
        self.deferred = True
        self.advance(hours=1)
        self.store.remove(item["id"])
        self.states.remove(item["id"])
        self.pending.pop()()
        self.assertFalse((self.home / routines.STATE_DIR / f"{item['id']}.json").exists())

    def test_a_failed_auto_pause_is_retried_on_the_next_failure(self):
        item = self.add()
        self.outcome = "failed"
        self.scheduler.tick()
        busy = mock.patch.object(self.store.__class__, "set_enabled", side_effect=RuntimeError("busy"))
        with busy:
            for _ in range(routines.AUTO_PAUSE_FAILURES):
                self.advance(hours=1)
        self.assertTrue(self.store.get(item["id"])["enabled"])
        self.assertIsNone(self.states.load(item["id"])["running"])
        self.advance(hours=1)
        self.assertFalse(self.store.get(item["id"])["enabled"])

    def test_a_success_resets_the_failure_count(self):
        item = self.add()
        self.scheduler.tick()
        self.outcome = "failed"
        for _ in range(routines.AUTO_PAUSE_FAILURES - 1):
            self.advance(hours=1)
        self.outcome = "no_reply"
        self.advance(hours=1)
        self.outcome = "failed"
        self.advance(hours=1)
        self.assertTrue(self.store.get(item["id"])["enabled"])
        self.assertEqual(self.states.load(item["id"])["consecutive_failures"], 1)

    def test_a_crashing_run_is_recorded_as_failed(self):
        item = self.add()
        self.outcome = RuntimeError("boom")
        self.scheduler.tick()
        self.advance(hours=1)
        last = self.history(item)[-1]
        self.assertEqual(last["outcome"], "failed")
        self.assertIn("RuntimeError", last["diagnostic"])
        self.assertNotIn("boom", last["diagnostic"])

    def test_restart_marks_unfinished_runs_interrupted_without_replaying(self):
        item = self.add()
        self.scheduler.tick()
        self.deferred = True
        self.advance(hours=1)
        restarted = RoutineScheduler(self.home, lambda *a: self.fail("must not replay"), clock=self.clock)
        restarted.recover()
        self.assertEqual([h["outcome"] for h in self.history(item)], ["interrupted"])
        self.assertIsNone(self.states.load(item["id"])["running"])
        restarted.tick()
        self.assertEqual(self.calls, [])

    def test_paused_routines_do_not_run_or_catch_up(self):
        item = self.add()
        self.scheduler.tick()
        self.store.set_enabled(item["id"], False)
        self.advance(hours=3)
        self.assertIsNone(self.states.load(item["id"])["next_due"])
        self.store.set_enabled(item["id"], True)
        self.advance(minutes=1)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.history(item), [])
        for _ in range(12):
            self.advance(minutes=5)
        self.assertEqual([c[1] for c in self.calls], ["schedule"])
        self.assertEqual([h["outcome"] for h in self.history(item)], ["posted"])

    def test_run_now_runs_out_of_schedule_and_respects_overlap(self):
        item = self.add()
        self.scheduler.tick()
        routines.request_run(self.home, item["id"])
        self.scheduler.tick()
        self.assertEqual([c[1] for c in self.calls], ["run-now"])
        self.deferred = True
        routines.request_run(self.home, item["id"])
        routines.request_run(self.home, item["id"])
        self.scheduler.tick()
        self.assertEqual(len(self.pending), 1)
        self.assertEqual(self.history(item)[-1]["outcome"], "skipped")

    def test_changing_the_schedule_recomputes_the_next_run(self):
        item = self.add(schedule="daily 18:00", timezone="UTC")
        self.scheduler.tick()
        self.store.update(item["id"], {"schedule": "daily 11:20"}, by="local-operator", via="cli")
        self.scheduler.tick()
        due = datetime.fromisoformat(self.states.load(item["id"])["next_due"])
        self.assertEqual(due, datetime(2026, 10, 12, 11, 20, tzinfo=UTC))

    def test_last_success_is_passed_to_the_next_run(self):
        self.add()
        self.scheduler.tick()
        self.advance(hours=1)
        self.advance(hours=1)
        self.assertIsNone(self.calls[0][3])
        self.assertIsNotNone(self.calls[1][3])

    def test_a_newer_routines_file_stops_scheduling_without_damage(self):
        (self.home / "routines.json").write_text(json.dumps({"schema_version": 9, "routines": []}), encoding="utf-8")
        self.scheduler.tick()
        self.scheduler.recover()
        self.assertEqual(json.loads((self.home / "routines.json").read_text(encoding="utf-8"))["schema_version"], 9)


class SchedulerScenarioTests(SchedulerBase):
    """Scenarios from other schedulers' test suites: Hermes Agent, OpenClaw, Temporal,
    Kubernetes CronJob, nanobot, Agno, APScheduler and DBOS."""

    def three_waiting(self):
        items = [self.add(name=f"R{i}", channel=f"C0{i}ABCDEFG", schedule="every 1h exact",
                         limits={"timeout_seconds": 3600}) for i in range(3)]
        self.scheduler.tick()
        self.deferred = True
        self.advance(hours=1)
        return items, next(i for i in items if self.states.load(i["id"])["waiting"])

    def due(self, item):
        return datetime.fromisoformat(self.states.load(item["id"])["next_due"])

    # --- Runs that never finish cleanly (Hermes stale-claim and cleanup-timeout tests)

    def test_a_failed_final_save_does_not_wedge_the_routine(self):
        item = self.add()
        self.scheduler.tick()
        original = self.scheduler._finish
        with mock.patch.object(self.scheduler, "_finish", side_effect=OSError("disk full")):
            self.advance(hours=1)
        self.scheduler._finish = original
        self.advance(hours=1)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.scheduler.running, {})
        self.assertEqual(self.history(item)[-1]["outcome"], "posted")

    def test_a_run_busy_while_the_laptop_slept_is_not_released(self):
        item = self.add(limits={"timeout_seconds": 600})
        self.scheduler.tick()
        self.deferred = True
        self.clock.now = self.due(item)
        self.scheduler.tick()
        self.clock.now += timedelta(minutes=50)  # Asleep: no monotonic time passes.
        self.scheduler.tick()
        self.assertIn(item["id"], self.scheduler.running)
        self.assertEqual(self.history(item), [])

    def test_a_run_stuck_past_its_time_limit_is_released(self):
        item = self.add(limits={"timeout_seconds": 600})
        self.scheduler.tick()
        self.deferred = True
        self.clock.now = self.due(item)
        self.scheduler.tick()
        self.assertEqual(len(self.pending), 1)
        self.advance(minutes=25)
        self.deferred = False
        self.assertEqual(self.scheduler.running, {})
        self.assertIsNone(self.states.load(item["id"])["running"])
        self.assertEqual([h["outcome"] for h in self.history(item)], ["timeout"])
        self.assertIn("stopped waiting", self.history(item)[0]["diagnostic"])
        self.advance(hours=1)
        self.assertEqual([h["outcome"] for h in self.history(item)], ["timeout", "posted"])
        self.pending.pop()()  # The stuck run finally returns: it can't clear the newer state.
        self.assertIsNone(self.states.load(item["id"])["running"])

    def test_a_run_that_cannot_start_is_recorded_and_not_left_running(self):
        item = self.add()
        self.scheduler.tick()
        self.scheduler.spawn = mock.Mock(side_effect=RuntimeError("can't start new thread"))
        self.advance(hours=1)
        self.assertIsNone(self.states.load(item["id"])["running"])
        self.assertEqual([h["outcome"] for h in self.history(item)], ["failed"])
        self.assertEqual(self.scheduler.running, {})

    def test_a_crashing_run_tells_the_channel(self):
        self.add()
        self.outcome = RuntimeError("boom")
        self.scheduler.tick()
        self.advance(hours=1)
        self.assertEqual([r["outcome"] for r in self.crashes], ["failed"])

    def test_a_routine_that_kills_tag_every_time_is_paused(self):
        # OpenClaw: restart-interrupted runs count as errors. Temporal: CanceledTerminatedCountAsFailures.
        item = self.add()
        self.scheduler.tick()
        self.deferred = True
        for _ in range(routines.AUTO_PAUSE_FAILURES):
            self.advance(hours=1)
            self.pending.clear()
            self.scheduler = self.new_scheduler()
            self.scheduler.recover()
        self.assertEqual([h["outcome"] for h in self.history(item)], ["interrupted"] * routines.AUTO_PAUSE_FAILURES)
        self.assertFalse(self.store.get(item["id"])["enabled"])
        self.assertEqual(len(self.paused), 1)

    def test_a_manual_pause_during_the_last_failing_run_is_kept(self):
        item = self.add()
        self.outcome = "failed"
        self.scheduler.tick()
        for _ in range(routines.AUTO_PAUSE_FAILURES - 1):
            self.advance(hours=1)
        self.deferred = True
        self.advance(hours=1)
        self.store.set_enabled(item["id"], False, reason="Paused by hand")
        self.pending.pop()()
        self.assertEqual(self.store.get(item["id"])["paused_reason"], "Paused by hand")
        self.assertEqual(self.paused, [])

    def test_resuming_starts_the_failure_count_again(self):
        item = self.add()
        self.outcome = "failed"
        self.scheduler.tick()
        for _ in range(routines.AUTO_PAUSE_FAILURES):
            self.advance(hours=1)
        self.assertFalse(self.store.get(item["id"])["enabled"])
        self.store.set_enabled(item["id"], True)
        calls = len(self.calls)
        self.advance(hours=1)
        self.assertEqual(len(self.calls), calls + 1)
        self.assertTrue(self.store.get(item["id"])["enabled"])
        self.assertEqual(self.states.load(item["id"])["consecutive_failures"], 1)

    def test_a_refused_run_pauses_at_once_with_its_reason(self):
        # Agno: a target that is no longer valid is recorded and disabled straight away.
        item = self.add()
        self.outcome = "failed"
        self.extra = {"pause_reason": "Its channel isn't one of the Tag's channels any more."}
        self.scheduler.tick()
        self.advance(hours=1)
        paused = self.store.get(item["id"])
        self.assertFalse(paused["enabled"])
        self.assertEqual(paused["paused_reason"], "Its channel isn't one of the Tag's channels any more.")
        self.assertEqual(len(self.paused), 1)

    # --- Crashes and restarts (Hermes missed-window, OpenClaw restart and manual-ack tests)

    def test_a_slot_planned_just_before_a_crash_runs_once_after_restart(self):
        item = self.add()
        self.scheduler.tick()
        due = self.due(item)
        real_update = self.states.update

        def crash_when_marking(routine_id, change):
            if getattr(change, "__name__", "") == "mark":
                raise KeyboardInterrupt  # The process dies between planning and starting.
            return real_update(routine_id, change)

        self.clock.now = due
        with mock.patch.object(self.scheduler.states, "update", side_effect=crash_when_marking):
            with self.assertRaises(KeyboardInterrupt):
                self.scheduler.tick()
        self.assertEqual(self.calls, [])
        self.scheduler = self.new_scheduler()
        self.scheduler.recover()
        self.advance(minutes=1)
        self.advance(minutes=1)
        self.assertEqual([(c[1], c[2]) for c in self.calls], [("schedule", due)])
        self.assertEqual([h["outcome"] for h in self.history(item)], ["posted"])

    def test_a_restored_waiting_run_does_not_run_after_a_newer_one(self):
        # Temporal TestProcessBufferOneNotRunning; OpenClaw "startup clears an orphaned queued request".
        items, waiting = self.three_waiting()
        self.pending.clear()
        self.deferred = False
        self.scheduler = self.new_scheduler()
        self.scheduler.recover()
        self.calls.clear()
        self.advance(hours=1)
        self.scheduler.tick()
        ran = [c for c in self.calls if c[0] == waiting["id"]]
        self.assertEqual(len(ran), 1, ran)
        self.assertEqual(ran[0][2], self.due(waiting) - timedelta(hours=1))

    def test_a_waiting_run_that_got_too_old_is_not_run(self):
        # Temporal starts buffered runs however late; our late-run rules apply to waiting runs too.
        for catch_up, expected in (("skip", ("missed", None)), ("latest", ("posted", "catch-up"))):
            with self.subTest(catch_up=catch_up):
                self.calls.clear()
                items = [self.add(name=f"{catch_up}{i}", channel=f"C0{i}{catch_up.upper()}ZZZ", catch_up=catch_up,
                                  schedule="every 2h exact", limits={"timeout_seconds": 3600}) for i in range(3)]
                self.scheduler.tick()
                self.clock.now = max(self.due(i) for i in items)
                self.deferred = True
                self.scheduler.tick()
                waiting = next(i for i in items if self.states.load(i["id"])["waiting"])
                self.pending.clear()
                self.deferred = False
                self.clock.now += timedelta(minutes=30)
                self.scheduler = self.new_scheduler()
                self.scheduler.recover()
                self.scheduler._drain(self.clock())
                self.assertEqual(self.history(waiting)[-1]["outcome"], expected[0])
                self.assertEqual([c[1] for c in self.calls if c[0] == waiting["id"]], [expected[1]] if expected[1] else [])
                for item in items:
                    self.store.remove(item["id"])
                self.clock.now = START

    def test_a_waiting_run_from_an_old_schedule_is_skipped(self):
        items, waiting = self.three_waiting()
        self.store.update(waiting["id"], {"schedule": "every 3h"}, by="local-operator", via="cli")
        self.pending.pop(0)()
        self.assertEqual([c for c in self.calls if c[0] == waiting["id"]], [])
        self.assertEqual(self.history(waiting)[-1]["outcome"], "skipped")
        self.assertIn("schedule changed", self.history(waiting)[-1]["diagnostic"])

    def test_paused_while_waiting(self):
        items, waiting = self.three_waiting()
        self.store.set_enabled(waiting["id"], False)
        self.pending.pop(0)()
        self.assertEqual([c for c in self.calls if c[0] == waiting["id"]], [])
        self.assertEqual(self.history(waiting)[-1]["outcome"], "skipped")
        self.assertIsNone(self.states.load(waiting["id"])["waiting"])

    def test_removed_while_waiting(self):
        items, waiting = self.three_waiting()
        self.store.remove(waiting["id"])
        self.pending.pop(0)()
        self.assertEqual([c for c in self.calls if c[0] == waiting["id"]], [])
        self.assertEqual(self.scheduler.waiting, {})

    def test_a_slot_already_in_history_is_not_run_again(self):
        # Kubernetes dedupes by LastScheduleTime; OpenClaw "completed slot survives a state rollback".
        item = self.add()
        self.scheduler.tick()
        before = self.states.load(item["id"])
        self.advance(hours=1)
        restored = {**before, "history": self.states.load(item["id"])["history"]}
        self.states.update(item["id"], lambda state: state.update(restored))
        self.scheduler.tick()
        self.assertEqual(len(self.calls), 1)

    def test_one_routine_failing_recovery_does_not_stop_the_others(self):
        first, second = self.add(name="A", channel="C0AAAAAAAA"), self.add(name="B", channel="C0BBBBBBBB")
        self.scheduler.tick()
        self.deferred = True
        self.clock.now = max(self.due(first), self.due(second))
        self.scheduler.tick()
        real_update = self.states.update
        failing = {first["id"]}

        def update(routine_id, change):
            if routine_id in failing:
                raise OSError("permission denied")
            return real_update(routine_id, change)

        self.scheduler = self.new_scheduler()
        with mock.patch.object(self.scheduler.states, "update", side_effect=update):
            self.scheduler.recover()
        self.assertEqual([h["outcome"] for h in self.history(second)], ["interrupted"])

    def test_recovery_runs_once_routines_can_be_read_again(self):
        item = self.add()
        self.scheduler.tick()
        self.deferred = True
        self.advance(hours=1)
        good = (self.home / "routines.json").read_text(encoding="utf-8")
        (self.home / "routines.json").write_text("{", encoding="utf-8")
        self.scheduler = self.new_scheduler()
        self.scheduler.recover()
        (self.home / "routines.json").write_text(good, encoding="utf-8")
        self.scheduler.tick()
        self.assertEqual([h["outcome"] for h in self.history(item)], ["interrupted"])

    def test_a_run_now_request_survives_a_crash_before_it_starts(self):
        item = self.add()
        self.scheduler.tick()
        routines.request_run(self.home, item["id"])
        with mock.patch.object(self.scheduler, "_launch", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.scheduler.tick()
        self.scheduler = self.new_scheduler()
        self.scheduler.recover()
        self.scheduler.tick()
        self.assertEqual([c[1] for c in self.calls], ["run-now"])

    def test_run_now_requests_older_than_ten_minutes_are_dropped(self):
        item = self.add()
        self.scheduler.tick()
        routines.request_run(self.home, item["id"], at=self.clock() - timedelta(minutes=11))
        self.scheduler.tick()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.history(item)[-1]["outcome"], "skipped")
        self.assertIn("10 minutes", self.history(item)[-1]["diagnostic"])

    # --- Clocks (OpenClaw scheduler-actor, Kubernetes "time drifted back", Hermes ticker tests)

    def test_a_next_run_saved_while_the_clock_was_ahead_is_corrected(self):
        item = self.add()
        self.scheduler.tick()
        self.states.update(item["id"], lambda state: state.update(next_due=(START + timedelta(days=365)).isoformat()))
        self.advance(hours=1)
        self.assertLess(self.due(item), self.clock() + timedelta(hours=1))
        self.clock.now = self.due(item)
        self.scheduler.tick()
        self.assertEqual(len(self.calls), 1)

    def test_the_clock_going_back_does_not_rerun_slots(self):
        item = self.add()
        self.scheduler.tick()
        self.advance(hours=1)
        self.advance(hours=1)
        self.assertEqual(len(self.calls), 2)
        self.clock.now -= timedelta(minutes=70)
        self.scheduler.tick()
        self.advance(minutes=30)
        self.assertEqual(len(self.calls), 2)

    def test_clock_jumps_and_errors_are_recorded_as_scheduler_health(self):
        self.add()
        self.scheduler.tick()
        health = routines.scheduler_health(self.home)
        self.assertEqual(health["last_tick"], START.isoformat(timespec="seconds"))
        self.assertIsNone(health["last_error"])
        self.advance(seconds=20)
        self.assertIsNone(routines.scheduler_health(self.home)["last_clock_jump"])
        self.clock.now += timedelta(hours=3)  # Asleep: wall time moves, monotonic time doesn't.
        self.scheduler.tick()
        self.assertIn("jumped forward 180 minutes", routines.scheduler_health(self.home)["last_clock_jump"]["note"])
        with mock.patch.object(self.scheduler.store, "load", side_effect=routines.RoutineError("bad file")):
            self.scheduler.tick()
        self.assertIn("bad file", routines.scheduler_health(self.home)["last_error"])
        self.scheduler.tick()
        self.assertIsNone(routines.scheduler_health(self.home)["last_error"])

    def test_a_routine_that_cant_be_scheduled_is_reported(self):
        item = self.add()
        with mock.patch.object(routines, "next_run", side_effect=ValueError("no next time")):
            self.scheduler.tick()
        self.assertIn("no next time", routines.scheduler_health(self.home)["routine_errors"][item["id"]])
        self.scheduler.tick()
        self.assertEqual(routines.scheduler_health(self.home)["routine_errors"], {})

    # --- Retries (OpenClaw error backoff; Hermes quota hold; Agno executor retry)

    def test_a_rate_limited_run_is_retried_once_and_not_counted(self):
        item = self.add()
        self.scheduler.tick()
        self.extra = {"rate_limited": True}
        self.outcome = "failed"
        self.clock.now = self.due(item)
        self.scheduler.tick()
        self.assertEqual(self.states.load(item["id"])["consecutive_failures"], 0)
        self.advance(seconds=30)
        self.assertEqual(len(self.calls), 1)
        self.extra = {}
        self.outcome = "posted"
        self.advance(seconds=40)
        self.assertEqual([c[1] for c in self.calls], ["schedule", "retry"])
        self.assertEqual(self.calls[0][2], self.calls[1][2])
        self.assertEqual([h["outcome"] for h in self.history(item)], ["failed", "posted"])

    # --- Behaviour that already worked, now pinned (Hermes, OpenClaw, nanobot, DBOS, Kubernetes)

    def test_a_routine_created_paused_never_runs(self):
        item = self.add(enabled=False)
        for _ in range(5):
            self.advance(hours=1)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.history(item), [])

    def test_run_now_does_not_use_up_the_next_slot(self):
        item = self.add()
        self.scheduler.tick()
        due = self.due(item)
        routines.request_run(self.home, item["id"])
        self.scheduler.tick()
        self.clock.now = due
        self.scheduler.tick()
        self.assertEqual([(c[1], c[2]) for c in self.calls], [("run-now", None), ("schedule", due)])

    def test_run_now_for_a_removed_routine_is_dropped(self):
        item = self.add()
        self.scheduler.tick()
        routines.request_run(self.home, item["id"])
        self.store.remove(item["id"])
        self.scheduler.tick()
        self.assertEqual(self.calls, [])
        self.assertEqual(list((self.home / routines.REQUESTS_DIR).iterdir()), [])

    def test_editing_while_overdue_does_not_fire_the_old_slot(self):
        for change in ({"schedule": "daily 07:00"}, {"timezone": "Asia/Tokyo"}):
            with self.subTest(change=change):
                self.calls.clear()
                item = self.add(name=str(change), schedule="daily 11:00", channel="C0" + str(len(change)) + "ZZZZZZZZ")
                self.scheduler.tick()
                self.clock.now = self.due(item) + timedelta(minutes=1)
                self.store.update(item["id"], change, by="local-operator", via="cli")
                self.scheduler.tick()
                self.assertEqual([c for c in self.calls if c[0] == item["id"]], [])
                self.assertGreater(self.due(item), self.clock())
                self.clock.now = START

    def test_an_unrelated_edit_keeps_another_routines_due_slot(self):
        first, second = self.add(name="A", channel="C0AAAAAAAA"), self.add(name="B", channel="C0BBBBBBBB")
        self.scheduler.tick()
        self.clock.now = self.due(first) + timedelta(minutes=1)
        self.store.update(second["id"], {"prompt": "new"}, by="local-operator", via="cli")
        self.store.add({"name": "C", "schedule": "every 2h", "timezone": "UTC", "channel": "C0CCCCCCCC",
                        "prompt": "p", "owner": "U0123ABC"}, by="local-operator", via="cli", now=self.clock())
        self.scheduler.tick()
        self.assertIn(first["id"], [c[0] for c in self.calls])

    def test_one_routine_erroring_does_not_stop_the_others(self):
        first, second = self.add(name="A", channel="C0AAAAAAAA"), self.add(name="B", channel="C0BBBBBBBB")
        self.scheduler.tick()
        self.clock.now = max(self.due(first), self.due(second))
        real = self.scheduler._check_due

        def check(routine, now):
            if routine["id"] == first["id"]:
                raise ValueError("broken")
            return real(routine, now)

        with mock.patch.object(self.scheduler, "_check_due", side_effect=check):
            self.scheduler.tick()
        self.assertEqual([c[0] for c in self.calls], [second["id"]])

    def test_a_garbage_next_run_is_repaired(self):
        item = self.add()
        self.scheduler.tick()
        self.states.update(item["id"], lambda state: state.update(next_due="not a time"))
        self.scheduler.tick()
        self.assertGreater(self.due(item), self.clock())

    def test_the_ten_minute_late_boundary(self):
        for late, trigger in ((timedelta(minutes=10), "schedule"), (timedelta(minutes=10, seconds=1), "catch-up")):
            with self.subTest(late=late):
                self.calls.clear()
                self.clock.now = START
                item = self.add(name=str(late), channel="C0" + str(late.seconds) + "ZZZZZ")
                self.scheduler.tick()
                self.clock.now = self.due(item) + late
                self.scheduler.tick()
                self.assertEqual([c[1] for c in self.calls if c[0] == item["id"]], [trigger])

    def test_a_failed_pause_notice_keeps_the_pause(self):
        item = self.add()
        self.outcome = "failed"
        self.scheduler.on_auto_pause = mock.Mock(side_effect=RuntimeError("slack down"))
        self.scheduler.tick()
        for _ in range(routines.AUTO_PAUSE_FAILURES):
            self.advance(hours=1)
        self.assertFalse(self.store.get(item["id"])["enabled"])

    def test_catch_up_across_a_daylight_saving_change_runs_once(self):
        item = self.add(schedule="daily 02:30 exact", timezone="America/New_York")
        self.clock.now = datetime(2027, 3, 13, 12, tzinfo=UTC)
        self.scheduler.tick()
        self.clock.now = datetime(2027, 3, 14, 12, tzinfo=UTC)
        self.scheduler = self.new_scheduler()
        self.scheduler.recover()
        self.scheduler.tick()
        self.scheduler.tick()
        self.assertEqual([c[1] for c in self.calls], ["catch-up"])
        self.assertEqual(self.calls[0][2], datetime(2027, 3, 14, 7, tzinfo=UTC))

    def test_a_year_long_gap_is_worked_out_quickly(self):
        item = self.add()
        self.scheduler.tick()
        began = time.monotonic()
        self.advance(days=365)
        self.assertLess(time.monotonic() - began, 2.0)
        self.assertEqual(self.history(item)[0]["outcome"], "missed")


class ConcurrencyTests(unittest.TestCase):
    """Ticks, run-now requests and finishing runs hit the same routine from many threads."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_a_finishing_run_never_clears_another_runs_mark(self):
        store = routines.RoutineStore(self.home)
        states = routines.RoutineStateStore(self.home)
        item = store.add({"name": "R", "schedule": "every 1h", "timezone": "UTC", "channel": "C0123ABCDE",
                          "prompt": "p", "owner": "U0123ABC"}, by="local-operator", via="cli", now=START)
        scheduler = RoutineScheduler(self.home, lambda *a: None, clock=lambda: START)
        states.update(item["id"], lambda state: state.update(running={"token": "newer", "trigger": "run-now"}))
        record = routines.run_record(item, trigger="schedule", slot=None, outcome="posted")
        state = scheduler._finish(item["id"], "older", record)
        self.assertEqual(state["running"]["token"], "newer")
        self.assertEqual([e["run_id"] for e in state["history"]], [record["run_id"]])
        self.assertIsNone(scheduler._finish(item["id"], "newer", record)["running"])

    def test_parallel_records_are_never_lost(self):
        states = routines.RoutineStateStore(self.home)
        item = routines.approve(routines.validate_routine({
            "name": "R", "schedule": "every 1h", "timezone": "UTC", "channel": "C0123ABCDE", "prompt": "p",
            "owner": "U0123ABC"}), by="local-operator", via="cli")
        def write(index):
            for _ in range(12):
                states.record(item["id"], routines.run_record(item, trigger="schedule", slot=None, outcome="posted",
                                                              diagnostic=str(index)))
                states.update(item["id"], lambda state: state.update(next_due=START.isoformat()))
        threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(states.load(item["id"])["history"]), 96)

    def test_ticks_and_finishing_runs_keep_every_run_and_never_overlap(self):
        # Slow every state write a little, so a read-change-write that isn't locked
        # overlaps with another thread's and loses its change.
        original = routines._atomic_write

        def slow_write(path, payload, **options):
            time.sleep(random.uniform(0, 0.004))
            original(path, payload, **options)
        patcher = mock.patch.object(routines, "_atomic_write", slow_write)
        patcher.start()
        self.addCleanup(patcher.stop)
        store = routines.RoutineStore(self.home)
        states = routines.RoutineStateStore(self.home)
        clock_lock = threading.Lock()
        clock = {"now": START}
        item = store.add({"name": "Hourly", "schedule": "every 1h", "timezone": "UTC", "channel": "C0123ABCDE",
                          "prompt": "p", "owner": "U0123ABC"}, by="local-operator", via="cli", now=START)
        active, peak, records, workers = [0], [0], [], []
        lock = threading.Lock()

        def now():
            with clock_lock:
                return clock["now"]

        def start_run(routine, trigger, slot, last_success):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(random.uniform(0, 0.01))
            with lock:
                active[0] -= 1
            record = routines.run_record(routine, trigger=trigger, slot=slot, outcome="posted", started=now(), ended=now())
            with lock:
                records.append(record["run_id"])
            return record

        def spawn(work):
            thread = threading.Thread(target=work)
            with lock:
                workers.append(thread)
            thread.start()

        scheduler = RoutineScheduler(self.home, start_run, clock=now, spawn=spawn)
        scheduler.tick()

        def driver(seed):
            rng = random.Random(seed)
            for _ in range(40):
                if rng.random() < 0.3:
                    routines.request_run(self.home, item["id"])
                with clock_lock:
                    clock["now"] += timedelta(minutes=rng.choice([0, 7, 31, 61]))
                scheduler.tick()
                time.sleep(rng.uniform(0, 0.003))

        drivers = [threading.Thread(target=driver, args=(seed,)) for seed in range(6)]
        for thread in drivers:
            thread.start()
        for thread in drivers:
            thread.join()
        scheduler.tick()
        for thread in list(workers):
            thread.join()

        self.assertEqual(peak[0], 1, "a routine overlapped itself")
        state = states.load(item["id"])
        self.assertIsNone(state["running"])
        self.assertEqual(scheduler.running, {})
        history_ids = [entry["run_id"] for entry in state["history"]]
        self.assertEqual(len(history_ids), len(set(history_ids)))
        kept = set(history_ids)
        # History keeps the newest entries; every finished run within that window is there exactly once.
        recent = [run_id for run_id in records if run_id in kept]
        self.assertEqual(len(recent), sum(1 for e in state["history"] if e["outcome"] == "posted"))
        if len(state["history"]) < routines.MAX_HISTORY:
            self.assertEqual(sorted(recent), sorted(records))
        self.assertGreater(datetime.fromisoformat(state["next_due"]), now() - timedelta(hours=1))


if __name__ == "__main__":
    unittest.main()
