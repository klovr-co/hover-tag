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

    def test_local_mfs_endpoint_excludes_remote_and_non_http_urls(self) -> None:
        self.assertTrue(tag_cli.local_mfs_endpoint("http://127.0.0.1:13619"))
        self.assertTrue(tag_cli.local_mfs_endpoint("http://localhost:13619"))
        self.assertTrue(tag_cli.local_mfs_endpoint("http://Localhost:13619"))
        self.assertTrue(tag_cli.local_mfs_endpoint("http://localhost:13619/"))
        self.assertFalse(tag_cli.local_mfs_endpoint("https://mfs.example.com"))
        self.assertFalse(tag_cli.local_mfs_endpoint("file://local/mfs"))
        self.assertFalse(tag_cli.local_mfs_endpoint("http://localhost:14000"))
        self.assertFalse(tag_cli.local_mfs_endpoint("http://[::1]:13619"))
        self.assertFalse(tag_cli.local_mfs_endpoint("http://localhost:13619/api"))
        self.assertFalse(tag_cli.local_mfs_endpoint("http://localhost:13619?mode=test"))

    def test_memory_status_includes_redacted_shared_log_tail(self) -> None:
        shared = self.root / "shared/mfs"
        shared.mkdir(parents=True)
        secret = "xoxb-shared-log-secret"
        (shared / "mfs.log").write_text(f"startup failed: {secret}\n", encoding="utf-8")

        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}, clear=False), patch.object(
            sys, "argv", ["tag", "memory", "status"]
        ), patch.object(tag_cli, "healthy", return_value=False), redirect_stdout(StringIO()) as output:
            self.assertEqual(tag_cli.main(), 0)

        self.assertIn("RECENT MEMORY OUTPUT", output.getvalue())
        self.assertIn("startup failed: <redacted>", output.getvalue())
        self.assertNotIn(secret, output.getvalue())

    def test_memory_start_uses_saved_mfs_settings_before_onboarding_completes(self) -> None:
        config = self.home / "config/settings.json"
        config.write_text('{"MFS_URL":"http://localhost:13619"}')

        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}, clear=False), patch.object(
            sys, "argv", ["tag", "memory", "start"]
        ), patch.object(tag_cli, "ensure_shared_memory") as ensure, patch.object(
            tag_cli, "process_for", return_value=None
        ), patch.object(tag_cli, "healthy", return_value=True), redirect_stdout(StringIO()):
            self.assertEqual(tag_cli.main(), 0)

        environment = ensure.call_args.args[1]
        self.assertEqual(environment["MFS_URL"], "http://localhost:13619")
        self.assertEqual(environment["TAG_INSTANCE_HOME"], str(self.home))

    def test_shared_memory_start_reuses_the_installation_lock_and_workspace(self) -> None:
        context = tag_instances.resolve(self.root)
        environment = {
            "MFS_URL": "http://127.0.0.1:13619",
            "OPENTAG_MFS_STARTUP_ATTEMPTS": "1",
        }
        with patch.object(
            tag_cli, "healthy", side_effect=[False, False, True]
        ), patch.object(
            tag_cli, "mfs_server_executable", return_value="/bin/mfs-server"
        ), patch.object(tag_cli, "replace_unmanaged_local_mfs"), patch.object(
            tag_cli, "start_process"
        ) as start:
            tag_cli.ensure_shared_memory(context, environment)

        self.assertFalse((context.shared_mfs_home / "start.lock").exists())
        self.assertEqual(start.call_args.kwargs["state_dir"], context.shared_mfs_home)
        self.assertEqual(start.call_args.kwargs["cwd"], context.workspace)

    def test_local_mfs_listener_matches_the_resolved_configured_address(self) -> None:
        expected = MagicMock(pid=22)
        expected.cmdline.return_value = ["python", "-m", "mfs_server", "run"]
        with patch.object(
            tag_cli.shutil, "which", return_value="/usr/sbin/lsof"
        ), patch.object(
            tag_cli.socket,
            "getaddrinfo",
            return_value=[(None, None, None, None, ("127.0.0.1", 13619))],
        ), patch.object(
            tag_cli.subprocess,
            "run",
            return_value=SimpleNamespace(stdout="22\n"),
        ) as run, patch.object(
            psutil, "Process", return_value=expected
        ) as process:
            listener = tag_cli.local_mfs_listener("http://127.0.0.1:13619")

        self.assertIs(expected, listener)
        run.assert_called_once_with(
            [
                "lsof",
                "-nP",
                "-iTCP@127.0.0.1:13619",
                "-sTCP:LISTEN",
                "-t",
            ],
            check=False,
            text=True,
            stdout=tag_cli.subprocess.PIPE,
            stderr=tag_cli.subprocess.DEVNULL,
        )
        process.assert_called_once_with(22)

    def test_local_mfs_listener_rejects_multiple_matching_processes(self) -> None:
        first = MagicMock(pid=11)
        first.cmdline.return_value = ["mfs-server", "run"]
        second = MagicMock(pid=22)
        second.cmdline.return_value = ["mfs-server", "run"]
        connections = [
            SimpleNamespace(
                status=psutil.CONN_LISTEN,
                pid=11,
                laddr=SimpleNamespace(ip="127.0.0.1", port=13619),
            ),
            SimpleNamespace(
                status=psutil.CONN_LISTEN,
                pid=22,
                laddr=SimpleNamespace(ip="127.0.0.1", port=13619),
            ),
        ]
        with patch.object(tag_cli.shutil, "which", return_value=None), patch.object(
            tag_cli.socket,
            "getaddrinfo",
            return_value=[(None, None, None, None, ("127.0.0.1", 13619))],
        ), patch.object(psutil, "net_connections", return_value=connections), patch.object(
            psutil, "Process", side_effect=[first, second]
        ):
            listener = tag_cli.local_mfs_listener("http://127.0.0.1:13619")

        self.assertIsNone(listener)

    def test_unmanaged_local_mfs_is_adopted_then_stopped(self) -> None:
        process = MagicMock(pid=1234)
        process.create_time.return_value = 42.0
        with patch.object(tag_cli, "healthy", return_value=True), patch.object(
            tag_cli, "process_for", side_effect=[None]
        ), patch.object(tag_cli, "local_mfs_listener", return_value=process), patch.object(
            tag_cli, "stop_process"
        ) as stop:
            self.assertTrue(
                tag_cli.replace_unmanaged_local_mfs(
                    self.home, "http://127.0.0.1:13619"
                )
            )

        stop.assert_called_once_with(self.home, "mfs")
        record = json.loads((self.home / "state/mfs.json").read_text(encoding="utf-8"))
        self.assertEqual(
            {"pid": 1234, "created": 42.0, "adopted": True}, record
        )

    def test_remote_mfs_is_never_adopted(self) -> None:
        with patch.object(tag_cli, "healthy") as healthy, patch.object(
            tag_cli, "local_mfs_listener"
        ) as listener:
            self.assertFalse(
                tag_cli.replace_unmanaged_local_mfs(
                    self.home, "https://mfs.example.com"
                )
            )
        healthy.assert_not_called()
        listener.assert_not_called()

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
