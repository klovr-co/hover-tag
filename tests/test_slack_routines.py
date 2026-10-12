from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    import slack_bolt  # noqa: F401
except ModuleNotFoundError:
    raise unittest.SkipTest("slack_bolt is installed by the Slack bridge runtime")

from scripts import agent_models, slack_socket_agent, tag_routines
from scripts.tag_activity import ActivityStore, recent_activity

from tests.test_slack_socket_agent import FakeApp

SLOT = datetime(2026, 10, 13, 9, 3, tzinfo=timezone.utc)


def setUpModule() -> None:
    summary = patch.object(slack_socket_agent, "queue_reply_summary")
    summary.start()
    unittest.addModuleCleanup(summary.stop)


class RoutineRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = ActivityStore(root / "activity")
        self.workdir = root / "work"
        self.workdir.mkdir()
        self.client = MagicMock()
        self.client.chat_postMessage.side_effect = (
            lambda **kwargs: {"ts": f"100.{self.client.chat_postMessage.call_count}"})
        self.client.auth_test.return_value = {"team_id": "T1"}
        self.calls: list[dict] = []
        self.routine = tag_routines.approve(tag_routines.validate_routine({
            "name": "PR digest", "schedule": "weekdays 09:00", "timezone": "UTC", "channel": "C0123ABCDE",
            "prompt": "Summarise merged PRs.", "owner": "UOWNER"}), by="local-operator", via="cli")
        env = {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C0123ABCDE", "SLACK_TEAM_ID": "T1",
               "OPENTAG_WORKDIR": str(self.workdir), "TAG_INSTANCE_HOME": str(root / "home")}
        self.patches = [
            patch.dict(os.environ, env, clear=True),
            patch.object(agent_models, "discover_codex_models", return_value=[]),
            patch.object(slack_socket_agent, "collect_health_checks", return_value=[]),
        ]
        for item in self.patches:
            item.start()
        self.fake_app = FakeApp()
        self.fake_app.client = self.client
        self.fake_app.logger = MagicMock()
        with patch.object(slack_socket_agent, "App", return_value=self.fake_app):
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}), activity_store=self.store,
                                          report_store=MagicMock())

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def run_routine(self, behaviour, routine=None, trigger="schedule"):
        def run_events(*args, **kwargs):
            self.calls.append({"args": args, "kwargs": kwargs})
            return behaviour(kwargs)

        with patch.object(slack_socket_agent, "run_backend_events", side_effect=run_events):
            return self.fake_app.tag_routine_scheduler.start_run(routine or self.routine, trigger, SLOT, None)

    def posted_texts(self):
        return [call.kwargs.get("text", "") for call in self.client.chat_postMessage.call_args_list]

    def test_a_reply_posts_once_in_the_routine_channel_with_limits_applied(self):
        def behaviour(kwargs):
            kwargs["on_trace_event"]({"type": "activity_start", "activity_id": "a", "label": "Searching the web…"})
            kwargs["on_trace_event"]({"type": "usage", "usage": {"input_tokens": 900, "output_tokens": 100},
                                      "cost_usd": 0.12})
            return "Three PRs merged.", True

        record = self.run_routine(behaviour)
        self.assertEqual(record["outcome"], "posted")
        self.assertEqual((record["cost_usd"], record["tokens"]), (0.12, 1000))
        self.assertEqual(record["tools_used"], ["Searching the web…"])
        self.assertEqual(record["prompt_sha256"], tag_routines.prompt_hash(self.routine))
        call = self.client.chat_postMessage.call_args_list[0].kwargs
        self.assertEqual(call["channel"], "C0123ABCDE")
        self.assertNotIn("thread_ts", call)
        self.assertIn("PR digest", call["text"])
        self.assertIn("Three PRs merged.", call["text"])
        args, kwargs = self.calls[0]["args"], self.calls[0]["kwargs"]
        self.assertEqual((args[2], args[4]), ("C0123ABCDE", "UOWNER"))
        self.assertIn("Summarise merged PRs.", args[5])
        self.assertIn("NO_REPLY", args[5])
        self.assertEqual(args[6], "")
        self.assertEqual(kwargs["run_limits"], {"max_turns": 40, "max_budget_usd": 0.5})
        self.assertEqual(kwargs["max_timeout"], 1200)
        activity = recent_activity(self.store.root)
        self.assertEqual(activity[0]["trigger"]["routine_id"], self.routine["id"])
        self.assertEqual(self.store.get(record["activity_run_id"])["thread_ts"], "100.1")

    def test_no_reply_posts_nothing(self):
        record = self.run_routine(lambda kwargs: ("NO_REPLY", True))
        self.assertEqual(record["outcome"], "no_reply")
        self.client.chat_postMessage.assert_not_called()
        self.client.chat_postEphemeral.assert_not_called()

    def test_approvals_open_a_thread_that_reaches_the_running_backend(self):
        resolved = {}

        def behaviour(kwargs):
            run = slack_socket_agent.ActiveBackendRun(MagicMock(), Path(self.temp.name) / "control", "r1")
            run.process.poll.return_value = None
            synthetic = slack_socket_agent.RunKey("T1", "C0123ABCDE", self.calls[0]["args"][3])
            slack_socket_agent.register_active_run(synthetic, run)
            try:
                kwargs["on_approval"]({"approval_id": "a" * 32, "label": "push to GitHub"})
                resolved["alias"] = slack_socket_agent.active_run(
                    slack_socket_agent.RunKey("T1", "C0123ABCDE", "100.1"))
                resolved["run"] = run
            finally:
                slack_socket_agent.unregister_active_run(synthetic, run)
            return "NO_REPLY", True

        record = self.run_routine(behaviour)
        self.assertEqual(record["outcome"], "no_reply")
        self.assertIs(resolved["alias"], resolved["run"])
        self.assertIsNone(slack_socket_agent.active_run(slack_socket_agent.RunKey("T1", "C0123ABCDE", "100.1")))
        root = self.client.chat_postMessage.call_args_list[0].kwargs
        self.assertIn("<@UOWNER>", root["text"])
        approval = self.client.chat_postEphemeral.call_args.kwargs
        self.assertEqual((approval["user"], approval["thread_ts"]), ("UOWNER", "100.1"))
        self.assertEqual(self.posted_texts()[-1], "Nothing new to report.")

    def test_spend_over_the_limit_stops_the_run(self):
        stopped = []

        def behaviour(kwargs):
            run = MagicMock()
            run.cancel.side_effect = lambda: stopped.append(True)
            with patch.object(slack_socket_agent, "active_run", return_value=run):
                kwargs["on_trace_event"]({"type": "usage", "usage": {"input_tokens": 10, "output_tokens": 1},
                                          "cost_usd": 0.75})
            return "Stopped.", False

        record = self.run_routine(behaviour)
        self.assertEqual(stopped, [True])
        self.assertEqual(record["outcome"], "budget_exceeded")
        self.assertIn("$0.50", record["diagnostic"])
        self.assertIn("didn't finish", self.posted_texts()[0])

    def test_unknown_cost_falls_back_to_the_token_limit(self):
        stopped = []

        def behaviour(kwargs):
            run = MagicMock()
            run.cancel.side_effect = lambda: stopped.append(True)
            with patch.object(slack_socket_agent, "active_run", return_value=run):
                kwargs["on_trace_event"]({"type": "usage", "usage": {"input_tokens": 1_000, "output_tokens": 10}})
                self.assertEqual(stopped, [])
                kwargs["on_trace_event"]({"type": "usage",
                                          "usage": {"input_tokens": 2_500_000, "output_tokens": 10}})
            return "Stopped.", False

        record = self.run_routine(behaviour)
        self.assertEqual((stopped, record["outcome"]), ([True], "budget_exceeded"))
        self.assertIn("tokens", record["diagnostic"])

    def test_failures_post_a_safe_notice_without_backend_detail(self):
        record = self.run_routine(lambda kwargs: ("Traceback: secret-token-123 leaked", False))
        self.assertEqual(record["outcome"], "failed")
        text = self.posted_texts()[0]
        self.assertIn("didn't finish", text)
        self.assertNotIn("secret-token-123", text)
        self.assertRegex(record["diagnostic"], r"Reference [A-F0-9]{8}")

    def test_timeouts_are_reported_as_timeouts(self):
        record = self.run_routine(lambda kwargs: ("Tag backend exceeded its maximum runtime of 1200s", False))
        self.assertEqual(record["outcome"], "timeout")
        self.assertIn("20-minute", record["diagnostic"])

    def test_owner_and_channel_must_still_be_allowed(self):
        stranger = tag_routines.approve({**self.routine, "owner": "USTRANGER"}, by="local-operator", via="cli")
        record = self.run_routine(lambda kwargs: self.fail("must not run"), routine=stranger)
        self.assertEqual(record["outcome"], "failed")
        self.assertIn("owner", record["diagnostic"])
        elsewhere = tag_routines.approve({**self.routine, "channel": "C0ELSEWHERE"}, by="local-operator", via="cli")
        record = self.run_routine(lambda kwargs: self.fail("must not run"), routine=elsewhere)
        self.assertIn("channel", record["diagnostic"])
        self.assertEqual(self.calls, [])

    def test_mentions_of_other_tags_are_removed_from_replies(self):
        with patch.object(slack_socket_agent.slack_identity, "is_bot_user", return_value=True, create=True):
            record = self.run_routine(lambda kwargs: ("Done.", True))
        self.assertEqual(record["outcome"], "posted")


if __name__ == "__main__":
    unittest.main()
