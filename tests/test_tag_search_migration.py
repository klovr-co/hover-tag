from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import tag_cli, tag_config, tag_instances, tag_search_migration as migration


class SearchMigrationTests(unittest.TestCase):
    """Upgrading from the local Slack index to live search, for older installations."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "Tag"
        self.index = self.base / "mfs-home"
        environment = patch.dict(os.environ, {"TAG_HOME": str(self.root), "MFS_HOME": str(self.index)})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("MFS_TOKEN", None)
        self.context = tag_instances.create(self.root, "t1-a1")
        self.home = self.context.home
        connector = self.home / "integrations/mfs/connectors/tag-t1-a1.toml"
        connector.parent.mkdir(parents=True)
        connector.write_text('token = "file:x"\n', encoding="utf-8")
        tag_config.save_config(self.home / "config/settings.json", {
            "OPENTAG_BACKEND": "codex", "SLACK_TEAM_ID": "T1", "SLACK_APP_ID": "A1", "SLACK_CHANNEL_IDS": "C1",
            "MFS_URL": "http://127.0.0.1:13619", "MFS_ALLOWED_SCOPES": "slack://tag-t1-a1/channels/launch__C1",
            "MFS_SLACK_TOKEN": "xoxb-history", "MFS_SLACK_CONNECTOR_URI": "slack://tag-t1-a1",
            "MFS_SLACK_CONNECTOR_CONFIG": str(connector)})
        credentials = self.home / "config/credentials"
        credentials.mkdir(parents=True)
        (credentials / "mfs-slack-token").write_text("xoxb-history", encoding="utf-8")
        self.index.mkdir()
        (self.index / "milvus.db").write_text("slack copies", encoding="utf-8")
        (self.index / "server.token").write_text("server-token", encoding="utf-8")

    def test_tag_settings_and_history_credential_are_removed_once(self) -> None:
        self.assertTrue(migration.retire_tag(self.home))
        values = tag_config.load_config(self.home / "config/settings.json")
        self.assertFalse([key for key in values if key.startswith("MFS_")])
        self.assertEqual("C1", values["SLACK_CHANNEL_IDS"])
        self.assertFalse((self.home / "config/credentials/mfs-slack-token").exists())
        self.assertFalse((self.home / "integrations/mfs/connectors/tag-t1-a1.toml").exists())
        self.assertFalse(migration.retire_tag(self.home))  # Recorded: nothing to do again.

    def test_a_running_old_index_drops_every_tags_slack_copies_then_stops(self) -> None:
        removed, state = [], {"running": True}
        with patch.object(migration, "_healthy", return_value=True), patch.object(
            migration, "_remove_connector", side_effect=lambda url, uri: removed.append(uri) or True
        ):
            result = migration.retire_installation(self.root, stop=lambda: state.update(running=False),
                                                   running=lambda: state["running"])
        self.assertEqual("done", result["state"])
        self.assertIn("slack://tag-t1-a1", removed)
        self.assertFalse(state["running"])
        # The index folder may hold other data, so Tag leaves it and says so.
        self.assertEqual(str(self.index), migration.leftover(self.root))
        self.assertTrue((self.index / "milvus.db").exists())

    def test_a_failed_removal_keeps_the_server_and_retries_later(self) -> None:
        stopped = []
        with patch.object(migration, "_healthy", return_value=True), patch.object(
            migration, "_remove_connector", return_value=False
        ):
            result = migration.retire_installation(self.root, stop=lambda: stopped.append(1), running=lambda: True)
        self.assertEqual("retry", result["state"])
        self.assertEqual([], stopped)
        self.assertFalse((self.root / migration.MARKER).exists())

    def test_removing_the_old_index_happens_only_when_asked(self) -> None:
        migration.retire_installation(self.root, stop=lambda: None, running=lambda: False)
        self.assertTrue((self.index / "milvus.db").exists())
        out = io.StringIO()
        with patch.object(sys, "argv", ["tag", "memory", "remove-old-index"]), redirect_stdout(out), \
                self.assertRaisesRegex(RuntimeError, "--yes to confirm"):
            tag_cli.main()
        self.assertTrue((self.index / "milvus.db").exists())
        with patch.object(sys, "argv", ["tag", "memory", "remove-old-index", "--yes", "--json"]), redirect_stdout(out):
            self.assertEqual(0, tag_cli.main())
        self.assertFalse((self.index / "milvus.db").exists())
        self.assertFalse((self.index / "server.token").exists())
        self.assertIsNone(migration.leftover(self.root))

    def test_tag_memory_says_slack_is_searched_live_and_keeps_fields_for_older_apps(self) -> None:
        out = io.StringIO()
        with patch.object(sys, "argv", ["tag", "memory", "status", "--json"]), redirect_stdout(out):
            self.assertEqual(0, tag_cli.main())
        result = json.loads(out.getvalue())
        self.assertTrue(result["retired"])
        self.assertIn("search Slack live", result["message"])
        self.assertEqual((True, False, None), (result["healthy"], result["keep_running"], result["embedding"]))

    def test_tag_start_migrates_before_the_bridge_and_rollback_is_refused_after(self) -> None:
        self.assertIsNone(tag_config.load_config(self.home / "config/settings.json").get("NONE"))
        with patch.object(tag_cli, "process_for", return_value=None):
            message = tag_cli._retire_search_index(self.context)
        self.assertIn("may still hold Slack copies", message)
        self.assertFalse([key for key in tag_config.load_config(self.home / "config/settings.json") if key.startswith("MFS_")])

    def test_rollback_to_a_release_that_needs_the_old_index_is_refused(self) -> None:
        root = self.base / "single"
        tag_instances.ensure_default(root)
        tag_config.save_config(root / migration.MARKER, {"version": migration.VERSION, "leftover": None})
        (root / "previous.json").write_text("{}", encoding="utf-8")
        with patch.dict(os.environ, {"TAG_HOME": str(root)}), patch.object(sys, "argv", ["tag", "rollback"]), \
                patch.object(tag_cli, "bridge_processes", return_value=[]), \
                redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "Upgrade instead"):
            tag_cli.main()


if __name__ == "__main__":
    unittest.main()
