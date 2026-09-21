import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import tag_paths, tag_reset


class ResetTests(unittest.TestCase):
    def seed_app(self):
        self.seed()
        self.config.write_text(json.dumps({"SLACK_APP_ID": "AOLD", "SLACK_TEAM_ID": "TTEST",
                                           "OPENTAG_BOT_NAME": "OpenMax", "SLACK_BOT_TOKEN": "xoxb-private"}))
        directory = self.home / "integrations/slack-cli/.slack"
        directory.mkdir()
        (directory / "apps.dev.json").write_text(json.dumps({"TTEST": {"app_id": "AOLD", "team_id": "TTEST"}}))

    def test_keep_app_is_default_and_displays_exact_saved_identity(self):
        self.seed_app()
        with patch.object(tag_reset.ui, "choose", side_effect=[1, 0]) as choose, patch(
            "subprocess.call", return_value=0
        ), patch("subprocess.run") as remote, redirect_stdout(StringIO()) as output:
            self.assertEqual(tag_reset.reset_and_setup(self.home, self.lifecycle), 0)
        self.assertEqual(choose.call_args.kwargs["default"], 0)
        for value in ("OpenMax", "App ID: AOLD", "Workspace Team ID: TTEST"):
            self.assertIn(value, output.getvalue())
        self.assertNotIn("xoxb-private", output.getvalue())
        remote.assert_not_called()
        kept = json.loads((self.home / "integrations/slack-cli/tag-kept-app.json").read_text())
        self.assertEqual(kept, {"app_id": "AOLD", "team_id": "TTEST", "saved_bot_name": "OpenMax"})
        self.assertFalse(self.config.exists())

    def test_delete_requires_matching_typed_app_id_before_any_change(self):
        self.seed_app()
        with patch.object(tag_reset.ui, "choose", side_effect=[1, 1]), patch(
            "builtins.input", return_value="AOTHER"
        ), patch.object(tag_reset.shutil, "which", return_value="/fixture/slack"), patch(
            "subprocess.run"
        ) as remote, redirect_stdout(StringIO()):
            self.assertEqual(tag_reset.reset_and_setup(self.home, self.lifecycle), 0)
        self.assertTrue(self.config.exists())
        self.lifecycle.stop_process.assert_not_called()
        remote.assert_not_called()

    def test_confirmed_deletion_targets_app_and_team_and_preserves_backup(self):
        self.seed_app()
        with patch.object(tag_reset.ui, "choose", side_effect=[1, 1]), patch(
            "builtins.input", return_value="AOLD"
        ), patch.object(tag_reset.shutil, "which", return_value="/fixture/slack"), patch(
            "subprocess.run", return_value=SimpleNamespace(returncode=0)
        ) as remote, patch("subprocess.call", return_value=0) as setup, redirect_stdout(StringIO()):
            self.assertEqual(tag_reset.reset_and_setup(self.home, self.lifecycle), 0)
        self.assertEqual(remote.call_args.args[0], ["/fixture/slack", "app", "delete", "--app", "AOLD",
                                                  "--team", "TTEST", "--force", "--skip-update", "--no-color"])
        backup = next((self.home / "config/backups").iterdir())
        self.assertTrue((backup / "slack-cli/.slack/apps.dev.json").exists())
        self.assertEqual(json.loads((backup / "app-deletion.json").read_text())["status"], "deleted")
        setup.assert_called_once()

    def test_unverified_deletion_keeps_backup_and_does_not_restart_setup(self):
        self.seed_app()
        with patch.object(tag_reset.ui, "choose", side_effect=[1, 1]), patch(
            "builtins.input", return_value="AOLD"
        ), patch.object(tag_reset.shutil, "which", return_value="/fixture/slack"), patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("slack", 90)
        ), patch("subprocess.call") as setup, redirect_stdout(StringIO()) as output:
            self.assertEqual(tag_reset.reset_and_setup(self.home, self.lifecycle), 1)
        setup.assert_not_called()
        backup = next((self.home / "config/backups").iterdir())
        self.assertTrue((backup / "settings.json").exists())
        self.assertEqual(json.loads((backup / "app-deletion.json").read_text())["status"], "unverified")
        self.assertIn("not confirmed", output.getvalue())

    def test_conflicting_app_link_prevents_reset_and_deletion(self):
        self.seed_app()
        (self.home / "integrations/slack-cli/.slack/apps.dev.json").write_text(
            '{"TTEST":{"app_id":"AOTHER","team_id":"TTEST"}}')
        with patch.object(tag_reset.ui, "choose", side_effect=[1, 1]), redirect_stdout(StringIO()), self.assertRaisesRegex(RuntimeError, "same single app"):
            tag_reset.reset_and_setup(self.home, self.lifecycle)
        self.lifecycle.stop_process.assert_not_called()
        self.assertTrue(self.config.exists())

    def test_changed_app_after_confirmation_is_rejected_under_lock(self):
        self.seed_app()
        app = tag_reset.selected_app(self.home)
        self.config.write_text('{"SLACK_APP_ID":"ANEW","SLACK_TEAM_ID":"TTEST"}')
        with self.assertRaisesRegex(RuntimeError, "changed during confirmation"):
            tag_reset.archive_setup(self.home, self.lifecycle, expected_app=app)
        self.lifecycle.stop_process.assert_not_called()
        self.assertTrue(self.config.exists())

    def test_missing_slack_cli_leaves_setup_intact(self):
        self.seed_app()
        with patch.object(tag_reset.ui, "choose", side_effect=[1, 1]), patch.object(
            tag_reset.shutil, "which", return_value=None
        ), redirect_stdout(StringIO()), self.assertRaisesRegex(RuntimeError, "Slack CLI is unavailable"):
            tag_reset.reset_and_setup(self.home, self.lifecycle)
        self.lifecycle.stop_process.assert_not_called()
        self.assertTrue(self.config.exists())

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "Tag home"
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("TAG_", "OPENTAG_", "SLACK_", "MFS_"))}
        environment["TAG_HOME"] = str(self.home)
        self.environment = patch.dict(os.environ, environment, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.lifecycle = SimpleNamespace(initialize=tag_paths.initialize, stop_process=Mock(),
                                         ROOT=Path(__file__).resolve().parents[1])

    def seed(self):
        tag_paths.initialize(self.home)
        self.config = self.home / "config/settings.json"
        self.config.write_text('{"SLACK_BOT_TOKEN":"xoxb-private"}')
        project = self.home / "integrations/slack-cli"
        project.mkdir()
        (project / "tag-create.json").write_text('{"app_id":"AOLD"}')
        (self.home / "state/slack-memory.json").write_text('{"state":"sync_requested"}')
        (self.home / "workspace/notes.txt").write_text("keep workspace")
        (self.home / "state/memory").mkdir()
        (self.home / "state/memory/index.db").write_text("keep indexed data")

    def test_archive_preserves_data_and_removes_resume_checkpoints(self):
        self.seed()
        backup = tag_reset.archive_setup(self.home, self.lifecycle)
        self.assertFalse(self.config.exists())
        self.assertFalse((self.home / "integrations/slack-cli").exists())
        self.assertFalse((self.home / "state/slack-memory.json").exists())
        self.assertIn("xoxb-private", (backup / "settings.json").read_text())
        self.assertIn("AOLD", (backup / "slack-cli/tag-create.json").read_text())
        self.assertTrue((backup / "restore-paths.json").exists())
        self.assertEqual((self.home / "workspace/notes.txt").read_text(), "keep workspace")
        self.assertEqual((self.home / "state/memory/index.db").read_text(), "keep indexed data")
        self.assertEqual([call.args[1] for call in self.lifecycle.stop_process.call_args_list], ["slack", "mfs"])
        if os.name != "nt":
            self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.home / "state/start.lock").exists())
        self.assertFalse(self.config.with_suffix(".json.lock").exists())

    def test_cancel_and_pause_do_not_initialize_or_stop(self):
        for response in (0, tag_reset.ui.Paused(), KeyboardInterrupt()):
            with self.subTest(response=response), patch.object(tag_reset.ui, "choose") as choose, redirect_stdout(StringIO()):
                if isinstance(response, BaseException):
                    choose.side_effect = response
                else:
                    choose.return_value = response
                self.assertEqual(tag_reset.reset_and_setup(self.home, self.lifecycle), 0)
                self.assertEqual(choose.call_args.kwargs["default"], 0)
                self.assertFalse(self.home.exists())
                self.lifecycle.stop_process.assert_not_called()

    def test_stop_failure_keeps_config_and_checkpoints(self):
        self.seed()
        self.lifecycle.stop_process.side_effect = RuntimeError("could not stop")
        with self.assertRaisesRegex(RuntimeError, "could not stop"):
            tag_reset.archive_setup(self.home, self.lifecycle)
        self.assertTrue(self.config.exists())
        self.assertTrue((self.home / "integrations/slack-cli/tag-create.json").exists())
        self.assertFalse((self.home / "state/start.lock").exists())

    def test_start_lock_blocks_reset_without_stopping(self):
        self.seed()
        (self.home / "state/start.lock").mkdir()
        with self.assertRaisesRegex(RuntimeError, "start or reset"):
            tag_reset.archive_setup(self.home, self.lifecycle)
        self.assertTrue(self.config.exists())
        self.lifecycle.stop_process.assert_not_called()

    def test_settings_lock_blocks_reset_without_stopping(self):
        self.seed()
        lock = self.config.with_suffix(".json.lock")
        lock.touch()
        with self.assertRaisesRegex(RuntimeError, "settings update"):
            tag_reset.archive_setup(self.home, self.lifecycle)
        self.assertTrue(lock.exists())
        self.assertTrue(self.config.exists())
        self.assertFalse((self.home / "state/start.lock").exists())
        self.lifecycle.stop_process.assert_not_called()

    def test_move_failure_restores_already_moved_files(self):
        self.seed()
        move = tag_reset.shutil.move
        def fail_project(source, destination):
            if source == str(self.home / "integrations/slack-cli"):
                raise OSError("move failed")
            return move(source, destination)
        with patch.object(tag_reset.shutil, "move", side_effect=fail_project), self.assertRaisesRegex(RuntimeError, "restored"):
            tag_reset.archive_setup(self.home, self.lifecycle)
        self.assertIn("xoxb-private", self.config.read_text())
        self.assertTrue((self.home / "integrations/slack-cli/tag-create.json").exists())

    def test_custom_config_and_invalid_json_are_recoverable(self):
        self.seed()
        custom = Path(self.temporary.name) / "custom.json"
        custom.write_text("broken json")
        with patch.dict(os.environ, {"OPENTAG_ENV_FILE": str(custom)}):
            backup = tag_reset.archive_setup(self.home, self.lifecycle)
        self.assertEqual((backup / "settings.json").read_text(), "broken json")
        self.assertFalse(custom.exists())
        self.assertTrue(self.config.exists())

    def test_confirm_launches_setup_and_propagates_its_result(self):
        self.seed()
        with patch.object(tag_reset.ui, "choose", return_value=1), patch(
            "subprocess.call", return_value=130
        ) as launch, redirect_stdout(StringIO()) as output:
            self.assertEqual(tag_reset.reset_and_setup(self.home, self.lifecycle), 130)
        self.assertEqual(launch.call_args.args[0][-1], "setup")
        self.assertFalse(self.config.exists())
        self.assertNotIn("xoxb-private", output.getvalue())
        self.assertIn("backed up at", output.getvalue())

    def test_reset_requires_a_terminal_and_does_not_write(self):
        result = subprocess.run([sys.executable, str(self.lifecycle.ROOT / "scripts/tag_cli.py"), "reset"],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.home.exists())

    def test_directory_config_override_is_never_archived(self):
        self.seed()
        with patch.dict(os.environ, {"OPENTAG_ENV_FILE": str(self.home)}), self.assertRaisesRegex(RuntimeError, "path type"):
            tag_reset.archive_setup(self.home, self.lifecycle)
        self.assertTrue(self.config.exists())
        self.lifecycle.stop_process.assert_not_called()

    @unittest.skipIf(os.name == "nt", "Symlink creation may require elevation")
    def test_symlinked_checkpoint_is_not_moved(self):
        self.seed()
        external = Path(self.temporary.name) / "external.json"
        external.write_text("keep")
        self.config.unlink()
        self.config.symlink_to(external)
        with self.assertRaisesRegex(RuntimeError, "symlinked"):
            tag_reset.archive_setup(self.home, self.lifecycle)
        self.assertEqual(external.read_text(), "keep")
        self.lifecycle.stop_process.assert_not_called()
