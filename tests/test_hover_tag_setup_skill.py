from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


SCRIPT = (
    Path(__file__).parents[1]
    / ".agents/skills/hover-tag-setup/scripts/slack_auth_clipboard.py"
)
SPEC = importlib.util.spec_from_file_location("slack_auth_clipboard", SCRIPT)
assert SPEC and SPEC.loader
slack_auth_clipboard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(slack_auth_clipboard)


class SlackAuthClipboardTests(unittest.TestCase):
    def test_begin_copies_command_but_emits_only_private_state_path(self):
        ticket = "secret-ticket"
        login = subprocess.CompletedProcess([], 0, f"/slackauthticket {ticket}\n", "")
        original_mkstemp = tempfile.mkstemp
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                slack_auth_clipboard, "slack_command", return_value="/bin/slack"
            ), patch.object(
                slack_auth_clipboard.subprocess, "run", return_value=login
            ), patch.object(
                slack_auth_clipboard, "write_clipboard"
            ) as clipboard, patch.object(
                slack_auth_clipboard.tempfile, "mkstemp",
                side_effect=lambda **kwargs: original_mkstemp(dir=directory, **kwargs),
            ), redirect_stdout(StringIO()) as output:
                self.assertEqual(slack_auth_clipboard.begin(), 0)

            payload = json.loads(output.getvalue())
            state = Path(payload["state_file"])
            clipboard.assert_called_once_with(f"/slackauthticket {ticket}")
            self.assertNotIn(ticket, output.getvalue())
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)
            self.assertEqual(json.loads(state.read_text())["ticket"], ticket)

    def test_complete_reads_clipboard_without_emitting_values_and_removes_state(self):
        ticket, challenge = "secret-ticket", "Short123"
        handle, raw_state = tempfile.mkstemp(
            prefix="hover-tag-slack-auth-", suffix=".json"
        )
        os.close(handle)
        state = Path(raw_state)
        try:
            state.write_text(json.dumps({"slack": "/bin/slack", "ticket": ticket}))
            with patch.object(
                slack_auth_clipboard, "read_clipboard", return_value=challenge
            ), patch.object(
                slack_auth_clipboard.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as run, redirect_stdout(StringIO()) as output:
                self.assertEqual(slack_auth_clipboard.complete(state), 0)

            command = run.call_args.args[0]
            self.assertIn(ticket, command)
            self.assertIn(challenge, command)
            self.assertNotIn(ticket, output.getvalue())
            self.assertNotIn(challenge, output.getvalue())
            self.assertFalse(state.exists())
        finally:
            state.unlink(missing_ok=True)

    def test_invalid_clipboard_value_is_not_submitted_and_state_is_removed(self):
        handle, raw_state = tempfile.mkstemp(
            prefix="hover-tag-slack-auth-", suffix=".json"
        )
        os.close(handle)
        state = Path(raw_state)
        try:
            state.write_text(json.dumps({"slack": "/bin/slack", "ticket": "ticket"}))
            with patch.object(
                slack_auth_clipboard, "read_clipboard", return_value="not a code"
            ), patch.object(
                slack_auth_clipboard.subprocess, "run"
            ) as run, redirect_stdout(StringIO()) as output:
                self.assertEqual(slack_auth_clipboard.complete(state), 1)

            run.assert_not_called()
            self.assertIn("does not contain", output.getvalue())
            self.assertFalse(state.exists())
        finally:
            state.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
