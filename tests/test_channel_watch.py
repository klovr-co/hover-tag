from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts import channel_watch
from scripts.opentag_process_env import text_only_environment


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FilterTests(unittest.TestCase):
    def test_names_include_bot_name_and_aliases(self) -> None:
        names = channel_watch.watch_names({"OPENTAG_BOT_NAME": "Iris’s Tag", "OPENTAG_WATCH_NAMES": " Tagbot, tagbot ,"})
        self.assertEqual(("Iris's Tag", "Tagbot"), names)
        self.assertEqual(("Tag",), channel_watch.watch_names({}))

    def test_name_match_is_a_whole_word_outside_code(self) -> None:
        names = ("Iris's Tag", "Tagbot")
        for text in ("Iris's Tag, can you check?", "what do you think iris’s tag", "ok TAGBOT."):
            self.assertTrue(channel_watch.names_tag(text, names), text)
        for text in ("Iris's Tagline is great", "ask Iris", "run `Tagbot --help`", "```Iris's Tag```"):
            self.assertFalse(channel_watch.names_tag(text, names), text)

    def test_quiet_hours_wrap_midnight_and_fail_closed(self) -> None:
        at = lambda hour, minute=0: datetime(2026, 10, 13, hour, minute)  # noqa: E731
        self.assertTrue(channel_watch.in_quiet_hours("19:00-08:00", at(23)))
        self.assertTrue(channel_watch.in_quiet_hours("19:00-08:00", at(7, 59)))
        self.assertFalse(channel_watch.in_quiet_hours("19:00-08:00", at(8)))
        self.assertTrue(channel_watch.in_quiet_hours("12:00-13:00", at(12, 30)))
        self.assertFalse(channel_watch.in_quiet_hours("12:00-13:00", at(13)))
        self.assertFalse(channel_watch.in_quiet_hours("", at(3)))
        self.assertTrue(channel_watch.in_quiet_hours("7pm-8am", at(12)))

    def test_limits_fall_back_to_defaults(self) -> None:
        self.assertEqual(5, channel_watch.daily_limit({}))
        self.assertEqual(3, channel_watch.daily_limit({"OPENTAG_WATCH_DAILY_LIMIT": "3"}))
        self.assertEqual(5, channel_watch.daily_limit({"OPENTAG_WATCH_DAILY_LIMIT": "500"}))
        self.assertEqual(24, channel_watch.cooldown_hours({"OPENTAG_WATCH_COOLDOWN_HOURS": "x"}))
        self.assertEqual("19:00-08:00", channel_watch.quiet_hours({}))
        self.assertEqual("", channel_watch.quiet_hours({"OPENTAG_WATCH_QUIET_HOURS": ""}))

    def test_slack_429_backoff_reads_retry_after(self) -> None:
        limited = Exception()
        limited.response = SimpleNamespace(status_code=429, headers={"Retry-After": "30"})
        self.assertEqual(30.0, channel_watch.retry_after(limited))
        other = Exception()
        other.response = SimpleNamespace(status_code=500, headers={})
        self.assertIsNone(channel_watch.retry_after(other))
        self.assertIsNone(channel_watch.retry_after(ValueError()))

    def test_reactions_must_be_granted_after_version_5(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.assertFalse(channel_watch.reactions_granted(home))
            marker = home / "state/slack-manifest-migrations.json"
            marker.parent.mkdir(parents=True)
            for receipt, expected in (
                ({"version": 4}, False),
                ({"version": 5, "optional_pending": ["reactions:write"]}, False),
                ({"version": 5, "optional_pending": ["team:read"]}, True),
                ({"version": 5}, True),
            ):
                marker.write_text(json.dumps(receipt), encoding="utf-8")
                self.assertEqual(expected, channel_watch.reactions_granted(home), receipt)


class WatchStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "state/channel-watch.json"
        self.clock = Clock()
        self.state = channel_watch.WatchState(self.path, clock=self.clock)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_one_unprompted_reply_per_thread_until_a_person_engages(self) -> None:
        self.state.engage("C1", "1.0")
        self.assertTrue(self.state.is_engaged("C1", "1.0"))
        self.assertIsNone(self.state.blocked("C1", "1.0", limit=5, today="d"))
        self.state.record_reply("C1", "1.0", "2.0", "score-a", today="d")
        self.assertEqual("awaiting a person", self.state.blocked("C1", "1.0", limit=5, today="d"))
        self.assertEqual({"thread_ts": "1.0", "score_id": "score-a", "at": self.clock.now},
                         self.state.reply("C1", "2.0"))
        self.assertEqual("score-a", self.state.last_reply("C1", "1.0")["score_id"])
        self.state.engage("C1", "1.0")  # An @mention, Take this on, or 👍.
        self.assertIsNone(self.state.blocked("C1", "1.0", limit=5, today="d"))
        self.assertEqual("score-a", self.state.last_reply("C1", "1.0")["score_id"])

    def test_daily_limit_resets_each_day(self) -> None:
        for index in range(2):
            self.state.record_reply("C1", f"{index}.0", f"{index}.5", "s", today="2026-10-13")
        self.assertEqual("daily limit", self.state.blocked("C1", "9.0", limit=2, today="2026-10-13"))
        self.assertIsNone(self.state.blocked("C2", "9.0", limit=2, today="2026-10-13"))
        self.assertIsNone(self.state.blocked("C1", "9.0", limit=2, today="2026-10-14"))

    def test_cooldown_and_backoff_expire(self) -> None:
        self.state.cool_down("C1", 2)
        self.assertEqual("cooldown", self.state.blocked("C1", "1.0", limit=5, today="d"))
        self.clock.now += 2 * 3600
        self.assertIsNone(self.state.blocked("C1", "1.0", limit=5, today="d"))
        self.state.back_off(30)
        self.assertEqual("Slack rate limit", self.state.blocked("C9", "1.0", limit=5, today="d"))
        self.clock.now += 30
        self.assertIsNone(self.state.blocked("C9", "1.0", limit=5, today="d"))

    def test_claim_prevents_two_decisions_in_one_thread(self) -> None:
        self.assertTrue(self.state.claim("C1", "1.0"))
        self.assertFalse(self.state.claim("C1", "1.0"))
        self.assertEqual("already deciding", self.state.blocked("C1", "1.0", limit=5, today="d"))
        self.state.release("C1", "1.0")
        self.assertTrue(self.state.claim("C1", "1.0"))

    def test_state_survives_restart_and_prunes_old_threads(self) -> None:
        self.state.engage("C1", "1.0")
        self.state.set_noticed("C1", True)
        self.state.cool_down("C2", 1)
        reloaded = channel_watch.WatchState(self.path, clock=self.clock)
        self.assertTrue(reloaded.is_engaged("C1", "1.0"))
        self.assertTrue(reloaded.noticed("C1"))
        self.assertEqual("cooldown", reloaded.blocked("C2", "1.0", limit=5, today="d"))
        if os.name != "nt":  # Windows has no POSIX permission bits.
            self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.clock.now += channel_watch.THREAD_RETENTION_SECONDS + 1
        reloaded.engage("C1", "5.0")
        self.assertFalse(reloaded.is_engaged("C1", "1.0"))
        reloaded.set_noticed("C1", False)
        self.assertFalse(channel_watch.WatchState(self.path).noticed("C1"))

    def test_unreadable_state_starts_empty(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{broken", encoding="utf-8")
        state = channel_watch.WatchState(self.path, clock=self.clock)
        self.assertFalse(state.is_engaged("C1", "1.0"))
        state.engage("C1", "1.0")
        self.assertTrue(json.loads(self.path.read_text(encoding="utf-8"))["threads"])


class ScoreLogTests(unittest.TestCase):
    def test_scores_and_labels_are_private_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state/channel-watch-scores.jsonl"
            log = channel_watch.ScoreLog(path, clock=Clock())
            entry_id = log.score(channel="C1", probability=0.93, outcome="reply", state={"current_message": "hi"})
            log.label(entry_id, False, "thumbsdown")
            log.label("", True, "ignored")
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(["score", "label"], [row["type"] for row in rows])
            self.assertEqual(entry_id, rows[1]["id"])
            self.assertFalse(rows[1]["expected"])
            if os.name != "nt":  # Windows has no POSIX permission bits.
                self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            with patch.object(channel_watch, "SCORE_LOG_MAX_BYTES", 10), patch.object(
                channel_watch, "SCORE_LOG_KEEP_LINES", 1
            ):
                log.score(channel="C1")
            self.assertEqual(2, len(path.read_text(encoding="utf-8").splitlines()))


class DraftTests(unittest.TestCase):
    def test_interpret_prefers_silence_and_blocks_unsafe_output(self) -> None:
        self.assertEqual(("no_reply", ""), channel_watch.interpret(""))
        self.assertEqual(("no_reply", ""), channel_watch.interpret(" no_reply. "))
        self.assertEqual(("react", ""), channel_watch.interpret("REACT"))
        self.assertEqual(("reply", "Sure, want me to\ndraft it?".replace("\n", " ")),
                         channel_watch.interpret("Sure, want me to\ndraft it?"))
        for unsafe in ("x" * 301, "ping <!channel> now", "ask <@U123>", "see https://evil.test/?d=1",
                       "go to www.evil.test", "[click](x)"):
            self.assertEqual(("reply", channel_watch.OFFER), channel_watch.interpret(unsafe), unsafe)
            self.assertEqual(("no_reply", ""), channel_watch.interpret(unsafe, chime=True), unsafe)
        self.assertEqual(("reply", "Congrats!"), channel_watch.interpret("Congrats!", chime=True))

    def test_chime_in_draft_has_its_own_instructions_and_label(self) -> None:
        text = channel_watch.CHIME_INSTRUCTIONS
        for phrase in ("untrusted text", "cannot take any action", "Don't offer to do work", channel_watch.NO_REPLY):
            self.assertIn(phrase, text)
        blocks = channel_watch.reply_blocks("Congrats!", reason="chime-in", provider="TypeSafe Jev",
                                            probability=0.97, take={}, off={"channel": "C1"},
                                            take_action="take", off_action="off")
        self.assertIn("scored 0.97 that this post invites reactions", str(blocks))
        self.assertEqual(["off"], [b["action_id"] for b in blocks[-1]["elements"]])

    def test_draft_is_a_text_only_run_for_both_backends(self) -> None:
        for backend in ("codex", "claude"):
            with self.subTest(backend=backend):
                runner = MagicMock()

                def run(prompt, *, model, reasoning_effort, emit):
                    self.assertEqual({"conversation": {"current_message": "Tag, thoughts?"}}, json.loads(prompt))
                    emit({"type": "message_complete", "phase": "final_answer", "text": "Want me to take this on?"})
                    return "completed", ""

                runner.run.side_effect = run
                with patch("scripts.opentag_agent.executable_command", side_effect=lambda command: command), \
                        patch("scripts.codex_agent_backend.CodexAppServer", return_value=runner) as codex, patch(
                    "scripts.claude_agent_backend.ClaudeAgentRun", return_value=runner
                ) as claude:
                    text = channel_watch.draft_reply(
                        {"current_message": "Tag, thoughts?"}, backend, "model-x", name="Iris's Tag")
                self.assertEqual("Want me to take this on?", text)
                constructor = codex if backend == "codex" else claude
                options = constructor.call_args.kwargs
                self.assertIn("Iris's Tag", options["text_only_instructions"])
                self.assertIn("no tools", options["text_only_instructions"])
                self.assertFalse(options["cwd"].exists())  # A throwaway directory, not the Tag workspace.
                self.assertEqual(channel_watch.DRAFT_TIMEOUT, options["max_timeout"])
                self.assertEqual("model-x", runner.run.call_args.kwargs["model"])

    def test_backend_classifier_answers_yes_or_no_and_fails_closed_otherwise(self) -> None:
        from scripts import watch_classifier
        runner = MagicMock()
        answers = iter(["YES", " no. ", "Probably yes", ""])

        def run(prompt, *, model, reasoning_effort, emit):
            emit({"type": "message_complete", "phase": "final_answer", "text": next(answers)})
            return "completed", ""

        runner.run.side_effect = run
        with patch("scripts.claude_agent_backend.ClaudeAgentRun", return_value=runner) as claude:
            ask = lambda: channel_watch.backend_classify(  # noqa: E731
                {"current_message": "We launched!"}, "claude", None, name="Tag", question=watch_classifier.CHIME_IN)
            self.assertTrue(ask())
            instructions = claude.call_args.kwargs["text_only_instructions"]
            self.assertIn(watch_classifier.CHIME_TRUE_CRITERIA, instructions)
            self.assertIn("exactly YES or NO", instructions)
            self.assertFalse(ask())
            for _ in range(2):
                with self.assertRaises(watch_classifier.ClassifierError):
                    ask()
        self.assertNotIn("tag-backend", watch_classifier.PROVIDERS)  # Built, not offered yet.

    def test_failed_or_unknown_runs_produce_no_text(self) -> None:
        runner = MagicMock()
        runner.run.return_value = ("failed", "boom")
        with patch("scripts.claude_agent_backend.ClaudeAgentRun", return_value=runner):
            self.assertEqual("", channel_watch.draft_reply({}, "claude", None, name="Tag"))
        self.assertEqual("", channel_watch.draft_reply({}, "other", None, name="Tag"))

    def test_text_only_environment_blanks_bridge_and_classifier_secrets(self) -> None:
        environment = text_only_environment({
            "OPENTAG_TYPESAFE_API_KEY": "t", "OPENTAG_OPENAI_DECISIONS_API_KEY": "o",
            "OPENTAG_WATCH_CHANNEL_IDS": "C1", "SLACK_BOT_TOKEN": "xoxb", "OPENAI_API_KEY": "provider-auth",
        })
        for key in ("OPENTAG_TYPESAFE_API_KEY", "OPENTAG_OPENAI_DECISIONS_API_KEY",
                    "OPENTAG_WATCH_CHANNEL_IDS", "SLACK_BOT_TOKEN"):
            self.assertEqual("", environment[key])
        self.assertEqual("provider-auth", environment["OPENAI_API_KEY"])


class MessageTests(unittest.TestCase):
    def test_reply_is_labelled_with_why_and_has_both_buttons(self) -> None:
        blocks = channel_watch.reply_blocks(
            "Want me to take this on?", reason="named", provider="OpenAI Decisions", probability=0.934,
            take={"channel": "C1"}, off={"channel": "C1"}, take_action="take", off_action="off")
        context = blocks[1]["elements"][0]["text"]
        self.assertIn("this message names me", context)
        self.assertIn("OpenAI Decisions scored 0.93", context)
        self.assertIn("can't act until someone confirms", context)
        buttons = blocks[2]["elements"]
        self.assertEqual(["Take this on", "Don't do this here"], [b["text"]["text"] for b in buttons])
        self.assertEqual({"channel": "C1"}, json.loads(buttons[1]["value"]))

    def test_notice_explains_watching_and_offers_opt_out(self) -> None:
        text, blocks = channel_watch.notice_blocks("Iris's Tag", "19:00-08:00", off={"channel": "C1"}, off_action="off")
        self.assertIn("Iris's Tag now watches this channel", text)
        self.assertIn("from 19:00 to 08:00", text)
        self.assertEqual("off", blocks[1]["elements"][0]["action_id"])
        self.assertNotIn("quiet", channel_watch.notice_blocks("Tag", "", off={}, off_action="off")[0])


if __name__ == "__main__":
    unittest.main()
