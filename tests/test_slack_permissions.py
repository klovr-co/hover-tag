import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

from scripts import slack_permissions as permissions


class PermissionRecoveryTests(unittest.TestCase):
    def test_manual_guidance_distinguishes_credentials(self):
        for method, scope, expected in (
            ("conversations.join", "channels:join", "Bot Token Scopes"),
            ("apps.connections.open", "connections:write", "App-Level Tokens"),
            ("conversations.history", "channels:history", "separate Slack-history credential"),
        ):
            with self.subTest(method=method), redirect_stdout(StringIO()) as output, patch.object(
                permissions.ui, "choose", return_value=2
            ) as menu:
                check = unittest.mock.Mock(side_effect=permissions.MissingScope(method, scope))
                with self.assertRaises(permissions.ui.Paused):
                    permissions.recover(check, "A123")
                self.assertIn(expected, output.getvalue())
                self.assertIn(scope, output.getvalue())
                self.assertIn("Tag will not change permissions", output.getvalue())
                self.assertEqual(menu.call_args.args[1], ["Open app settings", "Check again", "Save and exit"])
                self.assertEqual(check.call_count, 1)

    def test_permission_message_identifies_check_and_reason(self):
        error = permissions.MissingScope("bots.info", "users:read")
        self.assertIn("users:read", str(error))
        self.assertIn("selected app", str(error))

    def test_retry_does_not_open_browser_without_choice(self):
        check = unittest.mock.Mock(side_effect=[permissions.MissingScope("bots.info", "users:read"), {"ok": True}])
        with patch.object(permissions.ui, "choose", return_value=1), patch.object(permissions.webbrowser, "open") as browser, redirect_stdout(StringIO()):
            self.assertEqual(permissions.recover(check, "A123"), {"ok": True})
        browser.assert_not_called()
        self.assertEqual(check.call_count, 2)

    def test_open_settings_then_exit_does_not_retry(self):
        check = unittest.mock.Mock(side_effect=permissions.MissingScope("apps.connections.open", "connections:write"))
        with patch.object(permissions.ui, "choose", side_effect=[0, 2]), patch.object(permissions.webbrowser, "open") as browser, redirect_stdout(StringIO()):
            with self.assertRaises(permissions.ui.Paused):
                permissions.recover(check, "A123")
        browser.assert_called_once_with("https://api.slack.com/apps/A123")
        self.assertEqual(check.call_count, 1)

    def test_untrusted_scope_text_is_not_displayed(self):
        self.assertNotIn("xoxb-secret", str(permissions.MissingScope("bots.info", "xoxb-secret\n\x1b[31m")))
