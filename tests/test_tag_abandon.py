"""Abandoning an unfinished setup moves it aside; a Tag that reached Slack is refused."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

from scripts import tag_cli, tag_instances


class AbandonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.context = tag_instances.create(self.root, "new-tag")
        self.args = SimpleNamespace(json_output=True)
        patcher = mock.patch.object(tag_cli, "process_for", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_moves_unfinished_setup_into_a_backup(self) -> None:
        marker = self.context.home / "keep.txt"
        marker.write_text("x", encoding="utf-8")
        from contextlib import redirect_stdout
        import io
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(tag_cli._abandon_command(self.context, self.args), 0)
        backup = Path(json.loads(out.getvalue())["backup"])
        self.assertFalse(self.context.home.exists())
        self.assertEqual((backup / "keep.txt").read_text(encoding="utf-8"), "x")

    def test_refuses_a_tag_connected_to_slack(self) -> None:
        config = self.context.home / "config"
        config.mkdir(parents=True, exist_ok=True)
        (config / "settings.json").write_text(json.dumps({"SLACK_TEAM_ID": "T1", "SLACK_BOT_TOKEN": "xoxb-1"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            tag_cli._abandon_command(self.context, self.args)
        self.assertTrue(self.context.home.exists())

    def test_allows_a_tag_that_only_picked_a_workspace(self) -> None:
        config = self.context.home / "config"
        config.mkdir(parents=True, exist_ok=True)
        (config / "settings.json").write_text(json.dumps({"SLACK_TEAM_ID": "T1"}), encoding="utf-8")
        with mock.patch("builtins.print"):
            self.assertEqual(tag_cli._abandon_command(self.context, self.args), 0)
        self.assertFalse(self.context.home.exists())

    def test_refuses_a_tag_whose_app_creation_started(self) -> None:
        marker = self.context.home / "integrations/slack-cli/tag-create.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            tag_cli._abandon_command(self.context, self.args)


class RemoveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.context = tag_instances.create(self.root, "t1-a1")
        config = self.context.home / "config"
        config.mkdir(parents=True, exist_ok=True)
        (config / "settings.json").write_text(json.dumps(
            {"SLACK_APP_ID": "A0APP", "SLACK_TEAM_ID": "T0TEAM", "SLACK_BOT_TOKEN": "xoxb-1", "OPENTAG_BOT_NAME": "Tag"}), encoding="utf-8")
        self.args = SimpleNamespace(json_output=True, delete_app=False, confirm_app=None)
        try:
            import tag_reset
        except ImportError:
            from scripts import tag_reset
        self.reset = tag_reset
        for target in (mock.patch.object(tag_cli, "stop_process"),
                       mock.patch.object(tag_reset, "unregister_connector"),
                       mock.patch.object(tag_reset, "check_app_link"),
                       mock.patch("builtins.print")):
            target.start()
            self.addCleanup(target.stop)

    def test_remove_keeps_the_slack_app_by_default(self) -> None:
        with mock.patch.object(self.reset, "delete_slack_app") as delete:
            self.assertEqual(tag_cli._remove_command(self.context, self.args), 0)
        delete.assert_not_called()
        self.assertFalse(self.context.home.exists())
        self.assertTrue(any((self.root / "abandoned").iterdir()))

    def test_deleting_the_app_needs_its_exact_app_id(self) -> None:
        self.args.delete_app, self.args.confirm_app = True, "A0OTHER"
        with mock.patch.object(self.reset, "delete_slack_app") as delete, self.assertRaises(ValueError):
            tag_cli._remove_command(self.context, self.args)
        delete.assert_not_called()
        self.assertTrue(self.context.home.exists())

    def test_deletes_only_the_confirmed_app(self) -> None:
        self.args.delete_app, self.args.confirm_app = True, "A0APP"
        with mock.patch("shutil.which", return_value="/bin/slack"), \
                mock.patch.object(self.reset, "delete_slack_app", return_value=True) as delete:
            self.assertEqual(tag_cli._remove_command(self.context, self.args), 0)
        self.assertEqual(delete.call_args.args[2]["app_id"], "A0APP")
        self.assertFalse(self.context.home.exists())

    def test_unconfirmed_deletion_is_reported(self) -> None:
        self.args.delete_app, self.args.confirm_app = True, "A0APP"
        with mock.patch("shutil.which", return_value="/bin/slack"), \
                mock.patch.object(self.reset, "delete_slack_app", return_value=False), \
                self.assertRaises(RuntimeError):
            tag_cli._remove_command(self.context, self.args)


if __name__ == "__main__":
    unittest.main()
