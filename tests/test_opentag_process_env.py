from __future__ import annotations

import unittest

from scripts.opentag_process_env import backend_environment, current_channel_scopes


class BackendEnvironmentTests(unittest.TestCase):
    def test_bridge_access_control_is_not_inherited_by_backend(self) -> None:
        environment = backend_environment(
            {
                "SLACK_APP_TOKEN": "xapp-secret",
                "SLACK_ALLOWED_USER_IDS": "UOWNER",
                "SLACK_CHANNEL_ID": "C123",
                "OPENTAG_SLACK_DM_ENABLED": "1",
                "SLACK_BOT_TOKEN": "xoxb-needed-by-tools",
            },
            transport="slack",
            conversation_id="D123",
            caller_id="UOWNER",
        )

        self.assertNotIn("SLACK_APP_TOKEN", environment)
        self.assertNotIn("SLACK_ALLOWED_USER_IDS", environment)
        self.assertNotIn("SLACK_CHANNEL_ID", environment)
        self.assertNotIn("OPENTAG_SLACK_DM_ENABLED", environment)
        self.assertEqual("xoxb-needed-by-tools", environment["SLACK_BOT_TOKEN"])
        self.assertEqual("D123", environment["OPENTAG_CURRENT_CHANNEL_ID"])


class CurrentChannelMemoryTests(unittest.TestCase):
    def test_only_current_slack_channel_scope_reaches_backend(self) -> None:
        scopes = (
            "slack://tag-t1/channels/team__C1,"
            "slack://tag-t1/channels/team-copy__C10,"
            "slack://tag-t1/channels/private__G2"
        )
        self.assertEqual(
            "slack://tag-t1/channels/team__C1",
            current_channel_scopes(scopes, "C1"),
        )

    def test_backend_does_not_receive_connector_or_bridge_credentials(self) -> None:
        environment = backend_environment(
            {
                "SLACK_APP_TOKEN": "xapp-secret",
                "SLACK_BOT_TOKEN": "xoxb-bot",
                "MFS_SLACK_TOKEN": "xoxp-history",
                "MFS_ALLOWED_SCOPES": "slack://tag-t1/channels/team__C1",
            },
            transport="slack",
            conversation_id="C1",
            caller_id="U1",
        )
        self.assertNotIn("SLACK_APP_TOKEN", environment)
        self.assertNotIn("MFS_SLACK_TOKEN", environment)
        self.assertEqual("xoxb-bot", environment["SLACK_BOT_TOKEN"])

    def test_wrong_channel_has_no_slack_memory_scope(self) -> None:
        self.assertEqual(
            "",
            current_channel_scopes("slack://tag-t1/channels/team__C1", "C9"),
        )

    def test_non_slack_scopes_do_not_reach_slack_replies(self) -> None:
        self.assertEqual(
            "slack://tag-t1/channels/team__C1",
            current_channel_scopes(
                "file://local/private,slack://tag-t1/channels/team__C1", "C1"
            ),
        )

    def test_pre_authorized_cross_channel_scopes_replace_default_narrowing(self) -> None:
        environment = backend_environment(
            {"MFS_ALLOWED_SCOPES": "slack://tag-t1/channels/current__C1"},
            transport="slack",
            conversation_id="C1",
            caller_id="U1",
            authorized_scopes="slack://tag-t1/channels/support__C2",
            channel_labels='{"C2": "support"}',
            slack_search_grant='{"mode": "all"}',
        )
        self.assertEqual(
            "slack://tag-t1/channels/support__C2",
            environment["MFS_ALLOWED_SCOPES"],
        )
        self.assertEqual('{"C2": "support"}', environment["OPENTAG_SLACK_CHANNEL_LABELS"])
        self.assertEqual('{"mode": "all"}', environment["OPENTAG_SLACK_SEARCH_GRANT"])
