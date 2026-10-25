import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from scripts import tag_routines as routines

UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")


def routine(**overrides):
    raw = {"name": "PR digest", "schedule": "weekdays 09:00", "timezone": "America/New_York",
           "channel": "C0123ABCDE", "prompt": "Summarise merged PRs.", "owner": "U0123ABC"}
    raw.update(overrides)
    return routines.validate_routine(raw, now=datetime(2026, 10, 13, 12, 0, tzinfo=UTC))


def local(moments, zone):
    return [moment.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z") for moment in moments]


class ScheduleParsingTests(unittest.TestCase):
    def test_accepts_the_small_forms(self):
        self.assertEqual(routines.parse_schedule("every 2h")["every_seconds"], 7200)
        self.assertEqual(routines.parse_schedule("Every 90m")["every_seconds"], 5400)
        self.assertEqual(routines.parse_schedule("every 1d")["every_seconds"], 86400)
        weekly = routines.parse_schedule("fri,mon 17:30,9:00")
        self.assertEqual(weekly["days"], ["mon", "fri"])
        self.assertEqual(weekly["times"], ["09:00", "17:30"])
        self.assertEqual(routines.parse_schedule("weekends 10:00")["days"], ["sat", "sun"])
        self.assertEqual(routines.parse_schedule("daily 09:00")["days"], list(routines.DAY_ORDER))
        self.assertEqual(routines.parse_schedule("monthly last 17:00")["day"], -1)
        self.assertTrue(routines.parse_schedule("daily 09:00 exact")["exact"])
        self.assertEqual(routines.parse_schedule("  weekdays   09:00 ")["text"], "weekdays 09:00")

    def test_rejects_unclear_or_too_frequent_schedules(self):
        for text in ("", "every 30m", "every 59m", "every 2x", "daily", "daily 25:00", "monday 09:00",
                     "monthly 29 09:00", "monthly 0 09:00", "weekdays 09:00,09:30", "every 40d",
                     "daily 9", "0 9 * * 1"):
            with self.subTest(text=text), self.assertRaises(routines.RoutineError):
                routines.parse_schedule(text)

    def test_times_must_be_an_hour_apart_across_midnight_too(self):
        with self.assertRaises(routines.RoutineError):
            routines.parse_schedule("daily 00:10,23:30")
        self.assertEqual(routines.parse_schedule("daily 00:30,23:30")["times"], ["00:30", "23:30"])


class NextRunTests(unittest.TestCase):
    def test_on_the_hour_times_move_a_stable_few_minutes_off(self):
        item = routine()
        offset = routines.stagger_minutes(item["id"], item["schedule"])
        self.assertTrue(1 <= offset <= 5)
        self.assertEqual(offset, routines.stagger_minutes(item["id"], item["schedule"]))
        first = routines.next_run(item, datetime(2026, 10, 16, 12, 0, tzinfo=UTC))  # Friday 08:00 New York
        self.assertEqual(first.astimezone(NEW_YORK).strftime("%a %H:%M"), f"Fri 09:0{offset}")
        following = routines.next_run(item, first)
        self.assertEqual(following.astimezone(NEW_YORK).strftime("%a"), "Mon")

    def test_exact_and_off_the_hour_times_are_kept(self):
        exact = routine(schedule="weekdays 09:00 exact")
        self.assertEqual(routines.next_run(exact, datetime(2026, 10, 16, 12, tzinfo=UTC))
                         .astimezone(NEW_YORK).strftime("%H:%M"), "09:00")
        odd = routine(schedule="weekdays 09:20")
        self.assertEqual(routines.next_run(odd, datetime(2026, 10, 16, 12, tzinfo=UTC))
                         .astimezone(NEW_YORK).strftime("%H:%M"), "09:20")

    def test_a_time_skipped_by_spring_forward_runs_when_the_gap_ends(self):
        item = routine(schedule="daily 02:30")
        times = routines.upcoming(item, datetime(2026, 3, 7, 5, 0, tzinfo=UTC), 3)
        self.assertEqual(local(times, NEW_YORK),
                         ["2026-03-07 02:30 EST", "2026-03-08 03:00 EDT", "2026-03-09 02:30 EDT"])

    def test_a_time_repeated_by_fall_back_runs_once(self):
        item = routine(schedule="daily 01:30")
        times = routines.upcoming(item, datetime(2026, 10, 31, 4, 0, tzinfo=UTC), 3)
        self.assertEqual(local(times, NEW_YORK),
                         ["2026-10-31 01:30 EDT", "2026-11-01 01:30 EDT", "2026-11-02 01:30 EST"])

    def test_london_daylight_saving_keeps_the_wall_clock_time(self):
        item = routine(schedule="daily 09:30", timezone="Europe/London")
        times = routines.upcoming(item, datetime(2026, 10, 24, 12, tzinfo=UTC), 3)
        self.assertEqual(local(times, LONDON),
                         ["2026-10-25 09:30 GMT", "2026-10-26 09:30 GMT", "2026-10-27 09:30 GMT"])
        self.assertEqual(times[0] - datetime(2026, 10, 24, 8, 30, tzinfo=UTC), timedelta(days=1, hours=1))

    def test_monthly_last_day_and_several_times(self):
        item = routine(schedule="monthly last 17:30,09:30", timezone="Europe/London")
        times = routines.upcoming(item, datetime(2026, 2, 1, tzinfo=UTC), 4)
        self.assertEqual(local(times, LONDON), ["2026-02-28 09:30 GMT", "2026-02-28 17:30 GMT",
                                                "2026-03-31 09:30 BST", "2026-03-31 17:30 BST"])

    def test_intervals_count_elapsed_time_from_their_anchor(self):
        item = routines.validate_routine(
            {"name": "Every two hours", "schedule": "every 2h", "timezone": "America/New_York",
             "channel": "C0123ABCDE", "prompt": "p", "owner": "U0123ABC"},
            now=datetime(2026, 3, 8, 0, 0, 30, tzinfo=UTC))
        anchor = datetime.fromisoformat(item["anchor"])
        self.assertTrue(datetime(2026, 3, 8, 0, 1, tzinfo=UTC) <= anchor <= datetime(2026, 3, 8, 0, 5, tzinfo=UTC))
        times = routines.upcoming(item, datetime(2026, 3, 8, 5, 0, tzinfo=UTC), 3)
        self.assertEqual([t - s for s, t in zip(times, times[1:])], [timedelta(hours=2)] * 2)
        self.assertEqual(routines.next_run(item, datetime(2026, 3, 7, tzinfo=UTC)), anchor)
        exact = routines.interval_anchor("r-0123456789", routines.parse_schedule("every 2h exact"),
                                         datetime(2026, 3, 8, 0, 0, 30, tzinfo=UTC))
        self.assertEqual(exact, "2026-03-08T00:01:00+00:00")

    def test_interval_anchor_avoids_the_top_of_the_hour(self):
        anchor = routines.interval_anchor("r-0123456789", routines.parse_schedule("every 1h"),
                                          datetime(2026, 1, 1, 8, 59, 10, tzinfo=UTC))
        self.assertNotEqual(datetime.fromisoformat(anchor).minute, 0)


class DefinitionTests(unittest.TestCase):
    def test_validation_fills_defaults_and_rejects_bad_fields(self):
        item = routine()
        self.assertRegex(item["id"], r"^r-[0-9a-f]{10}$")
        self.assertEqual(item["limits"], routines.DEFAULT_LIMITS)
        self.assertEqual(item["catch_up"], "latest")
        self.assertTrue(item["enabled"])
        for change in ({"channel": "D0123ABCDE"}, {"channel": "#general"}, {"owner": "B0123"},
                       {"timezone": "Mars/Base"}, {"prompt": " "}, {"name": "a\nb"}, {"catch_up": "all"},
                       {"limits": {"max_budget_usd": 100}}, {"limits": {"tools": 1}}, {"surprise": True}):
            with self.subTest(change=change), self.assertRaises(routines.RoutineError):
                routine(**change)

    def test_approval_covers_the_frozen_fields_but_not_pausing(self):
        item = routines.approve(routine(), by="local-operator", via="cli")
        self.assertTrue(routines.approved(item))
        self.assertTrue(routines.approved({**item, "enabled": False}))
        for change in ({"prompt": "Do something else"}, {"channel": "C0999ZZZZZ"},
                       {"limits": {**item["limits"], "max_budget_usd": 2.0}}, {"owner": "U0999ZZZ"},
                       {"schedule": routines.parse_schedule("daily 10:00")}, {"catch_up": "skip"}):
            with self.subTest(change=change):
                self.assertFalse(routines.approved({**item, **change}))
        self.assertFalse(routines.approved(routine()))

    def test_run_prompt_is_self_contained_and_never_approval(self):
        text = routines.run_prompt(routine(), trigger="catch-up",
                                   slot=datetime(2026, 10, 13, 13, 1, tzinfo=UTC), last_success=None)
        self.assertIn("NO_REPLY", text)
        self.assertIn("late", text)
        self.assertIn("first successful run", text)
        self.assertIn("not approval", text)
        self.assertTrue(text.endswith("Summarise merged PRs."))
        self.assertTrue(routines.is_no_reply(" `NO_REPLY` "))
        self.assertTrue(routines.is_no_reply("NO_REPLY."))
        self.assertFalse(routines.is_no_reply("NO_REPLY, but here is a summary"))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.store = routines.RoutineStore(self.home)
        self.raw = {"name": "PR digest", "schedule": "weekdays 09:00", "timezone": "Europe/London",
                    "channel": "C0123ABCDE", "prompt": "p", "owner": "U0123ABC"}

    def tearDown(self):
        self.temp.cleanup()

    def test_a_missing_file_means_no_routines(self):
        self.assertEqual(self.store.load(), [])

    def test_add_approves_and_saves_a_versioned_file(self):
        added = self.store.add(self.raw, by="local-operator", via="cli")
        data = json.loads((self.home / "routines.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["routines"][0]["approval"]["by"], "local-operator")
        self.assertTrue(routines.approved(self.store.get("pr DIGEST")))
        self.assertEqual(self.store.get(added["id"])["created"]["by"], "cli")

    def test_add_ignores_a_supplied_approval(self):
        added = self.store.add({**self.raw, "approval": {"by": "U0SPOOFED", "hash": "x"}},
                               by="local-operator", via="cli")
        self.assertEqual(added["approval"]["by"], "local-operator")

    def test_limits_per_tag_and_per_channel_and_unique_names(self):
        with self.assertRaisesRegex(routines.RoutineError, "already exists"):
            self.store.add(self.raw, by="local-operator", via="cli")
            self.store.add(self.raw, by="local-operator", via="cli")
        for index in range(2):
            self.store.add({**self.raw, "name": f"Extra {index}"}, by="local-operator", via="cli")
        with self.assertRaisesRegex(routines.RoutineError, "channel"):
            self.store.add({**self.raw, "name": "Fourth"}, by="local-operator", via="cli")
        paused = self.store.add({**self.raw, "name": "Paused", "enabled": False}, by="local-operator", via="cli")
        with self.assertRaisesRegex(routines.RoutineError, "channel"):
            self.store.set_enabled(paused["id"], True)
        for index in range(7):
            self.store.add({**self.raw, "name": f"Other {index}", "channel": f"C0{index}OTHERCH"},
                           by="local-operator", via="cli")
        with self.assertRaisesRegex(routines.RoutineError, "at most 10"):
            self.store.add({**self.raw, "name": "Eleventh", "channel": "C0NEWCHANL"}, by="local-operator", via="cli")

    def test_edits_record_a_new_approval_and_pausing_does_not(self):
        added = self.store.add(self.raw, by="local-operator", via="cli")
        paused = self.store.set_enabled(added["id"], False)
        self.assertEqual(paused["approval"], added["approval"])
        edited = self.store.update(added["id"], {"prompt": "Different"}, by="U0123ABC", via="slack")
        self.assertEqual(edited["approval"]["by"], "U0123ABC")
        self.assertTrue(routines.approved(edited))
        unchanged = self.store.update(added["id"], {"prompt": "Different"}, by="local-operator", via="cli")
        self.assertEqual(unchanged["approval"]["by"], "U0123ABC")

    def test_changing_the_schedule_gives_intervals_a_new_anchor(self):
        added = self.store.add({**self.raw, "schedule": "every 2h"}, by="local-operator", via="cli",
                               now=datetime(2026, 1, 1, 8, 30, tzinfo=UTC))
        edited = self.store.update(added["id"], {"schedule": "every 3h"}, by="local-operator", via="cli",
                                   now=datetime(2026, 1, 2, 8, 30, tzinfo=UTC))
        self.assertEqual(edited["anchor"], routines.interval_anchor(
            added["id"], routines.parse_schedule("every 3h"), datetime(2026, 1, 2, 8, 30, tzinfo=UTC)))
        self.assertNotEqual(edited["anchor"], added["anchor"])

    def test_a_newer_schema_is_refused_and_never_overwritten(self):
        newer = {"schema_version": 2, "routines": [{"future": True}]}
        (self.home / "routines.json").write_text(json.dumps(newer), encoding="utf-8")
        with self.assertRaises(routines.RoutineSchemaError):
            self.store.load()
        with self.assertRaises(routines.RoutineSchemaError):
            self.store.add(self.raw, by="local-operator", via="cli")
        self.assertEqual(json.loads((self.home / "routines.json").read_text(encoding="utf-8")), newer)

    def test_one_damaged_routine_does_not_hide_the_others(self):
        good = self.store.add(self.raw, by="local-operator", via="cli")
        data = json.loads((self.home / "routines.json").read_text(encoding="utf-8"))
        data["routines"].append({"name": "broken"})
        (self.home / "routines.json").write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual([r["id"] for r in self.store.load()], [good["id"]])
        self.assertEqual(self.store.invalid_count(), 1)

    def test_remove_and_unknown_or_ambiguous_names(self):
        added = self.store.add(self.raw, by="local-operator", via="cli")
        with self.assertRaisesRegex(routines.RoutineError, "No routine"):
            self.store.get("nothing")
        self.store.remove("PR digest")
        self.assertEqual(self.store.load(), [])
        with self.assertRaises(routines.RoutineError):
            self.store.get(added["id"])

    def test_state_history_is_bounded_and_counts_failures(self):
        states = routines.RoutineStateStore(self.home)
        item = routines.approve(routine(), by="local-operator", via="cli")
        for _ in range(routines.MAX_HISTORY + 5):
            states.record(item["id"], routines.run_record(item, trigger="schedule", slot=None, outcome="failed"))
        state = states.load(item["id"])
        self.assertEqual(len(state["history"]), routines.MAX_HISTORY)
        self.assertEqual(state["consecutive_failures"], routines.MAX_HISTORY + 5)
        states.record(item["id"], routines.run_record(item, trigger="schedule", slot=None, outcome="no_reply",
                                                      ended=datetime(2026, 1, 1, tzinfo=UTC)))
        state = states.load(item["id"])
        self.assertEqual(state["consecutive_failures"], 0)
        self.assertEqual(state["last_success"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(state["history"][-1]["prompt_sha256"], routines.prompt_hash(item))

    def test_run_now_requests_are_claimed_once(self):
        routines.request_run(self.home, "r-0123456789")
        self.assertEqual([r["routine_id"] for r in routines.take_requests(self.home)], ["r-0123456789"])
        self.assertEqual(routines.take_requests(self.home), [])
        with self.assertRaises(routines.RoutineError):
            routines.request_run(self.home, "../escape")

    # Scenarios from other schedulers' test suites (Hermes Agent, OpenClaw, nanobot, Agno).

    def test_damaged_and_unknown_routines_survive_edits_to_the_others(self):
        good = self.store.add(self.raw, by="local-operator", via="cli")
        data = json.loads((self.home / "routines.json").read_text(encoding="utf-8"))
        damaged = [{"name": "broken"}, {**data["routines"][0], "id": "r-00000000ff", "name": "From a newer Tag",
                                        "colour": "blue"}]
        data["routines"].extend(damaged)
        (self.home / "routines.json").write_text(json.dumps(data), encoding="utf-8")
        self.store.update(good["id"], {"prompt": "Changed"}, by="local-operator", via="cli")
        self.store.set_enabled(good["id"], False)
        self.store.add({**self.raw, "name": "Second"}, by="local-operator", via="cli")
        self.store.remove("Second")
        saved = json.loads((self.home / "routines.json").read_text(encoding="utf-8"))["routines"]
        self.assertEqual(saved[1:], damaged)
        self.assertEqual(self.store.invalid_count(), 2)

    def test_a_schedule_without_text_is_one_damaged_routine(self):
        good = self.store.add(self.raw, by="local-operator", via="cli")
        data = json.loads((self.home / "routines.json").read_text(encoding="utf-8"))
        data["routines"].append({**data["routines"][0], "id": "r-00000000ee", "name": "Odd", "schedule": {}})
        data["routines"].append({**data["routines"][0], "id": "r-00000000dd", "name": "Odder", "schedule": 7})
        (self.home / "routines.json").write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual([r["id"] for r in self.store.load()], [good["id"]])

    def test_saving_the_same_interval_again_keeps_its_timing(self):
        added = self.store.add({**self.raw, "schedule": "every 2h"}, by="local-operator", via="cli",
                               now=datetime(2026, 1, 1, 8, 30, tzinfo=UTC))
        for text in ("every 2h", "Every 2h", " every  2h "):
            edited = self.store.update(added["id"], {"schedule": text}, by="local-operator", via="cli",
                                       now=datetime(2026, 1, 2, 8, 30, tzinfo=UTC))
            self.assertEqual(edited["anchor"], added["anchor"], text)

    def test_resume_refuses_a_routine_that_changed_after_approval(self):
        added = self.store.add(self.raw, by="local-operator", via="cli")
        self.store.set_enabled(added["id"], False)
        data = json.loads((self.home / "routines.json").read_text(encoding="utf-8"))
        data["routines"][0]["prompt"] = "Edited by hand"
        (self.home / "routines.json").write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(routines.RoutineError, "approve"):
            self.store.set_enabled(added["id"], True)
        self.assertFalse(self.store.get(added["id"])["enabled"])

    def test_a_rejected_add_writes_nothing(self):
        with self.assertRaises(routines.RoutineError):
            self.store.add({**self.raw, "schedule": "every 5m"}, by="local-operator", via="cli")
        self.assertFalse((self.home / "routines.json").exists())

    def test_saves_reach_the_disk_before_they_replace_the_file(self):
        with mock.patch.object(routines.os, "fsync", wraps=routines.os.fsync) as fsync:
            self.store.add(self.raw, by="local-operator", via="cli")
        self.assertGreaterEqual(fsync.call_count, 1)


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.states = routines.RoutineStateStore(self.home)
        self.item = routines.approve(routine(), by="local-operator", via="cli")
        self.path = self.home / routines.STATE_DIR / f"{self.item['id']}.json"
        self.path.parent.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def record(self, outcome, **extra):
        return routines.run_record(self.item, trigger="schedule", slot=None, outcome=outcome, **extra)

    def test_a_damaged_state_file_is_kept_aside_not_overwritten(self):
        self.path.write_text('{"schema_version": 1, "history": [', encoding="utf-8")
        self.assertEqual(self.states.peek(self.item["id"])["history"], [])
        state = self.states.update(self.item["id"], lambda s: routines.add_run(s, self.record("posted")))
        kept = list(self.path.parent.glob(f"{self.item['id']}.corrupt-*.json"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_text(encoding="utf-8"), '{"schema_version": 1, "history": [')
        self.assertEqual([h["outcome"] for h in state["history"]], ["failed", "posted"])
        self.assertIn(kept[0].name, state["history"][0]["diagnostic"])
        self.assertEqual(state["consecutive_failures"], 0)

    def test_a_state_file_from_a_newer_tag_is_refused(self):
        newer = {"schema_version": routines.STATE_SCHEMA_VERSION + 1, "history": [{"outcome": "posted"}]}
        self.path.write_text(json.dumps(newer), encoding="utf-8")
        with self.assertRaises(routines.RoutineSchemaError):
            self.states.update(self.item["id"], lambda s: routines.add_run(s, self.record("posted")))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), newer)
        self.assertEqual(self.states.peek(self.item["id"])["history"], [])

    def test_malformed_history_entries_are_dropped_and_old_files_start_at_zero_failures(self):
        self.path.write_text(json.dumps({"schema_version": 1, "history": [
            None, {"note": "no outcome"}, {"outcome": "posted", "started": "2026-01-01T00:00:00+00:00"}]}),
            encoding="utf-8")
        state = self.states.load(self.item["id"])
        self.assertEqual([h["outcome"] for h in state["history"]], ["posted"])
        self.assertEqual(state["consecutive_failures"], 0)

    def test_interrupted_runs_count_as_failures_and_cancelled_or_rate_limited_ones_do_not(self):
        state = routines.empty_state()
        routines.add_run(state, self.record("interrupted"))
        routines.add_run(state, self.record("cancelled"))
        routines.add_run(state, self.record("failed", rate_limited=True))
        self.assertEqual(state["consecutive_failures"], 1)
        routines.add_run(state, self.record("failed"))
        self.assertEqual(state["consecutive_failures"], 2)

    def test_each_scheduled_run_has_one_id_per_slot(self):
        slot = datetime(2026, 10, 13, 9, 3, tzinfo=UTC)
        first = routines.run_record(self.item, trigger="schedule", slot=slot, outcome="posted")
        again = routines.run_record(self.item, trigger="catch-up", slot=slot, outcome="posted")
        self.assertEqual(first["run_id"], again["run_id"])
        self.assertIn(self.item["id"], first["run_id"])
        by_hand = routines.run_record(self.item, trigger="run-now", slot=None, outcome="posted")
        self.assertNotEqual(by_hand["run_id"], first["run_id"])


class DaylightSavingTests(unittest.TestCase):
    """Daylight-saving cases, in the spirit of OpenClaw's schedule tests and APScheduler's DST tests.

    Our rule: a time skipped by a change runs when the gap ends; a repeated time runs
    once, at its first occurrence; intervals count elapsed time.
    """

    CASES = [
        # (zone, schedule, after (UTC), expected local times)
        ("America/New_York", "daily 02:30 exact", datetime(2027, 3, 13, 12, tzinfo=UTC),
         ["2027-03-14 03:00", "2027-03-15 02:30"]),
        ("America/New_York", "daily 01:30 exact", datetime(2027, 11, 6, 12, tzinfo=UTC),
         ["2027-11-07 01:30 -0400", "2027-11-08 01:30 -0500"]),
        ("Australia/Lord_Howe", "daily 02:15 exact", datetime(2026, 10, 3, 0, tzinfo=UTC),
         ["2026-10-04 02:30", "2026-10-05 02:15"]),
        ("Australia/Lord_Howe", "daily 01:45 exact", datetime(2026, 4, 4, 0, tzinfo=UTC),
         ["2026-04-05 01:45 +1100", "2026-04-06 01:45 +1030"]),
        ("Australia/Sydney", "daily 02:30 exact", datetime(2026, 10, 3, 0, tzinfo=UTC),
         ["2026-10-04 03:00", "2026-10-05 02:30"]),
        ("Australia/Sydney", "daily 02:30 exact", datetime(2026, 4, 4, 0, tzinfo=UTC),
         ["2026-04-05 02:30 +1100", "2026-04-06 02:30 +1000"]),
        ("Africa/Cairo", "daily 00:30 exact", datetime(2024, 4, 25, 12, tzinfo=UTC),
         ["2024-04-26 01:00", "2024-04-27 00:30"]),
        ("Asia/Hebron", "daily 00:00 exact", datetime(2022, 3, 26, 12, tzinfo=UTC),
         ["2022-03-27 01:00", "2022-03-28 00:00"]),
        ("America/Sao_Paulo", "daily 00:00 exact", datetime(2018, 11, 3, 12, tzinfo=UTC),
         ["2018-11-04 01:00", "2018-11-05 00:00"]),
        ("America/Santiago", "daily 00:15 exact", datetime(2026, 9, 5, 12, tzinfo=UTC),
         ["2026-09-06 01:00", "2026-09-07 00:15"]),
        ("America/Santiago", "daily 23:30 exact", datetime(2026, 4, 4, 12, tzinfo=UTC),
         ["2026-04-04 23:30 -0300", "2026-04-05 23:30 -0400"]),
        # A gap that crosses midnight moves the run to the next day, and so to the next weekday.
        ("America/Nuuk", "daily 23:30 exact", datetime(2024, 3, 30, 12, tzinfo=UTC),
         ["2024-03-31 00:00", "2024-03-31 23:30"]),
        ("America/Nuuk", "sat 23:30 exact", datetime(2024, 3, 30, 12, tzinfo=UTC),
         ["2024-03-31 00:00", "2024-04-06 23:30"]),
        # Samoa skipped 30 December 2011 entirely.
        ("Pacific/Apia", "daily 09:00 exact", datetime(2011, 12, 29, 0, tzinfo=UTC),
         ["2011-12-29 09:00", "2011-12-31 00:00", "2011-12-31 09:00"]),
        ("Europe/London", "monthly last 09:30", datetime(2028, 2, 1, tzinfo=UTC),
         ["2028-02-29 09:30", "2028-03-31 09:30"]),
    ]

    def test_daylight_saving_vectors(self):
        for zone, schedule, after, expected in self.CASES:
            with self.subTest(zone=zone, schedule=schedule):
                item = routine(schedule=schedule, timezone=zone)
                times = routines.upcoming(item, after, len(expected))
                with_offset = any(len(text) > 16 for text in expected)
                shown = [t.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M" + (" %z" if with_offset else ""))
                         for t in times]
                self.assertEqual(shown, expected)

    def test_two_times_that_land_in_one_gap_post_once(self):
        for schedule in ("daily 02:00,03:00", "daily 02:15,03:15 exact", "daily 02:15,03:30 exact"):
            with self.subTest(schedule=schedule):
                item = routine(schedule=schedule, timezone="America/New_York")
                times = routines.upcoming(item, datetime(2027, 3, 14, 5, tzinfo=UTC), 3)
                gaps = [b - a for a, b in zip(times, times[1:])]
                self.assertGreaterEqual(min(gaps), timedelta(minutes=30), local(times, NEW_YORK))

    def test_a_start_inside_the_repeated_hour_still_gets_a_future_time(self):
        item = routine(schedule="daily 01:45 exact")
        second_pass = datetime(2027, 11, 7, 6, 35, tzinfo=UTC)  # 01:35 EST, the second 01:35
        following = routines.next_run(item, second_pass)
        self.assertGreater(following, second_pass)
        self.assertEqual(local([following], NEW_YORK), ["2027-11-08 01:45 EST"])

    def test_a_year_of_daily_runs_keeps_the_clock_time(self):
        item = routine(schedule="daily 09:00")
        offset = routines.stagger_minutes(item["id"], item["schedule"])
        times = routines.upcoming(item, datetime(2027, 1, 1, tzinfo=UTC), 365)
        self.assertEqual({t.astimezone(NEW_YORK).strftime("%H:%M") for t in times}, {f"09:0{offset}"})
        self.assertEqual(len({t.astimezone(NEW_YORK).date() for t in times}), 365)

    def test_next_run_is_the_same_from_any_start_before_it(self):
        for zone, schedule in (("America/New_York", "daily 01:30,02:30 exact"), ("Europe/London", "weekdays 09:00"),
                               ("Australia/Lord_Howe", "daily 02:15 exact"), ("America/New_York", "every 2h")):
            with self.subTest(zone=zone, schedule=schedule):
                item = routine(schedule=schedule, timezone=zone)
                start = datetime(2027, 3, 12, tzinfo=UTC) if zone != "Australia/Lord_Howe" else datetime(
                    2026, 10, 2, tzinfo=UTC)
                times = routines.upcoming(item, start, 8)
                for earlier, later in zip([start] + times, times):
                    probe = earlier
                    while probe < later:
                        self.assertEqual(routines.next_run(item, probe), later, probe)
                        probe += timedelta(minutes=7)

    def test_an_interval_without_a_saved_anchor_keeps_a_stable_one(self):
        raw = {"name": "x", "schedule": "every 2h", "timezone": "UTC", "channel": "C0123ABCDE",
               "prompt": "p", "owner": "U0123ABC", "id": "r-0123456789"}
        first = routines.validate_routine(raw, now=datetime(2026, 1, 1, tzinfo=UTC))
        later = routines.validate_routine(raw, now=datetime(2026, 1, 1, 0, 3, tzinfo=UTC))
        self.assertEqual(first["anchor"], later["anchor"])

    def test_a_start_time_without_a_zone_means_utc(self):
        raw = {"name": "x", "schedule": "every 2h", "timezone": "Asia/Tokyo", "channel": "C0123ABCDE",
               "prompt": "p", "owner": "U0123ABC", "anchor": "2026-01-01T10:05:00"}
        item = routines.validate_routine(raw)
        self.assertEqual(item["anchor"], "2026-01-01T10:05:00+00:00")
        self.assertEqual(routines.next_run(item, datetime(2026, 1, 1, tzinfo=UTC)),
                         datetime(2026, 1, 1, 10, 5, tzinfo=UTC))


class SilenceTests(unittest.TestCase):
    """Nothing-to-report replies, following Hermes Agent's matcher for scheduled runs."""

    def test_the_marker_alone_or_on_its_own_first_or_last_line_is_silence(self):
        for answer in ("NO_REPLY", "no_reply.", "**NO_REPLY**", "`NO_REPLY`", "NO REPLY", "[NO_REPLY]",
                       "NO_REPLY\n\nNothing merged since yesterday.", "Checked 4 repos.\n\nNO_REPLY",
                       "[NO_REPLY] nothing new", "[SILENT]"):
            with self.subTest(answer=answer):
                self.assertTrue(routines.is_no_reply(answer))

    def test_a_marker_inside_real_content_is_delivered(self):
        for answer in ("", "   ", "Three PRs merged. I didn't need NO_REPLY today.",
                       "NO_REPLY would be wrong: the build is red.\nDetails below.", "Silent retry succeeded."):
            with self.subTest(answer=answer):
                self.assertFalse(routines.is_no_reply(answer))

    def test_the_preamble_says_not_to_schedule_more_work(self):
        text = routines.run_prompt(routine(prompt="Every Monday, list open PRs."), trigger="schedule",
                                   slot=None, last_success=None)
        self.assertIn("Don't add or change routines", text)


if __name__ == "__main__":
    unittest.main()
