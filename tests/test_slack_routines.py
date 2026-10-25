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
        self.definitions = tag_routines.RoutineStore(root / "home")
        self.definitions.save([self.routine])
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
        self.assertIn(f"Reference {record['error_reference']}.", record["diagnostic"])
        self.assertEqual(record["slack_team"], "T1")

    def test_timeouts_are_reported_as_timeouts(self):
        def behaviour(kwargs):
            kwargs["on_error"]("maximum_runtime", "Tag backend exceeded its maximum runtime of 1200s")
            return "Tag backend exceeded its maximum runtime of 1200s", False

        record = self.run_routine(behaviour)
        self.assertEqual(record["outcome"], "timeout")
        self.assertIn("20-minute", record["diagnostic"])

    def test_claude_limit_codes_name_the_limit(self):
        def failing(code):
            def behaviour(kwargs):
                kwargs["on_error"](code, "Reached the limit")
                return "Reached the limit", False
            return behaviour

        budget = self.run_routine(failing("error_max_budget_usd"))
        self.assertEqual(budget["outcome"], "budget_exceeded")
        self.assertIn("$0.50", budget["diagnostic"])
        turns = self.run_routine(failing("error_max_turns"))
        self.assertEqual(turns["outcome"], "failed")
        self.assertIn("40 turns", turns["diagnostic"])

    def test_a_reply_that_mentions_limits_is_still_posted(self):
        text = "The max turns setting and the maximum runtime are fine; max_budget is unchanged."
        record = self.run_routine(lambda kwargs: (text, True))
        self.assertEqual(record["outcome"], "posted")
        self.assertIn(text, self.posted_texts()[0])

    def test_a_channel_the_tag_left_gets_no_notice(self):
        elsewhere = tag_routines.approve({**self.routine, "channel": "C0ELSEWHERE"}, by="local-operator", via="cli")
        record = self.run_routine(lambda kwargs: self.fail("must not run"), routine=elsewhere)
        self.assertEqual(record["outcome"], "failed")
        self.client.chat_postMessage.assert_not_called()

    def test_routine_names_cannot_ping_the_channel(self):
        loud = tag_routines.approve({**self.routine, "name": "<!channel> digest"}, by="local-operator", via="cli")
        self.run_routine(lambda kwargs: ("Done.", True), routine=loud)
        text = self.posted_texts()[0]
        self.assertNotIn("<!channel>", text)
        self.assertIn("&lt;!channel&gt; digest", text)

    def test_the_token_limit_counts_new_tokens_not_cached_context(self):
        stopped = []

        def behaviour(kwargs):
            run = MagicMock()
            run.cancel.side_effect = lambda: stopped.append(True)
            with patch.object(slack_socket_agent, "active_run", return_value=run):
                kwargs["on_trace_event"]({"type": "usage", "usage": {
                    "input_tokens": 5_000_000, "cache_read_input_tokens": 4_900_000, "output_tokens": 20_000}})
            return "Done.", True

        record = self.run_routine(behaviour)
        self.assertEqual((stopped, record["outcome"]), ([], "posted"))
        self.assertEqual(record["tokens"], 5_020_000)

    def test_a_crash_in_the_backend_call_closes_its_activity(self):
        def behaviour(kwargs):
            raise RuntimeError("backend crashed")

        with self.assertRaises(RuntimeError):
            self.run_routine(behaviour)
        records = [self.store.get(p.stem) for p in self.store.root.glob("*.json")]
        self.assertEqual([r["outcome"] for r in records], ["failed"])

    # Scenarios from other schedulers' test suites (Hermes Agent, OpenClaw, nanobot, Agno).

    def test_an_empty_answer_is_a_failure_not_a_post(self):
        # Hermes: test_tick_marks_empty_response_as_error.
        for answer in ("", "  \n "):
            with self.subTest(answer=answer):
                self.client.chat_postMessage.reset_mock()
                record = self.run_routine(lambda kwargs: (answer, True))
                self.assertEqual(record["outcome"], "failed")
                self.assertIn("empty", record["diagnostic"])
                self.assertNotIn("PR digest*\n\n", "".join(self.posted_texts()))

    def test_a_note_after_the_silence_marker_still_posts_nothing(self):
        record = self.run_routine(lambda kwargs: ("NO_REPLY\n\nNothing merged since yesterday.", True))
        self.assertEqual(record["outcome"], "no_reply")
        self.client.chat_postMessage.assert_not_called()

    def test_a_routine_paused_or_removed_while_it_runs_posts_nothing(self):
        # OpenClaw: removing a job during its run cancels it; an ordinary edit doesn't.
        for change in ("pause", "remove", "edit"):
            with self.subTest(change=change):
                self.definitions.save([self.routine])
                self.client.chat_postMessage.reset_mock()

                def behaviour(kwargs):
                    if change == "pause":
                        self.definitions.set_enabled(self.routine["id"], False)
                    elif change == "remove":
                        self.definitions.remove(self.routine["id"])
                    else:
                        self.definitions.update(self.routine["id"], {"name": "Renamed"}, by="local-operator",
                                                via="cli")
                    return "Three PRs merged.", True

                record = self.run_routine(behaviour)
                if change == "edit":
                    self.assertEqual(record["outcome"], "posted")
                else:
                    self.assertEqual(record["outcome"], "cancelled")
                    self.client.chat_postMessage.assert_not_called()

    def test_a_run_stopped_by_a_person_is_cancelled_not_failed(self):
        # nanobot: test_run_history_records_job_cancellation.
        def behaviour(kwargs):
            kwargs["on_cancelled"]()
            return "Stopped. Actions completed before the stop were not rolled back.", False

        record = self.run_routine(behaviour)
        self.assertEqual(record["outcome"], "cancelled")
        self.assertNotIn("didn't finish", "".join(self.posted_texts()))

    def test_a_rate_limited_run_says_nothing_until_its_retry_fails(self):
        def behaviour(kwargs):
            kwargs["on_error"]("rate_limit", "429 Too Many Requests")
            return "429 Too Many Requests", False

        first = self.run_routine(behaviour)
        self.assertEqual((first["outcome"], first.get("rate_limited")), ("failed", True))
        self.client.chat_postMessage.assert_not_called()
        retry = self.run_routine(behaviour, trigger="retry")
        self.assertEqual((retry["outcome"], retry.get("rate_limited")), ("failed", None))
        self.assertIn("usage limit", self.posted_texts()[0])

    def test_an_error_after_the_reply_posted_still_counts_as_posted(self):
        # Hermes: test_run_one_job_exception_after_delivery_does_not_redeliver.
        with patch.object(slack_socket_agent, "deliver_output_artifacts", side_effect=RuntimeError("upload failed")):
            record = self.run_routine(lambda kwargs: ("Three PRs merged.", True))
        self.assertEqual(record["outcome"], "posted")
        self.assertIn("files", record["diagnostic"])
        self.assertEqual(len(self.posted_texts()), 1)

    def test_refused_runs_ask_to_pause_and_the_pause_notice_respects_the_channel(self):
        # Agno: an owner whose grant was revoked is refused; the run is recorded and the schedule disabled.
        elsewhere = tag_routines.approve({**self.routine, "channel": "C0ELSEWHERE"}, by="local-operator", via="cli")
        record = self.run_routine(lambda kwargs: self.fail("must not run"), routine=elsewhere)
        self.assertIn("channel", record["pause_reason"])
        self.fake_app.tag_routine_scheduler.on_auto_pause({**elsewhere, "enabled": False,
                                                           "paused_reason": record["pause_reason"]})
        self.client.chat_postMessage.assert_not_called()
        self.fake_app.tag_routine_scheduler.on_auto_pause({**self.routine, "enabled": False,
                                                           "paused_reason": "Paused after 5 failed runs in a row."})
        self.assertIn("Paused after 5 failed runs", self.posted_texts()[0])

    def test_a_crash_inside_tag_tells_the_channel(self):
        record = tag_routines.run_record(self.routine, trigger="schedule", slot=SLOT, outcome="failed",
                                         diagnostic="Tag hit an internal error (RuntimeError).")
        self.fake_app.tag_routine_scheduler.on_crash(self.routine, record)
        self.assertIn("didn't finish", self.posted_texts()[0])
        elsewhere = {**self.routine, "channel": "C0ELSEWHERE"}
        self.client.chat_postMessage.reset_mock()
        self.fake_app.tag_routine_scheduler.on_crash(elsewhere, record)
        self.client.chat_postMessage.assert_not_called()

    def test_the_agent_knows_it_is_inside_a_routine_run(self):
        self.run_routine(lambda kwargs: ("Done.", True))
        self.assertEqual(self.calls[0]["kwargs"]["routine_id"], self.routine["id"])
        from scripts import opentag_process_env
        marked = opentag_process_env.backend_environment(
            {"OPENTAG_ROUTINE_ID": "stale"}, transport="slack", conversation_id="C1", caller_id="U1",
            routine_id=self.routine["id"])
        self.assertEqual(marked["OPENTAG_ROUTINE_ID"], self.routine["id"])
        unmarked = opentag_process_env.backend_environment(
            {"OPENTAG_ROUTINE_ID": "stale"}, transport="slack", conversation_id="C1", caller_id="U1")
        self.assertNotIn("OPENTAG_ROUTINE_ID", unmarked)

    def test_owner_must_still_be_allowed_and_the_channel_is_told(self):
        stranger = tag_routines.approve({**self.routine, "owner": "USTRANGER"}, by="local-operator", via="cli")
        record = self.run_routine(lambda kwargs: self.fail("must not run"), routine=stranger)
        self.assertEqual(record["outcome"], "failed")
        self.assertIn("owner", record["diagnostic"])
        self.assertIn("owner", self.posted_texts()[0])
        self.assertEqual(self.client.chat_postMessage.call_args.kwargs["channel"], "C0123ABCDE")
        self.assertEqual(self.calls, [])

    def test_mentions_of_other_tags_are_removed_from_replies(self):
        self.client.users_info.return_value = {"user": {"is_bot": True, "profile": {"display_name": "Tag B"}}}
        record = self.run_routine(lambda kwargs: ("Asked <@U0TAGB001> for help. Done.", True))
        self.assertEqual(record["outcome"], "posted")
        text = self.posted_texts()[0]
        self.assertNotIn("<@U0TAGB001>", text)
        self.assertIn("Tag B", text)


if __name__ == "__main__":
    unittest.main()
