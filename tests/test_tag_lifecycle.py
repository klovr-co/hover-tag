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
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psutil

from scripts import slack_invitation_memory, tag_cli, tag_instances


class TagLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.home = tag_instances.ensure_default(self.root).home

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
        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}, clear=False), patch.object(
            sys, "argv", ["tag", "status"]
        ), patch.object(tag_cli, "healthy", return_value=False), patch.object(
            tag_cli, "slack_ready", return_value=False
        ), redirect_stdout(output):
            result = tag_cli.main()

        self.assertEqual(1, result)
        # Slack is the only required service: there is no memory service to report.
        self.assertNotIn("Memory", output.getvalue())
        self.assertIn("Slack", output.getvalue())
        self.assertIn("Not connected or unverified", output.getvalue())

    def test_failed_start_removes_process_identity(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "fixture dependency exploded"):
            tag_cli.start_process(
                self.home,
                "fixture",
                [
                    sys.executable,
                    "-c",
                    "import sys; print('fixture dependency exploded', file=sys.stderr); raise SystemExit(7)",
                ],
            )

        self.assertFalse((self.home / "state/fixture.json").exists())

    def test_failed_start_redacts_tokens_from_reported_log(self) -> None:
        token = "xox" + "b-this-is-a-secret-token"
        with self.assertRaises(RuntimeError) as raised:
            tag_cli.start_process(
                self.home,
                "fixture",
                [sys.executable, "-c", f"print('{token}'); raise SystemExit(7)"],
            )

        self.assertNotIn(token, str(raised.exception))
        self.assertIn("<redacted>", str(raised.exception))


    ARGS = ("custom", "--base-url", "https://api.example.invalid/v1", "--embedding-model", "embed-x")


    def run_memory_start(self, reindex):
        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}, clear=False), \
                patch.object(sys, "argv", ["tag", "memory", "start"]), \
                patch.object(tag_cli, "ensure_shared_memory"), \
                patch.object(tag_cli, "process_for", return_value=None), \
                patch.object(tag_cli, "healthy", return_value=True), \
                patch.object(tag_cli, "reindex_slack_memory", side_effect=reindex) as run, \
                redirect_stdout(StringIO()), patch("sys.stderr", StringIO()) as errors:
            code = tag_cli.main()
        return code, run, errors.getvalue()


    def test_legacy_slack_readiness_requires_a_fresh_connected_heartbeat(self) -> None:
        path = self.home / "runtime/slack-connected.json"
        path.parent.mkdir()
        path.write_text(
            json.dumps({"connected": True, "time": time.time()}), encoding="utf-8"
        )
        self.assertTrue(tag_cli.legacy_slack_ready(self.home))

        path.write_text(
            json.dumps({"connected": True, "time": time.time() - 60}),
            encoding="utf-8",
        )
        self.assertFalse(tag_cli.legacy_slack_ready(self.home))

    def test_legacy_slack_readiness_rejects_orphaned_fresh_heartbeat(self) -> None:
        legacy = self.home / "legacy"
        legacy.mkdir()
        command = legacy / "tag"
        command.write_text("#!/bin/sh\n", encoding="utf-8")
        (self.home / "state/legacy-command.json").write_text(
            json.dumps({"command": str(command)}), encoding="utf-8"
        )
        heartbeat = self.home / "runtime/slack-connected.json"
        heartbeat.parent.mkdir()
        heartbeat.write_text(
            json.dumps({"connected": True, "time": time.time()}), encoding="utf-8"
        )

        with patch.object(psutil, "process_iter", return_value=[]):
            self.assertFalse(tag_cli.legacy_slack_ready(self.home))

        legacy_process = SimpleNamespace(
            info={
                "cmdline": [
                    sys.executable,
                    str(legacy / "scripts/slack_socket_agent.py"),
                ]
            }
        )
        with patch.object(psutil, "process_iter", return_value=[legacy_process]):
            self.assertTrue(tag_cli.legacy_slack_ready(self.home))

    def test_start_rejects_an_incomplete_runtime_before_service_checks(self) -> None:
        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}, clear=False), patch.object(
            sys, "argv", ["tag", "start"]
        ), patch.object(
            tag_cli, "missing_runtime_dependencies", return_value=("slack_bolt",)
        ), patch.object(tag_cli, "healthy") as healthy:
            with self.assertRaisesRegex(RuntimeError, "./install.sh --dependencies-only"):
                tag_cli.main()

        healthy.assert_not_called()

    def test_paths_defaults_to_a_readable_screen_and_keeps_json_for_automation(self) -> None:
        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}, clear=False), patch.object(
            sys, "argv", ["tag", "paths"]
        ), redirect_stdout(StringIO()) as output:
            self.assertEqual(tag_cli.main(), 0)
        self.assertIn("@Tag by Hover  /  Paths", output.getvalue())
        self.assertIn("https://hover.team/tag", output.getvalue())
        self.assertIn("tag paths --json", output.getvalue())
        self.assertNotIn('"workspace":', output.getvalue())

        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}, clear=False), patch.object(
            sys, "argv", ["tag", "paths", "--json"]
        ), redirect_stdout(StringIO()) as output:
            self.assertEqual(tag_cli.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["workspace"], str(self.home / "workspace"))

    def test_invitation_check_must_succeed_before_the_bridge_starts(self) -> None:
        status = self.home / "state/slack-memory.json"
        status.parent.mkdir(parents=True, exist_ok=True)
        (self.home / "config").mkdir(parents=True, exist_ok=True)
        (self.home / "config/settings.json").write_text(json.dumps({"SLACK_CHANNEL_IDS": "C1"}), encoding="utf-8")
        for state in ("ready", "no_joined_channels"):
            status.write_text(json.dumps({"state": state}), encoding="utf-8")
            with patch.dict(sys.modules, {"slack_invitation_memory": slack_invitation_memory}), patch.object(
                slack_invitation_memory.InvitationMemory, "tick"
            ) as tick, patch.dict(os.environ, {}, clear=False):
                tag_cli.reconcile_invitation_channels(self.home)
                self.assertEqual("C1", os.environ["SLACK_CHANNEL_IDS"])
            tick.assert_called_once_with()
        status.write_text(json.dumps({"state": "needs_attention", "check": "membership"}), encoding="utf-8")
        with patch.dict(sys.modules, {"slack_invitation_memory": slack_invitation_memory}), patch.object(
            slack_invitation_memory.InvitationMemory, "tick"
        ):
            with self.assertRaisesRegex(RuntimeError, "couldn't check which channels"):
                tag_cli.reconcile_invitation_channels(self.home)


if __name__ == "__main__":
    unittest.main()
