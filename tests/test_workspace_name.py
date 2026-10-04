"""Older Tags learn their Slack workspace's name, so apps stop showing the Team ID."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from scripts import tag_cli, tag_instances


class WorkspaceNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.home = tag_instances.create(self.root, "t1-a1").home
        self.values = {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_TEAM_ID": "T0BND7V5J2W"}
        self.calls: list[str] = []

    def api(self, payload):
        def call(token, method, params):
            self.calls.append(method)
            if isinstance(payload, Exception):
                raise payload
            return payload
        return call

    def test_an_older_tag_records_its_workspace_name_once(self) -> None:
        self.assertIsNone(tag_instances.workspace_name(self.home))
        self.assertEqual("Klovr", tag_cli._refresh_workspace_name(self.home, self.values, api=self.api({"ok": True, "team": "Klovr"})))
        self.assertEqual("Klovr", tag_instances.workspace_name(self.home))
        # Repeated starts don't ask Slack again.
        self.assertIsNone(tag_cli._refresh_workspace_name(self.home, self.values, api=self.api({"ok": True, "team": "Other"})))
        self.assertEqual(["auth.test"], self.calls)
        self.assertEqual("Klovr", tag_instances.workspace_name(self.home))

    def test_a_saved_name_is_kept(self) -> None:
        tag_instances.record_workspace_name(self.home, "Acme Inc")
        self.assertIsNone(tag_cli._refresh_workspace_name(self.home, self.values, api=self.api({"ok": True, "team": "Klovr"})))
        self.assertEqual("Acme Inc", tag_instances.workspace_name(self.home))
        self.assertEqual([], self.calls)

    def test_a_failure_changes_nothing_and_is_tried_again(self) -> None:
        self.assertIsNone(tag_cli._refresh_workspace_name(self.home, self.values, api=self.api(RuntimeError("offline"))))
        self.assertIsNone(tag_instances.workspace_name(self.home))
        self.assertIsNone(tag_cli._refresh_workspace_name(self.home, self.values, api=self.api({"ok": True})))
        self.assertEqual("Klovr", tag_cli._refresh_workspace_name(self.home, self.values, api=self.api({"ok": True, "team": "Klovr"})))

    def test_a_tag_without_slack_credentials_is_skipped(self) -> None:
        self.assertIsNone(tag_cli._refresh_workspace_name(self.home, {}, api=self.api({"ok": True, "team": "Klovr"})))
        self.assertEqual([], self.calls)


if __name__ == "__main__":
    unittest.main()
