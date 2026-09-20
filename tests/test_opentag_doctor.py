from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import opentag_doctor
from scripts.opentag_doctor import check_offline


class OpenTagDoctorTests(unittest.TestCase):
    def test_runtime_check_lists_missing_dependencies(self) -> None:
        with patch.object(
            opentag_doctor.importlib.util,
            "find_spec",
            side_effect=lambda name: None if name == "slack_bolt" else object(),
        ):
            self.assertFalse(opentag_doctor.check_runtime_dependencies())

    def test_offline_check_needs_no_credentials_or_network(self) -> None:
        root = Path(__file__).resolve().parents[1]
        environment = {
            "OPENTAG_TRANSPORT": "slack",
            "OPENTAG_BACKEND": "codex",
            "OPENTAG_WORKDIR": str(root),
            "MFS_URL": "http://127.0.0.1:13619",
            "MFS_ALLOWED_SCOPES": f"file://local{root}",
            "SLACK_ALLOWED_USER_IDS": "UOWNER",
        }

        with patch.dict(os.environ, environment, clear=True), patch.object(
            opentag_doctor, "check_runtime_dependencies", return_value=True
        ):
            self.assertTrue(check_offline(root))

    def test_offline_slack_check_fails_without_allowed_user(self) -> None:
        root = Path(__file__).resolve().parents[1]
        environment = {
            "OPENTAG_TRANSPORT": "slack",
            "OPENTAG_BACKEND": "codex",
            "OPENTAG_WORKDIR": str(root),
            "MFS_URL": "http://127.0.0.1:13619",
            "MFS_ALLOWED_SCOPES": f"file://local{root}",
        }

        with patch.dict(os.environ, environment, clear=True):
            self.assertFalse(check_offline(root))
