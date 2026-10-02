from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts import slack_manifest_migrations, tag_cli, tag_config, tag_instances


class TagGroupTests(unittest.TestCase):
    """Nicknames from `tag rename` and lifecycle for a whole Slack workspace."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Tag"
        self.env = patch.dict(os.environ, {"TAG_HOME": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def add(self, tag_id: str, team: str, workspace: str | None = None) -> Path:
        home = tag_instances.create(self.root, tag_id).home
        tag_config.save_config(home / "config/settings.json", {
            "SLACK_TEAM_ID": team, "SLACK_APP_ID": "A" + tag_id.split("-a")[-1].upper(),
            "OPENTAG_BOT_NAME": "Tag",
        })
        if workspace:
            tag_instances.record_workspace_name(home, workspace)
        return home

    def cli(self, *arguments: str) -> tuple[int, str]:
        with patch.object(sys, "argv", ["tag", *arguments]), redirect_stdout(StringIO()) as output:
            code = tag_cli.main()
        return code, output.getvalue()

    def test_rename_changes_slack_first_then_saves_name_and_nickname(self) -> None:
        home = self.add("t1-a1", "T1")
        with patch.object(slack_manifest_migrations, "set_display_name", return_value=True) as slack:
            code, output = self.cli("t1-a1", "rename", "Maya's Tag", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(slack.call_args.args[2], "Maya's Tag")
        self.assertEqual(slack.call_args.kwargs["retry"], 'tag t1-a1 rename "Maya\'s Tag"')
        self.assertEqual(json.loads(output)["nickname"], "mayas-tag")
        self.assertEqual(tag_config.load_config(home / "config/settings.json")["OPENTAG_BOT_NAME"], "Maya's Tag")
        self.assertEqual(tag_instances.resolve_reference(self.root, "mayas-tag"), "t1-a1")

    def test_failed_slack_rename_changes_nothing_locally(self) -> None:
        home = self.add("t1-a1", "T1")
        with patch.object(slack_manifest_migrations, "set_display_name",
                          side_effect=RuntimeError("run `slack login`")), self.assertRaises(RuntimeError):
            self.cli("t1-a1", "rename", "Research")
        self.assertEqual(tag_config.load_config(home / "config/settings.json")["OPENTAG_BOT_NAME"], "Tag")
        self.assertIsNone(tag_instances.nickname(home))

    def test_nicknames_never_collide_with_other_tags(self) -> None:
        self.add("t1-a1", "T1")
        self.add("t2-a2", "T2")
        with patch.object(slack_manifest_migrations, "set_display_name", return_value=True):
            self.cli("t1-a1", "rename", "Research")
            with self.assertRaisesRegex(ValueError, "already uses 'research'"):
                self.cli("t2-a2", "rename", "Research")
            with self.assertRaisesRegex(ValueError, "already uses 't1-a1'"):
                self.cli("t2-a2", "rename", "Other", "--nickname", "t1-a1")
            code, _ = self.cli("t2-a2", "rename", "Research", "--nickname", "research-acme")
        self.assertEqual(code, 0)

    def test_nickname_selects_its_tag_for_any_command(self) -> None:
        self.add("t1-a1", "T1")
        tag_instances.set_nickname(self.root, "t1-a1", "research")
        with patch.object(tag_cli, "stop_process") as stop:
            code, _ = self.cli("research", "stop")
        self.assertEqual(code, 0)
        stop.assert_called_once_with(tag_instances.resolve(self.root, "t1-a1").home, "slack")

    def test_workspace_start_runs_every_tag_in_it_and_reports_failures(self) -> None:
        self.add("t1-a1", "T1", "Klovr")
        self.add("t1-a2", "T1", "Klovr")
        self.add("t2-a3", "T2", "Acme")
        with patch.object(tag_cli.subprocess, "call", side_effect=[0, 1]) as call:
            code, output = self.cli("start", "--workspace", "klovr", "--json")
        self.assertEqual(code, 1)
        started = [item.args[0][-2] for item in call.call_args_list]
        self.assertEqual(started, ["t1-a1", "t1-a2"])
        result = json.loads(output)
        self.assertFalse(result["ok"])
        self.assertEqual(result["tags"], [{"tag": "t1-a1", "exit_code": 0}, {"tag": "t1-a2", "exit_code": 1}])

    def test_workspace_start_skips_tags_that_have_not_finished_setup(self) -> None:
        self.add("t1-a1", "T1", "Klovr")
        unfinished = tag_instances.create(self.root, "new-tag", provisional=True).home
        tag_config.save_config(unfinished / "config/settings.json", {"SLACK_TEAM_ID": "T1"})
        with patch.object(tag_cli.subprocess, "call", return_value=0) as call:
            code, output = self.cli("start", "--workspace", "T1", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(call.call_count, 1)
        self.assertIn({"tag": "new-tag", "exit_code": None, "skipped": "setup_incomplete"}, json.loads(output)["tags"])

    def test_workspace_accepts_team_id_and_rejects_unknown(self) -> None:
        self.add("t2-a3", "T2", "Acme")
        with patch.object(tag_cli.subprocess, "call", return_value=0) as call:
            self.assertEqual(self.cli("stop", "--workspace", "T2")[0], 0)
        self.assertEqual(call.call_args.args[0][-2:], ["t2-a3", "stop"])
        with self.assertRaisesRegex(RuntimeError, "No Tags"):
            self.cli("stop", "--workspace", "Nowhere")

    def test_list_groups_tags_by_workspace(self) -> None:
        self.add("t1-a1", "T1", "Klovr")
        self.add("t2-a3", "T2", "Acme")
        self.add("t1-a2", "T1", "Klovr")
        with patch.object(tag_cli, "healthy", return_value=False), patch.object(
            tag_cli, "process_for", return_value=None
        ):
            _, output = self.cli("list")
        klovr, acme = output.index("KLOVR (T1)"), output.index("ACME (T2)")
        self.assertLess(klovr, output.index("t1-a2"))
        self.assertLess(output.index("t1-a2"), acme)


class SlackDisplayNameTests(unittest.TestCase):
    def test_renames_app_and_bot_user_then_verifies(self) -> None:
        before = {"display_information": {"name": "Tag"}, "features": {"bot_user": {"display_name": "Tag"}}}
        after = {"display_information": {"name": "Research"}, "features": {"bot_user": {"display_name": "Research"}}}
        values = {"SLACK_TEAM_ID": "T1", "SLACK_APP_ID": "A1"}
        with tempfile.TemporaryDirectory() as directory, patch.object(
            slack_manifest_migrations.shutil, "which", return_value="/bin/slack"
        ), patch.object(slack_manifest_migrations, "remote_manifest", side_effect=[before, after]), patch.object(
            slack_manifest_migrations, "_migration_project", return_value=Path(directory)
        ) as project, patch.object(slack_manifest_migrations, "_sync_command", return_value=["sync"]), patch.object(
            slack_manifest_migrations, "_run", return_value=subprocess.CompletedProcess([], 0)
        ):
            self.assertTrue(slack_manifest_migrations.set_display_name(Path(directory), values, "Research", retry="r"))
        sent = project.call_args.args[1]
        self.assertEqual(sent["display_information"]["name"], "Research")
        self.assertEqual(sent["features"]["bot_user"]["display_name"], "Research")

    def test_unsaved_rename_is_reported_with_the_retry_command(self) -> None:
        unchanged = {"display_information": {"name": "Tag"}, "features": {"bot_user": {"display_name": "Tag"}}}
        with tempfile.TemporaryDirectory() as directory, patch.object(
            slack_manifest_migrations.shutil, "which", return_value="/bin/slack"
        ), patch.object(slack_manifest_migrations, "remote_manifest", return_value=unchanged), patch.object(
            slack_manifest_migrations, "_migration_project", return_value=Path(directory)
        ), patch.object(slack_manifest_migrations, "_sync_command", return_value=["sync"]), patch.object(
            slack_manifest_migrations, "_run", return_value=subprocess.CompletedProcess([], 0)
        ), self.assertRaisesRegex(RuntimeError, "retry `tag x rename`"):
            slack_manifest_migrations.set_display_name(
                Path(directory), {"SLACK_TEAM_ID": "T1", "SLACK_APP_ID": "A1"}, "Research", retry="tag x rename"
            )


if __name__ == "__main__":
    unittest.main()
