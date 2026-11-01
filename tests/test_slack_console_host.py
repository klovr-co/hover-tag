from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from scripts import slack_console_host as host

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "scripts/slack_console_host.py"
WINDOWLESS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# As in tag_dependencies.run_slack: a venv launcher's job would end the survivor.
BASE_PYTHON = getattr(sys, "_base_executable", None) or sys.executable

# Reads the console input like the Slack CLI's background-color query: CONIN$
# opened directly (its stdin is redirected), raw VT input, a blocking ReadFile.
READ_CONSOLE_REPLY = r'''
import ctypes, os, sys, threading
from ctypes import wintypes
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.restype = wintypes.HANDLE
conin = kernel32.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)
kernel32.SetConsoleMode(conin, 0x200)
threading.Timer(15, lambda: os._exit(3)).start()
buffer, read = ctypes.create_string_buffer(256), wintypes.DWORD()
kernel32.ReadFile(conin, buffer, 256, ctypes.byref(read), None)
sys.stdout.write(buffer.raw[:read.value].decode("ascii"))
sys.stdout.flush()
os._exit(0)
'''


class SlackConsoleHostTests(unittest.TestCase):
    def test_reply_reports_a_dark_background_and_ends_with_device_attributes(self):
        # The CLI keeps reading until the DA1 reply, so it must come last.
        self.assertTrue(host.TERMINAL_REPLY.startswith("\x1b]11;rgb:0000/0000/0000\x07"))
        self.assertTrue(host.TERMINAL_REPLY.endswith("\x1b[?62c"))

    def test_runs_the_command_and_returns_its_exit_code(self):
        self.assertEqual(host.main([sys.executable, "-c", "raise SystemExit(3)"]), 3)
        self.assertEqual(host.main([]), 2)

    def test_reports_a_command_that_cannot_start(self):
        missing = str(Path(tempfile.gettempdir()) / "no-such-slack-cli" / "slack.exe")
        self.assertEqual(host.main([missing, "version"]), 127)

    @unittest.skipUnless(os.name == "nt", "Windows consoles")
    def test_windowless_console_answers_the_terminal_query(self):
        result = subprocess.run([BASE_PYTHON, "-I", str(HOST), sys.executable, "-I", "-c", READ_CONSOLE_REPLY],
                                capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
                                creationflags=WINDOWLESS)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, host.TERMINAL_REPLY)

    @unittest.skipUnless(os.name == "nt", "Windows job objects")
    def test_a_timeout_ends_the_command_and_everything_it_started(self):
        # A grandchild holding the output pipes would otherwise keep the caller waiting.
        grandchild = ("import subprocess, sys; "
                      "subprocess.call([sys.executable, '-c', 'import time; time.sleep(120)'])")
        outcome = []

        def run():
            started = time.monotonic()
            try:
                subprocess.run([BASE_PYTHON, "-I", str(HOST), sys.executable, "-c", grandchild],
                               capture_output=True, timeout=3, stdin=subprocess.DEVNULL, creationflags=WINDOWLESS)
            except subprocess.TimeoutExpired:
                outcome.append(time.monotonic() - started)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(60)
        self.assertFalse(thread.is_alive(), "the caller waited for an orphaned grandchild")
        self.assertTrue(outcome and outcome[0] < 30, outcome)

    @unittest.skipUnless(os.name == "nt", "Windows job objects")
    def test_a_normal_finish_leaves_what_the_command_started_on_purpose(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "survived"
            survivor = f"import time; time.sleep(2); open({str(marker)!r}, 'w').close()"
            starter = ("import subprocess, sys; subprocess.Popen([sys.executable, '-c', %r], "
                       "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)" % survivor)
            subprocess.run([BASE_PYTHON, "-I", str(HOST), sys.executable, "-c", starter],
                           timeout=60, stdin=subprocess.DEVNULL, creationflags=WINDOWLESS)
            deadline = time.monotonic() + 30
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.2)
            self.assertTrue(marker.exists())


if __name__ == "__main__":
    unittest.main()
