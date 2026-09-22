from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts import opentag_doctor, opentag_setup, slack_manifest_migrations, tag_cli, tag_config, tag_control, tag_instances

ROOT = Path(__file__).resolve().parents[1]


class TagControlTests(unittest.TestCase):
    def test_config_show_targets_named_tag_settings(self):
        self.complete()
        with patch.object(tag_control.ui.display, "next_action") as next_action, redirect_stdout(StringIO()):
            tag_control.config_command(
                self.home, ["show"], json_output=False, stdin=False, tag_id="personal"
            )

        next_action.assert_called_once_with(
            "Edit settings interactively", "tag personal settings"
        )

    def test_settings_keyboard_agent_choice_saves_and_returns(self):
        self.complete()
        with patch.object(tag_control.ui, "keyboard_available", return_value=True), patch.object(
            tag_control.ui, "choose", side_effect=[2, 0, 1, 4]
        ) as choose, redirect_stdout(StringIO()):
            tag_control.settings_menu(self.home)
        self.assertEqual(tag_config.read_config(self.path)["OPENTAG_BACKEND"], "claude")
        self.assertEqual(choose.call_args_list[2].kwargs["default"], 0)

    def test_settings_pause_preserves_config_and_masks_credentials(self):
        self.complete()
        original = self.path.read_bytes()
        with patch.object(tag_control.ui, "keyboard_available", return_value=True), patch.object(
            tag_control.ui, "choose", side_effect=[0, tag_control.ui.Paused()]
        ) as choose, redirect_stdout(StringIO()) as output:
            tag_control.settings_menu(self.home)
        labels = str(choose.call_args_list[1])
        self.assertNotIn("xoxb-fixture", labels)
        self.assertNotIn("xapp-fixture", labels)
        self.assertIn("[set]", labels)
        self.assertIn("Settings closed", output.getvalue())
        self.assertEqual(self.path.read_bytes(), original)

    def test_settings_plain_number_navigation_still_works(self):
        self.complete()
        with patch.object(tag_control.ui, "keyboard_available", return_value=False), patch(
            "builtins.input", side_effect=["3", "1", "claude", "0"]
        ), redirect_stdout(StringIO()):
            tag_control.settings_menu(self.home)
        self.assertEqual(tag_config.read_config(self.path)["OPENTAG_BACKEND"], "claude")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "Tag home"
        self.home = tag_instances.ensure_default(self.root).home
        self.path = self.home / "config/settings.json"
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("TAG_", "OPENTAG_", "MFS_", "SLACK_"))}
        environment["TAG_HOME"] = str(self.root)
        self.environment = patch.dict(os.environ, environment, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.mfs_client = patch.object(
            opentag_setup.lifecycle,
            "mfs_client_executable",
            return_value="/runtime/bin/mfs",
        )
        self.mfs_client.start()
        self.addCleanup(self.mfs_client.stop)
        self.addCleanup(self.temporary.cleanup)

    def complete(self, backend="codex"):
        connector = self.home / "integrations/tag-ttest.toml"
        connector.parent.mkdir(parents=True, exist_ok=True)
        connector.write_text(opentag_setup.render_slack_connector("TTEST", [
            opentag_setup.slack_channels.SlackChannel("CTEST", "team", False, True)
        ], "30"), encoding="utf-8")
        values = dict(tag_config.DEFAULTS, OPENTAG_BACKEND=backend,
                      MFS_ALLOWED_SCOPES="slack://tag-ttest/channels/team__CTEST", SLACK_APP_TOKEN="xapp-fixture",
                      SLACK_BOT_TOKEN="xoxb-fixture", SLACK_ALLOWED_USER_IDS="UOWNER",
                      SLACK_TEAM_ID="TTEST", SLACK_APP_ID="ATEST", SLACK_CHANNEL_IDS="CTEST",
                      MFS_SLACK_TOKEN="xoxb-history", MFS_SLACK_CONNECTOR_URI="slack://tag-ttest",
                      MFS_SLACK_CONNECTOR_CONFIG=str(connector))
        tag_config.save_config(self.path, values)
        return values

    def cli(self, *args, input=None):
        return subprocess.run([sys.executable, str(ROOT / "scripts/tag_cli.py"), *args],
                              input=input, text=True, capture_output=True, timeout=10)

    def test_inspection_is_read_only_and_offline_does_not_probe(self):
        with patch.object(tag_cli, "healthy") as healthy, patch.object(tag_cli, "slack_ready") as ready:
            report = tag_control.inspect(self.home, tag_cli, offline=True)
        self.assertEqual(report["state"], "not_configured")
        self.assertEqual(report["next_command"], "tag setup")
        self.assertFalse(self.path.exists())
        healthy.assert_not_called()
        ready.assert_not_called()

    def test_partial_and_invalid_settings_are_recoverable_states(self):
        tag_config.save_config(self.path, {"OPENTAG_BACKEND": "claude"})
        report = tag_control.inspect(self.home, tag_cli, offline=True)
        self.assertEqual(report["state"], "setup_incomplete")
        self.assertIn("SLACK_APP_TOKEN", report["configuration"]["fields"])
        self.path.write_text('{"SLACK_BOT_TOKEN":"secret-in-malformed-json', encoding="utf-8")
        report = tag_control.inspect(self.home, tag_cli, offline=True)
        self.assertEqual(report["state"], "invalid_configuration")
        self.assertNotIn("secret-in-malformed-json", json.dumps(report))

    def test_runtime_states_and_unknown_authentication(self):
        self.complete()
        with patch.object(tag_control.shutil, "which", return_value="codex"), patch.object(
            tag_cli, "healthy", return_value=True
        ), patch.object(tag_cli, "slack_ready", return_value=True), patch.object(tag_cli, "process_for", return_value=object()):
            report = tag_control.inspect(self.home, tag_cli)
            self.assertEqual(report["state"], "running")
            self.assertEqual(report["backend"]["authentication"], "not_checked")
            self.assertEqual(report["first_reply"], "not_verified")
            with patch.object(tag_cli, "slack_ready", return_value=False):
                self.assertEqual(tag_control.inspect(self.home, tag_cli)["state"], "needs_attention")
                with patch.object(tag_cli, "process_for", return_value=None):
                    self.assertEqual(tag_control.inspect(self.home, tag_cli)["state"], "stopped")

    def test_update_preserves_secrets_and_extension_keys(self):
        values = self.complete()
        values["OPENTAG_CUSTOM_CREDENTIAL"] = "extension-secret"
        tag_config.save_config(self.path, values)
        tag_config.update_config(self.path, {"OPENTAG_BACKEND": "claude"})
        actual = tag_config.read_config(self.path)
        self.assertEqual(actual, dict(values, OPENTAG_BACKEND="claude"))
        shown = json.dumps(tag_config.public_config(actual))
        self.assertNotIn("extension-secret", shown)
        self.assertNotIn("xoxb-fixture", shown)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_invalid_update_and_concurrent_update_leave_file_intact(self):
        self.complete()
        original = self.path.read_bytes()
        for changes in ({"OPENTAG_BACKEND": "other"}, {"OPENTAG_TIMEOUT_SECONDS": "-1"},
                        {"OPENTAG_MAX_TIMEOUT_SECONDS": "0"},
                        {"OPENTAG_CODEX_TRANSPORT": "socket"},
                        {"OPENTAG_SLACK_DM_ENABLED": "maybe"},
                        {"SLACK_ALLOWED_USER_IDS": ""}, {"MFS_URL": "http://user:secret@host"},
                        {"OPENTAG_WORKDIR": "/other"}, {"MFS_ALLOWED_SCOPES": "file://local/a/../b"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                tag_config.update_config(self.path, changes)
            self.assertEqual(self.path.read_bytes(), original)
        self.path.with_name("settings.json.lock").touch()
        with self.assertRaises(RuntimeError):
            tag_config.update_config(self.path, {"OPENTAG_BACKEND": "claude"})
        self.assertEqual(self.path.read_bytes(), original)

    def test_initializing_defaults_preserves_existing_choices(self):
        tag_config.save_config(self.path, {"OPENTAG_BACKEND": "claude", "OPENTAG_TIMEOUT_SECONDS": "900"})
        result = self.cli("config", "init", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["next_command"], "tag inspect --json")
        values = tag_config.read_config(self.path)
        self.assertEqual(values["OPENTAG_BACKEND"], "claude")
        self.assertEqual(values["OPENTAG_TIMEOUT_SECONDS"], "900")
        self.assertEqual(values["MFS_URL"], tag_config.DEFAULTS["MFS_URL"])

    def test_atomic_failure_does_not_truncate_config(self):
        self.complete()
        original = self.path.read_bytes()
        with patch.object(tag_config.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                tag_config.update_config(self.path, {"OPENTAG_BACKEND": "claude"})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob(".settings-*")), [])
        self.assertFalse(self.path.with_name("settings.json.lock").exists())

    def test_cli_noninteractive_and_json_contract(self):
        result = self.cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn("tag setup", result.stdout)
        self.assertFalse(self.path.exists())
        for command in ("setup", "settings"):
            self.assertEqual(self.cli(command).returncode, 2)
        self.assertEqual(self.cli("menu").returncode, 1)
        result = self.cli("inspect", "--offline", "--json")
        self.assertEqual(json.loads(result.stdout)["state"], "not_configured")
        result = self.cli("doctor", "--offline", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_config_stdin_never_echoes_secret_and_bad_updates_are_json(self):
        # Initialize once here so native Windows CLI writes have account ACLs too.
        tag_cli.initialize_instance(self.home)
        result = self.cli("config", "set", "SLACK_BOT_TOKEN", "--stdin", "--json", input="xoxb-test-secret\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("xoxb-test-secret", result.stdout + result.stderr)
        self.assertEqual(tag_config.read_config(self.path)["SLACK_BOT_TOKEN"], "xoxb-test-secret")
        result = self.cli("config", "show", "--json")
        self.assertEqual(json.loads(result.stdout)["settings"]["SLACK_BOT_TOKEN"], "[set]")
        result = self.cli("config", "set", "OPENTAG_BACKEND", "invalid", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_setup_resume_skips_saved_secrets_and_preserves_advanced_values(self):
        values = self.complete("claude")
        del values["SLACK_ALLOWED_USER_IDS"]
        values.update(OPENTAG_TIMEOUT_SECONDS="900")
        tag_config.save_config(self.path, values)
        with patch.object(opentag_setup, "selected_backend_available", return_value=True), patch.object(
            opentag_setup, "validate_slack_identity", return_value={"team_id": "TTEST", "app_id": "ATEST"}
        ), patch.object(opentag_setup, "validate_socket_token"
        ), patch.object(
            opentag_setup.slack_channels, "list_channels",
            return_value=[opentag_setup.slack_channels.SlackChannel("CTEST", "team", False, True)],
        ), patch(
            "builtins.input", side_effect=["UOWNER", "1", "1"]
        ), patch.object(
            opentag_setup, "finish_setup", return_value=0
        ), patch.object(
            opentag_setup.getpass, "getpass"
        ) as secret, redirect_stdout(StringIO()):
            self.assertEqual(opentag_setup.guided_setup(self.path), 0)
        secret.assert_not_called()
        self.assertEqual(tag_config.read_config(self.path), dict(values, SLACK_ALLOWED_USER_IDS="UOWNER"))

    def test_no_start_setup_never_calls_service_finish(self):
        tag_config.save_config(self.path, self.complete())
        channels = [opentag_setup.slack_channels.SlackChannel("CTEST", "team", False, True)]
        with patch.object(opentag_setup, "selected_backend_available", return_value=True), patch.object(
            opentag_setup, "validate_slack_identity", return_value={"team_id": "TTEST", "app_id": "ATEST"}
        ), patch.object(opentag_setup, "validate_socket_token"), patch.object(
            opentag_setup.slack_channels, "list_channels", return_value=channels
        ), patch.object(opentag_setup.ui, "choose", return_value=0), patch.object(
            opentag_setup, "finish_setup"
        ) as finish, redirect_stdout(StringIO()) as output:
            self.assertEqual(opentag_setup.guided_setup(self.path, start_services=False), 0)
        finish.assert_not_called()
        self.assertIn("no history was indexed", output.getvalue())

    def test_setup_uses_visual_picker_when_channel_is_missing(self):
        values = self.complete()
        del values["SLACK_CHANNEL_IDS"]
        values["MFS_ALLOWED_SCOPES"] = "slack://tag-ttest/channels/team__CTEST"
        tag_config.save_config(self.path, values)
        selected = [opentag_setup.slack_channels.SlackChannel("CTEAM", "team", False, True)]
        with patch.object(opentag_setup, "selected_backend_available", return_value=True), patch.object(
            opentag_setup, "validate_slack_identity", return_value={"team_id": "TTEST", "app_id": "ATEST"}
        ), patch.object(opentag_setup, "validate_socket_token"
        ), patch.object(
            opentag_setup.slack_channels, "choose_channels", return_value=selected
        ) as picker, patch.object(
            opentag_setup.slack_channels, "slack_api", return_value={"ok": True}
        ), patch.object(opentag_setup, "write_slack_connector", return_value=Path(values["MFS_SLACK_CONNECTOR_CONFIG"])), patch(
            "builtins.input", side_effect=["1", "1"]
        ), patch.object(
            opentag_setup, "finish_setup", return_value=0
        ), redirect_stdout(StringIO()):
            self.assertEqual(opentag_setup.guided_setup(self.path), 0)

        self.assertEqual(picker.call_args.args, ("xoxb-fixture", ""))
        self.assertEqual(picker.call_args.kwargs, {"app_id": "ATEST"})
        self.assertEqual(tag_config.read_config(self.path)["SLACK_CHANNEL_IDS"], "CTEAM")

    def test_interrupted_setup_keeps_completed_answers(self):
        with patch.object(opentag_setup, "selected_backend_available", return_value=True), patch.object(
            opentag_setup, "connect_slack_cli", return_value="TTEST"
        ), patch.object(opentag_setup, "ask_validated", return_value="TTEST"), patch.object(
            opentag_setup, "choose_slack_app", return_value="ATEST"
        ), patch.object(opentag_setup.ui, "choose", return_value=1
        ), patch.object(opentag_setup, "validate_socket_token"), patch.object(
            opentag_setup.getpass, "getpass", side_effect=["xapp-fixture", KeyboardInterrupt]
        ), patch.object(sys.stdin, "isatty", return_value=True), patch.object(
            sys, "argv", ["setup", "--config", str(self.path)]
        ), redirect_stdout(StringIO()):
            self.assertEqual(opentag_setup.main(), 130)
        self.assertEqual(tag_config.read_config(self.path)["SLACK_APP_TOKEN"], "xapp-fixture")
        self.assertNotIn("SLACK_BOT_TOKEN", tag_config.read_config(self.path))

    def test_setup_defaults_return_to_summary_and_update_connector_window(self):
        values = self.complete()
        channels = [opentag_setup.slack_channels.SlackChannel("CTEST", "team", False, True)]
        with patch.object(opentag_setup, "selected_backend_available", return_value=True), patch.object(
            opentag_setup, "validate_slack_identity", return_value={"team_id": "TTEST", "app_id": "ATEST"}
        ), patch.object(opentag_setup, "validate_socket_token"), patch.object(
            opentag_setup.slack_channels, "list_channels", return_value=channels
        ), patch.object(opentag_setup.slack_channels, "slack_api", return_value={"ok": True}), patch.object(
            opentag_setup.ui, "choose", side_effect=[2, 0, 1, 0, 0]
        ), patch.object(opentag_setup, "write_slack_connector", return_value=Path(values["MFS_SLACK_CONNECTOR_CONFIG"])) as connector, patch.object(
            opentag_setup, "finish_setup", return_value=0
        ), redirect_stdout(StringIO()):
            self.assertEqual(opentag_setup.guided_setup(self.path), 0)
        saved = tag_config.read_config(self.path)
        self.assertEqual(saved["SLACK_APP_ID"], "ATEST")
        self.assertEqual(saved["SLACK_CHANNEL_IDS"], "CTEST")
        self.assertEqual(saved["OPENTAG_BACKEND"], "claude")
        self.assertEqual(saved["MFS_SLACK_HISTORY_DAYS"], "7")
        connector.assert_called_once_with("TTEST", channels, "7", home=self.home)

    def test_setup_exit_before_approval_does_not_index_or_connect_slack(self):
        self.complete()
        channels = [opentag_setup.slack_channels.SlackChannel("CTEST", "team", False, True)]
        with patch.object(opentag_setup, "selected_backend_available", return_value=True), patch.object(
            opentag_setup, "validate_slack_identity", return_value={"team_id": "TTEST", "app_id": "ATEST"}
        ), patch.object(opentag_setup, "validate_socket_token"), patch.object(
            opentag_setup.slack_channels, "list_channels", return_value=channels
        ), patch.object(opentag_setup.ui, "choose", return_value=3), patch.object(
            opentag_setup, "write_slack_connector"
        ) as connector, patch.object(
            opentag_setup, "finish_setup"
        ) as start, patch.object(
            opentag_setup.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ) as run, redirect_stdout(StringIO()):
            with self.assertRaises(opentag_setup.ui.Paused):
                opentag_setup.guided_setup(self.path)
        connector.assert_not_called()
        start.assert_not_called()
        run.assert_not_called()

    def test_backend_selection_reaches_runtime_for_both_choices(self):
        tag_cli.initialize_instance(self.home)
        for backend in ("codex", "claude"):
            with self.subTest(backend=backend):
                self.complete(backend)
                with patch.object(sys, "argv", ["tag", "start"]), patch.object(
                    tag_cli, "missing_runtime_dependencies", return_value=()
                ), patch.object(slack_manifest_migrations, "reconcile", return_value=False
                ), patch.object(tag_cli, "healthy", return_value=True
                ), patch.object(tag_cli, "replace_unmanaged_local_mfs", return_value=False
                ), patch.object(tag_cli, "sync_configured_slack_memory"
                ), patch.object(tag_cli, "wait_for_configured_mfs_scopes", return_value=[]
                ), patch.object(tag_cli, "doctor_report", return_value=(0, {"checks": []})), patch.object(
                    tag_cli, "slack_ready", side_effect=[False, True]
                ), patch.object(tag_cli, "stop_process"), patch.object(
                    tag_cli, "start_process", return_value=True
                ) as start, redirect_stdout(StringIO()) as output:
                    self.assertEqual(tag_cli.main(), 0)
                command = start.call_args.args[2]
                self.assertEqual(command[command.index("--backend") + 1], backend)
                self.assertIn("Tag is connected", output.getvalue())
                self.assertNotIn("[ok]", output.getvalue())

    def test_start_allows_mfs_cold_initialization_beyond_thirty_seconds(self):
        self.complete()
        tag_cli.initialize_instance(self.home)
        health_checks = [False, *([False] * 31), True]
        with patch.object(sys, "argv", ["tag", "start"]), patch.object(
            tag_cli, "missing_runtime_dependencies", return_value=()
        ), patch.object(
            slack_manifest_migrations, "reconcile", return_value=False
        ), patch.object(
            tag_cli, "replace_unmanaged_local_mfs", return_value=False
        ), patch.object(
            tag_cli, "mfs_server_executable", return_value="/fixture/mfs-server"
        ), patch.object(
            tag_cli, "start_process", return_value=True
        ), patch.object(
            tag_cli, "process_for", return_value=object()
        ), patch.object(
            tag_cli, "healthy", side_effect=health_checks
        ), patch.object(
            tag_cli.time, "sleep"
        ), patch.object(
            tag_cli, "sync_configured_slack_memory"
        ), patch.object(
            tag_cli, "wait_for_configured_mfs_scopes", return_value=[]
        ), patch.object(
            tag_cli, "doctor_report", return_value=(0, {"checks": []})
        ), patch.object(
            tag_cli, "slack_ready", return_value=True
        ), patch.object(tag_cli, "stop_process"), redirect_stdout(StringIO()):
            self.assertEqual(tag_cli.main(), 0)

    def test_doctor_json_suppresses_raw_response_details(self):
        def checks(*args):
            opentag_doctor.print_check(False, "Slack bot auth.test", "sensitive-server-error")
            print("another sensitive hint")
            return 1
        output = StringIO()
        with patch.object(sys, "argv", ["doctor", "--json"]), patch.object(
            opentag_doctor, "run_checks", side_effect=checks
        ), redirect_stdout(output):
            self.assertEqual(opentag_doctor.main(), 1)
        report = json.loads(output.getvalue())
        self.assertFalse(report["ok"])
        self.assertIn("next_action", report["checks"][0])
        self.assertNotIn("sensitive", output.getvalue())
