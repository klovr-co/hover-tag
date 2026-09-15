from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.opentag_setup import ask_required, render_env, runtime_requirement, write_config


class OpenTagSetupTests(unittest.TestCase):
    @patch("builtins.input", side_effect=["", "UOWNER"])
    def test_required_owner_id_reprompts_until_set(self, _mock_input: object) -> None:
        self.assertEqual("UOWNER", ask_required("Owner Slack member ID"))

    def test_runtime_dependencies_come_from_the_pinned_requirement_file(self) -> None:
        self.assertEqual(runtime_requirement("mfs-server"), "mfs-server==0.4.6")

    def test_render_env_shell_quotes_values(self) -> None:
        rendered = render_env({"OPENTAG_WORKDIR": "/tmp/Tag workspace", "TOKEN": "a'b"})

        self.assertIn("export OPENTAG_WORKDIR='/tmp/Tag workspace'", rendered)
        self.assertIn("export TOKEN='a'\"'\"'b'", rendered)

    def test_write_config_uses_owner_only_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".env"
            write_config(path, {"OPENTAG_BACKEND": "codex"})

            mode = stat.S_IMODE(path.stat().st_mode)

        self.assertEqual(mode, 0o600)
