from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import tag_config, tag_instances, tag_rename

TEAM, APP, NAME = "T0ABC123", "A0XYZ789", "t0abc123-a0xyz789"


def configure(home: Path, **values: str) -> None:
    tag_config.save_config(home / "config/settings.json", {
        "SLACK_TEAM_ID": TEAM, "SLACK_APP_ID": APP,
        "MFS_SLACK_CONNECTOR_CONFIG": str(home / "integrations/mfs/tag.toml"), **values,
    })


class PortableRenameTests(unittest.TestCase):
    """An explicit TAG_HOME keeps every Tag under ROOT/instances/NAME."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "tag-home"
        self.lifecycle = Mock()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_built_in_tag_is_renamed_after_its_ids_and_becomes_main(self) -> None:
        old = tag_instances.ensure_default(self.root).home
        configure(old)
        (old / "workspace/notes.md").write_text("mine\n", encoding="utf-8")

        self.assertEqual(tag_rename.migrate(self.root, self.lifecycle), NAME)

        new = self.root / "instances" / NAME
        self.assertFalse(old.exists())
        self.assertEqual((new / "workspace/notes.md").read_text(encoding="utf-8"), "mine\n")
        settings = tag_config.load_config(new / "config/settings.json")
        self.assertEqual(settings["MFS_SLACK_CONNECTOR_CONFIG"], str(new / "integrations/mfs/tag.toml"))
        self.assertEqual(json.loads((new / "instance.json").read_text(encoding="utf-8"))["id"], NAME)
        self.assertEqual(tag_instances.main_tag(self.root), NAME)
        self.assertEqual(tag_instances.select_unnamed(self.root), NAME)
        self.assertFalse((self.root / "state/rename-default.json").exists())
        self.lifecycle.stop_process.assert_called_once_with(old, "slack")

    def test_waits_until_the_slack_app_exists(self) -> None:
        old = tag_instances.ensure_default(self.root).home
        tag_config.save_config(old / "config/settings.json", {"SLACK_TEAM_ID": TEAM})
        self.assertIsNone(tag_rename.migrate(self.root, self.lifecycle))
        self.assertTrue(old.exists())
        self.lifecycle.stop_process.assert_not_called()

    def test_repeated_runs_do_nothing(self) -> None:
        configure(tag_instances.ensure_default(self.root).home)
        self.assertEqual(tag_rename.migrate(self.root, self.lifecycle), NAME)
        self.assertIsNone(tag_rename.migrate(self.root, self.lifecycle))
        self.assertIsNone(tag_rename.migrate(self.root, self.lifecycle, NAME))

    def test_interrupted_rename_resumes_without_choosing_a_new_name(self) -> None:
        old = tag_instances.ensure_default(self.root).home
        configure(old)
        # Crash after the folder moved but before paths were rewritten.
        new = self.root / "instances" / NAME
        tag_config.save_config(self.root / "state/rename-default.json",
                               {"version": 1, "to": NAME, "workspace_name": "Acme"})
        os.rename(old, new)

        self.assertEqual(tag_rename.migrate(self.root, self.lifecycle), NAME)
        settings = tag_config.load_config(new / "config/settings.json")
        self.assertEqual(settings["MFS_SLACK_CONNECTOR_CONFIG"], str(new / "integrations/mfs/tag.toml"))
        self.assertEqual(tag_instances.workspace_name(new), "Acme")
        self.assertFalse((self.root / "state/rename-default.json").exists())

    def test_existing_destination_stops_without_changing_either(self) -> None:
        old = tag_instances.ensure_default(self.root).home
        configure(old)
        (self.root / "instances" / NAME).mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            tag_rename.migrate(self.root, self.lifecycle)
        self.assertTrue((old / "config/settings.json").is_file())
        self.assertFalse((self.root / "state/rename-default.json").exists())

    def test_added_tag_is_renamed_without_taking_over_main(self) -> None:
        first = tag_instances.ensure_default(self.root).home
        configure(first)
        tag_rename.migrate(self.root, self.lifecycle)
        added = tag_instances.create(self.root, "new-tag", provisional=True).home
        configure(added, SLACK_APP_ID="A0SECOND1")

        self.assertEqual(tag_rename.migrate(self.root, self.lifecycle, "new-tag"), "t0abc123-a0second1")
        self.assertEqual(tag_instances.main_tag(self.root), NAME)
        record = json.loads((self.root / "instances/t0abc123-a0second1/instance.json").read_text(encoding="utf-8"))
        self.assertNotIn("provisional", record)

    def test_tags_with_final_names_are_left_alone(self) -> None:
        home = tag_instances.create(self.root, "personal").home
        configure(home)
        self.assertIsNone(tag_rename.migrate(self.root, self.lifecycle, "personal"))
        self.assertTrue(home.exists())

    def test_names_must_come_from_real_slack_ids(self) -> None:
        with self.assertRaises(ValueError):
            tag_rename.id_name("acme", APP)


class NativeRenameTests(unittest.TestCase):
    """A normal installation keeps each Tag's files in ~/Tag/NAME."""

    def test_user_folder_moves_with_its_private_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HOME": directory, "USERPROFILE": directory}):
            root = Path(directory) / "Library/Application Support/Tag"
            with patch.object(tag_instances, "native_installation", return_value=True), patch(
                "scripts.tag_paths.platform_tag_home", return_value=root
            ):
                old = tag_instances.ensure_default(root).home
                self.assertEqual(old, Path(directory) / "Tag/default/.tag")
                configure(old)
                (old.parent / "plan.md").write_text("draft\n", encoding="utf-8")

                self.assertEqual(tag_rename.migrate(root, Mock()), NAME)

                folder = Path(directory) / "Tag" / NAME
                self.assertFalse((Path(directory) / "Tag/default").exists())
                self.assertEqual((folder / "plan.md").read_text(encoding="utf-8"), "draft\n")
                settings = tag_config.load_config(folder / ".tag/config/settings.json")
                self.assertEqual(settings["MFS_SLACK_CONNECTOR_CONFIG"],
                                 str(folder / ".tag/integrations/mfs/tag.toml"))


