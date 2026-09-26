import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import tag_cli, tag_mfs_runtime, tag_config


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.shared = self.root / "shared/mfs"
        self.shared.mkdir(parents=True)
        self.context = SimpleNamespace(home=self.root / "instance",
            shared_mfs_home=self.shared, workspace=self.root, is_default=False)
        self.process = SimpleNamespace(pid=123, cmdline=lambda: ["python", "/release/scripts/tag_mfs_server.py"])

    def test_identity_requires_matching_adapter_version_process_and_nonce(self):
        tag_config.save_config(self.shared / "mfs.json", {"slack_runtime_version": 1, "instance_id": "new"})
        path = self.shared / "slack-runtime-ready-v1.json"
        for ready, expected in (
            ({"version": 1, "pid": 123, "instance_id": "old"}, False),
            ({"version": 1, "pid": 999, "instance_id": "new"}, False),
            ({"version": 0, "pid": 123, "instance_id": "new"}, False),
            ({"version": 1, "pid": 123, "instance_id": "new"}, True),
        ):
            tag_config.save_config(path, ready)
            self.assertEqual(tag_mfs_runtime.active(self.shared, self.process), expected)

    def test_old_managed_runtime_is_migrated_once_and_verified_before_checkpoint(self):
        running, modern = True, False
        starts = []
        def stop(*args, **kwargs):
            nonlocal running
            running = False
        def start(*args, **kwargs):
            nonlocal running, modern
            self.assertFalse((self.shared / "slack-runtime-migration-v1.json").exists())
            starts.append((args, kwargs))
            running, modern = True, True
        with patch.object(tag_cli, "replace_unmanaged_local_mfs"), patch.object(
            tag_cli, "process_for", side_effect=lambda _: self.process if running else None
        ), patch.object(tag_cli, "healthy", side_effect=lambda _: running), patch.object(
            tag_mfs_runtime, "active", side_effect=lambda *args: modern
        ), patch.object(tag_cli, "mfs_server_executable", return_value="mfs-server"), patch.object(
            tag_cli, "stop_process", side_effect=stop
        ) as stopped, patch.object(tag_cli, "start_process", side_effect=start):
            tag_cli.ensure_shared_memory(self.context, {"CUSTOM": "keep"})
            tag_cli.ensure_shared_memory(self.context, {"CUSTOM": "keep"})
        stopped.assert_called_once()
        self.assertEqual(len(starts), 1)
        args, kwargs = starts[0]
        self.assertTrue(args[2][1].endswith("tag_mfs_server.py"))
        self.assertEqual(kwargs["environment"]["CUSTOM"], "keep")
        self.assertEqual(kwargs["metadata"]["slack_runtime_version"], 1)
        self.assertEqual(json.loads((self.shared / "slack-runtime-migration-v1.json").read_text()), {"version": 1})

    def test_failed_replacement_does_not_commit_migration(self):
        with patch.object(tag_cli, "replace_unmanaged_local_mfs"), patch.object(
            tag_cli, "process_for", return_value=None
        ), patch.object(tag_cli, "healthy", return_value=False), patch.object(
            tag_cli, "mfs_server_executable", return_value="mfs-server"
        ), patch.object(tag_cli, "start_process", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                tag_cli.ensure_shared_memory(self.context, {})
        self.assertFalse((self.shared / "slack-runtime-migration-v1.json").exists())

    def test_healthy_external_memory_is_not_restarted(self):
        with patch.object(tag_cli, "healthy", return_value=True), patch.object(tag_cli, "stop_process") as stop:
            tag_cli.ensure_shared_memory(self.context, {"MFS_URL": "https://external.test"})
        stop.assert_not_called()

    def test_cooldown_is_visible_and_readiness_does_not_spend_more_quota(self):
        with patch.dict(os.environ, {"TAG_HOME": str(self.root), "SLACK_TEAM_ID": "TTEST",
                                   "MFS_ALLOWED_SCOPES": "slack://tag-test/channels/one"}), patch.object(
            tag_cli.tag_slack_backoff, "cooldown", return_value=130
        ), patch.object(tag_cli.time, "time", return_value=100), patch.object(
            tag_cli.time, "sleep"
        ), patch.object(tag_cli, "resolve_indexed_mfs_scope") as probe, redirect_stdout(StringIO()) as output:
            with self.assertRaisesRegex(RuntimeError, "retry automatically"):
                tag_cli.wait_for_configured_mfs_scopes(attempts=2)
        probe.assert_not_called()
        self.assertIn("Indexing paused by Slack; retrying in 30 seconds", output.getvalue())

    def test_expired_cooldown_resumes_readiness_check(self):
        with patch.dict(os.environ, {"TAG_HOME": str(self.root), "SLACK_TEAM_ID": "TTEST",
                                   "MFS_ALLOWED_SCOPES": "slack://tag-test/channels/one"}), patch.object(
            tag_cli.tag_slack_backoff, "cooldown", side_effect=[130, 0]
        ), patch.object(tag_cli.time, "time", return_value=100), patch.object(
            tag_cli.time, "sleep"
        ), patch.object(tag_cli, "resolve_indexed_mfs_scope", return_value="slack://tag-test/channels/one") as probe, redirect_stdout(StringIO()):
            self.assertEqual(tag_cli.wait_for_configured_mfs_scopes(attempts=2), [])
        probe.assert_called_once()


if __name__ == "__main__":
    unittest.main()
