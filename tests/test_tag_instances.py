from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import stat
from contextlib import redirect_stdout
from io import StringIO

from scripts import opentag_setup, tag_cli, tag_credentials, tag_instances
from scripts.tag_paths import instance_home, runtime_environment


class TagInstanceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Tag"
        self.root.mkdir()

    def test_default_and_named_instances_use_the_same_isolated_layout(self) -> None:
        default = tag_instances.ensure_default(self.root)
        personal = tag_instances.create(self.root, "personal")

        self.assertEqual(default.home, self.root / "instances/default")
        self.assertEqual(personal.home, self.root / "instances/personal")
        for context in (default, personal):
            self.assertTrue((context.home / "workspace/.codex/config.toml").is_file())
            self.assertFalse((context.home / "releases").exists())
            self.assertEqual(
                json.loads((context.home / "instance.json").read_text())["id"],
                context.tag_id,
            )
        self.assertFalse((self.root / "config").exists())
        self.assertFalse((self.root / "workspace").exists())
        self.assertEqual(json.loads((personal.home / "instance.json").read_text())["id"], "personal")

    def test_invalid_unknown_duplicate_and_symlink_names_do_not_create_data(self) -> None:
        for name in ("Default", "../escape", "two words", "", "a" * 33, "status"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                tag_instances.create(self.root, name)
        self.assertFalse((self.root.parent / "escape").exists())

        tag_instances.create(self.root, "work")
        with self.assertRaisesRegex(ValueError, "already exists"):
            tag_instances.create(self.root, "work")
        with self.assertRaisesRegex(ValueError, "Unknown Tag"):
            tag_instances.resolve(self.root, "missing")

        link = self.root / "instances/link"
        link.symlink_to(self.root.parent)
        with self.assertRaisesRegex(ValueError, "symlink"):
            tag_instances.resolve(self.root, "link")

    def test_discovery_reports_one_malformed_instance_without_hiding_others(self) -> None:
        tag_instances.create(self.root, "healthy")
        malformed = self.root / "instances/broken"
        malformed.mkdir()
        (malformed / "instance.json").write_text("not json", encoding="utf-8")

        records = {str(item["id"]): item for item in tag_instances.discover(self.root)}
        self.assertEqual(set(records), {"default", "broken", "healthy"})
        self.assertFalse(records["broken"]["valid"])
        self.assertTrue(records["healthy"]["valid"])

    def test_child_environment_keeps_installation_root_and_explicit_instance(self) -> None:
        home = self.root / "instances/work"
        environment = runtime_environment(home, installation_root=self.root, tag_id="work")
        self.assertEqual(environment["TAG_HOME"], str(self.root))
        self.assertEqual(environment["TAG_INSTANCE_HOME"], str(home))
        self.assertEqual(environment["TAG_ID"], "work")
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(instance_home(), home)

    def test_default_cli_scrubs_inherited_instance_configuration(self) -> None:
        default = tag_instances.ensure_default(self.root)
        configured = {
            "TAG_HOME": str(self.root),
            "OPENTAG_BACKEND": "codex",
            "MFS_URL": "http://127.0.0.1:13619",
            "MFS_ALLOWED_SCOPES": "file://local/test",
            "SLACK_ALLOWED_USER_IDS": "UOWNER",
        }

        def doctor_report(_offline: bool) -> tuple[int, dict[str, object]]:
            self.assertEqual(os.environ.get("TAG_HOME"), str(self.root))
            self.assertEqual(os.environ.get("TAG_INSTANCE_HOME"), str(default.home))
            self.assertEqual(os.environ.get("TAG_ID"), "default")
            for name in configured.keys() - {"TAG_HOME"}:
                self.assertNotIn(name, os.environ)
            return 0, {"checks": []}

        with patch.dict(os.environ, configured, clear=True), patch.object(
            sys, "argv", ["tag", "doctor", "--offline"]
        ), patch.object(tag_cli, "doctor_report", side_effect=doctor_report), redirect_stdout(StringIO()) as output:
            self.assertEqual(tag_cli.main(), 0)
        self.assertIn("Slack workspace not configured", output.getvalue())

    def test_connector_uses_private_instance_credential_file(self) -> None:
        home = tag_instances.create(self.root, "work").home
        credential = tag_credentials.write_slack_history(home, "xoxb-secret")
        connector = opentag_setup.write_slack_connector(
            "T123", [opentag_setup.slack_channels.SlackChannel("C1", "team", False, True)],
            "30", home=home, app_id="A456", credential=credential,
        )
        content = connector.read_text(encoding="utf-8")
        self.assertIn(f'token = "file:{credential}"', content)
        self.assertNotIn("xoxb-secret", content)
        self.assertEqual(connector.name, "tag-t123-a456.toml")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(credential.stat().st_mode), 0o600)

    def test_cli_add_targets_setup_and_unknown_target_creates_nothing(self) -> None:
        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}, clear=False), patch.object(
            sys, "argv", ["tag", "add", "personal"]
        ), patch.object(tag_cli.subprocess, "call", return_value=0) as call, redirect_stdout(StringIO()):
            self.assertEqual(tag_cli.main(), 0)
        self.assertEqual(call.call_args.args[0][-2:], ["personal", "setup"])
        self.assertEqual(call.call_args.kwargs["env"]["TAG_INSTANCE_HOME"],
                         str(self.root / "instances/personal"))

        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}, clear=False), patch.object(
            sys, "argv", ["tag", "missing", "status"]
        ), self.assertRaisesRegex(ValueError, "Unknown Tag"):
            tag_cli.main()
        self.assertFalse((self.root / "instances/missing").exists())

    def test_cli_stop_targets_only_the_selected_bridge(self) -> None:
        home = tag_instances.create(self.root, "personal").home
        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}, clear=False), patch.object(
            sys, "argv", ["tag", "personal", "stop"]
        ), patch.object(tag_cli, "stop_process") as stop, redirect_stdout(StringIO()):
            self.assertEqual(tag_cli.main(), 0)
        stop.assert_called_once_with(home, "slack")


if __name__ == "__main__":
    unittest.main()
