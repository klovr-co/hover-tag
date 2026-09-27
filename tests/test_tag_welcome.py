from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import tag_welcome


class WelcomeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.values = {
            "SLACK_ALLOWED_USER_IDS": "UOWNER", "SLACK_TEAM_ID": "TTEAM",
            "SLACK_APP_ID": "AAPP", "SLACK_BOT_TOKEN": "xoxb-fixture",
        }
        self.path = self.home / "state/welcome-v1/TTEAM-AAPP-UOWNER.json"
        api = patch.object(tag_welcome.slack_channels, "slack_api_post", return_value={
            "ok": True, "channel": "DPRIVATE", "ts": "123.456",
        })
        self.api = api.start()
        self.addCleanup(api.stop)

    def test_first_start_of_new_or_older_installation_sends_once(self):
        self.assertIn("Sent", tag_welcome.send_once(self.home, self.values))
        self.assertIsNone(tag_welcome.send_once(self.home, self.values))
        self.api.assert_called_once()
        token, method, payload = self.api.call_args.args
        self.assertEqual((token, method), ("xoxb-fixture", "chat.postMessage"))
        self.assertEqual(payload["channel"], "UOWNER")
        self.assertIn(tag_welcome.COMMUNITY_INVITE_URL, payload["text"])
        self.assertIn("Send me a task here", payload["text"])
        self.assertEqual(payload["unfurl_links"], "false")
        blocks = json.loads(payload["blocks"])
        self.assertIn(f"<{tag_welcome.COMMUNITY_INVITE_URL}|Join the Hover Community>", blocks[-1]["text"]["text"])
        self.assertTrue(all(block["type"] == "section" for block in blocks))
        self.assertIn("Help me plan my week", blocks[1]["text"]["text"])
        receipt = json.loads(self.path.read_text())
        self.assertEqual(receipt["status"], "sent")
        self.assertEqual(receipt["channel"], "DPRIVATE")
        self.assertEqual(receipt["ts"], "123.456")
        self.assertNotIn("xoxb-fixture", self.path.read_text())

    def test_failure_remains_pending_and_retries_with_same_message_id(self):
        self.api.side_effect = [
            tag_welcome.slack_channels.SlackChannelError("unavailable"),
            {"ok": True, "channel": "DPRIVATE", "ts": "123.456"},
        ]
        with self.assertRaises(tag_welcome.slack_channels.SlackChannelError):
            tag_welcome.send_once(self.home, self.values)
        self.assertEqual(json.loads(self.path.read_text())["status"], "pending")
        tag_welcome.send_once(self.home, self.values)
        tag_welcome.send_once(self.home, self.values)
        self.assertEqual(self.api.call_count, 2)
        self.assertEqual(
            self.api.call_args_list[0].args[2]["client_msg_id"],
            self.api.call_args_list[1].args[2]["client_msg_id"],
        )

    def test_unconfirmed_response_never_commits_delivery(self):
        for response in (
            {"ok": False}, {"ok": True},
            {"ok": True, "channel": "CPUBLIC", "ts": "123.456"},
            {"ok": True, "channel": "DPRIVATE", "ts": ""},
        ):
            with self.subTest(response=response):
                self.api.return_value = response
                with self.assertRaises(ValueError):
                    tag_welcome.send_once(self.home, self.values)
                self.assertEqual(json.loads(self.path.read_text())["status"], "pending")

    def test_interruption_after_slack_success_reuses_pending_id(self):
        save = tag_welcome.tag_config.save_config
        count = 0
        def fail_receipt(path, value):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("disk full")
            save(path, value)
        with patch.object(tag_welcome.tag_config, "save_config", side_effect=fail_receipt):
            with self.assertRaises(OSError):
                tag_welcome.send_once(self.home, self.values)
        self.assertEqual(json.loads(self.path.read_text())["status"], "pending")
        tag_welcome.send_once(self.home, self.values)
        self.assertEqual(
            self.api.call_args_list[0].args[2]["client_msg_id"],
            self.api.call_args_list[1].args[2]["client_msg_id"],
        )

    def test_storage_failure_prevents_sending(self):
        with patch.object(tag_welcome.tag_config, "save_config", side_effect=OSError):
            with self.assertRaises(OSError):
                tag_welcome.send_once(self.home, self.values)
        self.api.assert_not_called()

    def test_multiple_or_missing_users_are_not_broadcast_to(self):
        for users in ("", "UONE,UTWO"):
            with self.subTest(users=users):
                self.assertIn("Skipped", tag_welcome.send_once(
                    self.home, {**self.values, "SLACK_ALLOWED_USER_IDS": users}
                ))
        self.api.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_invalid_identity_and_corrupt_receipt_do_not_send(self):
        with self.assertRaises(ValueError):
            tag_welcome.send_once(self.home, {**self.values, "SLACK_ALLOWED_USER_IDS": "CPUBLIC"})
        self.path.parent.mkdir(parents=True)
        for content in ("{broken", "[]", '{"status":"sent"}'):
            self.path.write_text(content)
            with self.assertRaises(ValueError):
                tag_welcome.send_once(self.home, self.values)
        self.api.assert_not_called()

    def test_identity_changes_do_not_overwrite_previous_receipts(self):
        tag_welcome.send_once(self.home, self.values)
        original = self.path.read_bytes()
        tag_welcome.send_once(self.home, {**self.values, "SLACK_APP_ID": "AOTHER"})
        tag_welcome.send_once(self.home, self.values)
        self.assertEqual(self.api.call_count, 2)
        self.assertEqual(self.path.read_bytes(), original)

    def test_disabled_dm_replies_suggest_channel_task(self):
        tag_welcome.send_once(self.home, {**self.values, "OPENTAG_SLACK_DM_ENABLED": "0"})
        message = self.api.call_args.args[2]["text"]
        self.assertIn("Mention me in a connected Slack channel", message)
        self.assertNotIn("Send me a task here", message)


if __name__ == "__main__":
    unittest.main()
