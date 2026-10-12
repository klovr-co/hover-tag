import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
