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

    def test_describe_changes_slack_first_then_saves_the_description(self) -> None:
        home = self.add("t1-a1", "T1")
        with patch.object(slack_manifest_migrations, "set_description", return_value=True) as slack:
            code, output = self.cli("t1-a1", "describe", "  Answers launch questions ", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(slack.call_args.args[2], "Answers launch questions")
        self.assertEqual(slack.call_args.kwargs["retry"], 'tag t1-a1 describe "Answers launch questions"')
        self.assertEqual(json.loads(output), {"schema_version": 1, "tag": "t1-a1",
                                              "description": "Answers launch questions", "slack_changed": True})
        self.assertEqual(tag_config.load_config(home / "config/settings.json")["OPENTAG_BOT_DESCRIPTION"], "Answers launch questions")

    def test_describe_with_an_empty_description_clears_it(self) -> None:
        home = self.add("t1-a1", "T1")
        tag_config.update_config(home / "config/settings.json", {"OPENTAG_BOT_DESCRIPTION": "Old"})
        with patch.object(slack_manifest_migrations, "set_description", return_value=True) as slack:
            code, output = self.cli("t1-a1", "describe", "", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(slack.call_args.args[2], "")
        self.assertIsNone(json.loads(output)["description"])
        self.assertEqual(tag_config.load_config(home / "config/settings.json").get("OPENTAG_BOT_DESCRIPTION", ""), "")

    def test_describe_rejects_long_descriptions_before_touching_slack(self) -> None:
        self.add("t1-a1", "T1")
        with patch.object(slack_manifest_migrations, "set_description") as slack, self.assertRaisesRegex(ValueError, "140"):
            self.cli("t1-a1", "describe", "x" * 141)
        slack.assert_not_called()

    def test_failed_slack_describe_changes_nothing_locally(self) -> None:
        home = self.add("t1-a1", "T1")
        with patch.object(slack_manifest_migrations, "set_description",
                          side_effect=RuntimeError("run `slack login`")), self.assertRaises(RuntimeError):
            self.cli("t1-a1", "describe", "New")
        self.assertNotIn("OPENTAG_BOT_DESCRIPTION", tag_config.load_config(home / "config/settings.json"))

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
    def setUp(self) -> None:
        delays = patch.object(slack_manifest_migrations, "VERIFY_DELAYS", (0.0, 0.0))
        delays.start()
        self.addCleanup(delays.stop)

    def test_waits_for_slack_to_report_the_new_name(self) -> None:
        before = {"display_information": {"name": "Tag"}, "features": {"bot_user": {"display_name": "Tag"}}}
        after = {"display_information": {"name": "Research"}, "features": {"bot_user": {"display_name": "Research"}}}
        with tempfile.TemporaryDirectory() as directory, patch.object(
            slack_manifest_migrations.shutil, "which", return_value="/bin/slack"
        ), patch.object(slack_manifest_migrations, "remote_manifest", side_effect=[before, before, after]), patch.object(
            slack_manifest_migrations, "_migration_project", return_value=Path(directory)
        ), patch.object(slack_manifest_migrations, "_sync_command", return_value=["sync"]), patch.object(
            slack_manifest_migrations, "_run", return_value=subprocess.CompletedProcess([], 0)
        ):
            self.assertTrue(slack_manifest_migrations.set_display_name(
                Path(directory), {"SLACK_TEAM_ID": "T1", "SLACK_APP_ID": "A1"}, "Research", retry="r"))

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



class SlackDescriptionTests(unittest.TestCase):
    def setUp(self) -> None:
        delays = patch.object(slack_manifest_migrations, "VERIFY_DELAYS", (0.0, 0.0))
        delays.start()
        self.addCleanup(delays.stop)

    def patched(self, directory: str, manifests: list[dict]):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(slack_manifest_migrations.shutil, "which", return_value="/bin/slack"))
        stack.enter_context(patch.object(slack_manifest_migrations, "remote_manifest", side_effect=manifests))
        project = stack.enter_context(patch.object(slack_manifest_migrations, "_migration_project", return_value=Path(directory)))
        stack.enter_context(patch.object(slack_manifest_migrations, "_sync_command", return_value=["sync"]))
        stack.enter_context(patch.object(slack_manifest_migrations, "_run", return_value=subprocess.CompletedProcess([], 0)))
        return stack, project

    def test_changes_the_app_description_then_verifies(self) -> None:
        before = {"display_information": {"name": "Tag", "description": "Old"}}
        after = {"display_information": {"name": "Tag", "description": "New"}}
        with tempfile.TemporaryDirectory() as directory:
            stack, project = self.patched(directory, [before, after])
            with stack:
                self.assertTrue(slack_manifest_migrations.set_description(
                    Path(directory), {"SLACK_TEAM_ID": "T1", "SLACK_APP_ID": "A1"}, "New", retry="r"))
        self.assertEqual(project.call_args.args[1]["display_information"], {"name": "Tag", "description": "New"})

    def test_clearing_removes_the_description(self) -> None:
        before = {"display_information": {"name": "Tag", "description": "Old"}}
        after = {"display_information": {"name": "Tag"}}
        with tempfile.TemporaryDirectory() as directory:
            stack, project = self.patched(directory, [before, after])
            with stack:
                self.assertTrue(slack_manifest_migrations.set_description(
                    Path(directory), {"SLACK_TEAM_ID": "T1", "SLACK_APP_ID": "A1"}, "", retry="r"))
        self.assertEqual(project.call_args.args[1]["display_information"], {"name": "Tag"})

    def test_an_unchanged_description_skips_slack(self) -> None:
        same = {"display_information": {"description": "Same"}}
        with tempfile.TemporaryDirectory() as directory:
            stack, project = self.patched(directory, [same])
            with stack:
                self.assertFalse(slack_manifest_migrations.set_description(
                    Path(directory), {"SLACK_TEAM_ID": "T1", "SLACK_APP_ID": "A1"}, "Same", retry="r"))
        project.assert_not_called()

    def test_unsaved_description_is_reported_with_the_retry_command(self) -> None:
        unchanged = {"display_information": {"description": "Old"}}
        with tempfile.TemporaryDirectory() as directory:
            stack, _ = self.patched(directory, [unchanged] * 4)
            with stack, self.assertRaisesRegex(RuntimeError, "retry `tag x describe`"):
                slack_manifest_migrations.set_description(
                    Path(directory), {"SLACK_TEAM_ID": "T1", "SLACK_APP_ID": "A1"}, "New", retry="tag x describe")


if __name__ == "__main__":
    unittest.main()
