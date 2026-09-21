import os
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
from scripts import tag_display


class DisplayTests(unittest.TestCase):
    def test_banner_fits_narrow_terminal_and_is_omitted_without_color(self):
        with patch.object(tag_display.shutil, "get_terminal_size", return_value=os.terminal_size((48, 24))), patch.object(
            tag_display, "color_available", return_value=True
        ), redirect_stdout(StringIO()) as output:
            self.assertTrue(tag_display.mascot_banner())
        import re
        plain = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue())
        self.assertTrue(all(len(line) <= 46 for line in plain.splitlines()))
        self.assertIn("@Tag by Hover", plain)
        self.assertIn("https://hover.team/tag", plain)
        with patch.object(tag_display, "color_available", return_value=False), redirect_stdout(StringIO()) as output:
            self.assertFalse(tag_display.mascot_banner())
        self.assertEqual(output.getvalue(), "")

    def test_every_header_includes_brand_name_and_link_without_color(self):
        with patch.object(tag_display, "color_available", return_value=False), redirect_stdout(StringIO()) as output:
            tag_display.header("Setup")
        text = output.getvalue()
        self.assertIn("@Tag by Hover  /  Setup", text)
        self.assertIn("https://hover.team/tag", text)

    def test_wide_banner_has_one_brand_name_without_a_duplicate_wordmark(self):
        with patch.object(tag_display.shutil, "get_terminal_size", return_value=os.terminal_size((80, 24))), patch.object(
            tag_display, "color_available", return_value=True
        ), redirect_stdout(StringIO()) as output:
            tag_display.mascot_banner()
        import re
        plain = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue())
        self.assertEqual(plain.count("@Tag by Hover"), 1)
        self.assertNotIn("▀█▀  ▄▀█  █▀▀", plain)

    def test_narrow_color_header_falls_back_without_clipping_brand_text(self):
        with patch.object(
            tag_display.shutil,
            "get_terminal_size",
            return_value=os.terminal_size((28, 24)),
        ), patch.object(
            tag_display, "color_available", return_value=True
        ), redirect_stdout(StringIO()) as output:
            self.assertFalse(tag_display.mascot_banner())
            tag_display.header("Setup")

        plain = output.getvalue().replace("\n", "").replace(" ", "")
        self.assertIn(tag_display.BRAND_NAME.replace(" ", ""), plain)
        self.assertIn(tag_display.BRAND_URL, plain)

    def test_backend_missing_does_not_execute(self):
        with patch.object(tag_display.shutil, "which", return_value=None), patch.object(tag_display.subprocess, "run") as run:
            self.assertEqual(tag_display.backend_status(), ("Not installed", False))
        run.assert_not_called()

    def test_signed_in_does_not_claim_task_success_or_echo_auth_output(self):
        from unittest.mock import Mock
        with patch.object(tag_display.shutil, "which", return_value="/custom/codex"), patch.object(tag_display.subprocess, "run", return_value=Mock(returncode=0, stdout="sensitive", stderr="sensitive")) as run, redirect_stdout(StringIO()) as output:
            tag_display.summary("ready", "tag status", backend="codex")
        self.assertEqual(run.call_args.args[0], ["/custom/codex", "login", "status"])
        self.assertIn("Signed in · task not tested", output.getvalue())
        self.assertNotIn("sensitive", output.getvalue())

    def test_auth_timeout_is_unverified(self):
        with patch.object(tag_display.shutil, "which", return_value="/custom/codex"), patch.object(tag_display.subprocess, "run", side_effect=tag_display.subprocess.TimeoutExpired("codex", 3)):
            self.assertFalse(tag_display.backend_status()[1])

    def test_redirected_output_has_no_escape_codes(self):
        with redirect_stdout(StringIO()) as output:
            tag_display.summary("needs_attention", "tag doctor", slack=False, memory=True)
        self.assertNotIn("\033", output.getvalue())
        self.assertIn("Not connected or unverified", output.getvalue())
        self.assertIn("tag doctor", output.getvalue())
        self.assertNotIn("Connecting", output.getvalue())

    def test_color_respects_terminal_and_no_color(self):
        for term, disabled, expected in [("xterm", False, True), ("dumb", False, False), ("xterm", True, False)]:
            env = {"TERM": term, **({"NO_COLOR": ""} if disabled else {})}
            with patch.dict(os.environ, env, clear=True), patch.object(tag_display.sys.stdout, "isatty", return_value=True):
                self.assertEqual("\033" in tag_display.styled("Ready", "32"), expected)

    def test_terminal_text_falls_back_when_stdout_cannot_encode_ui_glyphs(self):
        legacy_stdout = type("LegacyStdout", (), {"encoding": "cp1252"})()
        with patch.object(tag_display.sys, "stdout", legacy_stdout):
            self.assertEqual(tag_display.terminal_text("✓ › ─ ▀"), "+ > - #")

    def test_doctor_summary_collapses_successful_low_level_checks(self):
        report = {
            "ok": True,
            "checks": [
                {"check": "Tag runtime dependencies", "ok": True},
                {"check": "MFS healthz", "ok": True},
                {"check": "backend codex", "ok": True},
                {"check": "Slack bot auth.test", "ok": True},
                {"check": "Slack channel C123", "ok": True},
                {"check": "Slack channel history C123", "ok": True},
            ],
        }
        with redirect_stdout(StringIO()) as output:
            tag_display.doctor_summary(report)
        text = output.getvalue()
        self.assertIn("1 channel accessible", text)
        self.assertIn("All checks passed", text)
        self.assertNotIn("C123", text)

    def test_doctor_summary_keeps_failed_check_and_next_action(self):
        report = {
            "ok": False,
            "checks": [{
                "check": "Slack channel C123",
                "ok": False,
                "next_action": "Invite Tag to the channel",
            }],
        }
        with redirect_stdout(StringIO()) as output:
            tag_display.doctor_summary(report)
        text = output.getvalue()
        self.assertIn("Slack channel C123", text)
        self.assertIn("Invite Tag to the channel", text)
