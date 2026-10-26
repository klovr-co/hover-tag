import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import slack_invitation_memory as memory, slack_channel_names, tag_config
from scripts.slack_channels import SlackChannel


class InvitationChannelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.path = self.home / "config/settings.json"
        self.values = {
            "SLACK_CHANNEL_POLICY": "invited", "SLACK_TEAM_ID": "TTEST", "SLACK_APP_ID": "ATEST",
            "SLACK_BOT_TOKEN": "xoxb-fixture", "SLACK_ALLOWED_USER_IDS": "UOWNER", "SLACK_CHANNEL_IDS": "COLD",
        }
        tag_config.save_config(self.path, self.values)
        self.env = dict(self.values)
        self.worker = memory.InvitationMemory(self.home, self.env)
        self.addCleanup(patch.stopall)
        patch.dict("os.environ", {}, clear=True).start()
        self.identity = patch.object(memory, "validate_slack_identity", return_value={}).start()
        self.channels = patch.object(memory.slack_channels, "list_channels").start()

    def status(self) -> dict:
        return json.loads((self.home / "state/slack-memory.json").read_text(encoding="utf-8"))

    def test_the_tag_answers_in_every_channel_it_is_a_member_of(self):
        self.channels.return_value = [SlackChannel("CNEW", "new", False, True),
                                      SlackChannel("GPRIVATE", "private", True, True),
                                      SlackChannel("COTHER", "other", False, False)]
        self.worker.tick()
        self.assertTrue(self.worker.ready_for_requests())
        self.assertEqual("CNEW,GPRIVATE", self.env["SLACK_CHANNEL_IDS"])
        saved = tag_config.load_config(self.path)
        self.assertEqual("CNEW,GPRIVATE", saved["SLACK_CHANNEL_IDS"])
        self.assertEqual("UOWNER", saved["SLACK_ALLOWED_USER_IDS"])
        self.assertEqual({"CNEW": "new", "GPRIVATE": "private"}, slack_channel_names.read(self.home, "TTEST"))
        self.assertEqual(("ready", 2), (self.status()["state"], self.status()["channels"]))
        # Nothing is indexed or copied: no history credential, connector or index settings.
        self.assertFalse([key for key in saved if key.startswith("MFS_")])

    def test_unchanged_membership_leaves_the_settings_alone(self):
        self.channels.return_value = [SlackChannel("CONE", "one", False, True)]
        self.worker.tick()
        written = self.path.stat().st_mtime_ns
        self.worker.tick()
        self.assertEqual(written, self.path.stat().st_mtime_ns)

    def test_a_tag_in_no_channels_yet_is_ready_and_waits_for_an_invitation(self):
        self.channels.return_value = []
        self.worker.tick()
        self.assertTrue(self.worker.ready_for_requests())
        self.assertEqual("", self.env["SLACK_CHANNEL_IDS"])
        self.assertEqual("no_joined_channels", self.status()["state"])

    def test_leaving_a_channel_removes_it_at_once(self):
        self.channels.return_value = [SlackChannel("CONE", "one", False, True), SlackChannel("GTWO", "two", True, True)]
        self.worker.tick()
        self.channels.return_value = [SlackChannel("CONE", "one", False, True)]
        self.worker.tick()
        self.assertEqual("CONE", self.env["SLACK_CHANNEL_IDS"])
        self.assertEqual("CONE", tag_config.load_config(self.path)["SLACK_CHANNEL_IDS"])

    def test_failed_membership_lookup_does_not_reuse_stale_access(self):
        self.channels.side_effect = RuntimeError("sensitive-detail")
        self.worker.tick()
        self.assertFalse(self.worker.ready_for_requests())
        self.assertEqual("", self.env["SLACK_CHANNEL_IDS"])
        self.assertNotIn("sensitive-detail", (self.home / "state/slack-memory.json").read_text(encoding="utf-8"))
        self.assertEqual(("needs_attention", "membership"), (self.status()["state"], self.status()["check"]))
        self.assertEqual(self.values, tag_config.load_config(self.path))

    def test_selected_policy_has_no_discovery(self):
        del self.values["SLACK_CHANNEL_POLICY"]
        tag_config.save_config(self.path, self.values)
        self.worker.tick()
        self.channels.assert_not_called()
        self.identity.assert_not_called()
        self.assertEqual("COLD", self.env["SLACK_CHANNEL_IDS"])
        self.assertTrue(self.worker.ready_for_requests())

    def test_changed_settings_during_the_check_are_not_overwritten(self):
        def changed(*_args, **_kwargs):
            tag_config.update_config(self.path, {"SLACK_CHANNEL_POLICY": "selected"})
            return [SlackChannel("CONE", "one", False, True)]
        self.channels.side_effect = changed
        self.worker.tick()
        self.assertFalse(self.worker.ready_for_requests())
        saved = tag_config.load_config(self.path)
        self.assertEqual(("selected", "COLD"), (saved["SLACK_CHANNEL_POLICY"], saved["SLACK_CHANNEL_IDS"]))

    def test_refresh_checks_membership_without_waiting_for_the_minute(self):
        self.channels.return_value = []
        calls = []
        self.worker.tick = lambda: (calls.append(1), self.worker.stop() if len(calls) == 2 else None)
        self.worker.start()
        self.worker.thread.join(0.2)
        self.assertEqual(len(calls), 1)  # Idle: the next check is a minute away.
        self.worker.refresh()
        self.worker.thread.join(2)
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.worker.thread.is_alive())

    def test_background_failure_revokes_previous_readiness(self):
        calls = 0

        def tick():
            nonlocal calls
            calls += 1
            if calls == 1:
                self.worker.ready_event.set()
                return
            raise RuntimeError("settings unreadable")

        with patch.object(self.worker, "tick", side_effect=tick), patch.object(
            self.worker.stop_event, "is_set", side_effect=[False, False, True]
        ), patch.object(self.worker.wake_event, "wait"), patch.object(
            memory.threading, "Thread"
        ) as thread:
            self.worker.start()
            thread.call_args.kwargs["target"]()

        self.assertFalse(self.worker.ready_for_requests())
        self.assertEqual("", self.env["SLACK_CHANNEL_IDS"])


class BackgroundIdentityTests(unittest.TestCase):
    def test_wrong_workspace_and_missing_scope_fail_without_prompt(self):
        with patch.object(memory.slack_channels, "slack_api", return_value={"team_id": "TOTHER"}):
            with self.assertRaises(RuntimeError):
                memory.validate_slack_identity("xoxb-fixture", team_id="TTEST", label="Bot token")
        with patch.object(memory.slack_channels, "slack_api", side_effect=memory.slack_channels.MissingScope("auth.test")):
            with self.assertRaises(memory.slack_channels.MissingScope):
                memory.validate_slack_identity("xoxb-fixture", team_id="TTEST", label="Bot token")


if __name__ == "__main__":
    unittest.main()
