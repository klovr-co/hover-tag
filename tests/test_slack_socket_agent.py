from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import slack_socket_agent


class SlackChannelAllowlistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = os.environ.get("SLACK_CHANNEL_ID")

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("SLACK_CHANNEL_ID", None)
        else:
            os.environ["SLACK_CHANNEL_ID"] = self.previous

    def test_configured_channel_is_allowed(self) -> None:
        os.environ["SLACK_CHANNEL_ID"] = "C123"
        self.assertTrue(slack_socket_agent.slack_channel_allowed("C123"))
        self.assertFalse(slack_socket_agent.slack_channel_allowed("C999"))

    def test_empty_configuration_preserves_existing_behavior(self) -> None:
        os.environ["SLACK_CHANNEL_ID"] = ""
        self.assertTrue(slack_socket_agent.slack_channel_allowed("C999"))
