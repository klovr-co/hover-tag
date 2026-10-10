"""Mentions from other apps' bots listed in SLACK_ALLOWED_BOT_IDS (for example Hover)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from scripts import agent_models, opentag_process_env, slack_socket_agent, tag_config
from tests.test_slack_socket_agent import FakeApp

HOVER_BOT, HOVER_USER, OWN_USER = "B0HOVER", "U0HOVER", "U0TAG"
REQUEST = "<@U0TAG> /update-monday\n```{\"group_jid\": \"1@g.us\"}```"


def setUpModule() -> None:
    summary = patch.object(slack_socket_agent, "queue_reply_summary")
    summary.start()
    unittest.addModuleCleanup(summary.stop)


class ApprovedBotMentionTests(unittest.TestCase):
    def mention(self, event: dict, *, bots: str = HOVER_BOT, body: dict | None = None):
        fake_app, client = FakeApp(), MagicMock()
        env = {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C1", "SLACK_ALLOWED_BOT_IDS": bots}
        with patch.object(slack_socket_agent, "App", return_value=fake_app), \
             patch.dict(os.environ, env, clear=True), \
             patch.object(agent_models, "discover_codex_models", return_value=[]), \
             patch.object(slack_socket_agent, "build_thread_text", return_value="thread"), \
             patch.object(slack_socket_agent, "run_backend_events", return_value=("✅ done", True)) as run:
            slack_socket_agent.create_app("codex", 30, frozenset({"UOWNER"}))
            fake_app.events["app_mention"](
                {"channel": "C1", "ts": "2.0", "thread_ts": "1.0", "text": REQUEST, **event},
                body if body is not None else {"team_id": "T1", "authorizations": [{"user_id": OWN_USER, "is_bot": True}]},
                client, MagicMock())
        return run, client

    def test_listed_bot_id_runs_the_request_as_that_bot(self) -> None:
        run, client = self.mention({"user": HOVER_USER, "bot_id": HOVER_BOT})
        run.assert_called_once()
        self.assertEqual(HOVER_USER, run.call_args.args[4])
        self.assertIn("/update-monday", run.call_args.args[5])
        self.assertNotIn(slack_socket_agent.UNAUTHORIZED_USER_MESSAGE, str(client.chat_postMessage.call_args_list))

    def test_listed_bot_member_id_is_accepted_too(self) -> None:
        run, _ = self.mention({"user": HOVER_USER, "bot_id": HOVER_BOT}, bots=HOVER_USER)
        run.assert_called_once()

    def test_bot_requests_decline_approvals_and_never_hand_off(self) -> None:
        run, _ = self.mention({"user": HOVER_USER, "bot_id": HOVER_BOT})
        self.assertIsNone(run.call_args.kwargs["on_approval"])
        self.assertEqual(1, run.call_args.kwargs["handoff_depth"])

    def test_a_failed_bot_request_gets_a_fixed_reply_in_the_thread(self) -> None:
        client = MagicMock()
        slack_socket_agent.post_private_failure(
            client, "C1", "1.0", HOVER_USER, "secret path /home/x failed", None, bot_request=True
        )
        client.chat_postEphemeral.assert_not_called()
        client.chat_postMessage.assert_called_once()
        posted = client.chat_postMessage.call_args.kwargs["text"]
        self.assertIn("couldn't complete", posted)
        self.assertNotIn("secret", posted)

    def test_unlisted_bot_is_ignored(self) -> None:
        run, client = self.mention({"user": "U0OTHERBOT", "bot_id": "B0OTHER"})
        run.assert_not_called()
        client.chat_postMessage.assert_not_called()

    def test_bot_mentions_stay_ignored_when_nothing_is_listed(self) -> None:
        run, client = self.mention({"user": HOVER_USER, "bot_id": HOVER_BOT}, bots="")
        run.assert_not_called()
        client.chat_postMessage.assert_not_called()

    def test_this_tag_is_never_accepted_even_when_listed(self) -> None:
        run, client = self.mention({"user": OWN_USER, "bot_id": "B0TAG"}, bots=f"B0TAG,{OWN_USER}")
        run.assert_not_called()
        client.chat_postMessage.assert_not_called()

    def test_a_person_listed_as_a_bot_is_still_checked_against_the_owners(self) -> None:
        run, client = self.mention({"user": HOVER_USER}, bots=HOVER_USER)
        run.assert_not_called()
        client.chat_postMessage.assert_called_once_with(
            channel="C1", thread_ts="1.0", text=slack_socket_agent.UNAUTHORIZED_USER_MESSAGE)

    def test_owner_mentions_still_ask_for_approvals(self) -> None:
        run, _ = self.mention({"user": "UOWNER"})
        self.assertIsNotNone(run.call_args.kwargs["on_approval"])
        self.assertEqual(0, run.call_args.kwargs["handoff_depth"])


class ApprovedBotSettingTests(unittest.TestCase):
    def test_setting_is_public_validated_and_kept_from_backends(self) -> None:
        self.assertIn("SLACK_ALLOWED_BOT_IDS", tag_config.PUBLIC)
        self.assertIn("SLACK_ALLOWED_BOT_IDS", tag_config.LABELS)
        self.assertIn("SLACK_ALLOWED_BOT_IDS", opentag_process_env.SLACK_BRIDGE_ONLY_ENV)
        for good in ("", "B0123ABCD", "B0123ABCD, U0456EFGH", "W0123"):
            self.assertIsNone(tag_config.validation_error("SLACK_ALLOWED_BOT_IDS", good), good)
        for bad in ("Hover", "C0123", "b0123"):
            self.assertIn("bot IDs", tag_config.validation_error("SLACK_ALLOWED_BOT_IDS", bad), bad)

    def test_parses_trimmed_unique_ids(self) -> None:
        with patch.dict(os.environ, {"SLACK_ALLOWED_BOT_IDS": " B1, U2, B1, "}, clear=True):
            self.assertEqual(frozenset({"B1", "U2"}), slack_socket_agent.configured_slack_bot_ids())


if __name__ == "__main__":
    unittest.main()
