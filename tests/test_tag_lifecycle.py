from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import psutil

from scripts import tag_cli


class TagLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary_directory.name)
        tag_cli.initialize(self.home)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_slack_identity(self, instance_id: str, heartbeat: float | None = None) -> None:
        process = psutil.Process()
        (self.home / "state/slack.json").write_text(
            json.dumps(
                {
                    "pid": process.pid,
                    "created": process.create_time(),
                    "instance_id": instance_id,
                }
            ),
            encoding="utf-8",
        )
        (self.home / "state/slack.ready").write_text(
            f"{instance_id} {process.pid} {heartbeat or time.time()}\n",
            encoding="utf-8",
        )

    def test_slack_readiness_requires_matching_current_heartbeat(self) -> None:
        self.write_slack_identity("launch-one")
        self.assertTrue(tag_cli.slack_ready(self.home))

        (self.home / "state/slack.ready").write_text(
            f"another-launch {os.getpid()} {time.time()}\n", encoding="utf-8"
        )
        self.assertFalse(tag_cli.slack_ready(self.home))

        self.write_slack_identity("launch-one", heartbeat=time.time() - 30)
        self.assertFalse(tag_cli.slack_ready(self.home))

    def test_status_fails_when_required_services_are_unhealthy(self) -> None:
        output = StringIO()
        with patch.dict(os.environ, {"TAG_HOME": str(self.home)}, clear=False), patch.object(
            sys, "argv", ["tag", "status"]
        ), patch.object(tag_cli, "healthy", return_value=False), patch.object(
            tag_cli, "slack_ready", return_value=False
        ), redirect_stdout(output):
            result = tag_cli.main()

        self.assertEqual(1, result)
        self.assertIn("MFS: stopped or unhealthy", output.getvalue())
        self.assertIn("Slack bridge: stopped or disconnected", output.getvalue())

    def test_failed_start_removes_process_identity(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "exited during startup"):
            tag_cli.start_process(
                self.home,
                "fixture",
                [sys.executable, "-c", "raise SystemExit(7)"],
            )

        self.assertFalse((self.home / "state/fixture.json").exists())


if __name__ == "__main__":
    unittest.main()
