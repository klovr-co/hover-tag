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

from scripts import slack_invitation_memory, tag_cli


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
        self.assertIn("Memory", output.getvalue())
        self.assertIn("Not ready", output.getvalue())
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

    def test_start_rejects_an_incomplete_runtime_before_service_checks(self) -> None:
        with patch.dict(os.environ, {"TAG_HOME": str(self.home)}, clear=False), patch.object(
            sys, "argv", ["tag", "start"]
        ), patch.object(
            tag_cli, "missing_runtime_dependencies", return_value=("slack_bolt",)
        ), patch.object(tag_cli, "healthy") as healthy:
            with self.assertRaisesRegex(RuntimeError, "./install.sh --dependencies-only"):
                tag_cli.main()

        healthy.assert_not_called()

    def test_paths_defaults_to_a_readable_screen_and_keeps_json_for_automation(self) -> None:
        with patch.dict(os.environ, {"TAG_HOME": str(self.home)}, clear=False), patch.object(
            sys, "argv", ["tag", "paths"]
        ), redirect_stdout(StringIO()) as output:
            self.assertEqual(tag_cli.main(), 0)
        self.assertIn("@Tag by Hover  /  Paths", output.getvalue())
        self.assertIn("https://hover.team/tag", output.getvalue())
        self.assertIn("tag paths --json", output.getvalue())
        self.assertNotIn('"workspace":', output.getvalue())

        with patch.dict(os.environ, {"TAG_HOME": str(self.home)}, clear=False), patch.object(
            sys, "argv", ["tag", "paths", "--json"]
        ), redirect_stdout(StringIO()) as output:
            self.assertEqual(tag_cli.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["workspace"], str(self.home / "workspace"))

    def test_invitation_reconciliation_requires_a_registered_connector(self) -> None:
        status = self.home / "state/slack-memory.json"
        status.write_text(json.dumps({"state": "sync_requested"}), encoding="utf-8")
        with patch.dict(sys.modules, {"slack_invitation_memory": slack_invitation_memory}), patch.object(
            slack_invitation_memory.InvitationMemory, "tick"
        ) as tick:
            tag_cli.reconcile_invitation_memory(self.home)
        tick.assert_called_once_with()

        status.write_text(json.dumps({"state": "needs_attention"}), encoding="utf-8")
        with patch.dict(sys.modules, {"slack_invitation_memory": slack_invitation_memory}), patch.object(
            slack_invitation_memory.InvitationMemory, "tick"
        ):
            with self.assertRaisesRegex(RuntimeError, "Invitation memory could not be prepared"):
                tag_cli.reconcile_invitation_memory(self.home)

        status.write_text(json.dumps({"state": "needs_attention", "check": "mfs_history_credential"}), encoding="utf-8")
        with patch.dict(sys.modules, {"slack_invitation_memory": slack_invitation_memory}), patch.object(
            slack_invitation_memory.InvitationMemory, "tick"
        ):
            with self.assertRaisesRegex(tag_cli.MfsHistoryCredentialUnavailable, "already running without Tag"):
                tag_cli.reconcile_invitation_memory(self.home)

        status.write_text(json.dumps({"state": "needs_attention", "check": "mfs_slack_connector"}), encoding="utf-8")
        with patch.dict(sys.modules, {"slack_invitation_memory": slack_invitation_memory}), patch.object(
            slack_invitation_memory.InvitationMemory, "tick"
        ):
            with self.assertRaisesRegex(tag_cli.MfsSlackConnectorUnavailable, "Slack connector support is not installed"):
                tag_cli.reconcile_invitation_memory(self.home)

    def test_sync_explains_when_the_running_mfs_server_lacks_history_credential(self) -> None:
        config = self.home / "connector.toml"
        config.touch()
        completed = type("Completed", (), {
            "returncode": 1,
            "stdout": "",
            "stderr": "credential_ref 'env:MFS_SLACK_TOKEN': environment variable MFS_SLACK_TOKEN is not set",
        })()
        with patch.object(tag_cli.shutil, "which", return_value="mfs"), patch.object(
            tag_cli.subprocess, "run", return_value=completed
        ):
            with self.assertRaisesRegex(RuntimeError, "already running without Tag's Slack-history credential"):
                tag_cli.sync_configured_slack_memory({
                    "MFS_SLACK_CONNECTOR_URI": "slack://tag-test",
                    "MFS_SLACK_CONNECTOR_CONFIG": str(config),
                })

    def test_sync_explains_when_mfs_lacks_the_slack_connector(self) -> None:
        config = self.home / "connector.toml"
        config.touch()
        completed = type("Completed", (), {
            "returncode": 1,
            "stdout": "",
            "stderr": "error 501: no plugin for slack",
        })()
        with patch.object(tag_cli.shutil, "which", return_value="mfs"), patch.object(
            tag_cli.subprocess, "run", return_value=completed
        ):
            with self.assertRaisesRegex(tag_cli.MfsSlackConnectorUnavailable, "Slack connector support is not installed"):
                tag_cli.sync_configured_slack_memory({
                    "MFS_SLACK_CONNECTOR_URI": "slack://tag-test",
                    "MFS_SLACK_CONNECTOR_CONFIG": str(config),
                })

    def test_sync_updates_an_existing_connector_and_accepts_an_in_progress_sync(self) -> None:
        config = self.home / "connector.toml"
        config.touch()
        existing = type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "connector_already_registered"})()
        updated = type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        with patch.object(tag_cli.shutil, "which", return_value="mfs"), patch.object(
            tag_cli.subprocess, "run", side_effect=[existing, updated]
        ) as run:
            tag_cli.sync_configured_slack_memory({
                "MFS_SLACK_CONNECTOR_URI": "slack://tag-test",
                "MFS_SLACK_CONNECTOR_CONFIG": str(config),
            })
        self.assertEqual(run.call_args_list[1].args[0], ["mfs", "connector", "update", "slack://tag-test", "--config", str(config)])

        in_progress = type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "sync_already_running"})()
        with patch.object(tag_cli.shutil, "which", return_value="mfs"), patch.object(
            tag_cli.subprocess, "run", return_value=in_progress
        ):
            tag_cli.sync_configured_slack_memory({
                "MFS_SLACK_CONNECTOR_URI": "slack://tag-test",
                "MFS_SLACK_CONNECTOR_CONFIG": str(config),
            })

    def test_mfs_server_falls_back_to_path_when_python_runtime_has_no_server(self) -> None:
        with patch.object(tag_cli.Path, "is_file", return_value=False), patch.object(
            tag_cli.shutil, "which", return_value="/usr/local/bin/mfs-server"
        ):
            self.assertEqual(tag_cli.mfs_server_executable(), "/usr/local/bin/mfs-server")


if __name__ == "__main__":
    unittest.main()
