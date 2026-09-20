from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import opentag_setup as setup, slack_credentials as credentials


class SlackCredentialsTests(unittest.TestCase):
    def test_free_workspace_app_limit_identifies_the_actual_remedy(self):
        output = ("Your workspace has exhausted the 10 apps limit for free teams. "
                  "To create more apps, upgrade your Slack plan (service_limits_exceeded)")
        error = credentials.handoff_failure(output)
        self.assertIn("10", error.title)
        self.assertIn("uninstall", error.detail.lower())
        self.assertNotIn("did not specify", str(error))

    def test_first_connection_attempt_is_automatic(self):
        with patch.object(setup.slack_credentials, "receive", return_value=self.tokens) as receive, patch.object(
            setup.ui, "choose", side_effect=AssertionError("Unnecessary connection menu")
        ), patch.object(setup, "validate_slack_identity"), patch.object(
            setup, "validate_socket_token"
        ), redirect_stdout(StringIO()):
            setup.connect_app_credentials(self.home, self.config, "TTEST", "ATEST")
        receive.assert_called_once()

    def test_service_limit_is_actionable_without_echoing_cli_output(self):
        with patch.object(credentials.shutil, "which", return_value="/bin/slack"), patch.object(
            credentials.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, "xapp-private", "service_limits_exceeded xoxb-private"
            )
        ), self.assertRaisesRegex(RuntimeError, "service_limits_exceeded") as error:
            credentials.receive(self.project, "TTEST", "ATEST")
        self.assertIn("workspace admin", str(error.exception))
        self.assertNotIn("xapp-private", str(error.exception))
        self.assertNotIn("xoxb-private", str(error.exception))

    def test_failed_auto_attempt_pauses_and_opening_settings_does_not_retry(self):
        with patch.object(setup.slack_credentials, "receive", side_effect=RuntimeError("Slack limit reached")) as receive, patch.object(
            setup.ui, "choose", side_effect=[2, 3]
        ) as choose, patch.object(setup.webbrowser, "open") as browser, redirect_stdout(StringIO()) as output:
            with self.assertRaises(setup.ui.Paused):
                setup.connect_app_credentials(self.home, self.config, "TTEST", "ATEST")
        receive.assert_called_once()
        browser.assert_called_once_with("https://api.slack.com/apps/ATEST")
        self.assertEqual(choose.call_args.args[0], "Next step")
        self.assertEqual(output.getvalue().count("Connecting your app with Slack CLI"), 1)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Tag credential test ")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.project = setup.slack_project(self.home)
        self.config = self.home / "settings.json"
        setup.settings.save_config(self.config, {"SLACK_APP_ID": "ATEST", "SLACK_TEAM_ID": "TTEST", "MFS_TOKEN": "keep-memory"})
        setup.settings.save_config(self.project / ".slack/apps.json", {
            "apps": {"TTEST": {"app_id": "ATEST", "team_id": "TTEST"}}
        })
        self.tokens = {"SLACK_APP_TOKEN": "xapp-fake-app", "SLACK_BOT_TOKEN": "xoxb-fake-bot"}

    def test_receive_uses_exact_app_and_private_hook_without_original_project_changes(self):
        before = {str(p): p.read_bytes() for p in self.project.rglob("*.json")}
        destinations = []

        def fake_cli(command, **kwargs):
            self.assertEqual(command[1:6], ["deploy", "--team", "TTEST", "--app", "ATEST"])
            self.assertNotIn("--force", command)
            handoff = kwargs["cwd"]
            env = kwargs["env"]
            self.assertNotIn("SLACK_APP_TOKEN", env)
            self.assertNotIn("SLACK_BOT_TOKEN", env)
            self.assertNotIn("MFS_TOKEN", env)
            self.assertEqual(json.loads((handoff / ".slack/config.json").read_text()), {"manifest": {"source": "remote"}})
            hooks = json.loads((handoff / ".slack/hooks.json").read_text())["hooks"]
            self.assertEqual(set(hooks), {"deploy"})
            destination = Path(env["TAG_SLACK_HANDOFF_FILE"])
            destinations.append(destination)
            setup.settings.save_config(destination, self.tokens)
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(credentials.shutil, "which", return_value="/bin/slack"), patch.object(
            credentials.subprocess, "run", side_effect=fake_cli
        ), patch.dict(os.environ, {"SLACK_APP_TOKEN": "old-secret", "SLACK_BOT_TOKEN": "old-bot", "MFS_TOKEN": "old-memory"}):
            self.assertEqual(credentials.receive(self.project, "TTEST", "ATEST"), self.tokens)
        self.assertFalse(destinations[0].parent.exists())
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.project.rglob("*.json")})

    def test_failed_or_pending_handoff_never_prints_raw_output_or_saves(self):
        for code in (0, 1):
            with self.subTest(code=code), patch.object(credentials.shutil, "which", return_value="/bin/slack"), patch.object(
                credentials.subprocess, "run", return_value=subprocess.CompletedProcess([], code, "xapp-private", "xoxb-private")
            ), redirect_stdout(StringIO()) as output, self.assertRaisesRegex(RuntimeError, "did not provide credentials") as error:
                credentials.receive(self.project, "TTEST", "ATEST")
            self.assertNotIn("xapp-private", str(error.exception) + output.getvalue())
            self.assertNotIn("xoxb-private", str(error.exception) + output.getvalue())
            self.assertNotIn("SLACK_APP_TOKEN", setup.settings.load_config(self.config))

    def test_unknown_app_never_calls_cli(self):
        with patch.object(credentials.subprocess, "run") as run, self.assertRaisesRegex(RuntimeError, "link could not be confirmed"):
            credentials.receive(self.project, "TTEST", "AOTHER")
        run.assert_not_called()

    def test_real_hook_is_silent_private_and_rejects_overwrite(self):
        path = self.home / "receipt.json"
        env = dict(os.environ, **self.tokens, TAG_SLACK_HANDOFF_FILE=str(path))
        command = [sys.executable, str(credentials.ROOT / "scripts/slack_credential_hook.py")]
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout + result.stderr, "")
        self.assertEqual(json.loads(path.read_text()), self.tokens)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        retry = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(retry.returncode, 1)
        self.assertEqual(retry.stdout + retry.stderr, "")

    def test_real_hook_missing_credentials_creates_no_receipt(self):
        path = self.home / "receipt.json"
        env = {key: value for key, value in os.environ.items() if key not in self.tokens}
        env["TAG_SLACK_HANDOFF_FILE"] = str(path)
        result = subprocess.run([sys.executable, str(credentials.ROOT / "scripts/slack_credential_hook.py")],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout + result.stderr, "")
        self.assertFalse(path.exists())

    def test_automatic_connection_validates_pair_before_saving(self):
        with patch.object(setup.ui, "choose", return_value=0), patch.object(
            setup.slack_credentials, "receive", return_value=self.tokens
        ), patch.object(setup, "validate_slack_identity") as bot, patch.object(
            setup, "validate_socket_token"
        ) as socket, patch.object(setup.getpass, "getpass") as secret, redirect_stdout(StringIO()):
            result = setup.connect_app_credentials(self.home, self.config, "TTEST", "ATEST")
        bot.assert_called_once_with(self.tokens["SLACK_BOT_TOKEN"], team_id="TTEST", app_id="ATEST", label="Bot token")
        socket.assert_called_once_with(self.tokens["SLACK_APP_TOKEN"], "ATEST")
        secret.assert_not_called()
        self.assertEqual(result["SLACK_APP_TOKEN"], self.tokens["SLACK_APP_TOKEN"])
        self.assertEqual(result["MFS_TOKEN"], "keep-memory")
        self.assertNotIn("MFS_SLACK_TOKEN", result)

    def test_failed_validation_preserves_config_and_exits_without_manual_prompt(self):
        before = self.config.read_bytes()
        with patch.object(setup.ui, "choose", return_value=3), patch.object(
            setup.slack_credentials, "receive", return_value=self.tokens
        ), patch.object(setup, "validate_slack_identity", side_effect=RuntimeError("Wrong workspace")), patch.object(
            setup.getpass, "getpass"
        ) as secret, redirect_stdout(StringIO()), self.assertRaises(setup.ui.Paused):
            setup.connect_app_credentials(self.home, self.config, "TTEST", "ATEST")
        self.assertEqual(self.config.read_bytes(), before)
        secret.assert_not_called()

    def test_completed_credentials_are_preserved_without_cli(self):
        setup.settings.update_config(self.config, self.tokens)
        with patch.object(setup.slack_credentials, "receive") as receive, patch.object(setup.ui, "choose") as choose:
            setup.connect_app_credentials(self.home, self.config, "TTEST", "ATEST")
        receive.assert_not_called()
        choose.assert_not_called()

    def test_manual_and_exit_choices_do_not_repeat_failed_cli_attempt(self):
        for action in (1, 3):
            with self.subTest(action=action), patch.object(setup.ui, "choose", return_value=action), patch.object(
                setup.slack_credentials, "receive", side_effect=RuntimeError("Connection blocked")
            ) as receive, redirect_stdout(StringIO()):
                if action == 3:
                    with self.assertRaises(setup.ui.Paused):
                        setup.connect_app_credentials(self.home, self.config, "TTEST", "ATEST")
                else:
                    setup.connect_app_credentials(self.home, self.config, "TTEST", "ATEST")
                receive.assert_called_once()
