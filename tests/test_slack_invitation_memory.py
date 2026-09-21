import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import slack_invitation_memory as memory, tag_config
from scripts.slack_channels import SlackChannel
from scripts.opentag_process_env import backend_environment


class InvitationMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.path = self.home / "config/settings.json"
        self.values = {
            "SLACK_CHANNEL_POLICY": "invited", "SLACK_TEAM_ID": "TTEST", "SLACK_APP_ID": "ATEST",
            "SLACK_BOT_TOKEN": "xoxb-fixture", "MFS_SLACK_TOKEN": "xoxp-history",
            "MFS_SLACK_HISTORY_DAYS": "30", "SLACK_ALLOWED_USER_IDS": "UOWNER",
            "MFS_ALLOWED_SCOPES": "file://local/keep,slack://tag-ttest/channels/old__COLD",
            "SLACK_CHANNEL_IDS": "COLD", "MFS_TOKEN": "fixture-server",
        }
        tag_config.save_config(self.path, self.values)
        self.env = dict(self.values)
        self.worker = memory.InvitationMemory(self.home, self.env)
        self.addCleanup(patch.stopall)
        patch.dict("os.environ", {}, clear=True).start()
        self.identity = patch.object(memory, "validate_slack_identity", return_value={}).start()
        self.channels = patch.object(memory.slack_channels, "list_channels").start()
        self.history = patch.object(memory.slack_channels, "slack_api", return_value={"ok": True}).start()
        self.sync = patch.object(memory.tag_cli, "sync_configured_slack_memory").start()

    def test_invitation_syncs_only_members_and_keeps_credentials_separate(self):
        self.channels.return_value = [SlackChannel("CNEW", "new", False, True),
                                      SlackChannel("GPRIVATE", "private", True, True),
                                      SlackChannel("COTHER", "other", False, False)]
        self.worker.tick()
        saved = tag_config.load_config(self.path)
        self.assertEqual(saved["SLACK_CHANNEL_IDS"], "CNEW,GPRIVATE")
        self.assertEqual(saved["SLACK_ALLOWED_USER_IDS"], "UOWNER")
        self.assertEqual(saved["MFS_TOKEN"], "fixture-server")
        self.assertIn("file://local/keep", saved["MFS_ALLOWED_SCOPES"])
        self.assertNotIn("COLD", saved["MFS_ALLOWED_SCOPES"])
        text = Path(saved["MFS_SLACK_CONNECTOR_CONFIG"]).read_text()
        self.assertIn('channel_ids = ["CNEW", "GPRIVATE"]', text)
        self.assertNotIn("COTHER", text)
        self.assertEqual(self.history.call_args_list[0].args[0], "xoxp-history")
        self.assertEqual(self.sync.call_args.args[0]["MFS_SLACK_TOKEN"], "xoxp-history")
        scoped = backend_environment(self.env, transport="slack", conversation_id="CNEW", caller_id="UOWNER")
        self.assertIn("CNEW", scoped["MFS_ALLOWED_SCOPES"])
        self.assertNotIn("GPRIVATE", scoped["MFS_ALLOWED_SCOPES"])
        self.assertNotIn("SLACK_CHANNEL_POLICY", scoped)

    def test_new_invitation_is_detected_and_unchanged_membership_is_idempotent(self):
        self.channels.return_value = [SlackChannel("CONE", "one", False, True)]
        self.worker.tick()
        self.worker.tick()
        self.assertEqual(self.sync.call_count, 1)
        self.channels.return_value.append(SlackChannel("GTWO", "two", True, True))
        self.worker.tick()
        self.assertEqual(self.sync.call_count, 2)
        self.assertIn("GTWO", self.env["SLACK_CHANNEL_IDS"])

    def test_no_members_revokes_live_access_without_empty_connector_sync(self):
        self.channels.return_value = []
        self.worker.tick()
        self.assertEqual(self.env["SLACK_CHANNEL_IDS"], "")
        self.assertEqual(self.env["MFS_ALLOWED_SCOPES"], "file://local/keep")
        self.sync.assert_not_called()
        self.history.assert_not_called()

    def test_failed_membership_lookup_does_not_reuse_stale_access(self):
        self.channels.side_effect = RuntimeError("sensitive-detail")
        self.worker.tick()
        self.assertEqual(self.env["SLACK_CHANNEL_IDS"], "")
        self.sync.assert_not_called()
        self.assertNotIn("sensitive-detail", (self.home / "state/slack-memory.json").read_text())
        self.assertEqual(tag_config.load_config(self.path), self.values)

    def test_history_failure_is_not_marked_synced_and_retries(self):
        self.channels.return_value = [SlackChannel("CONE", "one", False, True)]
        self.history.side_effect = RuntimeError("history not authorized")
        self.worker.tick()
        self.sync.assert_not_called()
        self.assertEqual(self.env["SLACK_CHANNEL_IDS"], "")
        self.history.side_effect = None
        self.worker.tick()
        self.sync.assert_called_once()

    def test_sync_failure_keeps_config_and_retries(self):
        self.channels.return_value = [SlackChannel("CONE", "one", False, True)]
        self.sync.side_effect = RuntimeError("index failed")
        self.worker.tick()
        self.assertEqual(tag_config.load_config(self.path), self.values)
        self.assertIsNone(self.worker.last_signature)
        self.assertEqual(self.env["SLACK_CHANNEL_IDS"], "")

    def test_legacy_selected_policy_has_no_discovery_or_indexing(self):
        del self.values["SLACK_CHANNEL_POLICY"]
        tag_config.save_config(self.path, self.values)
        self.worker.tick()
        self.channels.assert_not_called()
        self.identity.assert_not_called()
        self.sync.assert_not_called()
        self.assertEqual(tag_config.load_config(self.path), self.values)

    def test_changed_settings_during_checks_abort_indexing(self):
        self.channels.return_value = [SlackChannel("CONE", "one", False, True)]
        self.history.side_effect = lambda *args: tag_config.update_config(self.path, {"SLACK_CHANNEL_POLICY": "selected"})
        self.worker.tick()
        self.sync.assert_not_called()
        self.assertEqual(tag_config.load_config(self.path)["SLACK_CHANNEL_POLICY"], "selected")

    def test_leaving_one_channel_removes_only_its_live_scope(self):
        self.channels.return_value = [SlackChannel("CONE", "one", False, True), SlackChannel("GTWO", "two", True, True)]
        self.worker.tick()
        self.channels.return_value = [SlackChannel("CONE", "one", False, True)]
        self.worker.tick()
        self.assertEqual(self.env["SLACK_CHANNEL_IDS"], "CONE")
        self.assertNotIn("GTWO", self.env["MFS_ALLOWED_SCOPES"])
        self.assertNotIn("GTWO", tag_config.load_config(self.path)["MFS_ALLOWED_SCOPES"])

    def test_stopped_worker_does_not_submit_indexing(self):
        self.channels.return_value = [SlackChannel("CONE", "one", False, True)]
        self.worker.stop()
        self.worker.tick()
        self.sync.assert_not_called()


class BackgroundIdentityTests(unittest.TestCase):
    def test_wrong_workspace_and_missing_scope_fail_without_prompt(self):
        with patch.object(memory.slack_channels, "slack_api", return_value={"team_id": "TOTHER"}):
            with self.assertRaises(RuntimeError):
                memory.validate_slack_identity("xoxp-fixture", team_id="TTEST", label="History")
        with patch.object(memory.slack_channels, "slack_api", side_effect=memory.slack_channels.MissingScope("auth.test")):
            with self.assertRaises(memory.slack_channels.MissingScope):
                memory.validate_slack_identity("xoxp-fixture", team_id="TTEST", label="History")


if __name__ == "__main__":
    unittest.main()
