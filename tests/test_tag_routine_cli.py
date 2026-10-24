import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from scripts import tag_routine_cli
from scripts import tag_routines as routines

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
VALUES = {"SLACK_ALLOWED_USER_IDS": "U0123ABC", "SLACK_CHANNEL_IDS": "C0123ABCDE,C0999ZZZZZ"}
CHANNELS = [{"id": "C0123ABCDE", "name": "eng-updates"}, {"id": "C0999ZZZZZ", "name": None}]


class RoutineCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.running = True

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *argv, values=VALUES, stdin=""):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch("sys.stdin", io.StringIO(stdin)):
            code = tag_routine_cli.run(self.home, "research", list(argv), values=values, channels=CHANNELS,
                                       tag_running=lambda _home: self.running, now=lambda: NOW)
        text = out.getvalue()
        return code, (json.loads(text) if "--json" in argv else text)

    def add(self, *extra):
        return self.cli("add", "--name", "PR digest", "--schedule", "weekdays 09:00", "--timezone",
                        "Europe/London", "--channel", "#eng-updates", "--prompt", "Summarise PRs.", "--json", *extra)

    def test_add_list_and_show(self):
        code, added = self.add()
        self.assertEqual(code, 0)
        routine = added["routine"]
        self.assertEqual((routine["channel"], routine["owner"], routine["approved"]), ("C0123ABCDE", "U0123ABC", True))
        self.assertEqual(routine["approval"]["by"], "local-operator")
        self.assertEqual(routine["next_run"][:15], "2026-10-14T08:0")
        _, listed = self.cli("list", "--json")
        self.assertEqual([r["name"] for r in listed["routines"]], ["PR digest"])
        self.assertEqual(listed["routines"][0]["channel_name"], "eng-updates")
        self.assertTrue(listed["tag_running"])
        self.assertEqual(listed["limits"]["per_tag"], routines.MAX_ROUTINES_PER_TAG)
        _, shown = self.cli("show", "pr digest", "--json")
        self.assertEqual(len(shown["upcoming"]), 5)
        self.assertEqual(shown["history"], [])
        code, text = self.cli("show", "PR digest")
        self.assertEqual(code, 0)
        self.assertIn("Summarise PRs.", text)

    def test_add_validates_against_this_tag(self):
        with self.assertRaisesRegex(routines.RoutineError, "own channels"):
            self.cli("add", "--name", "x", "--schedule", "daily 09:00", "--timezone", "UTC",
                     "--channel", "C0NOTMINE1", "--prompt", "p")
        with self.assertRaisesRegex(routines.RoutineError, "No channel named"):
            self.cli("add", "--name", "x", "--schedule", "daily 09:00", "--timezone", "UTC",
                     "--channel", "#random", "--prompt", "p")
        with self.assertRaisesRegex(routines.RoutineError, "allowed to use"):
            self.add("--owner", "U0STRANGER")
        with self.assertRaisesRegex(routines.RoutineError, "--owner"):
            self.cli("add", "--name", "x", "--schedule", "daily 09:00", "--timezone", "UTC",
                     "--channel", "C0123ABCDE", "--prompt", "p",
                     values={**VALUES, "SLACK_ALLOWED_USER_IDS": "U0AAA111,U0BBB222"})
        with self.assertRaisesRegex(routines.RoutineError, "--schedule"):
            self.cli("add", "--name", "x", "--channel", "C0123ABCDE", "--prompt", "p")
        with self.assertRaisesRegex(routines.RoutineError, "once an hour"):
            self.cli("add", "--name", "x", "--schedule", "every 15m", "--timezone", "UTC",
                     "--channel", "C0123ABCDE", "--prompt", "p")

    def test_add_from_json_is_how_other_components_create_routines(self):
        spec = {"name": "Standup notes", "schedule": "weekdays 09:30", "timezone": "Europe/London",
                "channel": "#eng-updates", "prompt": "Collect yesterday's notes.", "catch_up": "skip",
                "limits": {"max_budget_usd": 0.25}, "approval": {"by": "U0SPOOF", "hash": "forged"}}
        _, added = self.cli("add", "--from-json", "-", "--json", stdin=json.dumps(spec))
        routine = added["routine"]
        self.assertEqual(routine["channel"], "C0123ABCDE")
        self.assertEqual(routine["limits"]["max_budget_usd"], 0.25)
        self.assertEqual(routine["approval"]["by"], "local-operator")

    def test_edit_pause_resume_and_remove(self):
        _, added = self.add()
        routine_id = added["routine"]["id"]
        _, edited = self.cli("edit", routine_id, "--schedule", "mon,fri 17:30", "--max-budget-usd", "1.5",
                             "--via", "tag-app", "--json")
        self.assertEqual(edited["routine"]["schedule"], "mon,fri 17:30")
        self.assertEqual(edited["routine"]["limits"]["max_budget_usd"], 1.5)
        self.assertEqual(edited["routine"]["approval"]["via"], "tag-app")
        with self.assertRaisesRegex(routines.RoutineError, "Nothing to change"):
            self.cli("edit", routine_id)
        _, paused = self.cli("pause", "PR digest", "--json")
        self.assertFalse(paused["routine"]["enabled"])
        self.assertIsNone(paused["routine"]["next_run"])
        _, resumed = self.cli("resume", "PR digest", "--json")
        self.assertTrue(resumed["routine"]["enabled"])
        _, removed = self.cli("remove", routine_id, "--json")
        self.assertEqual(removed["removed"], routine_id)
        _, listed = self.cli("list", "--json")
        self.assertEqual(listed["routines"], [])

    def test_run_now_needs_a_running_tag_and_an_approved_routine(self):
        _, added = self.add()
        routine_id = added["routine"]["id"]
        _, requested = self.cli("run-now", routine_id, "--json")
        self.assertEqual(requested["requested"], routine_id)
        self.assertEqual([r["routine_id"] for r in routines.take_requests(self.home)], [routine_id])
        self.running = False
        with self.assertRaisesRegex(routines.RoutineError, "Start the Tag"):
            self.cli("run-now", routine_id)
        self.running = True
        data = json.loads((self.home / "routines.json").read_text(encoding="utf-8"))
        data["routines"][0]["prompt"] = "tampered"
        (self.home / "routines.json").write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(routines.RoutineError, "approved"):
            self.cli("run-now", routine_id)
        _, listed = self.cli("list", "--json")
        self.assertTrue(listed["routines"][0]["attention"])
        self.assertFalse(listed["routines"][0]["approved"])

    def test_stopped_tags_say_their_routines_wont_run(self):
        self.running = False
        self.add()
        code, text = self.cli("list")
        self.assertEqual(code, 0)
        self.assertIn("won't run", text)

    # Scenarios from other schedulers' test suites (nanobot, Agno, Hermes Agent, OpenClaw).

    def test_a_routine_run_cannot_add_or_change_routines(self):
        # nanobot: "cannot schedule new jobs from within a cron job execution".
        _, added = self.add()
        routine_id = added["routine"]["id"]
        with mock.patch.dict("os.environ", {"OPENTAG_ROUTINE_ID": routine_id, "OPENTAG_CALLER_ID": "U0123ABC"}):
            for argv in (("edit", routine_id, "--prompt", "More"), ("pause", routine_id), ("remove", routine_id),
                         ("run-now", routine_id), ("add", "--name", "x", "--schedule", "daily 09:00",
                                                   "--channel", "C0123ABCDE", "--prompt", "p")):
                with self.subTest(argv=argv), self.assertRaisesRegex(routines.RoutineError, "routine run"):
                    self.cli(*argv)
            _, shown = self.cli("show", routine_id, "--json")
        self.assertEqual(shown["routine"]["prompt"], "Summarise PRs.")

    def test_from_slack_only_the_owner_changes_a_routine_and_approves_as_themselves(self):
        # Agno: an agent can't rewrite another user's schedule.
        values = {**VALUES, "SLACK_ALLOWED_USER_IDS": "U0123ABC,U0OTHER99"}
        _, added = self.cli("add", "--name", "PR digest", "--schedule", "weekdays 09:00", "--timezone", "UTC",
                            "--channel", "C0123ABCDE", "--prompt", "p", "--owner", "U0123ABC", "--json",
                            values=values)
        routine_id = added["routine"]["id"]
        with mock.patch.dict("os.environ", {"OPENTAG_CALLER_ID": "U0OTHER99"}):
            with self.assertRaisesRegex(routines.RoutineError, "Only its owner"):
                self.cli("edit", routine_id, "--prompt", "Mine now", values=values)
            with self.assertRaisesRegex(routines.RoutineError, "Only its owner"):
                self.cli("pause", routine_id, values=values)
            with self.assertRaisesRegex(routines.RoutineError, "for yourself"):
                self.cli("add", "--name", "x", "--schedule", "daily 09:00", "--timezone", "UTC",
                         "--channel", "C0123ABCDE", "--prompt", "p", "--owner", "U0123ABC", values=values)
        with mock.patch.dict("os.environ", {"OPENTAG_CALLER_ID": "U0123ABC"}):
            _, edited = self.cli("edit", routine_id, "--prompt", "Better", "--json", values=values)
        self.assertEqual((edited["routine"]["approval"]["by"], edited["routine"]["approval"]["via"]),
                         ("U0123ABC", "slack"))

    def test_resume_checks_what_would_stop_the_routine_running(self):
        _, added = self.add()
        routine_id = added["routine"]["id"]
        self.cli("pause", routine_id)
        with self.assertRaisesRegex(routines.RoutineError, "allowed to use"):
            self.cli("resume", routine_id, values={**VALUES, "SLACK_ALLOWED_USER_IDS": "U0SOMEONE"})
        data = json.loads((self.home / "routines.json").read_text(encoding="utf-8"))
        data["routines"][0]["channel"] = "C0GONE0000"
        data["routines"][0]["approval"]["hash"] = routines.approval_hash(data["routines"][0])
        (self.home / "routines.json").write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(routines.RoutineError, "channel"):
            self.cli("resume", routine_id)

    def test_show_survives_odd_history_entries(self):
        _, added = self.add()
        routine_id = added["routine"]["id"]
        path = self.home / routines.STATE_DIR / f"{routine_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": 1, "history": [None, {"note": "x"}, {"outcome": "posted"}]}),
                        encoding="utf-8")
        code, text = self.cli("show", routine_id)
        self.assertEqual(code, 0)
        _, shown = self.cli("show", routine_id, "--json")
        self.assertEqual([h["outcome"] for h in shown["history"]], ["posted"])

    def test_a_scheduler_that_stopped_checking_is_shown(self):
        # Hermes: a failing tick is saved and shown in status, and cleared on recovery.
        self.add()
        routines.write_scheduler_health(self.home, {"last_tick": "2026-10-13T11:00:00+00:00",
                                                    "last_error": "Can't read routines.json", "routine_errors": {}})
        _, listed = self.cli("list", "--json")
        self.assertFalse(listed["scheduler"]["ok"])
        self.assertIn("Can't read", listed["scheduler"]["problem"])
        code, text = self.cli("list")
        self.assertIn("Can't read", text)
        routines.write_scheduler_health(self.home, {"last_tick": "2026-10-13T11:59:40+00:00", "last_error": None,
                                                    "routine_errors": {"r-0123456789": "This schedule has no next run"}})
        _, listed = self.cli("list", "--json")
        self.assertIn("1 routine can't be scheduled: This schedule has no next run", listed["scheduler"]["problem"])
        routines.write_scheduler_health(self.home, {"last_tick": "2026-10-13T11:59:40+00:00", "last_error": None,
                                                    "routine_errors": {}})
        _, listed = self.cli("list", "--json")
        self.assertTrue(listed["scheduler"]["ok"])

    def test_adding_while_stopped_says_it_wont_run_yet(self):
        self.running = False
        code, text = self.cli("add", "--name", "PR digest", "--schedule", "weekdays 09:00", "--timezone",
                              "Europe/London", "--channel", "#eng-updates", "--prompt", "Summarise PRs.")
        self.assertEqual(code, 0)
        self.assertIn("won't run", text)


if __name__ == "__main__":
    unittest.main()
