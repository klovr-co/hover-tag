from __future__ import annotations

import os
import select
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from scripts import setup_ui


class SetupUITests(unittest.TestCase):
    def test_narrow_screen_keeps_current_step_and_keyboard_help_visible(self):
        with patch.object(setup_ui.shutil, "get_terminal_size", return_value=os.terminal_size((48, 24))), redirect_stdout(StringIO()) as output:
            setup_ui.screen(3, "Where should Tag respond?", "Slack connected")
            print(setup_ui._instructions(multiple=True))
        text = output.getvalue()
        self.assertIn("STEP 3/4 · Channels", text)
        self.assertIn("Space Toggle", text)
        self.assertIn("q Exit", text)
        self.assertTrue(all(len(line) < 48 for line in text.splitlines()))

    def test_notice_wraps_prose_and_separates_error_code(self):
        with patch.object(setup_ui.shutil, "get_terminal_size", return_value=os.terminal_size((48, 24))), redirect_stdout(StringIO()) as output:
            setup_ui.notice("Slack couldn't connect", "Ask your workspace admin or Slack support to resolve the limit, then retry.",
                            code="service_limits_exceeded", footer="Progress saved. Setup is paused.")
        text = output.getvalue()
        self.assertTrue(all(len(line) <= 46 for line in text.splitlines()))
        self.assertIn("\n\n  Code: service_limits_exceeded\n\n", text)
        self.assertNotIn("\x1b", text)

    def test_picker_preserves_zero_value_and_default(self):
        with patch.object(setup_ui, "keyboard_available", return_value=True), patch(
            "questionary.select"
        ) as prompt, redirect_stdout(StringIO()):
            prompt.return_value.unsafe_ask.return_value = 0
            self.assertEqual(setup_ui.choose("Slack app", ["Create", "Link"], default=1), 0)
        options = prompt.call_args.kwargs
        self.assertEqual([choice.value for choice in options["choices"]], [0, 1])
        self.assertEqual(options["default"].value, 1)
        self.assertEqual(options["qmark"], " ")
        self.assertEqual(options["pointer"], " ›")
        self.assertIn("\n", options["instruction"])
        self.assertIn(("pointer", "fg:#38cff1 bold"), options["style"].style_rules)
        self.assertIn(("selected", "fg:default bg:default noreverse nobold"), options["style"].style_rules)

    def test_message_uses_the_shared_two_space_content_gutter(self):
        with redirect_stdout(StringIO()) as output:
            setup_ui.message("App configuration needs attention")
        self.assertEqual(output.getvalue(), "  App configuration needs attention\n")

    def test_checklist_preserves_selection_and_validates_nonempty(self):
        with patch.object(setup_ui, "keyboard_available", return_value=True), patch(
            "questionary.checkbox"
        ) as prompt, redirect_stdout(StringIO()):
            prompt.return_value.unsafe_ask.return_value = [1]
            self.assertEqual(setup_ui.checklist(["#one", "#two"], {0}), {1})
        options = prompt.call_args.kwargs
        self.assertEqual([choice.checked for choice in options["choices"]], [True, False])
        self.assertEqual(prompt.call_args.args[0], "Choose channels")
        self.assertIs(options["validate"]([0]), True)
        self.assertEqual(options["validate"]([]), "Select at least one channel.")

    def test_interactive_cancel_pauses(self):
        for error in (KeyboardInterrupt, EOFError):
            with self.subTest(error=error), patch("questionary.select") as prompt, patch.object(
                setup_ui, "keyboard_available", return_value=True
            ), redirect_stdout(StringIO()), self.assertRaises(setup_ui.Paused):
                prompt.return_value.unsafe_ask.side_effect = error
                setup_ui.choose("Continue?", ["Continue"])

    def test_no_color(self):
        from prompt_toolkit.output import ColorDepth
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            self.assertEqual(setup_ui._prompt_options()["color_depth"], ColorDepth.DEPTH_1_BIT)

    def test_plain_checklist_requires_selection_and_allows_removal(self):
        with patch.object(setup_ui, "keyboard_available", return_value=False), patch(
            "builtins.input", side_effect=["", "1,2", "2", ""]
        ), redirect_stdout(StringIO()):
            self.assertEqual(setup_ui.checklist(["#one", "#two"], set()), {0})

    def test_plain_menu_can_pause(self):
        with patch.object(setup_ui, "keyboard_available", return_value=False), patch(
            "builtins.input", return_value="q"
        ), redirect_stdout(StringIO()), self.assertRaises(setup_ui.Paused):
            setup_ui.choose("Continue?", ["Continue", "Save and exit"])

    @unittest.skipIf(os.name == "nt", "POSIX PTY test")
    def test_real_terminal_arrow_space_enter_and_terminal_restoration(self):
        self._run_terminal(
            "from scripts.setup_ui import checklist; print('RESULT', sorted(checklist(['#one', '#two'], set())), flush=True)",
            b"#two", b"\x1b[B \r", b"RESULT [1]",
        )

    @unittest.skipIf(os.name == "nt", "POSIX PTY test")
    def test_real_terminal_q_and_ctrl_c_pause(self):
        for key in (b"q", b"\x03"):
            with self.subTest(key=key):
                self._run_terminal(
                    "from scripts.setup_ui import choose, Paused\ntry: choose('App', ['Create', 'Link'])\nexcept Paused: print('PAUSED', flush=True)",
                    b"Link", key, b"PAUSED",
                )

    @unittest.skipIf(os.name == "nt", "POSIX PTY test")
    def test_real_terminal_default_and_no_color(self):
        self._run_terminal(
            "import os; os.environ['NO_COLOR']='1'\nfrom scripts.setup_ui import choose\nprint('RESULT', choose('App', ['Create', 'Link'], default=1), flush=True)",
            b"Link", b"\r", b"RESULT 1",
        )

    def _run_terminal(self, program, ready, keys, expected):
        import pty
        import termios
        master, slave = pty.openpty()
        original = termios.tcgetattr(slave)
        process = subprocess.Popen([sys.executable, "-c", program], stdin=slave, stdout=slave, stderr=slave,
                                   env=dict(os.environ, TERM="xterm", PROMPT_TOOLKIT_NO_CPR="1"))
        transcript = b""
        try:
            # Wait for Questionary to render before sending actual terminal keys.
            for _ in range(50):
                if select.select([master], [], [], 0.1)[0]:
                    transcript += os.read(master, 8192)
                    if ready in transcript:
                        break
            self.assertIn(ready, transcript)
            os.write(master, keys)
            # Drain output while the child restores the terminal (TCSADRAIN).
            deadline = time.monotonic() + 5
            while process.poll() is None and time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    transcript += os.read(master, 8192)
            process.wait(timeout=1)
            while select.select([master], [], [], 0.1)[0]:
                transcript += os.read(master, 8192)
            self.assertEqual(process.returncode, 0, transcript.decode(errors="replace"))
            self.assertIn(expected, transcript)
            restored = termios.tcgetattr(slave)
            # macOS may add its kernel-managed PENDIN flag after input.
            input_flags = termios.ECHO | termios.ICANON | termios.ISIG
            self.assertEqual(restored[3] & input_flags, original[3] & input_flags)
            self.assertEqual(restored[:3], original[:3])
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)
            os.close(slave)
