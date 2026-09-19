from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import opentag_setup as setup, slack_app_create as creation


class SlackAppCreationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="Tag creation ")
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.project = setup.slack_project(self.home)
        self.config = self.home / "settings.json"
        setup.settings.save_config(self.config, {"OPENTAG_BOT_NAME": "Tag Test", "MFS_TOKEN": "kept"})
        self.output = StringIO()
        self.redirect = redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def save_link(self, *args, **kwargs):
        setup.settings.save_config(self.project / ".slack/apps.json", {
            "apps": {"TTEST": {"app_id": "ATEST", "team_id": "TTEST"}},
            "default": "example",
        })
        return 0

    def save_state(self, **changes):
        setup.settings.save_config(self.project / "tag-create.json", {
            "team_id": "TTEST", "status": "attempting", **changes
        })

    def test_creation_requires_approval(self):
        run = Mock()
        with patch.object(creation.ui, "choose", return_value=1), self.assertRaises(creation.ui.Paused):
            creation.create_app(self.project, "TTEST", self.config, run)
        run.assert_not_called()
        self.assertFalse((self.project / "tag-create.json").exists())
        self.assertNotIn("SLACK_APP_ID", setup.settings.load_config(self.config))

    def test_new_app_uses_cli_and_saves_id_without_browser_or_manual_id(self):
        with patch.object(setup.ui, "choose", side_effect=[0, 0]), patch.object(
            setup, "run_slack_cli", side_effect=self.save_link
        ) as run, patch.object(creation, "is_installed", return_value=True), patch.object(
            setup, "inspect_slack_app", return_value=True
        ), patch.object(setup.webbrowser, "open") as browser, patch.object(setup, "ask_validated") as ask:
            self.assertEqual(setup.choose_slack_app(self.home, "TTEST", self.config), "ATEST")
        run.assert_called_once_with(
            ["app", "install", "--team", "TTEST", "--environment", "deployed"],
            cwd=self.project, interactive=True,
        )
        browser.assert_not_called()
        ask.assert_not_called()
        values = setup.settings.load_config(self.config)
        self.assertEqual(values["SLACK_APP_ID"], "ATEST")
        self.assertEqual(values["MFS_TOKEN"], "kept")
        self.assertNotIn("kept", self.output.getvalue())

    def test_pending_approval_does_not_count_as_installed_and_resumes(self):
        run = Mock(side_effect=self.save_link)
        with patch.object(creation.ui, "choose", side_effect=[0, 2]), patch.object(
            creation, "is_installed", return_value=False
        ), self.assertRaises(creation.ui.Paused):
            creation.create_app(self.project, "TTEST", self.config, run)
        self.assertEqual(setup.settings.load_config(self.config)["SLACK_APP_ID"], "ATEST")
        with patch.object(creation, "is_installed", return_value=True), patch.object(creation.ui, "choose") as choose:
            self.assertEqual(creation.create_app(self.project, "TTEST", self.config, run), "ATEST")
        self.assertEqual(run.call_count, 1)
        choose.assert_not_called()

    def test_retry_targets_same_id_and_never_forces_settings(self):
        self.save_link()
        self.save_state(app_id="ATEST")
        run = Mock(return_value=0)
        with patch.object(creation, "is_installed", side_effect=[False, True]), patch.object(
            creation.ui, "choose", return_value=1
        ):
            creation.create_app(self.project, "TTEST", self.config, run)
        run.assert_called_once_with(["app", "install", "--team", "TTEST", "--app", "ATEST"],
                                    cwd=self.project, interactive=True)

    def test_check_again_is_read_only(self):
        self.save_link()
        self.save_state(app_id="ATEST")
        run = Mock()
        with patch.object(creation, "is_installed", side_effect=[False, True]), patch.object(
            creation.ui, "choose", return_value=0
        ):
            creation.create_app(self.project, "TTEST", self.config, run)
        run.assert_not_called()

    def test_resume_recovers_cli_wrapped_apps_without_creation_or_relink(self):
        setup.settings.save_config(self.project / ".slack/apps.json", {
            "apps": {"TTEST": {"app_id": "ATEST", "team_id": "TTEST", "team_domain": "example"}},
            "default": "example",
        })
        self.save_state()
        with patch.object(setup, "run_slack_cli") as run, patch.object(
            creation, "is_installed", return_value=True
        ), patch.object(setup, "inspect_slack_app", return_value=True), patch.object(setup.ui, "choose") as choose:
            self.assertEqual(setup.choose_slack_app(self.home, "TTEST", self.config), "ATEST")
        run.assert_not_called()
        choose.assert_not_called()
        self.assertEqual(setup.settings.load_config(self.config)["SLACK_APP_ID"], "ATEST")

    def test_interruption_saves_created_identity(self):
        def interrupted(*args, **kwargs):
            self.save_link()
            raise KeyboardInterrupt()
        with patch.object(creation.ui, "choose", return_value=0), self.assertRaises(KeyboardInterrupt):
            creation.create_app(self.project, "TTEST", self.config, interrupted)
        self.assertEqual(setup.settings.load_config(self.config)["SLACK_APP_ID"], "ATEST")
        self.assertEqual(creation.read_object(self.project / "tag-create.json")["app_id"], "ATEST")

    def test_ambiguous_failure_never_blindly_recreates(self):
        run = Mock(return_value=1)
        with patch.object(creation.ui, "choose", return_value=0), self.assertRaisesRegex(RuntimeError, "did not save"):
            creation.create_app(self.project, "TTEST", self.config, run)
        with self.assertRaisesRegex(RuntimeError, "outcome is unknown"):
            creation.create_app(self.project, "TTEST", self.config, run)
        self.assertEqual(run.call_count, 1)

    def test_existing_links_are_not_modified(self):
        self.save_link()
        run = Mock()
        original = (self.project / ".slack/apps.json").read_bytes()
        with self.assertRaisesRegex(RuntimeError, "already linked"):
            creation.create_app(self.project, "TTEST", self.config, run)
        run.assert_not_called()
        self.assertEqual((self.project / ".slack/apps.json").read_bytes(), original)

    def test_wrong_workspace_cannot_resume(self):
        self.save_state(team_id="TOTHER")
        run = Mock()
        with self.assertRaisesRegex(RuntimeError, "another workspace"):
            creation.create_app(self.project, "TTEST", self.config, run)
        run.assert_not_called()

    def test_conflicting_or_malformed_identity_fails_closed(self):
        self.save_link()
        path = self.project / ".slack/apps.dev.json"
        for record in ({"team_id": "TTEST", "app_id": "AOTHER"}, {"team_id": "TOTHER", "app_id": "ATEST"}):
            with self.subTest(record=record):
                setup.settings.save_config(path, {"TTEST": record})
                with self.assertRaises(RuntimeError):
                    creation.linked_app(self.project, "TTEST")

    def test_malformed_wrapped_metadata_is_not_treated_as_no_app(self):
        for wrapper in (None, [], "invalid"):
            with self.subTest(wrapper=wrapper):
                setup.settings.save_config(self.project / ".slack/apps.json", {"apps": wrapper})
                with self.assertRaisesRegex(RuntimeError, "invalid wrapper"):
                    creation.linked_app(self.project, "TTEST")

    def test_legacy_dev_wrapper_and_flat_links_remain_readable(self):
        record = {"TTEST": {"app_id": "ATEST", "team_id": "TTEST"}}
        for value in ({"dev": record}, record):
            with self.subTest(value=value):
                setup.settings.save_config(self.project / ".slack/apps.json", value)
                self.assertEqual(creation.linked_app(self.project, "TTEST"), "ATEST")
                self.assertTrue(setup.saved_slack_app(self.project, "TTEST", "ATEST"))

    def test_custom_project_hooks_are_preserved(self):
        path = self.project / ".slack/hooks.json"
        setup.settings.save_config(path, {"hooks": {"get-manifest": "custom-hook"}})
        original = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "custom hooks"):
            creation.prepare_project(self.project, "Tag Test")
        self.assertEqual(path.read_bytes(), original)

    def test_prepare_project_recognizes_own_hook_after_python_path_changes(self):
        with patch.object(creation.sys, "executable", "/old uv environment/bin/python"):
            creation.prepare_project(self.project, "Tag Test")
        manifest_before = (self.project / "manifest.json").read_bytes()
        with patch.object(creation.sys, "executable", "/new uv environment/bin/python"):
            creation.prepare_project(self.project, "Tag Test")
        hook = creation.read_object(self.project / ".slack/hooks.json")["hooks"]["get-manifest"]
        self.assertIn("/new uv environment/bin/python", hook)
        self.assertNotIn("/old uv environment/bin/python", hook)
        self.assertEqual((self.project / "manifest.json").read_bytes(), manifest_before)

    def test_hook_recognition_preserves_extra_commands_and_custom_helpers(self):
        helper = str(creation.ROOT / "scripts/slack_manifest_hook.py")
        for command in (
            f"/bin/python {helper} --custom",
            f"/bin/python {helper}; echo custom",
            "/bin/python /somewhere/slack_manifest_hook.py",
            f"/bin/other {helper}",
        ):
            with self.subTest(command=command):
                self.assertFalse(creation.is_tag_manifest_hook({"hooks": {"get-manifest": command}}))
        self.assertFalse(creation.is_tag_manifest_hook({
            "hooks": {"get-manifest": f"/bin/python {helper}", "start": "custom"}
        }))

    def test_manifest_uses_name_as_data_and_keeps_all_scopes(self):
        import yaml
        name = 'Tag "Test" $(do-not-run)'
        actual = creation.prepare_project(self.project, name)
        expected = yaml.safe_load((setup.ROOT / "slack-app-manifest.yaml").read_text())
        expected["display_information"]["name"] = name
        expected["features"]["bot_user"]["display_name"] = name
        self.assertEqual(actual, expected)
        self.assertEqual(creation.read_object(self.project / "manifest.json"), expected)
        self.assertNotIn(name, (self.project / ".slack/hooks.json").read_text())

    def test_install_status_requires_exact_app_workspace_and_installed(self):
        for app, team, status, expected in (
            ("ATEST", "TTEST", "Installed", True),
            ("ATEST", "TTEST", "Uninstalled", False),
            ("ATEST", "TOTHER", "Installed", False),
            ("AOTHER", "TTEST", "Installed", False),
            ("ATEST", "TTEST", "Unknown", False),
        ):
            with self.subTest(app=app, team=team, status=status):
                output = f"App  ID: {app}\nTeam ID: {team}\nStatus: {status}\n"
                with patch.object(creation.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")):
                    self.assertEqual(creation.is_installed(self.project, "TTEST", "ATEST"), expected)

    def test_install_status_timeout_fails_closed(self):
        with patch.object(creation.subprocess, "run", side_effect=subprocess.TimeoutExpired("slack", 30)):
            self.assertFalse(creation.is_installed(self.project, "TTEST", "ATEST"))

    @unittest.skipUnless(shutil.which("slack") and os.getenv("TAG_TEST_SLACK_MANIFEST") == "1", "opt-in local CLI manifest test")
    def test_real_cli_reads_manifest_without_authorization(self):
        creation.prepare_project(self.project, "Tag Test")
        # Empty CLI config; --source local invokes only the manifest hook, not Slack APIs.
        (self.home / "empty-cli-config").mkdir()
        result = subprocess.run([
            shutil.which("slack"), "manifest", "info", "--source", "local", "--skip-update", "--no-color",
            "--config-dir", str(self.home / "empty-cli-config"),
        ], cwd=self.project, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = json.loads(result.stdout[result.stdout.index("{"):])
        self.assertEqual(manifest["display_information"]["name"], "Tag Test")
        self.assertTrue(manifest["settings"]["socket_mode_enabled"])
