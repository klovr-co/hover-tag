from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import slack_thread_file


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        pass


def info(channel: str = "C123", url: str = "https://files.slack.com/files-pri/T/F1/a.png") -> FakeResponse:
    return FakeResponse(json.dumps({"ok": True, "file": {
        "id": "F1ABC", "name": "say hi.png", "mimetype": "image/png",
        "url_private_download": url, "shares": {"public": {channel: [{}]}},
    }}).encode())


ENV = {"SLACK_BOT_TOKEN": "xoxb-test", "OPENTAG_CURRENT_CHANNEL_ID": "C123"}


class SlackThreadFileTests(unittest.TestCase):
    def test_downloads_file_shared_in_current_channel(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(os.environ, ENV), patch(
            "scripts.slack_thread_file.urllib.request.urlopen",
            side_effect=[info(), FakeResponse(b"png")],
        ):
            entry = slack_thread_file.download("F1ABC", Path(raw))
            self.assertEqual(Path(entry["path"]).read_bytes(), b"png")
        self.assertTrue(entry["path"].endswith("say-hi-F1ABC.png"))

    def test_rejects_file_from_another_channel(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(os.environ, ENV), patch(
            "scripts.slack_thread_file.urllib.request.urlopen", side_effect=[info(channel="C999")],
        ), self.assertRaises(PermissionError):
            slack_thread_file.download("F1ABC", Path(raw))

    def test_never_sends_token_outside_slack_file_host(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(os.environ, ENV), patch(
            "scripts.slack_thread_file.urllib.request.urlopen",
            side_effect=[info(url="https://evil.example/a.png")],
        ) as urlopen, self.assertRaisesRegex(RuntimeError, "downloadable"):
            slack_thread_file.download("F1ABC", Path(raw))
        self.assertEqual(urlopen.call_count, 1)

    def test_enforces_per_file_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(os.environ, ENV), patch.object(
            slack_thread_file, "MAX_FILE_BYTES", 2
        ), patch(
            "scripts.slack_thread_file.urllib.request.urlopen",
            side_effect=[info(), FakeResponse(b"toolong")],
        ), self.assertRaisesRegex(RuntimeError, "15 MB"):
            slack_thread_file.download("F1ABC", Path(raw))

    def test_rejects_invalid_file_id(self) -> None:
        with tempfile.TemporaryDirectory() as raw, self.assertRaises(ValueError):
            slack_thread_file.download("../etc", Path(raw))


if __name__ == "__main__":
    unittest.main()
