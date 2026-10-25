from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from scripts import watch_attachments as attachments

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
JPEG = b"\xff\xd8\xff" + b"0" * 20
VTT = """WEBVTT

1
00:00:00.000 --> 00:00:02.000
<v Maxine>We just shipped the new flow

2
00:00:02.000 --> 00:00:04.000
We just shipped the new flow

00:04.000 --> 00:06.000
and it's live for everyone.
"""


def event(*files: dict, previews: list | None = None) -> dict:
    return {"files": list(files), "attachments": previews or []}


class DescribeTests(unittest.TestCase):
    def test_descriptions_are_text_only_without_urls(self) -> None:
        described = attachments.describe(event(
            {"mimetype": "video/mp4", "title": "launch demo.mp4", "duration_ms": 61_400,
             "url_private": "https://files.slack.com/secret", "thumb_video": "https://files.slack.com/t"},
            {"mimetype": "image/png", "name": "screen.png"},
            previews=[{"service_name": "X", "title": "Post by Ann", "text": "We launched " * 50,
                       "title_link": "https://x.com/ann/status/1"},
                      {"files": [{"id": "F1"}], "text": "forwarded"}]))
        self.assertEqual([("video", 61), ("image", None), ("link preview", None)],
                         [(d["kind"], d.get("seconds")) for d in described])
        self.assertNotIn("https://", repr(described))
        self.assertLessEqual(len(described[2]["description"]), attachments.MAX_TEXT_CHARS)

    def test_captions_drop_timings_cues_tags_and_repeats(self) -> None:
        self.assertEqual("We just shipped the new flow and it's live for everyone.", attachments.captions_text(VTT))


class PrepareTests(unittest.TestCase):
    def prepare(self, *files: dict, download=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return attachments.prepare(event(*files), Path(directory.name), "xoxb", download or MagicMock())

    def test_images_and_a_videos_frame_and_captions_within_caps(self) -> None:
        def download(url, token, max_bytes, prefix):
            self.assertEqual("xoxb", token)
            return VTT.encode() if url.endswith(".vtt") else (JPEG if "frame" in url else PNG)

        described, images = self.prepare(
            {"mimetype": "image/png", "thumb_1024": "https://files.slack.com/img"},
            {"mimetype": "video/mp4", "thumb_video": "https://files.slack.com/frame",
             "vtt": "https://files.slack.com/captions.vtt"},
            download=download)
        self.assertEqual(["attachment-1-image.png", "attachment-2-frame.jpg"], [p.name for p in images])
        self.assertEqual(("image", "preview frame"), (described[0]["shown"], described[1]["shown"]))
        self.assertIn("live for everyone", described[1]["captions"])
        if os.name != "nt":
            self.assertEqual(0o600, images[0].stat().st_mode & 0o777)

    def test_caps_refusals_and_failures_leave_only_descriptions(self) -> None:
        download = MagicMock(side_effect=[PNG, b"<html>not an image", OSError("boom"), PNG, PNG])
        files = [{"mimetype": "image/png", "url_private": f"https://files.slack.com/{i}"} for i in range(6)]
        described, images = self.prepare(*files, download=download)
        self.assertEqual(3, len(images))  # At most three images, whatever fails along the way.
        self.assertEqual([None, True, True, None, None, None], [d.get("unavailable") for d in described])
        _described, images = self.prepare({"mimetype": "application/pdf", "url_private": "https://files.slack.com/p"})
        self.assertEqual((), images)
        described, _ = self.prepare({"mimetype": "video/mp4", "vtt": VTT})
        self.assertIn("shipped", described[0]["captions"])

    def test_the_bot_token_only_goes_to_slack_file_hosts(self) -> None:
        opener = MagicMock()
        for url in ("https://evil.test/x", "http://files.slack.com/x", "https://files.slack.com.evil.test/x"):
            with self.assertRaises(ValueError):
                attachments.slack_download(url, "xoxb", 10, "image/", opener=opener)
        opener.assert_not_called()
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers.get_content_type.return_value = "image/png"
        response.read.return_value = b"x" * 11
        with self.assertRaisesRegex(ValueError, "size cap"):
            attachments.slack_download("https://files.slack.com/x", "xoxb", 10, "image/",
                                       opener=MagicMock(return_value=response))
        response.read.return_value = PNG
        self.assertEqual(PNG, attachments.slack_download("https://files.slack.com/x", "xoxb", 100, "image/",
                                                         opener=MagicMock(return_value=response)))
        response.headers.get_content_type.return_value = "text/html"
        with self.assertRaises(ValueError):
            attachments.slack_download("https://files.slack.com/x", "xoxb", 100, "image/",
                                       opener=MagicMock(return_value=response))


if __name__ == "__main__":
    unittest.main()
