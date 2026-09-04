from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from scripts import slack_post_message


class FakeResponse:
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def read(self) -> bytes:
        return b'{"ok": true, "channel": "C123", "message": {"ts": "1.2"}}'


class SlackPostMessageTests(unittest.TestCase):
    @patch("scripts.slack_post_message.urllib.request.urlopen", return_value=FakeResponse())
    def test_posts_to_current_channel_only(self, mock_urlopen: object) -> None:
        with patch.dict(
            os.environ,
            {"SLACK_BOT_TOKEN": "xoxb-test", "OPENTAG_CURRENT_CHANNEL_ID": "C123"},
            clear=False,
        ):
            result = slack_post_message.post_to_current_channel(text="Hello team")

        self.assertEqual("C123", result["channel"])
        request = mock_urlopen.call_args.args[0]  # type: ignore[union-attr]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual({"channel": "C123", "text": "Hello team"}, payload)
        self.assertNotIn("thread_ts", payload)
