"""Regression checks for parity between onboarding, Settings and CLI commands."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import ANY, patch

from scripts import tag_cli, tag_control, tag_config, tag_instances, tag_reconfigure, opentag_setup
from scripts.tag_paths import initialize_instance


class FlowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "home"
        self.home = tag_instances.ensure_default(self.root).home
        self.config = self.home / "config/settings.json"
        self.env = patch.dict(os.environ, {"TAG_HOME": str(self.root), "OPENTAG_ENV_FILE": str(self.config)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def seed(self):
        channels = [opentag_setup.slack_channels.SlackChannel("COLD", "old", False, True)]
        connector = opentag_setup.write_slack_connector("TOLD", channels, "30", home=self.home)
        values = dict(tag_config.DEFAULTS, SLACK_APP_ID="AOLD", SLACK_TEAM_ID="TOLD",
                      SLACK_APP_TOKEN="xapp-old", SLACK_BOT_TOKEN="xoxb-old", SLACK_ALLOWED_USER_IDS="UOLD",
                      SLACK_CHANNEL_IDS="COLD", MFS_SLACK_TOKEN="xoxb-history", MFS_SLACK_CONNECTOR_URI="slack://tag-told",
                      MFS_SLACK_CONNECTOR_CONFIG=str(connector), MFS_ALLOWED_SCOPES="slack://tag-told/channels/old__COLD,file://local/keep",
                      OPENTAG_CUSTOM="keep")
        tag_config.save_config(self.config, values)
        opentag_setup.slack_project(self.home)
        return values

    def invoke(self, args):
        with patch.object(sys, "argv", ["tag", *args]), redirect_stdout(StringIO()) as output:
            result = tag_cli.main()
        return result, output.getvalue()

    def test_restart_stops_then_starts_and_stops_on_failure(self):
        with patch.object(tag_cli.subprocess, "call", side_effect=[0, 7]) as call:
            code, output = self.invoke(["restart"])
            self.assertEqual(code, 7)
            self.assertIn("@Tag by Hover  /  Restart", output)
            self.assertIn("https://hover.team/tag", output)
            self.assertEqual([c.args[0][-1] for c in call.call_args_list], ["stop", "start"])
            self.assertTrue(all(c.kwargs["env"]["TAG_RESTART_FLOW"] == "1" for c in call.call_args_list))
        with patch.object(tag_cli.subprocess, "call", return_value=4) as call:
            self.assertEqual(self.invoke(["restart"])[0], 4)
            self.assertEqual(call.call_count, 1)

    def test_source_snapshot_reports_added_removed_and_modified_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            first = scripts / "first.py"
            removed = scripts / "removed.py"
            first.write_text("before\n", encoding="utf-8")
            removed.write_text("remove\n", encoding="utf-8")
            before = tag_cli.source_snapshot(root)
            first.write_text("after with a different size\n", encoding="utf-8")
            removed.unlink()
            added = scripts / "added.py"
            added.write_text("new\n", encoding="utf-8")
            after = tag_cli.source_snapshot(root)
        self.assertEqual(tag_cli.changed_sources(before, after), [added, first, removed])

    def test_dev_loads_configuration_and_enters_development_loop(self):
        self.seed()
        with patch.object(tag_cli, "missing_runtime_dependencies", return_value=()), patch.object(
            tag_cli, "legacy_slack_ready", return_value=False
        ), patch.object(tag_cli, "development_loop", return_value=0) as development:
            code, _ = self.invoke(["dev"])
        self.assertEqual(code, 0)
        development.assert_called_once_with(self.home)
        self.assertEqual(os.environ["OPENTAG_BACKEND"], "codex")

    def test_development_loop_owns_and_cleans_up_slack_bridge(self):
        with patch.object(tag_cli, "runtime_identity", return_value={"active_release": False}), patch.object(
            tag_cli.subprocess, "call", return_value=0
        ), patch.object(tag_cli, "stop_process") as stop, patch.object(
            tag_cli, "start_development_slack"
        ) as start, patch.object(tag_cli, "source_snapshot", return_value={}), patch.object(
            tag_cli.time, "sleep", side_effect=KeyboardInterrupt
        ), redirect_stdout(StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                tag_cli.development_loop(self.home)
        start.assert_called_once_with(self.home)
        self.assertEqual(
            stop.call_args_list,
            [
                unittest.mock.call(self.home, "slack"),
                unittest.mock.call(self.home, "slack"),
                unittest.mock.call(self.home, "mfs"),
            ],
        )

    def test_logs_are_bounded_redacted_and_offer_follow_mode(self):
        log = self.home / "state/slack.log"
        log.write_text("first\nsecond xoxb-secret-value\nthird\n", encoding="utf-8")
        code, output = self.invoke(["logs", "--limit", "2"])
        self.assertEqual(code, 0)
        self.assertNotIn("first", output)
        self.assertNotIn("xoxb-secret-value", output)
        self.assertIn("second <redacted>", output)
        self.assertIn("tag logs --follow", output)

    def test_status_and_plain_tag_use_same_checks_and_output(self):
        self.seed()
        with patch.object(tag_control.ui.display, "backend_status", return_value=("Signed in · task not tested", True)), patch.object(
            tag_cli, "healthy", return_value=True
        ), patch.object(tag_cli, "slack_ready", return_value=True), patch.object(tag_cli, "process_for", return_value=None), patch.object(
            tag_control.shutil, "which", return_value="codex"
        ):
            self.assertEqual(self.invoke([])[1], self.invoke(["status"])[1])
            code, output = self.invoke(["status", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)["backend"]["authentication"], "signed_in")

    def test_test_setup_is_separate_no_start_and_filters_live_credentials(self):
        self.seed()
        before = self.config.read_bytes()
        with patch.object(sys.stdin, "isatty", return_value=True), patch.object(tag_cli.subprocess, "call", return_value=0) as call, patch.dict(
            os.environ, {"SLACK_BOT_TOKEN": "do-not-inherit"}
        ):
            self.assertEqual(self.invoke(["setup", "--test"])[0], 0)
            self.assertIn("--no-start", call.call_args.args[0])
            self.assertIn("--test-mode", call.call_args.args[0])
            env = call.call_args.kwargs["env"]
            self.assertEqual(env["TAG_HOME"], str(self.root))
            self.assertEqual(
                env["TAG_INSTANCE_HOME"], str(self.home / "testing/onboarding")
            )
            self.assertNotIn("OPENTAG_ENV_FILE", env)
            self.assertNotIn("SLACK_BOT_TOKEN", env)
        self.assertEqual(self.config.read_bytes(), before)

    def test_completed_setup_only_reports_health(self):
        self.seed()
        with patch.object(tag_control, "status_report", return_value={"state": "stopped"}), patch.object(
            tag_control, "show_status"
        ), patch.object(opentag_setup, "guided_setup") as guided, patch.object(sys.stdin, "isatty", return_value=True), patch.object(
            sys, "argv", ["setup", "--config", str(self.config)]
        ), redirect_stdout(StringIO()):
            self.assertEqual(opentag_setup.main(), 0)
        guided.assert_not_called()

    def test_interrupted_complete_configuration_resumes_instead_of_skipping(self):
        self.seed()
        tag_config.save_config(self.config.with_name("setup-progress.json"), {"completed": False})
        with patch.object(opentag_setup, "guided_setup", return_value=0) as guided, patch.object(
            tag_control, "status_report"
        ) as status, patch.object(sys.stdin, "isatty", return_value=True), patch.object(
            sys, "argv", ["setup", "--config", str(self.config)]
        ), redirect_stdout(StringIO()):
            self.assertEqual(opentag_setup.main(), 0)
        guided.assert_called_once()
        status.assert_not_called()
        self.assertTrue(json.loads(self.config.with_name("setup-progress.json").read_text())["completed"])

    def test_review_forwards_no_start_and_only_success_writes_receipt(self):
        self.seed()
        receipt = self.home / "state/receipt.json"
        with patch.object(opentag_setup, "guided_setup", side_effect=opentag_setup.ui.Paused()) as guided, patch.object(
            sys.stdin, "isatty", return_value=True
        ), patch.object(sys, "argv", ["setup", "--config", str(self.config), "--review", "--no-start", "--completion-file", str(receipt)]), redirect_stdout(StringIO()):
            self.assertEqual(opentag_setup.main(), 0)
        guided.assert_called_once_with(
            self.config.resolve(),
            start_services=False,
            review_channels=False,
            telemetry_session=ANY,
        )
        self.assertFalse(receipt.exists())

    def test_paused_setup_says_onboarding_is_incomplete(self):
        with patch.object(
            opentag_setup, "guided_setup", side_effect=opentag_setup.ui.Paused()
        ), patch.object(sys.stdin, "isatty", return_value=True), patch.object(
            sys, "argv", ["setup", "--config", str(self.config)]
        ), redirect_stdout(StringIO()) as output:
            self.assertEqual(opentag_setup.main(), 0)

        self.assertIn("Setup is incomplete", output.getvalue())
        self.assertIn("run tag setup to continue", output.getvalue())

    def test_settings_channels_use_plural_guided_flow(self):
        self.seed()
        with patch.object(tag_control.ui, "keyboard_available", return_value=True), patch.object(
            tag_control.ui, "choose", side_effect=[0, 3, 4]
        ), patch.object(tag_reconfigure, "edit") as edit, redirect_stdout(StringIO()):
            tag_control.settings_menu(self.home)
        edit.assert_called_once_with(self.home, "channels")

    def test_cancelled_draft_keeps_active_settings_and_can_resume(self):
        self.seed()
        original = self.config.read_bytes()
        with patch.object(tag_reconfigure.lifecycle, "process_for", return_value=None), patch.object(
            tag_reconfigure.ui, "choose", return_value=0
        ), patch.object(tag_reconfigure.subprocess, "call", return_value=0) as call, redirect_stdout(StringIO()):
            tag_reconfigure.edit(self.home, "app")
            first = call.call_args.kwargs["env"]["TAG_INSTANCE_HOME"]
            draft = tag_config.read_config(Path(first) / "config/settings.json")
            self.assertNotIn("SLACK_APP_TOKEN", draft)
            self.assertNotIn("SLACK_CHANNEL_IDS", draft)
            self.assertEqual(draft["OPENTAG_CUSTOM"], "keep")
            self.assertEqual(draft["MFS_ALLOWED_SCOPES"], "file://local/keep")
            tag_reconfigure.edit(self.home, "app")
            self.assertEqual(call.call_args.kwargs["env"]["TAG_HOME"], str(self.root))
            self.assertEqual(call.call_args.kwargs["env"]["TAG_INSTANCE_HOME"], first)
        self.assertEqual(self.config.read_bytes(), original)

    def make_draft(self):
        original = self.seed()
        draft = self.home / "integrations/setup-drafts/settings-fixture"
        initialize_instance(draft)
        opentag_setup.slack_project(draft)
        connector = opentag_setup.write_slack_connector("TOLD", [opentag_setup.slack_channels.SlackChannel("CNEW", "new", False, True)], "7", home=draft)
        values = dict(original, SLACK_CHANNEL_IDS="CNEW", MFS_SLACK_CONNECTOR_CONFIG=str(connector), MFS_SLACK_HISTORY_DAYS="7")
        tag_config.save_config(draft / "config/settings.json", values)
        return original, draft

    def test_commit_keeps_backup_and_uses_durable_connector(self):
        original, draft = self.make_draft()
        with patch.object(tag_reconfigure.lifecycle, "process_for", return_value=None):
            tag_reconfigure.commit(self.home, draft, original)
        saved = tag_config.read_config(self.config)
        self.assertEqual(saved["SLACK_CHANNEL_IDS"], "CNEW")
        self.assertTrue(Path(saved["MFS_SLACK_CONNECTOR_CONFIG"]).is_file())
        self.assertEqual(tag_config.read_config(draft / "previous-settings.json"), original)
        self.assertTrue((draft / "previous-slack-cli").is_dir())

    def test_commit_removes_superseded_managed_credential(self):
        original, draft = self.make_draft()
        tag_config.update_config(
            draft / "config/settings.json", {"MFS_URL": "http://Localhost:13619"}
        )
        old_credential = tag_reconfigure.tag_credentials.write_slack_history(
            self.home, "xoxb-old-history"
        )
        connector = Path(original["MFS_SLACK_CONNECTOR_CONFIG"])
        connector.write_text(
            connector.read_text().replace(
                'token = "env:MFS_SLACK_TOKEN"',
                "token = " + json.dumps("file:" + str(old_credential)),
            ),
            encoding="utf-8",
        )

        with patch.object(tag_reconfigure.lifecycle, "process_for", return_value=None):
            tag_reconfigure.commit(self.home, draft, original)

        self.assertFalse(old_credential.exists())
        saved = tag_config.read_config(self.config)
        active_connector = Path(saved["MFS_SLACK_CONNECTOR_CONFIG"])
        self.assertIn("file:", active_connector.read_text(encoding="utf-8"))

    def test_commit_conflict_and_write_failure_keep_active_setup(self):
        original, draft = self.make_draft()
        with patch.object(tag_reconfigure.lifecycle, "process_for", return_value=None):
            tag_config.update_config(self.config, {"OPENTAG_BOT_NAME": "Changed"})
            with self.assertRaisesRegex(RuntimeError, "Settings changed"):
                tag_reconfigure.commit(self.home, draft, original)
            tag_config.save_config(self.config, original)
            save = tag_config.save_config
            def fail(path, values):
                if path == self.config:
                    raise OSError("write failure")
                return save(path, values)
            with patch.object(tag_config, "save_config", side_effect=fail), self.assertRaises(OSError):
                tag_reconfigure.commit(self.home, draft, original)
        self.assertEqual(tag_config.read_config(self.config), original)
        self.assertTrue((self.home / "integrations/slack-cli/.slack/config.json").is_file())
        self.assertFalse((self.home / "state/start.lock").exists())

    def test_running_services_block_guided_changes_without_launching(self):
        self.seed()
        original = self.config.read_bytes()
        with patch.object(tag_reconfigure.lifecycle, "process_for", return_value=object()), patch.object(
            tag_reconfigure.subprocess, "call"
        ) as call, redirect_stdout(StringIO()):
            tag_reconfigure.edit(self.home, "channels")
        call.assert_not_called()
        self.assertEqual(self.config.read_bytes(), original)

    def test_successful_guided_channel_change_requires_apply_and_replaces_scopes(self):
        original = self.seed()
        def child(command, *, env):
            draft = Path(env["TAG_INSTANCE_HOME"])
            path = draft / "config/settings.json"
            values = tag_config.read_config(path)
            self.assertNotIn("old__COLD", values["MFS_ALLOWED_SCOPES"])
            self.assertEqual(tag_config.read_config(self.config), original)
            self.assertIn("--review-channels", command)
            self.assertIn("--no-start", command)
            channels = [opentag_setup.slack_channels.SlackChannel("CNEW", "new", False, True)]
            connector = opentag_setup.write_slack_connector("TOLD", channels, "30", home=draft)
            values.update(SLACK_CHANNEL_IDS="CNEW", MFS_ALLOWED_SCOPES="file://local/keep,slack://tag-told/channels/new__CNEW",
                          MFS_SLACK_CONNECTOR_CONFIG=str(connector))
            tag_config.save_config(path, values)
            tag_config.save_config(draft / "state/setup-approved.json", {"approved": True})
            return 0
        with patch.object(tag_reconfigure.lifecycle, "process_for", return_value=None), patch.object(
            tag_reconfigure.ui, "choose", side_effect=[0, 0]
        ) as choose, patch.object(tag_reconfigure.subprocess, "call", side_effect=child), redirect_stdout(StringIO()):
            tag_reconfigure.edit(self.home, "channels")
        self.assertEqual(choose.call_args.args[0], "Apply validated changes?")
        saved = tag_config.read_config(self.config)
        self.assertEqual(saved["SLACK_CHANNEL_IDS"], "CNEW")
        self.assertEqual(saved["SLACK_CHANNEL_POLICY"], "selected")
        self.assertNotIn("old__COLD", saved["MFS_ALLOWED_SCOPES"])
        self.assertEqual(saved["OPENTAG_CUSTOM"], "keep")

    def test_declining_apply_does_not_replace_active_configuration(self):
        original, draft = self.make_draft()
        def child(*args, **kwargs):
            tag_config.save_config(draft / "state/setup-approved.json", {"approved": True})
            return 0
        with patch.object(tag_reconfigure.ui, "choose", return_value=1), patch.object(
            tag_reconfigure.subprocess, "call", side_effect=child
        ), patch.object(tag_reconfigure, "commit") as commit, redirect_stdout(StringIO()):
            tag_reconfigure.run_draft(self.home, draft, "channels", original)
        commit.assert_not_called()
        self.assertEqual(tag_config.read_config(self.config), original)
