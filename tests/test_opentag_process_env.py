from __future__ import annotations

import unittest

from scripts.opentag_process_env import backend_environment


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
