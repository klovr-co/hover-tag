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

    def test_native_install_creates_editable_workspace_in_user_directory(self) -> None:
        user_home = self.root.parent / "person"
        installation = user_home / "Library/Application Support/Tag"

        with patch("pathlib.Path.home", return_value=user_home), patch("sys.platform", "darwin"):
            context = tag_instances.ensure_default(installation)
            workspace = context.workspace

        self.assertEqual(workspace, user_home / "Tag/default")
        self.assertTrue((workspace / ".codex/config.toml").is_file())
        self.assertFalse((context.home / "workspace").exists())

    def test_invalid_unknown_duplicate_and_symlink_names_do_not_create_data(self) -> None:
        for name in ("Default", "../escape", "two words", "", "a" * 33, "status"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                tag_instances.create(self.root, name)
        self.assertFalse((self.root.parent / "escape").exists())

        tag_instances.create(self.root, "work")
        with self.assertRaisesRegex(ValueError, "already exists"):
            tag_instances.create(self.root, "work")
        with self.assertRaisesRegex(ValueError, "Unknown workspace alias"):
            tag_instances.resolve(self.root, "missing")

        link = self.root / "instances/link"
        link.symlink_to(self.root.parent)
        with self.assertRaisesRegex(ValueError, "symlink"):
            tag_instances.resolve(self.root, "link")

    def test_workspace_alias_suggestion_uses_workspace_name_and_avoids_collisions(self) -> None:
        self.assertEqual(tag_instances.suggest_name(self.root, "Maxine Personal"), "maxine-personal")
        tag_instances.create(self.root, "maxine-personal")
        self.assertEqual(tag_instances.suggest_name(self.root, "Maxine Personal"), "maxine-personal-2")
        self.assertEqual(tag_instances.suggest_name(self.root, "Status"), "workspace")

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

    def test_instance_environment_preserves_only_supported_startup_overrides(self) -> None:
        context = tag_instances.create(self.root, "work")
        environment = tag_cli.instance_environment(context, values={
            "OPENTAG_STARTUP_ATTEMPTS": "99",
            "OPENTAG_MFS_STARTUP_ATTEMPTS": "101",
        }, source={
            "PATH": "/fixture/bin",
            "OPENTAG_STARTUP_ATTEMPTS": "7",
            "OPENTAG_MFS_STARTUP_ATTEMPTS": "11",
            "OPENTAG_ENV_FILE": "/wrong/settings.json",
            "OPENTAG_BACKEND": "claude",
        })

        self.assertEqual(environment["OPENTAG_STARTUP_ATTEMPTS"], "7")
        self.assertEqual(environment["OPENTAG_MFS_STARTUP_ATTEMPTS"], "11")
        self.assertEqual(
            environment["OPENTAG_ENV_FILE"], str(context.home / "config/settings.json")
        )
        self.assertNotIn("OPENTAG_BACKEND", environment)

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
            sys, "argv", ["tag", "add"]
        ), patch.object(
            sys.stdin, "isatty", return_value=True
        ), patch.object(
            opentag_setup, "connect_slack_workspace", return_value=("T123", "Personal")
        ), patch.object(
            opentag_setup, "ask", return_value="personal"
        ), patch.object(tag_cli.subprocess, "call", return_value=0) as call, redirect_stdout(StringIO()):
            self.assertEqual(tag_cli.main(), 0)
        self.assertEqual(call.call_args.args[0][-2:], ["personal", "setup"])
        self.assertEqual(call.call_args.kwargs["env"]["TAG_INSTANCE_HOME"],
                         str(self.root / "instances/personal"))
        self.assertEqual(
            json.loads((self.root / "instances/personal/config/settings.json").read_text())["SLACK_TEAM_ID"],
            "T123",
        )

        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}, clear=False), patch.object(
            sys, "argv", ["tag", "missing", "status"]
        ), self.assertRaisesRegex(ValueError, "Unknown workspace alias"):
            tag_cli.main()
        self.assertFalse((self.root / "instances/missing").exists())

    def test_cli_add_requires_terminal_before_connecting_slack(self) -> None:
        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}, clear=False), patch.object(
            sys, "argv", ["tag", "add"]
        ), patch.object(sys.stdin, "isatty", return_value=False), patch.object(
            opentag_setup, "connect_slack_workspace"
        ) as connect:
            self.assertEqual(tag_cli.main(), 2)

        connect.assert_not_called()
        self.assertFalse((self.root / "instances/default").exists())

    def test_cli_stop_targets_only_the_selected_bridge(self) -> None:
        home = tag_instances.create(self.root, "personal").home
        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}, clear=False), patch.object(
            sys, "argv", ["tag", "personal", "stop"]
        ), patch.object(tag_cli, "stop_process") as stop, redirect_stdout(StringIO()):
            self.assertEqual(tag_cli.main(), 0)
        stop.assert_called_once_with(home, "slack")

    def test_stop_explains_memory_ownership_and_remaining_tags(self) -> None:
        tag_instances.ensure_default(self.root)
        for managed, bridges, expected in (
            (True, [], "Still running · no active Tags"),
            (True, ["personal"], "Still running for: personal"),
            (False, [], "No Tag-managed process running"),
            (False, ["personal"], "No Tag-managed process running · active Tags: personal"),
        ):
            with self.subTest(managed=managed, bridges=bridges):
                output = StringIO()
                with patch.dict(os.environ, {"TAG_HOME": str(self.root)}, clear=False), patch.object(
                    sys, "argv", ["tag", "stop"]
                ), patch.object(tag_cli, "stop_process") as stop, patch.object(
                    tag_cli, "process_for", return_value=object() if managed else None
                ), patch.object(tag_cli, "bridge_processes", return_value=bridges) as running, redirect_stdout(output):
                    self.assertEqual(tag_cli.main(), 0)
                stop.assert_called_once_with(self.root / "instances/default", "slack")
                running.assert_called_once_with(self.root)
                self.assertIn(expected, output.getvalue())
                self.assertEqual("Stop memory too" in output.getvalue(), managed and not bridges)
                self.assertEqual(
                    "Stop those Tags before running tag memory stop." in output.getvalue(),
                    managed and bool(bridges),
                )
                self.assertNotIn("Shared service left running", output.getvalue())


if __name__ == "__main__":
    unittest.main()
