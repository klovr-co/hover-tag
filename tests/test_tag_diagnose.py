import os
import tempfile
import unittest
from pathlib import Path
from io import StringIO
from contextlib import redirect_stdout
from unittest.mock import patch, Mock
from scripts import tag_diagnose as diagnosis


class DiagnosisTests(unittest.TestCase):
    def test_report_does_not_forward_log_content(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "error.log"
            log.write_text("BrokenPipeError xoxb-secret private Slack message token=arbitrary-secret")
            payload = diagnosis.report(1, False, True, [log])
        self.assertEqual(payload["historical_log_signals"], ["socket_broken_pipe_observed"])
        self.assertNotIn("secret", str(payload))
        self.assertNotIn("private Slack", str(payload))

    def test_decline_and_noninteractive_never_launch(self):
        for terminal in (True, False):
            with patch.object(diagnosis.sys.stdin, "isatty", return_value=terminal), patch("builtins.input", return_value=""), patch.object(diagnosis.subprocess, "run") as run, redirect_stdout(StringIO()):
                diagnosis.offer({})
            run.assert_not_called()

    def test_opt_in_uses_safeguards_and_strips_service_credentials(self):
        help_result = Mock(returncode=0, stdout="--ignore-user-config --ephemeral --sandbox --disable")
        with patch.object(diagnosis.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="y"), patch.object(diagnosis.shutil, "which", return_value="/bin/codex"), patch.dict(os.environ, {"SLACK_BOT_TOKEN": "private", "MFS_TOKEN": "private"}), patch.object(diagnosis.subprocess, "run", side_effect=[help_result, Mock(returncode=0, stdout="Suggestion")]) as run, redirect_stdout(StringIO()):
            diagnosis.offer({"slack_connected": False})
        args, kwargs = run.call_args
        self.assertIn("read-only", args[0])
        self.assertIn("shell_tool", args[0])
        self.assertIn("--ignore-user-config", args[0])
        self.assertNotIn("SLACK_BOT_TOKEN", kwargs["env"])
        self.assertNotIn("MFS_TOKEN", kwargs["env"])
        self.assertIn('"slack_connected": false', kwargs["input"])
        self.assertIn("friendly teammate", kwargs["input"])
        self.assertIn("at most 80 words", kwargs["input"])
        self.assertIn("a reply still needs testing", kwargs["input"])
        self.assertIn("never copy a healthy example when a check fails", kwargs["input"])

    def test_old_codex_fails_closed(self):
        with patch.object(diagnosis.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="y"), patch.object(diagnosis.shutil, "which", return_value="/bin/codex"), patch.object(diagnosis.subprocess, "run", return_value=Mock(returncode=0, stdout="old help")) as run, redirect_stdout(StringIO()):
            diagnosis.offer({})
        self.assertEqual(run.call_count, 1)
