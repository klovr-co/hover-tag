"""Routine limits reach both backends, and routine runs never crowd people out of Activity."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts import claude_agent_backend
from scripts.opentag_process_env import backend_environment
from scripts.tag_activity import MAX_RECORDS, MAX_ROUTINE_RECORDS, ActivityStore, recent_activity


class ChildEnvironmentTests(unittest.TestCase):
    def test_limits_come_only_from_the_bridge(self):
        inherited = {"OPENTAG_RUN_MAX_TURNS": "999", "OPENTAG_RUN_MAX_BUDGET_USD": "999", "PATH": "/bin"}
        plain = backend_environment(inherited, transport="slack", conversation_id="C1", caller_id="U1")
        self.assertNotIn("OPENTAG_RUN_MAX_TURNS", plain)
        self.assertNotIn("OPENTAG_RUN_MAX_BUDGET_USD", plain)
        limited = backend_environment(inherited, transport="slack", conversation_id="C1", caller_id="U1",
                                      run_limits={"max_turns": 40, "max_budget_usd": 0.5})
        self.assertEqual((limited["OPENTAG_RUN_MAX_TURNS"], limited["OPENTAG_RUN_MAX_BUDGET_USD"]), ("40", "0.5"))


class ClaudeLimitTests(unittest.TestCase):
    def test_claude_enforces_turns_and_budget_natively(self):
        self.assertEqual(claude_agent_backend.run_limits(
            {"OPENTAG_RUN_MAX_TURNS": "40", "OPENTAG_RUN_MAX_BUDGET_USD": "0.5"}),
            {"max_turns": 40, "max_budget_usd": 0.5})
        self.assertEqual(claude_agent_backend.run_limits({}), {})
        self.assertEqual(claude_agent_backend.run_limits(
            {"OPENTAG_RUN_MAX_TURNS": "x", "OPENTAG_RUN_MAX_BUDGET_USD": "-1"}), {})


class RoutineActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ActivityStore(Path(self.temp.name) / "activity")

    def tearDown(self):
        self.temp.cleanup()

    def create(self, routine=False):
        trigger = {"type": "routine", "routine_id": "r-0123456789", "name": "PR digest"} if routine else None
        return self.store.create(team="T1", channel="C1", thread_ts="" if routine else "1.2",
                                 request_ts="" if routine else "1.2", requester="U1", trigger=trigger)

    def test_routine_runs_keep_their_own_smaller_budget(self):
        people = [self.create() for _ in range(5)]
        for _ in range(MAX_ROUTINE_RECORDS + 10):
            self.create(routine=True)
        records = [json.loads(p.read_text(encoding="utf-8")) for p in self.store.root.glob("*.json")]
        self.assertEqual(sum(1 for r in records if "trigger" in r), MAX_ROUTINE_RECORDS)
        self.assertTrue(all(self.store.get(run_id) for run_id in people))
        self.assertGreater(MAX_RECORDS, MAX_ROUTINE_RECORDS)

    def test_a_routine_run_gets_its_thread_later_and_shows_its_trigger(self):
        run_id = self.create(routine=True)
        self.assertEqual(self.store.get(run_id)["thread_ts"], "")
        self.store.set_thread(run_id, "100.1")
        record = self.store.get(run_id)
        self.assertEqual((record["thread_ts"], record["request_ts"]), ("100.1", "100.1"))
        item = recent_activity(self.store.root)[0]
        self.assertEqual(item["trigger"], {"type": "routine", "routine_id": "r-0123456789",
                                           "name": "PR digest", "trigger": "schedule"})

    def test_forged_or_older_records_have_no_trigger(self):
        run_id = self.store.create(team="T1", channel="C1", thread_ts="1.2", request_ts="1.2", requester="U1",
                                   trigger={"type": "routine", "routine_id": "../x", "name": "x"})
        self.assertNotIn("trigger", self.store.get(run_id))
        self.assertNotIn("trigger", recent_activity(self.store.root)[0])


if __name__ == "__main__":
    unittest.main()
