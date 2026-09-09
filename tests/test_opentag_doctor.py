from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.opentag_doctor import check_offline


class OpenTagDoctorTests(unittest.TestCase):
    def test_offline_check_needs_no_credentials_or_network(self) -> None:
        root = Path(__file__).resolve().parents[1]
        environment = {
            "OPENTAG_TRANSPORT": "slack",
            "OPENTAG_BACKEND": "codex",
            "OPENTAG_WORKDIR": str(root),
            "MFS_URL": "http://127.0.0.1:13619",
            "MFS_ALLOWED_SCOPES": f"file://local{root}",
        }

        with patch.dict(os.environ, environment, clear=True):
            self.assertTrue(check_offline(root))