class MainTagTests(unittest.TestCase):
    def test_unnamed_commands_prefer_main_then_the_only_tag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(tag_instances.select_unnamed(root), "default")
            tag_instances.create(root, "t1-a1")
            self.assertEqual(tag_instances.select_unnamed(root), "t1-a1")
            tag_instances.create(root, "t2-a2")
            self.assertEqual(tag_instances.select_unnamed(root), "default")
            tag_instances.set_main_tag(root, "t2-a2")
            self.assertEqual(tag_instances.select_unnamed(root), "t2-a2")
            self.assertTrue(tag_instances.resolve(root, "t2-a2").is_main)
            self.assertEqual(tag_instances.resolve(root, "t2-a2").command("start"), "tag start")
            self.assertEqual(tag_instances.resolve(root, "t1-a1").command("start"), "tag t1-a1 start")


if __name__ == "__main__":
    unittest.main()


class RestartAfterRenameTests(unittest.TestCase):
    def test_a_running_tag_restarts_under_its_new_name(self) -> None:
        import sys
        from contextlib import redirect_stdout
        from io import StringIO
        from scripts import tag_cli
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Tag"
            tag_instances.ensure_default(root)
            with patch.dict(os.environ, {"TAG_HOME": str(root)}), \
                    patch.object(sys, "argv", ["tag", "setup"]), \
                    patch.object(sys.stdin, "isatty", return_value=True), \
                    patch("scripts.tag_dependencies.migrate"), patch("scripts.tag_layout.migrate"), \
                    patch.object(tag_cli, "_rename", return_value=NAME), \
                    patch.object(tag_cli, "process_for", return_value=object()), \
                    patch.object(tag_cli, "show_upgrade_reminder"), \
                    patch.object(tag_cli.subprocess, "call", return_value=0) as call, \
                    redirect_stdout(StringIO()):
                self.assertEqual(tag_cli.main(), 0)
            restart = call.call_args_list[-1].args[0]
            self.assertEqual(restart[-2:], [NAME, "start"])
