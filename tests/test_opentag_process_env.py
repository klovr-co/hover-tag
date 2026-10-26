from __future__ import annotations

import json
import unittest

from scripts.opentag_process_env import (
    backend_environment,
    text_only_environment,
    without_telemetry_environment,
)


class BackendEnvironmentTests(unittest.TestCase):
    def test_telemetry_settings_do_not_reach_any_child_environment(self) -> None:
        source = {
            "PATH": "/fixture/bin",
            "TAG_TELEMETRY": "off",
            "TAG_POSTHOG_PROJECT_TOKEN": "public-project-token",
            "THIRD_PARTY_TELEMETRY_MODE": "enabled",
            "DISABLE_TELEMETRY": "1",
            "POSTHOG_PERSONAL_API_KEY": "third-party-key",
        }

        environment = without_telemetry_environment(source)

        self.assertEqual(environment, {
            "PATH": "/fixture/bin",
            "THIRD_PARTY_TELEMETRY_MODE": "enabled",
            "DISABLE_TELEMETRY": "1",
            "POSTHOG_PERSONAL_API_KEY": "third-party-key",
        })

    def test_the_memory_helper_learns_the_slack_thread_of_this_run_only(self) -> None:
        source = {"PATH": "/bin", "OPENTAG_CURRENT_THREAD_TS": "1.1"}
        here = backend_environment(source, transport="slack", conversation_id="C1", caller_id="U1",
                                   thread_ts="1729000000.000100")
        self.assertEqual("1729000000.000100", here["OPENTAG_CURRENT_THREAD_TS"])
        unthreaded = backend_environment(source, transport="slack", conversation_id="C1", caller_id="U1")
        self.assertNotIn("OPENTAG_CURRENT_THREAD_TS", unthreaded)
        self.assertEqual("", text_only_environment(here)["OPENTAG_CURRENT_THREAD_TS"])

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


class LiveSearchEnvironmentTests(unittest.TestCase):
    def test_old_index_settings_and_credentials_never_reach_a_run(self) -> None:
        environment = backend_environment(
            {
                "SLACK_APP_TOKEN": "xapp-secret",
                "SLACK_BOT_TOKEN": "xoxb-bot",
                "MFS_SLACK_TOKEN": "xoxp-history",
                "MFS_ALLOWED_SCOPES": "slack://tag-t1/channels/team__C1",
                "MFS_CHANNEL_SCOPES": '{"C1": ["file://archive"]}',
            },
            transport="slack",
            conversation_id="C1",
            caller_id="U1",
        )
        self.assertNotIn("SLACK_APP_TOKEN", environment)
        self.assertFalse([key for key in environment if key.startswith("MFS_")])
        self.assertEqual("xoxb-bot", environment["SLACK_BOT_TOKEN"])

    def test_the_search_grant_labels_and_action_token_reach_only_this_run(self) -> None:
        environment = backend_environment(
            {"OPENTAG_SLACK_ACTION_TOKEN": "stale"},
            transport="slack",
            conversation_id="C1",
            caller_id="U1",
            channel_labels='{"C2": "support"}',
            slack_search_grant='{"mode": "all"}',
            action_token="action-1",
        )
        self.assertEqual('{"C2": "support"}', environment["OPENTAG_SLACK_CHANNEL_LABELS"])
        self.assertEqual('{"mode": "all"}', environment["OPENTAG_SLACK_SEARCH_GRANT"])
        self.assertEqual("action-1", environment["OPENTAG_SLACK_ACTION_TOKEN"])
        unrelated = backend_environment({"OPENTAG_SLACK_ACTION_TOKEN": "stale"}, transport="slack",
                                        conversation_id="C1", caller_id="U1")
        self.assertNotIn("OPENTAG_SLACK_ACTION_TOKEN", unrelated)
        self.assertEqual("", text_only_environment(environment)["OPENTAG_SLACK_ACTION_TOKEN"])

    def test_memory_receipts_reach_only_the_requested_run(self) -> None:
        environment = backend_environment(
            {"OPENTAG_MEMORY_RECEIPTS": "/stale/receipts.jsonl"},
            transport="slack",
            conversation_id="C1",
            caller_id="U1",
            memory_receipts="/tmp/run/receipts.jsonl",
        )
        self.assertEqual("/tmp/run/receipts.jsonl", environment["OPENTAG_MEMORY_RECEIPTS"])
        self.assertEqual(("C1", "U1"), (environment["OPENTAG_CURRENT_CHANNEL_ID"], environment["OPENTAG_CALLER_ID"]))

        environment = backend_environment(
            {"OPENTAG_MEMORY_RECEIPTS": "/stale/receipts.jsonl"},
            transport="slack",
            conversation_id="C1",
            caller_id="U1",
        )
        self.assertNotIn("OPENTAG_MEMORY_RECEIPTS", environment)
