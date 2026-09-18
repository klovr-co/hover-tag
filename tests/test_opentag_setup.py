from __future__ import annotations

import stat
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts.opentag_setup import (
    ask_required,
    choose_backend,
    main,
    render_env,
    runtime_requirement,
    selected_backend_available,
    write_config,
)


class OpenTagSetupTests(unittest.TestCase):
    @patch("scripts.opentag_setup.shutil.which", side_effect=lambda command: f"/bin/{command}")
    @patch("builtins.input", return_value="")
    def test_backend_menu_defaults_to_codex(
        self, _mock_input: object, _mock_which: object
    ) -> None:
        output = StringIO()

        with redirect_stdout(output):
            backend = choose_backend()

        self.assertEqual(backend, "codex")
        self.assertIn("Codex        Recommended and supported", output.getvalue())
        self.assertIn("Claude Code  Experimental", output.getvalue())

    @patch(
        "scripts.opentag_setup.shutil.which",
        side_effect=lambda command: "/bin/codex" if command == "codex" else None,
    )
    @patch("builtins.input", return_value="2")
    def test_backend_menu_can_select_experimental_claude(
        self, _mock_input: object, _mock_which: object
    ) -> None:
        output = StringIO()

        with redirect_stdout(output):
            backend = choose_backend()

        self.assertEqual(backend, "claude")
        self.assertIn("Claude Code  Experimental", output.getvalue())
        self.assertIn("not found", output.getvalue())

    @patch("scripts.opentag_setup.shutil.which", return_value="/bin/claude")
    @patch("builtins.input", return_value="Claude Code")
    def test_backend_menu_accepts_agent_name(
        self, _mock_input: object, _mock_which: object
    ) -> None:
        with redirect_stdout(StringIO()):
            self.assertEqual(choose_backend(), "claude")

    @patch("scripts.opentag_setup.shutil.which", return_value="/bin/codex")
    def test_selected_backend_is_available(self, _mock_which: object) -> None:
        self.assertTrue(selected_backend_available("codex"))

    @patch("scripts.opentag_setup.shutil.which", return_value=None)
    def test_missing_selected_backend_has_actionable_guidance(
        self, _mock_which: object
    ) -> None:
        output = StringIO()

        with redirect_stdout(output):
            available = selected_backend_available("claude")

        self.assertFalse(available)
        self.assertIn("Claude Code was selected", output.getvalue())
        self.assertIn("run ./tag setup again", output.getvalue())

    @patch("scripts.opentag_setup.shutil.which", return_value="/bin/codex")
    @patch("builtins.input", side_effect=["other", "codex"])
    def test_backend_menu_reprompts_after_invalid_choice(
        self, _mock_input: object, _mock_which: object
    ) -> None:
        output = StringIO()

        with redirect_stdout(output):
            backend = choose_backend()

        self.assertEqual(backend, "codex")
        self.assertIn("Choose 1 for Codex or 2 for Claude Code.", output.getvalue())

    @patch("scripts.opentag_setup.ask_secret")
    @patch("scripts.opentag_setup.selected_backend_available", return_value=False)
    @patch("scripts.opentag_setup.choose_backend", return_value="claude")
    def test_setup_stops_before_secrets_when_backend_is_missing(
        self,
        _mock_choose: object,
        _mock_available: object,
        mock_ask_secret: object,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config = Path(temporary_directory) / ".env"
            with patch("sys.argv", ["opentag_setup.py", "--config", str(config)]):
                with redirect_stdout(StringIO()):
                    result = main()

        self.assertEqual(result, 1)
        mock_ask_secret.assert_not_called()

    @patch("builtins.input", side_effect=["", "UOWNER"])
    def test_required_owner_id_reprompts_until_set(self, _mock_input: object) -> None:
        self.assertEqual("UOWNER", ask_required("Owner Slack member ID"))

    def test_runtime_dependencies_come_from_the_pinned_requirement_file(self) -> None:
        self.assertEqual(runtime_requirement("mfs-server"), "mfs-server==0.4.6")

    def test_render_env_shell_quotes_values(self) -> None:
        rendered = render_env({"OPENTAG_WORKDIR": "/tmp/Tag workspace", "TOKEN": "a'b"})

        self.assertIn("export OPENTAG_WORKDIR='/tmp/Tag workspace'", rendered)
        self.assertIn("export TOKEN='a'\"'\"'b'", rendered)

    @unittest.skipIf(os.name == "nt", "Windows uses account directory ACLs, not POSIX mode bits")
    def test_write_config_uses_owner_only_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".env"
            write_config(path, {"OPENTAG_BACKEND": "codex"})

            mode = stat.S_IMODE(path.stat().st_mode)

        self.assertEqual(mode, 0o600)
