from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import slack_channel_names as cache, tag_activity


class ChannelNameCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.values = {"SLACK_TEAM_ID": "T1", "SLACK_CHANNEL_IDS": "C1,C2", "SLACK_BOT_TOKEN": "fixture"}

    def test_old_installation_backfills_once_and_keeps_cache_across_restarts(self):
        store = tag_activity.ActivityStore(self.home / "state/activity")
        store.create(team="T1", channel="G3", thread_ts="1.0", request_ts="1.0", requester="U1")
        store.create(team="T1", channel="D4", thread_ts="1.0", request_ts="1.0", requester="U1")
        store.create(team="TOTHER", channel="C5", thread_ts="1.0", request_ts="1.0", requester="U1")
        api = Mock(side_effect=lambda _, __, p: {"ok": True, "channel": {"id": p["channel"], "name": "name-" + p["channel"]}})
        self.assertTrue(cache.migrate(self.home, self.values, api=api))
        self.assertEqual({"C1", "C2", "G3"}, cache.read(self.home, "T1").keys())
        self.assertTrue(cache.migrate(self.home, self.values, api=api))
        self.assertEqual(3, api.call_count)
        self.assertEqual({}, cache.read(self.home, "TOTHER"))
        path = self.home / "state/slack-channel-names/T1/C1.json"
        self.assertEqual(0o600, path.stat().st_mode & 0o777)
        self.assertNotIn("fixture", path.read_text(encoding="utf-8"))

    def test_partial_failure_retries_only_missing_names_and_marks_after_verification(self):
        marker = self.home / "state/migrations/slack-channel-names-v1.json"
        api = Mock(side_effect=[{"ok": True, "channel": {"id": "C1", "name": "one"}}, RuntimeError("offline")])
        self.assertFalse(cache.migrate(self.home, self.values, api=api))
        self.assertFalse(marker.exists())
        retry = Mock(return_value={"ok": True, "channel": {"id": "C2", "name": "two"}})
        self.assertTrue(cache.migrate(self.home, self.values, api=retry))
        retry.assert_called_once_with("fixture", "conversations.info", {"channel": "C2"})
        self.assertEqual(1, json.loads(marker.read_text(encoding="utf-8"))["version"])

    def test_saved_names_seed_cache_and_live_names_replace_them_without_rewrites(self):
        values = {**self.values, "MFS_ALLOWED_SCOPES": "slack://tag-t1/channels/one__C1,slack://tag-t1/channels/two__C2"}
        api = Mock()
        self.assertTrue(cache.migrate(self.home, values, api=api))
        api.assert_not_called()
        cache.remember(self.home, "T1", {"C1": "renamed"})
        with patch.object(cache.tag_config, "save_config") as write:
            cache.migrate(self.home, values, api=api)
            cache.remember(self.home, "T1", {"C1": "renamed"})
        write.assert_not_called()
        self.assertEqual("renamed", cache.read(self.home, "T1")["C1"])

    def test_corrupt_cache_wrong_responses_and_interrupted_writes_recover(self):
        cache.remember(self.home, "T1", {"C1": "one"})
        (self.home / "state/slack-channel-names/T1/C1.json").write_text("broken", encoding="utf-8")
        self.assertEqual({}, cache.read(self.home, "T1"))
        api = Mock(return_value={"ok": True, "channel": {"id": "COTHER", "name": "wrong"}})
        self.assertFalse(cache.migrate(self.home, self.values, api=api))
        with patch.object(cache.tag_config, "save_config", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                cache.remember(self.home, "T1", {"C1": "one"})
        cache.remember(self.home, "T1", {"C1": "one"})
        self.assertEqual({"C1": "one"}, cache.read(self.home, "T1"))
        cache.remember(self.home, "../escape", {"C1": "bad"})
        cache.remember(self.home, "T1", {"../escape": "bad", "D1": "private person"})
        self.assertEqual({"C1": "one"}, cache.read(self.home, "T1"))
