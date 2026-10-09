from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from dataclasses import replace
import os
import signal
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

try:
    import slack_bolt  # noqa: F401
except ModuleNotFoundError:
    raise unittest.SkipTest("slack_bolt is installed by the Slack bridge runtime")

from scripts import agent_models, slack_socket_agent
from scripts.tag_error_reporting import ErrorReportStore, ReportOrigin, make_error_report
from scripts.tag_activity import ActivityStore


def setUpModule() -> None:
    summary = patch.object(slack_socket_agent, "queue_reply_summary")
    summary.start()
    unittest.addModuleCleanup(summary.stop)


class FakeApp:
    def __init__(self) -> None:
        self.events: dict[str, object] = {}
        self.actions: dict[str, object] = {}
        self.views: dict[str, object] = {}

    def event(self, name: str):
        def register(handler: object) -> object:
            self.events[name] = handler
            return handler

        return register

    def action(self, name: str):
        def register(handler: object) -> object:
            self.actions[name] = handler
            return handler

        return register

    def view(self, name: str):
        def register(handler: object) -> object:
            self.views[name] = handler
            return handler

        return register


class SlackBotNameTests(unittest.TestCase):
    def test_tag_is_the_backend_independent_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(slack_socket_agent.suggested_bot_name("codex"), "Tag")
            self.assertEqual(slack_socket_agent.suggested_bot_name("claude"), "Tag")

    def test_operator_can_override_the_display_name(self) -> None:
        with patch.dict(os.environ, {"OPENTAG_BOT_NAME": "TeamBot"}, clear=True):
            self.assertEqual(slack_socket_agent.suggested_bot_name("codex"), "TeamBot")


class LiveSummaryTests(unittest.TestCase):
    def summary(self, env: dict[str, str]) -> str:
        from contextlib import redirect_stdout
        from io import StringIO

        out = StringIO()
        with patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test", **env}, clear=True), patch.object(
            slack_socket_agent.slack_channels, "channel_label", side_effect=lambda _t, c: f"#{c}"
        ), redirect_stdout(out):
            slack_socket_agent.print_live_summary("claude", frozenset({"U1"}))
        return out.getvalue()

    def test_configured_channel_is_listening(self) -> None:
        self.assertIn("listening for @mentions in channel #C123", self.summary({"SLACK_CHANNEL_IDS": "C123"}))

    def test_no_channel_reports_mentions_disabled(self) -> None:
        output = self.summary({})
        self.assertIn("channel mentions disabled (no channels configured)", output)
        self.assertNotIn("listening for @mentions", output)

    def test_invited_policy_without_channels(self) -> None:
        output = self.summary({"SLACK_CHANNEL_POLICY": "invited"})
        self.assertIn("listening in channels Tag is invited to", output)


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def read(self, _size: int = -1) -> bytes:
        body, self.body = self.body, b""
        return body


class StoredAttachmentNameTests(unittest.TestCase):
    def test_long_names_fit_on_disk_and_preserve_identity_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for extension in (".png", ".pdf", ""):
                for length in (242, 243, 244, 255):
                    with self.subTest(extension=extension, length=length):
                        file_id = "F1234567890"
                        original_stem = "a" * (length - len(extension))
                        name = slack_socket_agent.stored_attachment_name(
                            {"name": original_stem + extension, "id": file_id}, 1
                        )
                        self.assertLessEqual(len(name.encode()), 255)
                        self.assertTrue(name.endswith(f"-{file_id}{extension}"))
                        self.assertEqual(len(name), min(length + 12, 255))
                        path = Path(tmp) / name
                        path.write_bytes(b"attachment")
                        self.assertEqual(path.read_bytes(), b"attachment")


class SlackTextAttachmentTests(unittest.TestCase):
    @patch(
        "scripts.slack_socket_agent.urllib.request.urlopen",
        return_value=FakeResponse(b"opening line\nclosing line"),
    )
    def test_includes_slack_text_snippet_in_thread_context(self, mock_urlopen: object) -> None:
        messages = [
            {
                "files": [
                    {
                        "id": "F123",
                        "name": "lecture-transcript.txt",
                        "mimetype": "text/plain",
                        "url_private_download": "https://files.slack.com/F123",
                    }
                ]
            }
        ]
        with patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test"}, clear=False):
            text = slack_socket_agent.download_thread_text_files(messages)

        self.assertEqual(["[Slack text attachment: lecture-transcript.txt]\nopening line\nclosing line"], text)
        request = mock_urlopen.call_args.args[0]  # type: ignore[union-attr]
        self.assertEqual("Bearer xoxb-test", request.get_header("Authorization"))

    def test_skips_binary_attachments(self) -> None:
        messages = [{"files": [{"id": "F123", "name": "slides.pdf", "mimetype": "application/pdf"}]}]
        with patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test"}, clear=False):
            self.assertEqual([], slack_socket_agent.download_thread_text_files(messages))

    def test_truncates_large_text_attachment(self) -> None:
        with patch(
            "scripts.slack_socket_agent.download_file_bytes",
            return_value=b"a" * (slack_socket_agent.MAX_ATTACHMENT_TEXT_CHARS + 1),
        ), patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test"}, clear=False):
            text = slack_socket_agent.download_thread_text_files(
                [{"files": [{"id": "F123", "name": "transcript.txt", "mimetype": "text/plain", "url_private": "https://example.test/F123"}]}]
            )

        self.assertTrue(text[0].endswith("[Attachment text truncated]"))


class ThreadReservationTests(unittest.TestCase):
    def test_a_thread_runs_one_request_until_it_is_released(self) -> None:
        key = slack_socket_agent.RunKey("T1", "C1", "1.0")
        self.addCleanup(slack_socket_agent.release_thread, key)

        self.assertTrue(slack_socket_agent.reserve_thread(key))
        self.assertFalse(slack_socket_agent.reserve_thread(key))
        self.assertTrue(slack_socket_agent.reserve_thread(slack_socket_agent.RunKey("T1", "C1", "2.0")))
        slack_socket_agent.release_thread(key)
        self.assertTrue(slack_socket_agent.reserve_thread(key))
        slack_socket_agent.release_thread(slack_socket_agent.RunKey("T1", "C1", "2.0"))


class SlackBinaryAttachmentTests(unittest.TestCase):
    def test_downloads_binary_attachment_to_invocation_directory(self) -> None:
        messages = [{"files": [{
            "id": "FZIP",
            "name": "tag-feature-catalog.zip",
            "mimetype": "application/zip",
            "url_private_download": "https://files.slack.com/FZIP",
        }]}]
        client = MagicMock()
        client.conversations_replies.return_value = {"messages": messages}
        with tempfile.TemporaryDirectory() as raw_dir, patch(
            "scripts.slack_socket_agent.download_file_bytes",
            return_value=b"PK\x03\x04archive",
        ), patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test"}, clear=False):
            thread_text = slack_socket_agent.build_thread_text(
                client, "C123", "1.23", Path(raw_dir)
            )
            downloaded = Path(raw_dir) / "tag-feature-catalog-FZIP.zip"

            self.assertEqual(b"PK\x03\x04archive", downloaded.read_bytes())
            self.assertIn(
                f"[Slack file attachment: tag-feature-catalog-FZIP.zip (application/zip) at {downloaded}]",
                thread_text,
            )

    def test_downloads_files_nested_in_a_forwarded_message(self) -> None:
        messages = [{
            "user": "UOWNER",
            "text": "<@BOT> use the attachments in this context",
            "attachments": [{
                "is_msg_unfurl": True,
                "author_name": "Xian Jun",
                "text": "Here are the documents you need for the project.",
                "files": [
                    {
                        "id": "FPDF",
                        "name": "partnership-agreement.pdf",
                        "mimetype": "application/pdf",
                        "url_private_download": "https://files.slack.com/FPDF",
                    },
                    {
                        "id": "FXLSX",
                        "name": "developer-qa-report.xlsx",
                        "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        "url_private_download": "https://files.slack.com/FXLSX",
                    },
                ],
            }],
        }]
        client = MagicMock()
        client.conversations_replies.return_value = {"messages": messages}
        with tempfile.TemporaryDirectory() as raw_dir, patch(
            "scripts.slack_socket_agent.download_file_bytes",
            side_effect=[b"%PDF-agreement", b"PK\x03\x04spreadsheet"],
        ) as download, patch.dict(
            os.environ, {"SLACK_BOT_TOKEN": "xoxb-test"}, clear=False
        ):
            thread_text = slack_socket_agent.build_thread_text(
                client, "C123", "1.23", Path(raw_dir)
            )
            attachment_dir = Path(raw_dir)

            self.assertEqual(
                b"%PDF-agreement",
                (attachment_dir / "partnership-agreement-FPDF.pdf").read_bytes(),
            )
            self.assertEqual(
                b"PK\x03\x04spreadsheet",
                (attachment_dir / "developer-qa-report-FXLSX.xlsx").read_bytes(),
            )
            self.assertIn("Body: Here are the documents you need for the project.", thread_text)
            self.assertIn("[Slack file attachment: partnership-agreement-FPDF.pdf", thread_text)
            self.assertIn("[Slack file attachment: developer-qa-report-FXLSX.xlsx", thread_text)
            self.assertEqual(2, download.call_count)

    def test_same_named_binary_files_use_distinct_stored_names(self) -> None:
        messages = [{"files": [
            {
                "id": "FONE",
                "name": "report.pdf",
                "mimetype": "application/pdf",
                "url_private": "https://files.slack.com/FONE",
            },
            {
                "id": "FTWO",
                "name": "report.pdf",
                "mimetype": "application/pdf",
                "url_private": "https://files.slack.com/FTWO",
            },
        ]}]
        with tempfile.TemporaryDirectory() as raw_dir, patch(
            "scripts.slack_socket_agent.download_file_bytes",
            side_effect=[b"first", b"second"],
        ), patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test"}, clear=False):
            directory = Path(raw_dir)
            lines = slack_socket_agent.download_thread_binary_files(
                messages, directory
            )

            self.assertEqual((directory / "report-FONE.pdf").read_bytes(), b"first")
            self.assertEqual((directory / "report-FTWO.pdf").read_bytes(), b"second")
            self.assertIn("report-FONE.pdf", lines[0])
            self.assertIn("report-FTWO.pdf", lines[1])

    def test_same_named_images_use_distinct_stored_names(self) -> None:
        messages = [{"files": [
            {
                "id": "FONE",
                "name": "diagram.png",
                "mimetype": "image/png",
                "url_private": "https://files.slack.com/FONE",
            },
            {
                "id": "FTWO",
                "name": "diagram.png",
                "mimetype": "image/png",
                "url_private": "https://files.slack.com/FTWO",
            },
        ]}]
        with tempfile.TemporaryDirectory() as raw_dir, patch(
            "scripts.slack_socket_agent.download_file_bytes",
            side_effect=[b"first", b"second"],
        ), patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test"}, clear=False):
            directory = Path(raw_dir)
            lines = slack_socket_agent.download_thread_images(messages, directory)

            self.assertEqual((directory / "diagram-FONE.png").read_bytes(), b"first")
            self.assertEqual((directory / "diagram-FTWO.png").read_bytes(), b"second")
            self.assertIn("diagram-FONE.png", lines[0])
            self.assertIn("diagram-FTWO.png", lines[1])

    def test_streamed_bytes_over_limit_are_rejected_instead_of_becoming_prompt_text(self) -> None:
        messages = [{"files": [{
            "id": "FZIP",
            "name": "example.zip",
            "mimetype": "application/zip",
            "url_private_download": "https://files.slack.com/FZIP",
        }]}]
        client = MagicMock()
        client.conversations_replies.return_value = {"messages": messages}
        with tempfile.TemporaryDirectory() as raw_dir, patch.object(
            slack_socket_agent, "MAX_ATTACHMENT_BYTES", 4
        ), patch(
            "scripts.slack_socket_agent.urllib.request.urlopen",
            return_value=FakeResponse(b"12345"),
        ), patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test"}, clear=False):
            with self.assertRaisesRegex(
                slack_socket_agent.AttachmentLimitError,
                "example-FZIP.zip.*15 MB attachment limit",
            ):
                slack_socket_agent.build_thread_text(
                    client, "C123", "1.23", Path(raw_dir)
                )


class RequestAttachmentSelectionTests(unittest.TestCase):
    def file(self, index, name=None):
        return {"id": f"F{index}", "name": name or f"image-{index}.png",
                "mimetype": "image/png", "url_private": f"https://files.slack.com/F{index}"}

    def test_current_upload_ignores_twenty_older_files(self):
        messages = [{"ts": "1", "files": [self.file(i) for i in range(20)]}]
        request = {"ts": "2", "files": [self.file(21)], "text": "edit this"}
        self.assertEqual(slack_socket_agent.select_request_files(messages, request), request["files"])

    def test_followup_uses_latest_generated_group_and_deduplicates(self):
        latest = self.file(2)
        messages = [{"files": [self.file(1)]}, {"bot_id": "B1", "files": [latest, latest]}]
        self.assertEqual(slack_socket_agent.select_request_files(messages, {"text": "try again"}), [latest])

    def test_explicit_older_reference_and_current_upload(self):
        old, new = self.file(1), self.file(2)
        for reference in (old["name"], "https://workspace.slack.com/files/U/F1/image-1.png"):
            with self.subTest(reference=reference):
                selected = slack_socket_agent.select_request_files(
                    [{"files": [old]}], {"text": f"compare with {reference}", "files": [new]})
                self.assertEqual({f["id"] for f in selected}, {"F1", "F2"})

    def test_duplicate_filename_requires_clarification_unless_link_disambiguates(self):
        messages = [{"files": [self.file(1, "image.png"), self.file(2, "image.png")]}]
        with self.assertRaisesRegex(slack_socket_agent.AttachmentLimitError, "More than one"):
            slack_socket_agent.select_request_files(messages, {"text": "edit image.png"})
        selected = slack_socket_agent.select_request_files(messages, {"text": "edit https://slack.com/files/U/F1/image.png"})
        self.assertEqual([f["id"] for f in selected], ["F1"])

    def test_explicit_all_retains_limit(self):
        files = [self.file(i) for i in range(11)]
        selected = slack_socket_agent.select_request_files(
            [{"files": files}], {"text": "use all files in this thread"})
        with self.assertRaisesRegex(slack_socket_agent.AttachmentLimitError, "at most 10"):
            slack_socket_agent.validate_attachment_metadata(selected)

    def test_continued_thread_text_holds_only_others_messages_after_the_last_request(self):
        client = MagicMock()
        client.conversations_replies.return_value = {"messages": [
            {"ts": "1", "user": "U1", "text": "first request"},
            {"ts": "2", "user": "UBOT", "bot_id": "B1", "text": "Tag's earlier answer"},
            {"ts": "3", "user": "U2", "text": "a teammate adds context"},
        ]}
        request = {"ts": "4", "user": "U1", "text": "follow-up"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SLACK_BOT_TOKEN": "test"}):
            full, new = slack_socket_agent.build_thread_texts(
                client, "C1", "1", Path(directory), request=request, since_ts="1", own_user="UBOT")
        self.assertIn("first request", full)
        self.assertIn("Tag's earlier answer", full)
        self.assertNotIn("first request", new)
        self.assertNotIn("Tag's earlier answer", new)
        self.assertIn("U2: a teammate adds context", new)
        self.assertIn("U1: follow-up", new)

    def test_paginated_thread_downloads_only_current_upload_and_excludes_future(self):
        client = MagicMock()
        client.conversations_replies.side_effect = [
            {"messages": [{"ts": "1", "files": [self.file(i) for i in range(20)]}],
             "response_metadata": {"next_cursor": "page2"}},
            {"messages": [{"ts": "3", "text": "future", "files": [self.file(30)]}]},
        ]
        request = {"ts": "2", "text": "edit this", "files": [self.file(21)]}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SLACK_BOT_TOKEN": "test"}), patch.object(
            slack_socket_agent, "download_file_bytes", return_value=b"image"
        ) as download:
            text = slack_socket_agent.build_thread_text(client, "C1", "1", Path(directory), request=request)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(download.call_args.args[0], "https://files.slack.com/F21")
        self.assertNotIn("future", text)
        self.assertIn("Historical attachment, not downloaded", text)
        self.assertEqual(client.conversations_replies.call_args.kwargs["cursor"], "page2")
        self.assertEqual(client.conversations_replies.call_args.kwargs["latest"], "2")

    def test_event_without_files_preserves_api_metadata(self):
        client = MagicMock()
        client.conversations_replies.return_value = {"messages": [
            {"ts": "1", "files": [self.file(1)]},
            {"ts": "2", "files": [self.file(2)]},
        ]}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SLACK_BOT_TOKEN": "test"}), patch.object(
            slack_socket_agent, "download_file_bytes", return_value=b"image"
        ) as download:
            slack_socket_agent.build_thread_text(client, "C1", "1", Path(directory), request={"ts": "2", "text": "edit this"})
        self.assertEqual(download.call_count, 1)
        self.assertEqual(download.call_args.args[0], "https://files.slack.com/F2")

    def test_oversized_earlier_file_is_noted_instead_of_blocking_followups(self):
        video = {"id": "FVID", "name": "launch.MP4", "mimetype": "video/mp4",
                 "size": slack_socket_agent.MAX_ATTACHMENT_BYTES + 1,
                 "url_private": "https://files.slack.com/FVID"}
        for text in ("<@BOT> this is a local path", "<@BOT> how about /Users/me/Downloads/launch.mp4"):
            with self.subTest(text=text):
                client = MagicMock()
                client.conversations_replies.return_value = {"messages": [
                    {"ts": "1", "user": "U1", "text": "launch video", "files": [video]},
                ]}
                with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SLACK_BOT_TOKEN": "test"}), patch.object(
                    slack_socket_agent, "download_file_bytes"
                ) as download:
                    thread = slack_socket_agent.build_thread_text(
                        client, "C1", "1", Path(directory), request={"ts": "2", "user": "U1", "text": text})
                download.assert_not_called()
                self.assertIn("launch.MP4", thread)
                self.assertIn("15 MB attachment limit", thread)
                self.assertIn(text, thread)

    def test_oversized_earlier_file_does_not_make_same_name_ambiguous(self):
        big = {**self.file(1, "launch.mp4"), "size": slack_socket_agent.MAX_ATTACHMENT_BYTES + 1}
        small = {**self.file(2, "launch.mp4"), "size": 1024}
        client = MagicMock()
        client.conversations_replies.return_value = {"messages": [
            {"ts": "1", "user": "U1", "files": [big]},
            {"ts": "2", "user": "U1", "files": [small]},
        ]}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SLACK_BOT_TOKEN": "test"}), patch.object(
            slack_socket_agent, "download_file_bytes", return_value=b"x"
        ) as download:
            thread = slack_socket_agent.build_thread_text(
                client, "C1", "1", Path(directory),
                request={"ts": "3", "user": "U1", "text": "<@BOT> summarize launch.mp4"})
        self.assertEqual(download.call_count, 1)
        self.assertEqual(download.call_args.args[0], "https://files.slack.com/F2")
        self.assertIn("15 MB attachment limit", thread)

    def test_oversized_current_upload_still_rejects_the_request(self):
        client = MagicMock()
        client.conversations_replies.return_value = {"messages": [{"ts": "1", "text": "start"}]}
        request = {"ts": "2", "text": "look", "files": [{
            "id": "FVID", "name": "launch.mp4", "mimetype": "video/mp4",
            "size": slack_socket_agent.MAX_ATTACHMENT_BYTES + 1}]}
        with patch.dict(os.environ, {"SLACK_BOT_TOKEN": "test"}), self.assertRaisesRegex(
            slack_socket_agent.AttachmentLimitError, "launch.mp4.*15 MB attachment limit"
        ):
            slack_socket_agent.build_thread_text(client, "C1", "1", Path("unused"), request=request)

    def test_image_with_text_filetype_is_downloaded_once(self):
        file = {**self.file(1), "filetype": "text"}
        client = MagicMock()
        client.conversations_replies.return_value = {"messages": [{"files": [file]}]}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SLACK_BOT_TOKEN": "test"}), patch.object(
            slack_socket_agent, "download_file_bytes", return_value=b"image"
        ) as download:
            slack_socket_agent.build_thread_text(client, "C1", "1", Path(directory))
        self.assertEqual(download.call_count, 1)

    def test_incomplete_pagination_does_not_guess(self):
        client = MagicMock()
        client.conversations_replies.return_value = {"messages": [], "has_more": True}
        with self.assertRaisesRegex(slack_socket_agent.AttachmentLimitError, "complete thread"):
            slack_socket_agent.build_thread_text(client, "C1", "1", Path("unused"), request={"ts": "2"})


class SlackAttachmentLimitTests(unittest.TestCase):
    def test_rejects_too_many_or_too_large_a_combined_attachment_set(self) -> None:
        too_many = [
            {"id": f"F{index}", "name": f"file-{index}.txt", "size": 1}
            for index in range(slack_socket_agent.MAX_ATTACHMENTS_PER_REQUEST + 1)
        ]
        with self.assertRaisesRegex(
            slack_socket_agent.AttachmentLimitError, "at most 10 files"
        ):
            slack_socket_agent.validate_attachment_metadata(too_many)

        each_size = slack_socket_agent.MAX_TOTAL_ATTACHMENT_BYTES // 3 + 1
        combined_too_large = [
            {"id": f"F{index}", "name": f"file-{index}.zip", "size": each_size}
            for index in range(3)
        ]
        with self.assertRaisesRegex(
            slack_socket_agent.AttachmentLimitError, "30 MB per-request limit"
        ):
            slack_socket_agent.validate_attachment_metadata(combined_too_large)

    def test_declared_oversized_file_is_rejected_before_work_starts(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        event = {
            "channel": "C123",
            "ts": "1.23",
            "user": "UOWNER",
            "text": "<@BOT> inspect this",
            "files": [{
                "id": "FZIP",
                "name": "example.zip",
                "mimetype": "application/zip",
                "size": slack_socket_agent.MAX_ATTACHMENT_BYTES + 1,
            }],
        }
        with patch.object(
            slack_socket_agent, "App", return_value=fake_app
        ), patch.dict(
            os.environ,
            {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C123"},
            clear=True,
        ), patch.object(
            slack_socket_agent, "WorkingIndicator"
        ) as working_indicator, patch.object(
            slack_socket_agent, "run_backend"
        ) as run_backend:
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            fake_app.events["app_mention"](
                event,
                {"team_id": "T123"},
                client,
                MagicMock(),
            )

        client.chat_postMessage.assert_called_once_with(
            channel="C123",
            thread_ts="1.23",
            text=(
                "I couldn’t process example.zip because it exceeds Tag’s 15 MB "
                "attachment limit. Upload a smaller file or provide a local path/link."
            ),
        )
        working_indicator.assert_not_called()
        run_backend.assert_not_called()

    def test_download_discovered_oversize_gets_private_reply_without_retry(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        indicator = MagicMock()
        indicator.message_ts = None
        limit_error = slack_socket_agent.AttachmentLimitError.for_file("example.zip")
        with patch.object(
            slack_socket_agent, "App", return_value=fake_app
        ), patch.dict(
            os.environ,
            {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C123"},
            clear=True,
        ), patch.object(
            slack_socket_agent, "WorkingIndicator", return_value=indicator
        ), patch.object(
            slack_socket_agent, "build_thread_text", side_effect=limit_error
        ), patch.object(
            slack_socket_agent, "run_backend"
        ) as run_backend:
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            fake_app.events["app_mention"](
                {
                    "channel": "C123",
                    "ts": "1.23",
                    "user": "UOWNER",
                    "text": "<@BOT> inspect this",
                    "files": [{"id": "FZIP", "name": "example.zip"}],
                },
                {"team_id": "T123"},
                client,
                MagicMock(),
            )

        indicator.clear.assert_called_once()
        run_backend.assert_not_called()
        client.chat_postMessage.assert_not_called()
        posted = client.chat_postEphemeral.call_args.kwargs
        self.assertEqual("UOWNER", posted["user"])
        self.assertEqual(str(limit_error), posted["text"])
        self.assertEqual("section", posted["blocks"][0]["type"])


class SlackOutputArtifactTests(unittest.TestCase):
    def test_local_artifact_actions_allow_enabled_direct_messages(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        logger = MagicMock()
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir).resolve()
            artifact = root / "report.md"
            artifact.write_text("# Report\n", encoding="utf-8")
            with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
                os.environ,
                {"SLACK_BOT_TOKEN": "xoxb-test", "OPENTAG_SLACK_DM_ENABLED": "1"},
                clear=True,
            ), patch.object(
                slack_socket_agent, "default_workdir", return_value=root
            ), patch.object(
                slack_socket_agent, "open_local_artifact"
            ) as local_open, patch.object(
                slack_socket_agent, "open_local_artifact_directory"
            ) as directory_open:
                slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
                metadata = {
                    "user": "UOWNER",
                    "channel": "D123",
                    "thread_ts": "1.23",
                    "path": "report.md",
                }
                body = {
                    "user": {"id": "UOWNER"},
                    "channel": {"id": "D123"},
                    "actions": [{"value": json.dumps(metadata)}],
                }

                file_handler = fake_app.actions[
                    slack_socket_agent.OPEN_LOCAL_ARTIFACT_ACTION_ID
                ]
                file_handler(MagicMock(), body, client, logger)
                metadata["path"] = "."
                body["actions"][0]["value"] = json.dumps(metadata)
                directory_handler = fake_app.actions[
                    slack_socket_agent.OPEN_LOCAL_ARTIFACT_DIRECTORY_ACTION_ID
                ]
                directory_handler(MagicMock(), body, client, logger)

            local_open.assert_called_once_with(artifact)
            directory_open.assert_called_once_with(root)
            self.assertEqual(2, client.chat_postEphemeral.call_count)

    def test_local_artifact_actions_report_failures_in_enabled_direct_messages(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir).resolve()
            with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
                os.environ,
                {"SLACK_BOT_TOKEN": "xoxb-test", "OPENTAG_SLACK_DM_ENABLED": "1"},
                clear=True,
            ), patch.object(slack_socket_agent, "default_workdir", return_value=root):
                slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
                metadata = {
                    "user": "UOWNER",
                    "channel": "D123",
                    "thread_ts": "1.23",
                    "path": "missing",
                }
                body = {
                    "user": {"id": "UOWNER"},
                    "channel": {"id": "D123"},
                    "actions": [{"value": json.dumps(metadata)}],
                }

                fake_app.actions[slack_socket_agent.OPEN_LOCAL_ARTIFACT_ACTION_ID](
                    MagicMock(), body, client, MagicMock()
                )
                fake_app.actions[
                    slack_socket_agent.OPEN_LOCAL_ARTIFACT_DIRECTORY_ACTION_ID
                ](MagicMock(), body, client, MagicMock())

            self.assertEqual(2, client.chat_postEphemeral.call_count)
            messages = [
                call.kwargs["text"] for call in client.chat_postEphemeral.call_args_list
            ]
            self.assertTrue(any("local file" in message for message in messages))
            self.assertTrue(any("output folder" in message for message in messages))

    def test_local_artifact_actions_reject_disabled_direct_messages(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir).resolve()
            artifact = root / "report.md"
            artifact.write_text("# Report\n", encoding="utf-8")
            with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
                os.environ,
                {"SLACK_BOT_TOKEN": "xoxb-test", "OPENTAG_SLACK_DM_ENABLED": "0"},
                clear=True,
            ), patch.object(
                slack_socket_agent, "default_workdir", return_value=root
            ), patch.object(
                slack_socket_agent, "open_local_artifact"
            ) as local_open, patch.object(
                slack_socket_agent, "open_local_artifact_directory"
            ) as directory_open:
                slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
                metadata = {
                    "user": "UOWNER",
                    "channel": "D123",
                    "thread_ts": "1.23",
                    "path": "report.md",
                }
                body = {
                    "user": {"id": "UOWNER"},
                    "channel": {"id": "D123"},
                    "actions": [{"value": json.dumps(metadata)}],
                }

                fake_app.actions[slack_socket_agent.OPEN_LOCAL_ARTIFACT_ACTION_ID](
                    MagicMock(), body, client, MagicMock()
                )
                metadata["path"] = "."
                body["actions"][0]["value"] = json.dumps(metadata)
                fake_app.actions[
                    slack_socket_agent.OPEN_LOCAL_ARTIFACT_DIRECTORY_ACTION_ID
                ](MagicMock(), body, client, MagicMock())

            local_open.assert_not_called()
            directory_open.assert_not_called()
            client.chat_postEphemeral.assert_not_called()

    def test_builds_one_compact_local_open_row_for_all_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir).resolve()
            first = root / "launch-checklist.md"
            second = root / "owners.csv"
            first.write_text("# Checklist\n", encoding="utf-8")
            second.write_text("owner\nAda\n", encoding="utf-8")

            blocks = slack_socket_agent.output_artifact_button_blocks(
                [first, second],
                root,
                user_id="UOWNER",
                channel="C123",
                thread_ts="1.23",
            )

        self.assertEqual(1, len(blocks))
        buttons = blocks[0]["elements"]
        file_buttons = buttons[:-1]
        directory_button = buttons[-1]
        self.assertEqual(
            ["↗ launch-checklist.md", "↗ owners.csv"],
            [button["text"]["text"] for button in file_buttons],
        )
        action_ids = [button["action_id"] for button in buttons]
        self.assertEqual(len(action_ids), len(set(action_ids)))
        self.assertTrue(
            all(
                slack_socket_agent.OPEN_LOCAL_ARTIFACT_ACTION_PATTERN.fullmatch(action_id)
                for action_id in action_ids[:-1]
            )
        )
        self.assertEqual(
            slack_socket_agent.OPEN_LOCAL_ARTIFACT_DIRECTORY_ACTION_ID,
            directory_button["action_id"],
        )
        self.assertEqual("📁 Open folder", directory_button["text"]["text"])
        self.assertEqual(".", json.loads(directory_button["value"])["path"])
        self.assertEqual(
            ["launch-checklist.md", "owners.csv"],
            [json.loads(button["value"])["path"] for button in file_buttons],
        )
        self.assertEqual(
            [
                "Open launch-checklist.md on the Tag host",
                "Open owners.csv on the Tag host",
            ],
            [button["accessibility_label"] for button in file_buttons],
        )

    def test_local_open_directory_action_validates_and_opens_common_parent(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        logger = MagicMock()
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir).resolve()
            outputs = root / "exports"
            outputs.mkdir()
            with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
                os.environ,
                {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C123"},
                clear=True,
            ), patch.object(
                slack_socket_agent, "default_workdir", return_value=root
            ), patch.object(
                slack_socket_agent, "open_local_artifact_directory"
            ) as local_open:
                slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
                handler = fake_app.actions[
                    slack_socket_agent.OPEN_LOCAL_ARTIFACT_DIRECTORY_ACTION_ID
                ]
                ack = MagicMock()
                handler(
                    ack,
                    {
                        "user": {"id": "UOWNER"},
                        "channel": {"id": "C123"},
                        "actions": [
                            {
                                "value": json.dumps(
                                    {
                                        "user": "UOWNER",
                                        "channel": "C123",
                                        "thread_ts": "1.23",
                                        "path": "exports",
                                    }
                                )
                            }
                        ],
                    },
                    client,
                    logger,
                )

            ack.assert_called_once_with()
            local_open.assert_called_once_with(outputs)
            self.assertIn(
                "Opened the output folder",
                client.chat_postEphemeral.call_args.kwargs["text"],
            )

    def test_local_open_action_validates_user_and_workspace_path(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        logger = MagicMock()
        with tempfile.TemporaryDirectory() as raw_dir, tempfile.TemporaryDirectory() as outside_dir:
            root = Path(raw_dir).resolve()
            artifact = root / "report.md"
            artifact.write_text("# Report\n", encoding="utf-8")
            outside = Path(outside_dir) / "outside.md"
            outside.write_text("outside\n", encoding="utf-8")
            with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
                os.environ,
                {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C123"},
                clear=True,
            ), patch.object(
                slack_socket_agent, "default_workdir", return_value=root
            ), patch.object(slack_socket_agent, "open_local_artifact") as local_open:
                slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER", "UOTHER"}))
                handler = fake_app.actions[slack_socket_agent.OPEN_LOCAL_ARTIFACT_ACTION_ID]
                metadata = {
                    "user": "UOWNER",
                    "channel": "C123",
                    "thread_ts": "1.23",
                    "path": "report.md",
                }
                ack = MagicMock()
                handler(
                    ack,
                    {
                        "user": {"id": "UOWNER"},
                        "channel": {"id": "C123"},
                        "actions": [{"value": json.dumps(metadata)}],
                    },
                    client,
                    logger,
                )
                local_open.assert_called_once_with(artifact)
                ack.assert_called_once_with()
                self.assertIn(
                    "Opened `report.md`",
                    client.chat_postEphemeral.call_args.kwargs["text"],
                )

                local_open.reset_mock()
                client.chat_postEphemeral.reset_mock()
                handler(
                    MagicMock(),
                    {
                        "user": {"id": "UOTHER"},
                        "channel": {"id": "C123"},
                        "actions": [{"value": json.dumps(metadata)}],
                    },
                    client,
                    logger,
                )
                local_open.assert_not_called()
                client.chat_postEphemeral.assert_not_called()

                metadata["path"] = str(outside)
                handler(
                    MagicMock(),
                    {
                        "user": {"id": "UOWNER"},
                        "channel": {"id": "C123"},
                        "actions": [{"value": json.dumps(metadata)}],
                    },
                    client,
                    logger,
                )
                local_open.assert_not_called()
                self.assertIn(
                    "couldn’t open that local file",
                    client.chat_postEphemeral.call_args.kwargs["text"],
                )

    def test_uploads_requested_binary_file_to_originating_thread_unchanged(self) -> None:
        client = MagicMock()
        logger = MagicMock()
        upload_started = MagicMock()
        observed = b""
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            artifact = root / "release.zip"
            expected = b"PK\x03\x04\x00binary payload"
            artifact.write_bytes(expected)
            manifest = root / ".manifest.json"
            manifest.write_text(json.dumps([str(artifact)]), encoding="utf-8")

            def capture_upload(**kwargs: object) -> dict[str, object]:
                nonlocal observed
                observed = Path(str(kwargs["file"])).read_bytes()
                return {
                    "files": [
                        {
                            "id": "F123",
                            "permalink": "https://workspace.slack.com/files/F123/release.zip",
                        }
                    ]
                }

            client.files_upload_v2.side_effect = capture_upload
            messages = slack_socket_agent.deliver_output_artifacts(
                client,
                "C123",
                "1.23",
                manifest,
                root,
                logger,
                on_upload_start=upload_started,
            )

        self.assertEqual(expected, observed)
        upload_started.assert_called_once_with()
        client.files_upload_v2.assert_called_once_with(
            channel="C123",
            thread_ts="1.23",
            file=str(artifact.resolve()),
            filename="release.zip",
            title="release.zip",
        )
        self.assertEqual(
            [
                "Download [release.zip](https://workspace.slack.com/files/F123/release.zip)."
            ],
            messages,
        )

    def test_local_only_outputs_get_buttons_without_slack_attachments(self) -> None:
        client = MagicMock()
        logger = MagicMock()
        upload_started = MagicMock()
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            first = root / "launch-checklist.md"
            second = root / "owners.csv"
            first.write_text("# Checklist\n", encoding="utf-8")
            second.write_text("owner\nAda\n", encoding="utf-8")
            manifest = root / ".manifest.json"
            manifest.write_text(
                json.dumps(
                    [
                        {"path": str(first), "attach": False},
                        {"path": str(second), "attach": False},
                    ]
                ),
                encoding="utf-8",
            )

            paths, errors = slack_socket_agent.load_output_artifacts(manifest, root)
            messages = slack_socket_agent.deliver_output_artifacts(
                client,
                "C123",
                "1.23",
                manifest,
                root,
                logger,
                on_upload_start=upload_started,
            )

        self.assertEqual([first.resolve(), second.resolve()], paths)
        self.assertEqual([], errors)
        self.assertEqual([], messages)
        upload_started.assert_not_called()
        client.files_upload_v2.assert_not_called()

    def test_explicit_multi_file_delivery_uploads_every_output(self) -> None:
        client = MagicMock()
        client.files_upload_v2.side_effect = [
            {"file": {"permalink": "https://example.test/checklist"}},
            {"file": {"permalink": "https://example.test/owners"}},
        ]
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            first = root / "launch-checklist.md"
            second = root / "owners.csv"
            first.write_text("# Checklist\n", encoding="utf-8")
            second.write_text("owner\nAda\n", encoding="utf-8")
            manifest = root / ".manifest.json"
            manifest.write_text(
                json.dumps(
                    [
                        {"path": str(first), "attach": True},
                        {"path": str(second), "attach": True},
                    ]
                ),
                encoding="utf-8",
            )

            messages = slack_socket_agent.deliver_output_artifacts(
                client, "C123", "1.23", manifest, root, MagicMock()
            )

        self.assertEqual(2, client.files_upload_v2.call_count)
        self.assertEqual(
            ["launch-checklist.md", "owners.csv"],
            [call.kwargs["filename"] for call in client.files_upload_v2.call_args_list],
        )
        self.assertEqual(2, len(messages))

    def test_rejects_missing_and_out_of_workspace_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir, tempfile.TemporaryDirectory() as outside_dir:
            root = Path(raw_dir)
            outside = Path(outside_dir) / "secret.txt"
            outside.write_text("secret", encoding="utf-8")
            missing = root / "missing.csv"
            manifest = root / ".manifest.json"
            manifest.write_text(json.dumps([str(missing), str(outside)]), encoding="utf-8")

            artifacts, messages = slack_socket_agent.load_output_artifacts(manifest, root)

        self.assertEqual([], artifacts)
        self.assertEqual(2, len(messages))
        self.assertIn("missing.csv", messages[0])
        self.assertIn("secret.txt", messages[1])

    def test_fetches_permalink_when_upload_returns_only_file_id(self) -> None:
        client = MagicMock()
        client.files_upload_v2.return_value = {"files": [{"id": "F123"}]}
        client.files_info.return_value = {
            "file": {
                "id": "F123",
                "permalink": "https://workspace.slack.com/files/F123/report.md",
            }
        }
        logger = MagicMock()
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            artifact = root / "report.md"
            artifact.write_text("# Report\n", encoding="utf-8")
            manifest = root / ".manifest.json"
            manifest.write_text(json.dumps([str(artifact)]), encoding="utf-8")

            messages = slack_socket_agent.deliver_output_artifacts(
                client, "C123", "1.23", manifest, root, logger
            )

        client.files_info.assert_called_once_with(file="F123")
        self.assertEqual(
            ["Download [report.md](https://workspace.slack.com/files/F123/report.md)."],
            messages,
        )

    def test_keeps_successful_attachment_when_permalink_lookup_fails(self) -> None:
        client = MagicMock()
        client.files_upload_v2.return_value = {"file": {"id": "F123"}}
        client.files_info.side_effect = RuntimeError("lookup failed")
        logger = MagicMock()
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            artifact = root / "report.md"
            artifact.write_text("# Report\n", encoding="utf-8")
            manifest = root / ".manifest.json"
            manifest.write_text(json.dumps([str(artifact)]), encoding="utf-8")

            messages = slack_socket_agent.deliver_output_artifacts(
                client, "C123", "1.23", manifest, root, logger
            )

        self.assertEqual(["Attached `report.md` to this thread."], messages)
        logger.warning.assert_called_once()

    def test_oversized_output_keeps_local_access_and_skips_upload(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir, patch.object(
            slack_socket_agent, "MAX_OUTPUT_FILE_BYTES", 3
        ):
            root = Path(raw_dir)
            artifact = root / "large.bin"
            artifact.write_bytes(b"four")
            manifest = root / ".manifest.json"
            manifest.write_text(json.dumps([str(artifact)]), encoding="utf-8")

            artifacts, messages = slack_socket_agent.load_output_artifacts(manifest, root)

            self.assertEqual([artifact.resolve()], artifacts)
            self.assertEqual([], messages)
            client = MagicMock()
            messages = slack_socket_agent.deliver_output_artifacts(
                client, "C123", "1.23", manifest, root, MagicMock()
            )
            client.files_upload_v2.assert_not_called()
            self.assertTrue(artifact.exists())
            self.assertIn("saved locally", messages[0])
            self.assertIn("exceeds Tag’s", messages[0])
            self.assertIn("Open button", messages[0])

    def test_mixed_delivery_failures_preserve_files_and_allow_retry(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir, patch.object(
            slack_socket_agent, "MAX_OUTPUT_FILE_BYTES", 3
        ):
            root = Path(raw_dir).resolve()
            paths = [root / name for name in ("local.bin", "large.bin", "retry.txt", "ok.txt")]
            for path, content in zip(paths, (b"large", b"large", b"one", b"two")):
                path.write_bytes(content)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps([
                {"path": str(path), "attach": index != 0}
                for index, path in enumerate(paths)
            ]), encoding="utf-8")
            client = MagicMock()
            client.files_upload_v2.side_effect = [RuntimeError("upload failed"), {"file": {"id": "F1"}}]
            uploaded_paths: set[Path] = set()
            messages = slack_socket_agent.deliver_output_artifacts(
                client, "C123", "1.23", manifest, root, MagicMock(),
                uploaded_paths=uploaded_paths,
            )
            self.assertEqual(uploaded_paths, {paths[3]})
            blocks = slack_socket_agent.output_artifact_button_blocks(
                paths, root, uploaded_paths=uploaded_paths,
                user_id="UOWNER", channel="C123", thread_ts="1.23",
            )
            rendered = json.dumps(blocks)
            for name in ("local.bin", "large.bin", "retry.txt"):
                self.assertIn(name, rendered)
            self.assertNotIn("ok.txt", rendered)
            self.assertEqual(len(messages), 3)
            self.assertIn("exceeds", messages[0])
            self.assertIn("delivery failed", messages[1])
            self.assertIn("Attached", messages[2])
            self.assertEqual(client.files_upload_v2.call_count, 2)
            self.assertEqual(slack_socket_agent.load_output_artifacts(manifest, root), (paths, []))
            self.assertTrue(all(path.exists() for path in paths))
            manifest.write_text(json.dumps([{"path": str(paths[2]), "attach": True}]), encoding="utf-8")
            client.files_upload_v2.side_effect = None
            client.files_upload_v2.return_value = {"file": {"permalink": "https://example.test/retry"}}
            messages = slack_socket_agent.deliver_output_artifacts(
                client, "C123", "1.23", manifest, root, MagicMock()
            )
            self.assertIn("Download", messages[0])

    def test_upload_failure_distinguishes_local_save_from_slack_delivery(self) -> None:
        client = MagicMock()
        client.files_upload_v2.side_effect = RuntimeError("missing_scope")
        logger = MagicMock()
        with tempfile.TemporaryDirectory() as raw_dir, patch.object(
            slack_socket_agent.uuid, "uuid4", return_value=SimpleNamespace(hex="deadbeef0000")
        ):
            root = Path(raw_dir)
            artifact = root / "report.pdf"
            artifact.write_bytes(b"pdf")
            manifest = root / ".manifest.json"
            manifest.write_text(json.dumps([str(artifact)]), encoding="utf-8")

            messages = slack_socket_agent.deliver_output_artifacts(
                client, "C123", "1.23", manifest, root, logger
            )

        self.assertIn("saved locally, but Slack delivery failed", messages[0])
        self.assertIn("DEADBEEF", messages[0])
        self.assertIn("files:write", messages[0])
        logger.exception.assert_called_once()

    def test_mention_delivers_declared_output_and_cleans_request_manifest(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        indicator = MagicMock()
        indicator.native = False
        indicator.message_ts = None
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir).resolve()
            artifact = root / "requested.csv"
            artifact.write_text("owner,due\nAda,Monday\n", encoding="utf-8")
            client.files_upload_v2.return_value = {
                "files": [
                    {
                        "id": "FCSV",
                        "permalink": "https://workspace.slack.com/files/FCSV/requested.csv",
                    }
                ]
            }

            def finish_backend(*_args: object, **kwargs: object) -> tuple[str, bool]:
                manifest = kwargs["output_manifest"]
                assert isinstance(manifest, Path)
                manifest.write_text(json.dumps([str(artifact)]), encoding="utf-8")
                return "Saved requested.csv.", True

            with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
                os.environ,
                {
                    "SLACK_BOT_TOKEN": "xoxb-test",
                    "SLACK_CHANNEL_IDS": "C123",
                    "OPENTAG_SLACK_STREAMING": "0",
                    "OPENTAG_CLAUDE_TRANSPORT": "print",
                },
                clear=True,
            ), patch.object(
                slack_socket_agent, "default_workdir", return_value=root
            ), patch.object(
                slack_socket_agent, "WorkingIndicator", return_value=indicator
            ), patch.object(
                slack_socket_agent, "build_thread_text", return_value="thread"
            ), patch.object(
                slack_socket_agent, "run_backend", side_effect=finish_backend
            ):
                slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
                fake_app.events["app_mention"](
                    {
                        "channel": "C123",
                        "ts": "1.23",
                        "user": "UOWNER",
                        "text": "<@BOT> create requested.csv",
                    },
                    {"team_id": "T123"},
                    client,
                    MagicMock(),
                )

            self.assertEqual([], list(root.glob(f"{slack_socket_agent.OUTPUT_ARTIFACT_MANIFEST_PREFIX}*")))

        client.files_upload_v2.assert_called_once()
        upload = client.files_upload_v2.call_args.kwargs
        self.assertEqual(("C123", "1.23"), (upload["channel"], upload["thread_ts"]))
        posted = client.chat_postMessage.call_args.kwargs["text"]
        self.assertIn("Saved requested.csv.", posted)
        self.assertIn(
            "<https://workspace.slack.com/files/FCSV/requested.csv|requested.csv>",
            posted,
        )
        posted_blocks = client.chat_postMessage.call_args.kwargs["blocks"]
        actions = [element for block in posted_blocks if block["type"] == "actions"
                   for element in block["elements"]]
        self.assertEqual(
            [action["action_id"] for action in actions],
            [slack_socket_agent.OPEN_LOCAL_ARTIFACT_DIRECTORY_ACTION_ID],
        )
        self.assertEqual(actions[0]["text"]["text"], "📁 Open folder")


class SlackGeneratedImageTests(unittest.TestCase):
    def test_uploads_supported_images_to_originating_thread(self) -> None:
        client = MagicMock()
        with tempfile.TemporaryDirectory() as raw_dir:
            results_dir = Path(raw_dir)
            image = results_dir / "launch-card.png"
            image.write_bytes(b"png data")

            errors = slack_socket_agent.upload_generated_images(
                client,
                "C123",
                "1.23",
                results_dir,
            )

        self.assertEqual([], errors)
        client.files_upload_v2.assert_called_once_with(
            channel="C123",
            thread_ts="1.23",
            file=str(image),
            filename="launch-card.png",
            title="launch-card",
        )

    def test_keeps_copies_without_overwriting_earlier_results(self) -> None:
        client = MagicMock()
        with tempfile.TemporaryDirectory() as raw_dir:
            results_dir, keep_dir = Path(raw_dir) / "results", Path(raw_dir) / "images"
            results_dir.mkdir()
            (results_dir / "say-hi.png").write_bytes(b"new")
            keep_dir.mkdir()
            (keep_dir / "say-hi.png").write_bytes(b"old")

            errors = slack_socket_agent.upload_generated_images(
                client, "C123", "1.23", results_dir, keep_dir=keep_dir,
            )

            self.assertEqual([], errors)
            self.assertEqual((keep_dir / "say-hi.png").read_bytes(), b"old")
            self.assertEqual((keep_dir / "say-hi-2.png").read_bytes(), b"new")
        client.files_upload_v2.assert_called_once()

    @unittest.skipIf(os.name == "nt", "symlinks need extra privileges on Windows")
    def test_keeping_images_never_follows_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            image, outside = root / "say-hi.png", root / "outside"
            image.write_bytes(b"new")
            outside.mkdir()
            linked = root / "linked-images"
            linked.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(OSError):
                slack_socket_agent.keep_generated_images([image], linked)
            self.assertEqual([], list(outside.iterdir()))

            keep_dir = root / "images"
            keep_dir.mkdir()
            (keep_dir / "say-hi.png").symlink_to(outside / "escaped.png")
            slack_socket_agent.keep_generated_images([image], keep_dir)
            self.assertFalse((outside / "escaped.png").exists())
            self.assertEqual((keep_dir / "say-hi-2.png").read_bytes(), b"new")

    def test_upload_continues_when_copy_cannot_be_kept(self) -> None:
        client = MagicMock()
        with tempfile.TemporaryDirectory() as raw_dir:
            results_dir = Path(raw_dir) / "results"
            results_dir.mkdir()
            (results_dir / "card.png").write_bytes(b"png")
            blocker = Path(raw_dir) / "blocker"
            blocker.write_text("file, not a folder", encoding="utf-8")
            errors = slack_socket_agent.upload_generated_images(
                client, "C123", "1.23", results_dir, keep_dir=blocker / "images",
            )
        self.assertEqual([], errors)
        client.files_upload_v2.assert_called_once()

    def test_rejects_unsupported_oversized_and_symlinked_results(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            results_dir = Path(raw_dir)
            (results_dir / "notes.txt").write_text("not an image", encoding="utf-8")
            with (results_dir / "large.png").open("wb") as output:
                output.truncate(slack_socket_agent.MAX_ATTACHMENT_BYTES + 1)
            (results_dir / "linked.png").symlink_to(results_dir / "large.png")
            images, errors = slack_socket_agent.collect_generated_images(results_dir)

        self.assertEqual([], images)
        self.assertTrue(any("unsupported image type" in error for error in errors))
        self.assertTrue(any("15 MB" in error for error in errors))
        self.assertTrue(any("not a regular file" in error for error in errors))

    def test_mention_uploads_backend_image_result_after_text_answer(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()

        def backend_result(*args: object, **kwargs: object) -> tuple[str, bool]:
            del kwargs
            attachment_dir = args[5]
            assert isinstance(attachment_dir, Path)
            result = slack_socket_agent.generated_images_dir(attachment_dir) / "chart.png"
            result.write_bytes(b"png data")
            return "Here is the chart.", True

        with tempfile.TemporaryDirectory() as raw_home, patch.object(
            slack_socket_agent, "App", return_value=fake_app
        ), patch.dict(
            os.environ,
            {
                "TAG_HOME": raw_home,
                "SLACK_BOT_TOKEN": "xoxb-test",
                "SLACK_CHANNEL_IDS": "C123",
                "OPENTAG_SLACK_STREAMING": "0",
                "OPENTAG_CLAUDE_TRANSPORT": "print",
            },
            clear=True,
        ), patch.object(
            slack_socket_agent,
            "build_thread_text",
            return_value="UOWNER: make a chart",
        ), patch.object(slack_socket_agent, "run_backend", side_effect=backend_result):
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            handler = fake_app.events["app_mention"]
            handler(
                {"channel": "C123", "ts": "1.23", "user": "UOWNER", "text": "<@BOT> chart"},
                {"team_id": "T123"},
                client,
                MagicMock(),
            )

        client.chat_postMessage.assert_called_once()
        posted = client.chat_postMessage.call_args.kwargs
        self.assertEqual(("C123", "1.23", "Here is the chart."), (posted["channel"], posted["thread_ts"], posted["text"]))
        self.assertNotIn(slack_socket_agent.SETTINGS_ACTION_ID, json.dumps(posted["blocks"]))
        upload = client.files_upload_v2.call_args.kwargs
        self.assertEqual("C123", upload["channel"])
        self.assertEqual("1.23", upload["thread_ts"])
        self.assertEqual("chart.png", upload["filename"])
        self.assertTrue(
            Path(upload["file"]).is_relative_to(
                Path(raw_home) / "instances/default/tmp"
            )
        )

    def test_failed_backend_does_not_upload_partial_image_result(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()

        def backend_failure(*args: object, **kwargs: object) -> tuple[str, bool]:
            del kwargs
            attachment_dir = args[5]
            assert isinstance(attachment_dir, Path)
            result = slack_socket_agent.generated_images_dir(attachment_dir) / "partial.png"
            result.write_bytes(b"partial")
            return "Backend failed", False

        with tempfile.TemporaryDirectory() as raw_home, patch.object(
            slack_socket_agent, "App", return_value=fake_app
        ), patch.dict(
            os.environ,
            {
                "TAG_HOME": raw_home,
                "SLACK_BOT_TOKEN": "xoxb-test",
                "SLACK_CHANNEL_IDS": "C123",
                "OPENTAG_SLACK_STREAMING": "0",
            },
            clear=True,
        ), patch.object(
            slack_socket_agent,
            "build_thread_text",
            return_value="UOWNER: make a chart",
        ), patch.object(slack_socket_agent, "run_backend", side_effect=backend_failure):
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            handler = fake_app.events["app_mention"]
            handler(
                {"channel": "C123", "ts": "1.23", "user": "UOWNER", "text": "<@BOT> chart"},
                {"team_id": "T123"},
                client,
                MagicMock(),
            )

        client.files_upload_v2.assert_not_called()
        client.chat_postMessage.assert_not_called()
        private = client.chat_postEphemeral.call_args.kwargs
        self.assertEqual("UOWNER", private["user"])
        self.assertIn("Tag couldn't complete this request", private["text"])
        self.assertEqual("actions", private["blocks"][-1]["type"])


class SlackReplyChunkingTests(unittest.TestCase):
    def test_splits_at_paragraph_boundaries(self) -> None:
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."

        self.assertEqual(
            ["First paragraph.", "Second paragraph.", "Third paragraph."],
            slack_socket_agent.split_reply(text, max_chars=20),
        )

    def test_preserves_long_unbroken_text(self) -> None:
        text = "a" * 25

        self.assertEqual(["a" * 10, "a" * 10, "a" * 5], slack_socket_agent.split_reply(text, max_chars=10))


class SlackFailureReplyTests(unittest.TestCase):
    def test_basic_authorization_is_redacted_in_slack_reply(self) -> None:
        for detail in ('Gateway rejected Authorization: Basic dXNlcjpwYXNz',
                       '{"error":{"message":"Gateway rejected Authorization: bAsIc dXNlcjpwYXNz"}}'):
            reply = slack_socket_agent.user_facing_failure(detail, 420, 'ABC12345')
            self.assertNotIn('dXNlcjpwYXNz', reply)
            self.assertIn('redacted', reply)

    def test_private_failure_removes_public_progress_placeholder(self) -> None:
        client = MagicMock()
        slack_socket_agent.post_private_failure(
            client, "C1", "1.0", "UOWNER", "Tag couldn't complete this request.", "1.1"
        )

        client.chat_postMessage.assert_not_called()
        client.chat_delete.assert_called_once_with(channel="C1", ts="1.1")
        self.assertEqual("UOWNER", client.chat_postEphemeral.call_args.kwargs["user"])

    def test_timeout_copy_distinguishes_idle_and_maximum_deadlines(self) -> None:
        idle = slack_socket_agent.user_facing_failure(
            "Tag backend timed out: no backend activity for 420s", 420, "IDLE", 3600
        )
        maximum = slack_socket_agent.user_facing_failure(
            "Tag backend exceeded its maximum runtime of 3600s", 420, "MAX", 3600
        )

        self.assertIn("without backend activity", idle)
        self.assertIn("420", idle)
        self.assertIn("maximum runtime", maximum)
        self.assertIn("3600", maximum)

    def test_failure_copy_shows_redacted_backend_error(self) -> None:
        reply = slack_socket_agent.user_facing_failure(
            "RuntimeError: unsupported deployment token=secret", 420, "ABC12345"
        )

        self.assertIn("The backend reported: RuntimeError: unsupported deployment", reply)
        self.assertNotIn("token=secret", reply)
        self.assertIn("ABC12345", reply)
        self.assertIn("Please retry", reply)

    def test_unsupported_chatgpt_model_copy_identifies_safe_cause(self) -> None:
        with patch.dict(os.environ, {"TAG_ID": "default"}):
            reply = slack_socket_agent.user_facing_failure(
                "The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account.",
                420, "ABC12345", backend_code="invalid_request_error",
            )

        self.assertIn("Choose another model in the <hover-tag://tag/default|Tag app> → Details", reply)
        self.assertNotIn("Tag.app", reply)
        self.assertIn("The 'gpt-6.1-sol' model is not supported", reply)

    def test_failure_actions_keep_report_content_out_of_slack_metadata(self) -> None:
        blocks = slack_socket_agent.failure_action_blocks(
            team="T1",
            channel="C1",
            thread_ts="1.0",
            request_ts="1.1",
            error_reference="ABC12345",
        )
        actions = blocks[0]["elements"]

        self.assertEqual(
            [
                slack_socket_agent.RETRY_ACTION_ID,
                slack_socket_agent.FIX_WITH_AGENT_ACTION_ID,
                slack_socket_agent.REPORT_ISSUE_ACTION_ID,
            ],
            [action["action_id"] for action in actions],
        )
        self.assertEqual({"reference": "ABC12345"}, json.loads(actions[1]["value"]))
        self.assertEqual({"reference": "ABC12345"}, json.loads(actions[2]["value"]))

    def test_failure_recovery_has_no_configuration_controls(self) -> None:
        blocks = slack_socket_agent.failure_action_blocks(
            team="T1", channel="C1", thread_ts="1.0", request_ts="1.1",
            error_reference="ABC12345",
        )
        self.assertEqual(len(blocks), 1)
        actions = blocks[0]["elements"]
        self.assertEqual([action["text"]["text"] for action in actions],
                         ["Retry", "Fix with coding agent", "Report issue"])


    def test_report_modal_uses_selectable_text_and_manual_community_link(self) -> None:
        report = make_error_report(
            "ABC12345",
            "authentication failed: token=xoxb-secret",
            backend="codex",
            origin=ReportOrigin(channel_id="C1", requester_id="UOWNER"),
        )
        modal = slack_socket_agent.report_preview_modal(
            report,
            private_metadata={"reference": "ABC12345"},
        )

        report_input = next(block for block in modal["blocks"] if block.get("block_id") == "tag_report_text")
        join_block = next(block for block in modal["blocks"] if block["type"] == "actions")
        self.assertEqual("plain_text_input", report_input["element"]["type"])
        self.assertIn("Select the text to copy it", report_input["hint"]["text"])
        self.assertEqual(
            slack_socket_agent.COMMUNITY_INVITE_URL,
            join_block["elements"][0]["url"],
        )
        self.assertNotIn("xoxb-secret", report_input["element"]["initial_value"])

    def test_report_action_requires_the_original_caller(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        logger = MagicMock()
        with tempfile.TemporaryDirectory() as raw_dir:
            store = ErrorReportStore(Path(raw_dir))
            store.save(
                make_error_report(
                    "ABC12345",
                    "backend failed",
                    backend="claude",
                    origin=ReportOrigin(
                        team_id="T1",
                        channel_id="C1",
                        thread_ts="1.0",
                        request_ts="1.1",
                        requester_id="UOWNER",
                    ),
                )
            )
            with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
                os.environ,
                {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C1"},
                clear=True,
            ):
                slack_socket_agent.create_app(
                    "claude",
                    30,
                    frozenset({"UOWNER", "UOTHER"}),
                    report_store=store,
                )
                handler = fake_app.actions[slack_socket_agent.REPORT_ISSUE_ACTION_ID]
                body = {
                    "user": {"id": "UOTHER"},
                    "team": {"id": "T1"},
                    "channel": {"id": "C1"},
                    "trigger_id": "trigger",
                    "actions": [{"value": json.dumps({"reference": "ABC12345"})}],
                }
                handler(MagicMock(), body, client, logger)
                client.views_open.assert_not_called()

                body["user"] = {"id": "UOWNER"}
                handler(MagicMock(), body, client, logger)

        client.views_open.assert_called_once()
        self.assertEqual(
            slack_socket_agent.REPORT_VIEW_ID,
            client.views_open.call_args.kwargs["view"]["callback_id"],
        )

    def test_report_submission_keeps_sanitized_user_context_in_preview(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        logger = MagicMock()
        with tempfile.TemporaryDirectory() as raw_dir:
            store = ErrorReportStore(Path(raw_dir))
            store.save(
                make_error_report(
                    "ABC12345",
                    "backend failed",
                    backend="claude",
                    origin=ReportOrigin(channel_id="C1", requester_id="UOWNER"),
                )
            )
            with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
                os.environ,
                {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C1"},
                clear=True,
            ):
                slack_socket_agent.create_app(
                    "claude",
                    30,
                    frozenset({"UOWNER"}),
                    report_store=store,
                )
                handler = fake_app.views[slack_socket_agent.REPORT_VIEW_ID]
                ack = MagicMock()
                handler(
                    ack,
                    {
                        "user": {"id": "UOWNER"},
                        "view": {
                            "private_metadata": json.dumps({"reference": "ABC12345"}),
                            "state": {
                                "values": {
                                    "tag_report_text": {
                                        "report_text": {"value": "Reviewed report"}
                                    },
                                    "tag_user_context": {
                                        "user_context": {"value": "Summarize token=xoxb-secret"}
                                    },
                                }
                            },
                        },
                    },
                    client,
                    logger,
                )

        ack.assert_called_once_with()
        preview = client.chat_postEphemeral.call_args.kwargs["text"]
        self.assertIn("Reviewed report", preview)
        self.assertIn("User-provided context:", preview)
        self.assertIn("Summarize token=<redacted>", preview)
        self.assertNotIn("xoxb-secret", preview)

    def test_fix_action_posts_prompt_only_to_original_requester(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        logger = MagicMock()
        report = make_error_report(
            "ABC12345",
            "backend failed",
            backend="codex",
            origin=ReportOrigin(channel_id="C1", requester_id="UOWNER"),
        )
        with tempfile.TemporaryDirectory() as raw_dir:
            store = ErrorReportStore(Path(raw_dir))
            store.save(report)
            with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
                os.environ,
                {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C1"},
                clear=True,
            ):
                slack_socket_agent.create_app(
                    "codex",
                    30,
                    frozenset({"UOWNER", "UOTHER"}),
                    report_store=store,
                )
                handler = fake_app.actions[slack_socket_agent.FIX_WITH_AGENT_ACTION_ID]
                ack = MagicMock()
                body = {
                    "user": {"id": "UOTHER"},
                    "channel": {"id": "C1"},
                    "actions": [{"value": json.dumps({"reference": "ABC12345"})}],
                }
                handler(ack, body, client, logger)
                client.chat_postEphemeral.reset_mock()
                body["user"] = {"id": "UOWNER"}
                handler(ack, body, client, logger)

        self.assertEqual(2, ack.call_count)
        client.views_open.assert_not_called()
        client.chat_postMessage.assert_not_called()
        client.chat_postEphemeral.assert_called_once()
        private = client.chat_postEphemeral.call_args.kwargs
        self.assertEqual("UOWNER", private["user"])
        self.assertEqual("C1", private["channel"])
        self.assertIn("Help me fix this failed Tag request", private["text"])
        self.assertIn("ABC12345", private["text"])

    def test_retry_button_contains_only_request_identity(self) -> None:
        blocks = slack_socket_agent.retry_button_blocks(
            team="T1", channel="C1", thread_ts="1.0", request_ts="1.1"
        )
        button = blocks[0]["elements"][0]

        self.assertEqual(slack_socket_agent.RETRY_ACTION_ID, button["action_id"])
        self.assertEqual(
            {"team": "T1", "channel": "C1", "thread_ts": "1.0", "request_ts": "1.1"},
            json.loads(button["value"]),
        )

    def test_retry_button_marks_direct_message_origin(self) -> None:
        blocks = slack_socket_agent.retry_button_blocks(
            team="T1",
            channel="D1",
            thread_ts="1.0",
            request_ts="1.1",
            direct_message=True,
        )

        self.assertIs(True, json.loads(blocks[0]["elements"][0]["value"])["direct_message"])

    def test_retry_action_reloads_original_slack_request(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        client.conversations_replies.return_value = {
            "messages": [{"ts": "1.1", "text": "<@BOT> try this again"}]
        }
        metadata = {
            "team": "T1",
            "channel": "C1",
            "thread_ts": "1.0",
            "request_ts": "1.1",
        }
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ,
            {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C1"},
            clear=True,
        ), patch.object(agent_models, "discover_codex_models", return_value=[]), patch.object(
            slack_socket_agent, "build_thread_text", return_value="thread"
        ), patch.object(
            slack_socket_agent, "run_backend_events", return_value=("done", True)
        ) as run_backend:
            slack_socket_agent.create_app("codex", 30, frozenset({"UOWNER"}))
            ack = MagicMock()
            fake_app.actions[slack_socket_agent.RETRY_ACTION_ID](
                ack,
                {
                    "user": {"id": "UOWNER"},
                    "actions": [{"value": json.dumps(metadata)}],
                },
                client,
                MagicMock(),
            )

        ack.assert_called_once_with()
        self.assertEqual("try this again", run_backend.call_args.args[5])
        self.assertEqual("UOWNER", run_backend.call_args.args[4])


class SlackApprovalTests(unittest.TestCase):
    def tearDown(self) -> None:
        with slack_socket_agent.ACTIVE_RUNS_LOCK:
            slack_socket_agent.ACTIVE_RUNS.clear()

    def test_approval_buttons_contain_only_request_identity(self) -> None:
        blocks = slack_socket_agent.approval_button_blocks(
            team="T1",
            channel="C1",
            thread_ts="1.0",
            user_id="U1",
            approval_id="a" * 32,
            label="run a command outside the workspace sandbox",
        )

        buttons = blocks[1]["elements"]
        self.assertEqual(["Approve once", "Deny"], [button["text"]["text"] for button in buttons])
        self.assertEqual(
            {
                "team": "T1",
                "channel": "C1",
                "thread_ts": "1.0",
                "user": "U1",
                "approval_id": "a" * 32,
            },
            json.loads(buttons[0]["value"]),
        )
        self.assertNotIn("command", buttons[0]["value"])

    def test_native_rule_buttons_show_target_but_keep_metadata_opaque(self) -> None:
        from scripts.tag_approval_choices import approval_choices, public_approval_choices
        choices = public_approval_choices(approval_choices("item/commandExecution/requestApproval", {
            "availableDecisions": ["acceptForSession", {"applyNetworkPolicyAmendment": {
                "network_policy_amendment": {"host": "forms.google.com", "action": "allow"}}}],
        }))
        blocks = slack_socket_agent.approval_button_blocks(
            team="T1", channel="C1", thread_ts="1", user_id="U1", approval_id="a" * 32,
            label="run a command outside the workspace sandbox", choices=choices,
        )
        buttons = [b["accessory"] for b in blocks if "accessory" in b]
        self.assertEqual(["Allow for this task", "Always allow this host"],
                         [b["text"]["text"] for b in buttons])
        self.assertIn("forms.google.com", blocks[-1]["text"]["text"])
        self.assertIn("forms.google.com", buttons[1]["confirm"]["text"]["text"])
        self.assertNotIn("forms.google.com", buttons[1]["value"])
        self.assertEqual("1", json.loads(buttons[1]["value"])["choice"])

    def test_plain_choices_share_one_row_and_rules_sit_beside_their_buttons(self) -> None:
        choices = [{"id": "0", "label": "Allow once", "detail": ""},
                   {"id": "1", "label": "Always allow", "detail": "Save rule: Bash(x:*)", "persistent": True},
                   {"id": "2", "label": "Deny", "detail": ""}]
        blocks = slack_socket_agent.approval_button_blocks(
            team="T1", channel="C1", thread_ts="1", user_id="U1", approval_id="a" * 32,
            label="run a command that requires approval", backend="claude", choices=choices,
        )
        self.assertEqual(["section", "actions", "section"], [b["type"] for b in blocks])
        self.assertEqual(["Allow once", "Deny"], [b["text"]["text"] for b in blocks[1]["elements"]])
        self.assertEqual("Save rule: Bash(x:*)", blocks[2]["text"]["text"])
        self.assertEqual("Always allow", blocks[2]["accessory"]["text"]["text"])
        self.assertEqual("Save Claude rule?", blocks[2]["accessory"]["confirm"]["title"]["text"])

    def test_main_row_hides_other_choices_behind_more_options(self) -> None:
        choices = [{"id": "0", "label": "Allow once", "detail": "", "primary": True},
                   {"id": "1", "label": "Allow for this task", "detail": "Task: Bash(x)"},
                   {"id": "2", "label": "Always allow x commands", "detail": "Save: Bash(x:*)", "persistent": True,
                    "primary": True},
                   {"id": "3", "label": "Deny", "detail": "", "primary": True},
                   {"id": "4", "label": "Deny and stop", "detail": ""}]
        kwargs = dict(team="T1", channel="C1", thread_ts="1", user_id="U1", approval_id="a" * 32,
                      label="run a command that requires approval", backend="claude", choices=choices)
        compact = slack_socket_agent.approval_button_blocks(**kwargs)
        self.assertEqual(["section", "actions"], [b["type"] for b in compact])
        row = compact[1]["elements"]
        self.assertEqual(["Allow once", "Always allow x commands", "Deny", "More options"], [b["text"]["text"] for b in row])
        self.assertEqual("Save: Bash(x:*)", row[1]["confirm"]["text"]["text"])
        self.assertEqual(slack_socket_agent.APPROVAL_MORE_ACTION_ID, row[3]["action_id"])
        self.assertNotIn("choice", json.loads(row[3]["value"]))
        full = slack_socket_agent.approval_button_blocks(**kwargs, expanded=True)
        labels = [e["text"]["text"] for b in full for e in b.get("elements", []) + [b.get("accessory")] if e]
        self.assertEqual(["Allow once", "Deny", "Deny and stop", "Allow for this task", "Always allow x commands"], labels)

    def test_more_options_redraws_only_a_pending_prompt_for_its_requester(self) -> None:
        fake_app = FakeApp()
        with tempfile.TemporaryDirectory() as raw, patch.object(
            slack_socket_agent, "App", return_value=fake_app
        ), patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C1"}, clear=True), patch.object(
            slack_socket_agent, "discover_tag_models", return_value=[]
        ):
            slack_socket_agent.create_app("codex", 30, frozenset({"UOWNER", "UOTHER"}))
            handler = fake_app.actions[slack_socket_agent.APPROVAL_MORE_ACTION_ID]
            process = MagicMock()
            process.poll.return_value = None
            run = slack_socket_agent.ActiveBackendRun(process, Path(raw) / "control", "run", Path(raw))
            aid = "e" * 32
            choices = [{"id": "0", "label": "Allow once", "detail": "", "primary": True},
                       {"id": "1", "label": "Deny and stop", "detail": ""}]
            run.register_approval(aid, choices)
            run.pending_approval_prompts[aid] = {"approval_id": aid, "label": "run a command that requires approval",
                                                 "choices": choices, "backend": "claude"}
            key = slack_socket_agent.RunKey("T1", "C1", "1.0")
            slack_socket_agent.register_active_run(key, run)
            metadata = {"team": "T1", "channel": "C1", "thread_ts": "1.0", "user": "UOWNER", "approval_id": aid}

            def click(user: str) -> MagicMock:
                respond = MagicMock()
                handler(ack=MagicMock(), client=MagicMock(), logger=MagicMock(), respond=respond, body={
                    "user": {"id": user}, "channel": {"id": "C1"}, "team": {"id": "T1"},
                    "actions": [{"action_id": slack_socket_agent.APPROVAL_MORE_ACTION_ID,
                                 "value": json.dumps(metadata)}]})
                return respond

            try:
                self.assertFalse(click("UOTHER").called)
                shown = click("UOWNER").call_args.kwargs
                self.assertTrue(shown["replace_original"])
                self.assertIn("Deny and stop", json.dumps(shown["blocks"]))
                self.assertIn(aid, run.pending_approvals)  # Expanding decides nothing.
                self.assertTrue(run.resolve_approval(aid, choice="1"))
                self.assertIn("expired", click("UOWNER").call_args.kwargs["text"])
            finally:
                slack_socket_agent.unregister_active_run(key, run)

    def test_native_choice_registry_rejects_forgery_replay_and_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            process = MagicMock()
            process.poll.return_value = None
            run = slack_socket_agent.ActiveBackendRun(process, Path(raw) / "control", "run", Path(raw))
            aid = "a" * 32
            self.assertTrue(run.register_approval(aid, [{"id": "0"}, {"id": "1"}]))
            self.assertFalse(run.resolve_approval(aid, choice="99"))
            self.assertFalse(run.resolve_approval(aid, approved=True))
            self.assertTrue(run.resolve_approval(aid, choice="1"))
            self.assertEqual({"choice": "1"}, json.loads((Path(raw) / (aid + ".json")).read_text(encoding="utf-8")))
            self.assertFalse(run.resolve_approval(aid, choice="1"))
            self.assertTrue(run.register_approval("b" * 32, [{"id": "0"}]))
            run.finish()
            self.assertFalse(run.resolve_approval("b" * 32, choice="0"))

    def test_native_choice_callback_authorizes_owner_and_preserves_selection(self) -> None:
        fake_app = FakeApp()
        with tempfile.TemporaryDirectory() as raw, patch.object(
            slack_socket_agent, "App", return_value=fake_app
        ), patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C1"}, clear=True), patch.object(
            slack_socket_agent, "discover_tag_models", return_value=[]
        ):
            slack_socket_agent.create_app("codex", 30, frozenset({"UOWNER", "UOTHER"}))
            handler = next(fn for key, fn in fake_app.actions.items()
                           if hasattr(key, "pattern") and "approval_choice" in key.pattern)
            process = MagicMock()
            process.poll.return_value = None
            run = slack_socket_agent.ActiveBackendRun(process, Path(raw) / "control", "run", Path(raw))
            aid = "d" * 32
            run.register_approval(aid, [{"id": "0"}, {"id": "1"}])
            slack_socket_agent.register_active_run(slack_socket_agent.RunKey("T1", "C1", "1.0"), run)
            value = {"team": "T1", "channel": "C1", "thread_ts": "1.0", "user": "UOWNER",
                     "approval_id": aid, "choice": "1"}
            body = {"team": {"id": "T1"}, "channel": {"id": "C1"}, "user": {"id": "UOTHER"},
                    "actions": [{"action_id": slack_socket_agent.APPROVAL_CHOICE_ACTION_PREFIX + "1",
                                 "value": json.dumps(value)}]}
            client, respond = MagicMock(), MagicMock()
            handler(MagicMock(), body, client, MagicMock(), respond)
            self.assertFalse((Path(raw) / (aid + ".json")).exists())
            client.chat_postEphemeral.assert_called_once()
            body["user"]["id"] = "UOWNER"
            handler(MagicMock(), body, client, MagicMock(), respond)
            self.assertEqual({"choice": "1"}, json.loads((Path(raw) / (aid + ".json")).read_text(encoding="utf-8")))
            self.assertIn("sent to Tag", respond.call_args.kwargs["text"])
            handler(MagicMock(), body, client, MagicMock(), respond)
            self.assertIn("expired", respond.call_args.kwargs["text"])

    def test_auto_review_details_are_literal_rich_text_and_not_button_metadata(self) -> None:
        client = MagicMock()
        slack_socket_agent.post_codex_approval(
            client, team="T1", channel="C1", thread_ts="1", user_id="UOWNER",
            approval={"approval_id": "a" * 32, "label": "retry an action denied by automatic review",
                      "review_details": {"action": "Use connected tool: chrome/connect",
                                         "reason": "Other signed-in tabs. <!channel> token=hidden-token"}},
        )
        client.chat_postMessage.assert_not_called()
        sent = client.chat_postEphemeral.call_args.kwargs
        self.assertEqual("UOWNER", sent["user"])
        blocks = sent["blocks"]
        self.assertEqual("header", blocks[0]["type"])
        action = blocks[1]["elements"][0]["elements"]
        reason = blocks[2]["elements"][0]["elements"]
        self.assertEqual({"bold": True}, action[0]["style"])
        self.assertIn("chrome/connect", action[1]["text"])
        self.assertIn("Other signed-in tabs.", reason[1]["text"])
        self.assertEqual("text", reason[1]["type"])
        self.assertIn("<!channel>", reason[1]["text"])
        self.assertNotIn("hidden-token", json.dumps(sent))
        for button in blocks[-1]["elements"]:
            self.assertNotIn("chrome", button["value"])
            self.assertNotIn("tabs", button["value"])

    def test_auto_review_buttons_explain_one_retry(self) -> None:
        blocks = slack_socket_agent.approval_button_blocks(
            team="T1", channel="C1", thread_ts="1.0", user_id="U1",
            approval_id="a" * 32, label="retry an action denied by automatic review",
        )
        self.assertEqual(["Approve retry", "Dismiss"],
                         [b["text"]["text"] for b in blocks[-1]["elements"]])
        self.assertIn("Automatic review still applies", blocks[-2]["elements"][0]["text"])
        self.assertEqual("U1", json.loads(blocks[-1]["elements"][0]["value"])["user"])

    def test_approval_prompt_is_visible_only_to_requesting_user(self) -> None:
        client = MagicMock()

        slack_socket_agent.post_codex_approval(
            client,
            team="T1",
            channel="C1",
            thread_ts="1.0",
            user_id="UOWNER",
            approval={
                "approval_id": "a" * 32,
                "label": "run a command outside the workspace sandbox",
            },
        )

        client.chat_postEphemeral.assert_called_once()
        self.assertEqual("UOWNER", client.chat_postEphemeral.call_args.kwargs["user"])
        client.chat_postMessage.assert_not_called()

    def test_initiating_user_can_approve_active_request_once(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        logger = MagicMock()
        approval_id = "b" * 32
        metadata = {
            "team": "T1",
            "channel": "C1",
            "thread_ts": "1.0",
            "user": "UOWNER",
            "approval_id": approval_id,
        }
        with tempfile.TemporaryDirectory() as raw_dir, patch.object(
            slack_socket_agent, "App", return_value=fake_app
        ), patch.dict(
            os.environ,
            {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C1"},
            clear=True,
        ), patch.object(agent_models, "discover_codex_models", return_value=[]):
            slack_socket_agent.create_app("codex", 30, frozenset({"UOWNER"}))
            process = MagicMock()
            process.poll.return_value = None
            run = slack_socket_agent.ActiveBackendRun(
                process,
                Path(raw_dir) / "control",
                "run-1",
                Path(raw_dir),
            )
            self.assertTrue(run.register_approval(approval_id))
            slack_socket_agent.register_active_run(
                slack_socket_agent.RunKey("T1", "C1", "1.0"), run
            )
            ack = MagicMock()
            respond = MagicMock()

            fake_app.actions[slack_socket_agent.APPROVAL_APPROVE_ACTION_ID](
                ack,
                {
                    "team": {"id": "T1"},
                    "user": {"id": "UOWNER"},
                    "channel": {"id": "C1"},
                    "container": {"message_ts": "1.2"},
                    "actions": [{
                        "action_id": slack_socket_agent.APPROVAL_APPROVE_ACTION_ID,
                        "value": json.dumps(metadata),
                    }],
                },
                client,
                logger,
                respond,
            )

            decision = json.loads(
                (Path(raw_dir) / f"{approval_id}.json").read_text(encoding="utf-8")
            )

        ack.assert_called_once_with()
        self.assertEqual({"decision": "approve"}, decision)
        respond.assert_called_once()
        self.assertTrue(respond.call_args.kwargs["replace_original"])
        self.assertEqual("ephemeral", respond.call_args.kwargs["response_type"])
        self.assertIn("Approved once", respond.call_args.kwargs["text"])
        client.chat_update.assert_not_called()
        self.assertFalse(run.resolve_approval(approval_id, approved=True))

    def test_other_user_cannot_decide_approval(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        approval_id = "c" * 32
        with tempfile.TemporaryDirectory() as raw_dir, patch.object(
            slack_socket_agent, "App", return_value=fake_app
        ), patch.dict(
            os.environ,
            {
                "SLACK_BOT_TOKEN": "xoxb-test",
                "SLACK_CHANNEL_IDS": "C1",
            },
            clear=True,
        ), patch.object(agent_models, "discover_codex_models", return_value=[]):
            slack_socket_agent.create_app("codex", 30, frozenset({"UOWNER", "UOTHER"}))
            process = MagicMock()
            process.poll.return_value = None
            run = slack_socket_agent.ActiveBackendRun(
                process,
                Path(raw_dir) / "control",
                "run-1",
                Path(raw_dir),
            )
            self.assertTrue(run.register_approval(approval_id))
            slack_socket_agent.register_active_run(
                slack_socket_agent.RunKey("T1", "C1", "1.0"), run
            )

            fake_app.actions[slack_socket_agent.APPROVAL_DENY_ACTION_ID](
                MagicMock(),
                {
                    "team": {"id": "T1"},
                    "user": {"id": "UOTHER"},
                    "channel": {"id": "C1"},
                    "actions": [{
                        "action_id": slack_socket_agent.APPROVAL_DENY_ACTION_ID,
                        "value": json.dumps({
                            "team": "T1",
                            "channel": "C1",
                            "thread_ts": "1.0",
                            "user": "UOWNER",
                            "approval_id": approval_id,
                        }),
                    }],
                },
                client,
                MagicMock(),
                MagicMock(),
            )

            self.assertFalse((Path(raw_dir) / f"{approval_id}.json").exists())

        client.chat_postEphemeral.assert_called_once()
        self.assertIn(
            "Only the authorized user",
            client.chat_postEphemeral.call_args.kwargs["text"],
        )
        self.assertIn(approval_id, run.pending_approvals)


class SlackChannelAllowlistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = os.environ.get("SLACK_CHANNEL_ID")
        self.previous_many = os.environ.get("SLACK_CHANNEL_IDS")

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("SLACK_CHANNEL_ID", None)
        else:
            os.environ["SLACK_CHANNEL_ID"] = self.previous
        if self.previous_many is None:
            os.environ.pop("SLACK_CHANNEL_IDS", None)
        else:
            os.environ["SLACK_CHANNEL_IDS"] = self.previous_many

    def test_configured_channel_is_allowed(self) -> None:
        os.environ["SLACK_CHANNEL_ID"] = "C123"
        self.assertTrue(slack_socket_agent.slack_channel_allowed("C123"))
        self.assertFalse(slack_socket_agent.slack_channel_allowed("C999"))

    def test_multiple_configured_channels_are_allowed(self) -> None:
        os.environ["SLACK_CHANNEL_IDS"] = "C123,G456"
        self.assertTrue(slack_socket_agent.slack_channel_allowed("C123"))
        self.assertTrue(slack_socket_agent.slack_channel_allowed("G456"))
        self.assertFalse(slack_socket_agent.slack_channel_allowed("C999"))

    def test_empty_configuration_fails_closed(self) -> None:
        os.environ["SLACK_CHANNEL_ID"] = ""
        os.environ["SLACK_CHANNEL_IDS"] = ""
        self.assertFalse(slack_socket_agent.slack_channel_allowed("C999"))

    def test_invited_channel_mention_is_allowed_before_membership_poll(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        client.conversations_info.return_value = {"channel": {"is_member": True}}
        event = {
            "channel": "CNEW",
            "ts": "1.23",
            "user": "UOTHER",
            "text": "<@BOT> hi",
        }
        with patch.object(
            slack_socket_agent, "App", return_value=fake_app
        ), patch.dict(
            os.environ,
            {
                "SLACK_BOT_TOKEN": "xoxb-test",
                "SLACK_CHANNEL_IDS": "COLD",
                "SLACK_CHANNEL_POLICY": "invited",
            },
            clear=True,
        ):
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            fake_app.events["app_mention"](
                event,
                {"team_id": "T123"},
                client,
                MagicMock(),
            )

        client.conversations_info.assert_called_once_with(channel="CNEW")
        client.chat_postMessage.assert_called_once_with(
            channel="CNEW",
            thread_ts="1.23",
            text=slack_socket_agent.UNAUTHORIZED_USER_MESSAGE,
        )

    def test_invited_policy_still_rejects_channel_without_membership(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        client.conversations_info.return_value = {"channel": {"is_member": False}}
        logger = MagicMock()
        with patch.object(
            slack_socket_agent, "App", return_value=fake_app
        ), patch.dict(
            os.environ,
            {
                "SLACK_BOT_TOKEN": "xoxb-test",
                "SLACK_CHANNEL_IDS": "COLD",
                "SLACK_CHANNEL_POLICY": "invited",
            },
            clear=True,
        ):
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            fake_app.events["app_mention"](
                {
                    "channel": "COTHER",
                    "ts": "1.23",
                    "user": "UOWNER",
                    "text": "<@BOT> hi",
                },
                {"team_id": "T123"},
                client,
                logger,
            )

        client.chat_postMessage.assert_not_called()
        logger.warning.assert_called_once()

    def test_invited_membership_lookup_failure_fails_closed(self) -> None:
        client = MagicMock()
        client.conversations_info.side_effect = RuntimeError("Slack unavailable")
        with patch.dict(
            os.environ,
            {"SLACK_CHANNEL_POLICY": "invited"},
            clear=True,
        ):
            self.assertFalse(
                slack_socket_agent.newly_invited_channel_allowed("CNEW", client)
            )


class SlackCrossChannelSearchTests(unittest.TestCase):
    def configured_client(self) -> MagicMock:
        client = MagicMock()
        client.users_info.return_value = {
            "user": {"id": "UOWNER", "team_id": "T123"}
        }
        client.conversations_info.side_effect = lambda *, channel: {
            "channel": {
                "id": channel,
                "name": {"C123": "general", "C456": "support"}[channel],
                "is_private": False,
                "is_member": True,
            }
        }
        return client

    def test_working_indicator_starts_before_all_channel_scope_resolution(self) -> None:
        fake_app = FakeApp()
        client = self.configured_client()
        indicator = MagicMock()
        indicator.native = False
        indicator.message_ts = None
        events: list[str] = []
        indicator.start.side_effect = lambda: events.append("indicator")
        plan_search_scopes = slack_socket_agent.plan_search_scopes

        def tracked_plan(**kwargs: object):
            events.append(f"plan:{kwargs['intent'].mode}")
            return plan_search_scopes(**kwargs)

        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ,
            {
                "SLACK_BOT_TOKEN": "xoxb-test",
                "SLACK_TEAM_ID": "T123",
                "SLACK_CHANNEL_IDS": "C123,C456",
                "MFS_ALLOWED_SCOPES": (
                    "slack://tag-t123/channels/general__C123,"
                    "slack://tag-t123/channels/support__C456"
                ),
                "OPENTAG_SLACK_STREAMING": "0",
            },
            clear=True,
        ), patch(
            "scripts.slack_search_scope.resolve_mfs_channel_scope",
            side_effect=lambda channel: channel.scope,
        ), patch.object(
            slack_socket_agent, "WorkingIndicator", return_value=indicator
        ), patch.object(
            slack_socket_agent, "plan_search_scopes", side_effect=tracked_plan
        ), patch.object(
            slack_socket_agent, "build_thread_text", return_value="thread"
        ), patch.object(
            slack_socket_agent, "run_backend", return_value=("done", True)
        ):
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            fake_app.events["app_mention"](
                {
                    "channel": "C123",
                    "ts": "1.23",
                    "user": "UOWNER",
                    "text": "<@BOT> search all channels for launch notes",
                },
                {"team_id": "T123"},
                client,
                MagicMock(),
            )

        self.assertEqual(["plan:current", "indicator", "plan:all"], events[:3])

    def test_explicit_named_scope_reaches_backend_for_claude(self) -> None:
        fake_app = FakeApp()
        client = self.configured_client()
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ,
            {
                "SLACK_BOT_TOKEN": "xoxb-test",
                "SLACK_TEAM_ID": "T123",
                "SLACK_CHANNEL_IDS": "C123,C456",
                "MFS_ALLOWED_SCOPES": (
                    "slack://tag-t123/channels/general__C123,"
                    "slack://tag-t123/channels/old-support__C456"
                ),
                "OPENTAG_SLACK_STREAMING": "0",
                "OPENTAG_CLAUDE_TRANSPORT": "print",
            },
            clear=True,
        ), patch(
            "scripts.slack_search_scope.resolve_mfs_channel_scope",
            side_effect=lambda channel: channel.scope,
        ), patch.object(
            slack_socket_agent, "build_thread_text", return_value="thread"
        ), patch.object(
            slack_socket_agent, "run_backend", return_value=("done", True)
        ) as run_backend:
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            fake_app.events["app_mention"](
                {
                    "channel": "C123",
                    "ts": "1.23",
                    "user": "UOWNER",
                    "text": "<@BOT> search #support for launch notes",
                },
                {"team_id": "T123"},
                client,
                MagicMock(),
            )

        plan = run_backend.call_args.kwargs["scope_plan"]
        grant = run_backend.call_args.kwargs["slack_search_grant"]
        self.assertEqual("current", plan.mode)
        self.assertEqual(("slack://tag-t123/channels/general__C123",), plan.scopes)
        self.assertEqual("all", grant.mode)
        self.assertEqual({"C123": "general", "C456": "support"}, grant.channel_labels)

    def test_natural_all_channel_wording_reaches_agent_with_authorized_grant(self) -> None:
        fake_app = FakeApp()
        client = self.configured_client()
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ,
            {
                "SLACK_BOT_TOKEN": "xoxb-test",
                "SLACK_TEAM_ID": "T123",
                "SLACK_CHANNEL_IDS": "C123,C456",
                "MFS_ALLOWED_SCOPES": (
                    "slack://tag-t123/channels/general__C123,"
                    "slack://tag-t123/channels/support__C456"
                ),
                "OPENTAG_SLACK_STREAMING": "0",
                "OPENTAG_CLAUDE_TRANSPORT": "print",
            },
            clear=True,
        ), patch(
            "scripts.slack_search_scope.resolve_mfs_channel_scope",
            side_effect=lambda channel: channel.scope,
        ), patch.object(
            slack_socket_agent, "build_thread_text", return_value="thread"
        ), patch.object(
            slack_socket_agent, "run_backend", return_value=("done", True)
        ) as run_backend:
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            fake_app.events["app_mention"](
                {
                    "channel": "C123",
                    "ts": "1.23",
                    "user": "UOWNER",
                    "text": "<@BOT> ANYTHING RELATED TO MARKETING ACROSS ALL CHANNELS",
                },
                {"team_id": "T123"},
                client,
                MagicMock(),
            )

        plan = run_backend.call_args.kwargs["scope_plan"]
        grant = run_backend.call_args.kwargs["slack_search_grant"]
        self.assertEqual("current", plan.mode)
        self.assertEqual(("slack://tag-t123/channels/general__C123",), plan.scopes)
        self.assertEqual("all", grant.mode)
        self.assertEqual(
            (
                "slack://tag-t123/channels/general__C123",
                "slack://tag-t123/channels/support__C456",
            ),
            grant.scopes,
        )

    def test_scope_meaning_is_left_to_runtime_agent(self) -> None:
        fake_app = FakeApp()
        client = self.configured_client()
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ,
            {
                "SLACK_BOT_TOKEN": "xoxb-test",
                "SLACK_CHANNEL_IDS": "C123,C456",
                "OPENTAG_SLACK_STREAMING": "0",
                "OPENTAG_CLAUDE_TRANSPORT": "print",
            },
            clear=True,
        ), patch.object(
            slack_socket_agent, "build_thread_text", return_value="thread"
        ), patch.object(
            slack_socket_agent, "run_backend", return_value=("please clarify", True)
        ) as run_backend:
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            fake_app.events["app_mention"](
                {
                    "channel": "C123",
                    "ts": "1.23",
                    "user": "UOWNER",
                    "text": "<@BOT> search general workspace all",
                },
                {"team_id": "T123"},
                client,
                MagicMock(),
            )

        run_backend.assert_called_once()
        self.assertEqual(
            "search general workspace all", run_backend.call_args.args[3]
        )


class SlackAppHomeTests(unittest.TestCase):
    def test_invited_policy_replaces_picker_and_ignores_stale_actions(self):
        fake_app = FakeApp()
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ, {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_POLICY": "invited"}, clear=True
        ), patch.object(slack_socket_agent, "save_home_channels") as save:
            view = slack_socket_agent.app_home_view("C123")
            self.assertNotIn("multi_conversations_select", str(view))
            self.assertIn("automatic channel memory", str(view))
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            fake_app.actions[slack_socket_agent.HOME_CHANNEL_ACTION_ID](
                MagicMock(), {"actions": [{"selected_conversations": ["COTHER"]}], "user": {"id": "UOWNER"}},
                MagicMock(), MagicMock(),
            )
        save.assert_not_called()

    def test_home_uses_native_visual_channel_selector(self) -> None:
        view = slack_socket_agent.app_home_view("C123,G456")

        selector = view["blocks"][1]["accessory"]
        self.assertEqual("multi_conversations_select", selector["type"])
        self.assertEqual(["public", "private"], selector["filter"]["include"])
        self.assertEqual(["C123", "G456"], selector["initial_conversations"])

    def test_authorized_user_can_save_a_joined_channel_from_app_home(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        client.conversations_info.return_value = {"channel": {"is_member": True}}
        ack = MagicMock()
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ, {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "COLD"}, clear=True
        ), patch.object(slack_socket_agent, "save_home_channels") as save:
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            fake_app.actions[slack_socket_agent.HOME_CHANNEL_ACTION_ID](
                ack,
                {"actions": [{"selected_conversations": ["CNEW", "CSECOND"]}], "user": {"id": "UOWNER"}},
                client,
                MagicMock(),
            )

        ack.assert_called_once_with()
        save.assert_called_once_with(["CNEW", "CSECOND"])
        self.assertEqual(["CNEW", "CSECOND"], client.views_publish.call_args.kwargs["view"]["blocks"][1]["accessory"]["initial_conversations"])

    def test_app_home_rejects_channel_until_bot_is_invited(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        client.conversations_info.return_value = {"channel": {"is_member": False}}
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ, {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "COLD"}, clear=True
        ), patch.object(slack_socket_agent, "save_home_channels") as save:
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            fake_app.actions[slack_socket_agent.HOME_CHANNEL_ACTION_ID](
                MagicMock(),
                {"actions": [{"selected_conversations": ["CNEW"]}], "user": {"id": "UOWNER"}},
                client,
                MagicMock(),
            )

        save.assert_not_called()
        published = client.views_publish.call_args.kwargs["view"]
        self.assertIn("Invite the Tag bot", str(published))


class SlackUserAllowlistTests(unittest.TestCase):
    def test_parses_trimmed_unique_user_ids(self) -> None:
        self.assertEqual(
            frozenset({"UOWNER", "UHELPER"}),
            slack_socket_agent.parse_slack_user_ids(" UOWNER, UHELPER, UOWNER, "),
        )

    def test_empty_allowlist_fails_closed(self) -> None:
        with patch.dict(os.environ, {"SLACK_ALLOWED_USER_IDS": ", ,"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "at least one Slack member ID"):
                slack_socket_agent.configured_slack_user_ids()

    def test_missing_event_user_is_not_allowed(self) -> None:
        self.assertFalse(slack_socket_agent.slack_user_allowed("", frozenset({"UOWNER"})))

    def test_configured_owner_is_allowed(self) -> None:
        self.assertTrue(slack_socket_agent.slack_user_allowed("UOWNER", frozenset({"UOWNER"})))

    def test_unauthorized_mention_is_denied_before_work_starts(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        logger = MagicMock()
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ,
            {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C123"},
            clear=True,
        ), patch.object(slack_socket_agent, "build_thread_text") as build_thread_text, patch.object(
            slack_socket_agent, "run_backend"
        ) as run_backend:
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            handler = fake_app.events["app_mention"]
            handler(
                {"channel": "C123", "ts": "1.23", "user": "UOTHER", "text": "<@BOT> hi"},
                {"team_id": "T123"},
                client,
                logger,
            )

        client.chat_postMessage.assert_called_once_with(
            channel="C123",
            thread_ts="1.23",
            text=slack_socket_agent.UNAUTHORIZED_USER_MESSAGE,
        )
        build_thread_text.assert_not_called()
        run_backend.assert_not_called()
        client.assistant_threads_setStatus.assert_not_called()

    def test_unauthorized_user_cannot_open_thread_settings(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        ack = MagicMock()
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ,
            {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C123"},
            clear=True,
        ), patch.object(agent_models, "discover_codex_models", return_value=[]):
            slack_socket_agent.create_app("codex", 30, frozenset({"UOWNER"}))
            handler = fake_app.actions[slack_socket_agent.SETTINGS_ACTION_ID]
            handler(
                ack,
                {
                    "actions": [{"value": json.dumps({"channel": "C123", "thread_ts": "1.23"})}],
                    "trigger_id": "trigger",
                    "user": {"id": "UOTHER"},
                },
                client,
                MagicMock(),
            )

        ack.assert_called_once_with()
        client.views_open.assert_not_called()
        client.chat_postEphemeral.assert_called_once()

    def test_old_configure_button_explains_settings_moved(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        ack = MagicMock()
        metadata = {"team": "T123", "channel": "C123", "thread_ts": "1.23"}
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ,
            {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C123"},
            clear=True,
        ), patch.object(agent_models, "discover_codex_models", return_value=[]):
            slack_socket_agent.create_app("codex", 30, frozenset({"UOWNER"}))
            handler = fake_app.actions[slack_socket_agent.SETTINGS_ACTION_ID]
            handler(
                ack,
                {
                    "actions": [{"value": json.dumps(metadata)}],
                    "trigger_id": "trigger",
                    "user": {"id": "UOWNER"},
                },
                client,
                MagicMock(),
            )

        ack.assert_called_once_with()
        client.views_open.assert_called_once()
        self.assertEqual("trigger", client.views_open.call_args.kwargs["trigger_id"])
        view = client.views_open.call_args.kwargs["view"]
        self.assertEqual("Model settings moved", view["title"]["text"])
        self.assertNotIn("submit", view)
        self.assertIn("|Tag app> → Details", view["blocks"][0]["text"]["text"])
        self.assertNotIn("Tag.app", view["blocks"][0]["text"]["text"])

class SlackDirectMessageTests(unittest.TestCase):
    def test_direct_messages_default_on_and_can_be_disabled(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(slack_socket_agent.direct_messages_enabled())
        with patch.dict(os.environ, {"OPENTAG_SLACK_DM_ENABLED": "0"}, clear=True):
            self.assertFalse(slack_socket_agent.direct_messages_enabled())

    def test_disabled_direct_message_is_ignored(self) -> None:
        fake_app = FakeApp()
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ,
            {
                "SLACK_BOT_TOKEN": "xoxb-test",
                "OPENTAG_SLACK_DM_ENABLED": "0",
            },
            clear=True,
        ), patch.object(slack_socket_agent, "run_backend") as run_backend:
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            fake_app.events["message"](
                {
                    "channel": "D123",
                    "channel_type": "im",
                    "ts": "1.00",
                    "user": "UOWNER",
                    "text": "hello",
                },
                {"team_id": "T123"},
                MagicMock(),
                MagicMock(),
            )

        run_backend.assert_not_called()

    def test_top_level_messages_start_fresh_tasks_and_replies_reuse_the_root(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ,
            {
                "SLACK_BOT_TOKEN": "xoxb-test",
                "SLACK_CHANNEL_ID": "C-SANDBOX",
                "OPENTAG_SLACK_DM_ENABLED": "1",
                "OPENTAG_SLACK_STREAMING": "0",
                "OPENTAG_CLAUDE_TRANSPORT": "print",
            },
            clear=True,
        ), patch.object(
            slack_socket_agent, "build_thread_text", return_value="bounded context"
        ) as build_thread_text, patch.object(
            slack_socket_agent, "run_backend", return_value=("done", True)
        ) as run_backend:
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            handler = fake_app.events["message"]
            for event in (
                {"ts": "1.00", "text": "first task"},
                {"ts": "1.01", "thread_ts": "1.00", "text": "follow up"},
                {"ts": "2.00", "text": "second task"},
            ):
                handler(
                    {
                        "channel": "D123",
                        "channel_type": "im",
                        "user": "UOWNER",
                        **event,
                    },
                    {"team_id": "T123"},
                    client,
                    MagicMock(),
                )

        self.assertEqual(
            ["1.00", "1.00", "2.00"],
            [call.args[2] for call in build_thread_text.call_args_list],
        )
        self.assertEqual(
            ["first task", "follow up", "second task"],
            [call.args[3] for call in run_backend.call_args_list],
        )

    def test_unauthorized_direct_message_is_denied_before_thread_read(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ,
            {
                "SLACK_BOT_TOKEN": "xoxb-test",
                "OPENTAG_SLACK_DM_ENABLED": "1",
            },
            clear=True,
        ), patch.object(slack_socket_agent, "build_thread_text") as build_thread_text:
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            fake_app.events["message"](
                {
                    "channel": "D123",
                    "channel_type": "im",
                    "ts": "1.00",
                    "user": "UOTHER",
                    "text": "hello",
                },
                {"team_id": "T123"},
                client,
                MagicMock(),
            )

        client.chat_postMessage.assert_called_once_with(
            channel="D123",
            thread_ts="1.00",
            text=slack_socket_agent.UNAUTHORIZED_USER_MESSAGE,
        )
        build_thread_text.assert_not_called()

    def test_bot_and_non_dm_message_events_are_ignored(self) -> None:
        fake_app = FakeApp()
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ,
            {
                "SLACK_BOT_TOKEN": "xoxb-test",
                "OPENTAG_SLACK_DM_ENABLED": "1",
            },
            clear=True,
        ), patch.object(slack_socket_agent, "run_backend") as run_backend:
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            handler = fake_app.events["message"]
            handler(
                {
                    "channel": "D123",
                    "channel_type": "im",
                    "ts": "1.00",
                    "bot_id": "B123",
                    "text": "bot reply",
                },
                {},
                MagicMock(),
                MagicMock(),
            )
            handler(
                {
                    "channel": "C123",
                    "channel_type": "channel",
                    "ts": "2.00",
                    "user": "UOWNER",
                    "text": "ordinary channel message",
                },
                {},
                MagicMock(),
                MagicMock(),
            )

        run_backend.assert_not_called()


class SlackWorkingIndicatorTests(unittest.TestCase):
    def test_journals_native_session_until_clear_succeeds(self) -> None:
        client = MagicMock()
        journal = MagicMock()
        indicator = slack_socket_agent.WorkingIndicator(
            client,
            "C123",
            "1.23",
            MagicMock(),
            journal=journal,
            team="T123",
        )

        with patch("scripts.slack_socket_agent.threading.Timer"):
            indicator.start()
            indicator.clear()

        self.assertEqual(2, journal.add.call_count)
        journal.add.assert_called_with(
            "T123",
            "C123",
            "1.23",
            pending_session_api=True,
            pending_legacy_status=True,
        )
        journal.set_pending.assert_called_once_with(
            "T123",
            "C123",
            "1.23",
            pending_session_api=False,
            pending_legacy_status=False,
        )

    def test_backend_retry_status_replaces_generic_working_copy(self) -> None:
        client = MagicMock()
        indicator = slack_socket_agent.WorkingIndicator(client, "C123", "1.23", MagicMock())
        indicator.native = indicator.legacy_status = True

        indicator.status("Backend busy — retrying (2/3)…")

        self.assertEqual(
            "Backend busy — retrying (2/3)…",
            client.assistant_threads_setStatus.call_args.kwargs["status"],
        )

    def test_uses_native_slack_loading_status(self) -> None:
        client = MagicMock()
        indicator = slack_socket_agent.WorkingIndicator(client, "C123", "1.23", MagicMock())

        with patch("scripts.slack_socket_agent.threading.Timer") as timer:
            indicator.start()
            indicator.clear()

        first_call = client.assistant_threads_setStatus.call_args_list[0].kwargs
        self.assertEqual("is working on this…", first_call["status"])
        self.assertEqual(
            "",
            client.assistant_threads_setStatus.call_args_list[-1].kwargs["status"],
        )
        self.assertEqual(slack_socket_agent.LOADING_MESSAGES, first_call["loading_messages"])
        self.assertEqual(
            ["processing", "active"],
            [call.kwargs["json"]["status"] for call in client.api_call.call_args_list],
        )
        timer.assert_called_once_with(
            slack_socket_agent.STATUS_REFRESH_SECONDS,
            indicator.refresh,
        )
        timer.return_value.start.assert_called_once()
        timer.return_value.cancel.assert_called_once()
        client.chat_postMessage.assert_not_called()

    def test_retries_terminal_status_after_transient_slack_failure(self) -> None:
        client = MagicMock()
        client.api_call.side_effect = RuntimeError("temporary network failure")
        client.assistant_threads_setStatus.side_effect = RuntimeError(
            "temporary network failure"
        )
        journal = MagicMock()
        indicator = slack_socket_agent.WorkingIndicator(
            client,
            "C123",
            "1.23",
            MagicMock(),
            journal=journal,
            team="T123",
        )
        indicator.native = indicator.session_api = indicator.legacy_status = True

        with patch("scripts.slack_socket_agent.threading.Timer") as timer:
            indicator.clear()
            retry = timer.call_args.args[1]

        self.assertEqual(
            slack_socket_agent.STATUS_CLEANUP_RETRY_DELAYS[0],
            timer.call_args.args[0],
        )
        journal.set_pending.assert_called_once_with(
            "T123",
            "C123",
            "1.23",
            pending_session_api=True,
            pending_legacy_status=True,
        )

        client.api_call.side_effect = None
        client.assistant_threads_setStatus.side_effect = None
        retry()

        client.api_call.assert_called_with(
            "agents.sessions.setStatus",
            json={"channel_id": "C123", "thread_ts": "1.23", "status": "active"},
        )
        client.assistant_threads_setStatus.assert_called_with(
            channel_id="C123", thread_ts="1.23", status=""
        )
        journal.set_pending.assert_called_with(
            "T123",
            "C123",
            "1.23",
            pending_session_api=False,
            pending_legacy_status=False,
        )

    def test_refresh_failure_does_not_discard_cleanup_obligation(self) -> None:
        client = MagicMock()
        client.api_call.side_effect = [
            None,
            RuntimeError("temporary refresh failure"),
            None,
        ]
        journal = MagicMock()
        indicator = slack_socket_agent.WorkingIndicator(
            client,
            "C123",
            "1.23",
            MagicMock(),
            journal=journal,
            team="T123",
        )

        with patch("scripts.slack_socket_agent.threading.Timer"):
            indicator.start()
            indicator.refresh()
            self.assertFalse(indicator.session_api)
            self.assertTrue(indicator.cleanup_session_api)
            indicator.clear()

        self.assertEqual(
            ["processing", "processing", "active"],
            [call.kwargs["json"]["status"] for call in client.api_call.call_args_list],
        )
        journal.set_pending.assert_called_once_with(
            "T123",
            "C123",
            "1.23",
            pending_session_api=False,
            pending_legacy_status=False,
        )

    def test_progress_message_changes_when_custom_status_is_unavailable(self) -> None:
        client = MagicMock()
        client.assistant_threads_setStatus.side_effect = RuntimeError(
            "unsupported for this thread"
        )
        client.chat_postMessage.return_value = {"ts": "2.34"}
        indicator = slack_socket_agent.WorkingIndicator(
            client, "C123", "1.23", MagicMock()
        )

        with patch("scripts.slack_socket_agent.threading.Timer"):
            indicator.start()
            indicator.activity(
                "activity_start", "search", "Searching workspace history…"
            )
            indicator.flush_activity()

        self.assertTrue(indicator.session_api)
        self.assertFalse(indicator.legacy_status)
        client.chat_postMessage.assert_called_once_with(
            channel="C123", thread_ts="1.23", text="Working on this…"
        )
        client.chat_update.assert_called_once_with(
            channel="C123", ts="2.34", text="Searching workspace history…"
        )


    def test_falls_back_to_temporary_message_when_native_status_fails(self) -> None:
        client = MagicMock()
        client.api_call.side_effect = RuntimeError("unsupported")
        client.assistant_threads_setStatus.side_effect = RuntimeError("unsupported")
        client.chat_postMessage.return_value = {"ts": "2.34"}
        indicator = slack_socket_agent.WorkingIndicator(client, "C123", "1.23", MagicMock())

        indicator.start()
        indicator.clear()

        self.assertFalse(indicator.native)
        self.assertEqual("2.34", indicator.message_ts)
        client.chat_postMessage.assert_called_once()
        client.assistant_threads_setStatus.assert_called_once()

    def test_activity_status_counts_concurrent_tools(self) -> None:
        client = MagicMock()
        indicator = slack_socket_agent.WorkingIndicator(client, "C123", "1.23", MagicMock())
        indicator.native = True
        indicator.legacy_status = True

        with patch("scripts.slack_socket_agent.threading.Timer") as timer:
            indicator.activity("activity_start", "one", "Searching the web…")
            indicator.activity("activity_start", "two", "Running a command…")
            timer.assert_called_once()
        indicator.flush_activity()

        self.assertEqual(
            "Running a command… (+1 other active)",
            client.assistant_threads_setStatus.call_args.kwargs["status"],
        )
        with patch("scripts.slack_socket_agent.threading.Timer"):
            indicator.activity("activity_complete", "two", "Running a command…")
        indicator.flush_activity()
        self.assertEqual(
            "Searching the web…",
            client.assistant_threads_setStatus.call_args.kwargs["status"],
        )

    def test_activity_hold_wait_and_answer_transition(self) -> None:
        client = MagicMock()
        indicator = slack_socket_agent.WorkingIndicator(client, "C123", "1.23", MagicMock())
        indicator.native = indicator.legacy_status = True
        with patch("scripts.slack_socket_agent.time.monotonic", return_value=100) as now, patch(
            "scripts.slack_socket_agent.threading.Timer"
        ) as timer:
            indicator.set_native_status()
            indicator.activity("activity_start", "one", "Searching GitHub issues…", "Still waiting for GitHub…")
            self.assertEqual(1.5, timer.call_args.args[0])
            now.return_value = 101.5
            indicator.flush_activity()
            self.assertEqual("Searching GitHub issues…", client.assistant_threads_setStatus.call_args.kwargs["status"])
            self.assertEqual(10.5, timer.call_args.args[0])
            now.return_value = 112
            indicator.flush_activity()
            self.assertEqual("Still waiting for GitHub…", client.assistant_threads_setStatus.call_args.kwargs["status"])
            indicator.activity("activity_complete", "one", "Searching GitHub issues…")
            now.return_value = 114
            indicator.flush_activity()
            self.assertEqual("is working on this…", client.assistant_threads_setStatus.call_args.kwargs["status"])
            indicator.answer_started()
            now.return_value = 116
            indicator.flush_activity()
            self.assertEqual("Preparing your answer…", client.assistant_threads_setStatus.call_args.kwargs["status"])
            indicator.clear(complete_session=False)
            calls = client.assistant_threads_setStatus.call_count
            indicator.flush_activity()
            self.assertEqual(calls, client.assistant_threads_setStatus.call_count)

    def test_short_activity_is_coalesced_without_stale_wait(self) -> None:
        client = MagicMock()
        indicator = slack_socket_agent.WorkingIndicator(client, "C123", "1.23", MagicMock())
        indicator.native = indicator.legacy_status = True
        with patch("scripts.slack_socket_agent.time.monotonic", return_value=100) as now, patch(
            "scripts.slack_socket_agent.threading.Timer"
        ) as timer:
            indicator.set_native_status()
            indicator.activity("activity_start", "one", "Searching GitHub issues…")
            indicator.activity("activity_complete", "one", "Searching GitHub issues…")
            timer.assert_called_once()
            now.return_value = 102
            indicator.flush_activity()
            client.assistant_threads_setStatus.assert_called_once()
            self.assertIsNone(indicator.activity_timer)

    def test_stream_takeover_stops_refresh_without_completing_session(self) -> None:
        client = MagicMock()
        indicator = slack_socket_agent.WorkingIndicator(client, "C123", "1.23", MagicMock())

        with patch("scripts.slack_socket_agent.threading.Timer"):
            indicator.start()
            indicator.clear(complete_session=False)

        self.assertFalse(indicator.native)
        self.assertTrue(indicator.session_api)
        self.assertEqual(
            ["processing"],
            [call.kwargs["json"]["status"] for call in client.api_call.call_args_list],
        )

        indicator.clear()
        self.assertEqual("active", client.api_call.call_args.kwargs["json"]["status"])


class SlackSessionJournalTests(unittest.TestCase):
    def test_sigterm_requests_graceful_cleanup(self) -> None:
        shutdown_requested = slack_socket_agent.threading.Event()
        handlers: dict[signal.Signals, object] = {}

        def capture(sig, handler):
            handlers[sig] = handler

        with patch("scripts.slack_socket_agent.signal.signal", side_effect=capture):
            slack_socket_agent.install_shutdown_handlers(shutdown_requested)

        handlers[signal.SIGTERM](signal.SIGTERM, None)
        self.assertTrue(shutdown_requested.is_set())

    def test_reconciles_session_left_by_a_crashed_process(self) -> None:
        client = MagicMock()
        logger = MagicMock()
        with tempfile.TemporaryDirectory() as raw_dir:
            path = Path(raw_dir) / "active-sessions.json"
            slack_socket_agent.SlackSessionJournal(path).add("T1", "C1", "1.23")

            journal = slack_socket_agent.SlackSessionJournal(path)
            self.assertEqual(1, journal.reconcile(client, logger))
            self.assertFalse(path.exists())

        client.api_call.assert_called_once_with(
            "agents.sessions.setStatus",
            json={"channel_id": "C1", "thread_ts": "1.23", "status": "active"},
        )
        client.assistant_threads_setStatus.assert_called_once_with(
            channel_id="C1", thread_ts="1.23", status=""
        )

    def test_retains_session_when_both_cleanup_apis_fail(self) -> None:
        client = MagicMock()
        client.api_call.side_effect = RuntimeError("unavailable")
        client.assistant_threads_setStatus.side_effect = RuntimeError("unavailable")
        with tempfile.TemporaryDirectory() as raw_dir:
            path = Path(raw_dir) / "active-sessions.json"
            journal = slack_socket_agent.SlackSessionJournal(path)
            journal.add("T1", "C1", "1.23")

            self.assertEqual(0, journal.reconcile(client, MagicMock()))
            self.assertTrue(path.exists())

    def test_reconcile_persists_and_retries_only_the_failed_cleanup(self) -> None:
        client = MagicMock()
        client.api_call.side_effect = RuntimeError("session API unavailable")
        with tempfile.TemporaryDirectory() as raw_dir:
            path = Path(raw_dir) / "active-sessions.json"
            journal = slack_socket_agent.SlackSessionJournal(path)
            journal.add("T1", "C1", "1.23")

            self.assertEqual(0, journal.reconcile(client, MagicMock()))
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    "pending_session_api": True,
                    "pending_legacy_status": False,
                },
                {
                    key: persisted["T1:C1:1.23"][key]
                    for key in ("pending_session_api", "pending_legacy_status")
                },
            )

            client.api_call.side_effect = None
            recovered = slack_socket_agent.SlackSessionJournal(path)
            self.assertEqual(1, recovered.reconcile(client, MagicMock()))
            self.assertFalse(path.exists())

        self.assertEqual(2, client.api_call.call_count)
        client.assistant_threads_setStatus.assert_called_once_with(
            channel_id="C1", thread_ts="1.23", status=""
        )

    def test_legacy_journal_entries_retry_both_cleanup_operations(self) -> None:
        client = MagicMock()
        with tempfile.TemporaryDirectory() as raw_dir:
            path = Path(raw_dir) / "active-sessions.json"
            path.write_text(
                json.dumps(
                    {
                        "T1:C1:1.23": {
                            "team": "T1",
                            "channel": "C1",
                            "thread_ts": "1.23",
                        }
                    }
                ),
                encoding="utf-8",
            )

            journal = slack_socket_agent.SlackSessionJournal(path)
            self.assertEqual(1, journal.reconcile(client, MagicMock()))
            self.assertFalse(path.exists())

        client.api_call.assert_called_once()
        client.assistant_threads_setStatus.assert_called_once()


class SlackBridgeReadinessTests(unittest.TestCase):
    def test_invitation_failure_withholds_socket_readiness(self) -> None:
        app = MagicMock()
        handler = MagicMock()
        handler.client.is_connected.return_value = True
        invitation_memory = MagicMock()
        invitation_memory.ready_for_requests.return_value = False
        shutdown_requested = MagicMock()
        shutdown_requested.is_set.side_effect = [False, True]
        journal = MagicMock()

        with tempfile.TemporaryDirectory() as raw_dir:
            ready_file = Path(raw_dir) / "slack.ready"

            def assert_not_ready(_seconds: float) -> None:
                self.assertFalse(ready_file.exists())

            with patch.dict(
                os.environ,
                {
                    "SLACK_ALLOWED_USER_IDS": "UOWNER",
                    "SLACK_APP_TOKEN": "xapp-fixture",
                    "SLACK_CHANNEL_POLICY": "invited",
                },
                clear=True,
            ), patch(
                "scripts.slack_socket_agent.sys.argv",
                [
                    "slack_socket_agent.py",
                    "--backend",
                    "codex",
                    "--ready-file",
                    str(ready_file),
                    "--process-id",
                    "fixture-process",
                ],
            ), patch.object(
                slack_socket_agent, "create_app", return_value=app
            ), patch.object(
                slack_socket_agent, "print_live_summary"
            ), patch.object(
                slack_socket_agent, "SocketModeHandler", return_value=handler
            ), patch.object(
                slack_socket_agent, "SlackSessionJournal", return_value=journal
            ), patch.object(
                slack_socket_agent, "install_shutdown_handlers"
            ), patch.object(
                slack_socket_agent.threading, "Event", return_value=shutdown_requested
            ), patch.object(
                slack_socket_agent.time, "sleep", side_effect=assert_not_ready
            ), patch(
                "scripts.slack_invitation_memory.InvitationMemory",
                return_value=invitation_memory,
            ), patch(
                "scripts.tag_paths.instance_home", return_value=Path(raw_dir)
            ):
                slack_socket_agent.main()

        invitation_memory.start.assert_called_once_with()
        invitation_memory.stop.assert_called_once_with()


class SocketConnectionTests(unittest.TestCase):
    """A client that stays disconnected, as after sleeping on another network, is replaced."""

    class InlineThread:
        def __init__(self, target, **_kwargs) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

    def connection(self, *handlers: MagicMock) -> tuple[Any, list[float]]:
        now = [0.0]
        opened = iter(handlers)
        connection = slack_socket_agent.SocketConnection(
            lambda: next(opened), recovery_seconds=60, clock=lambda: now[0]
        )
        return connection, now

    @staticmethod
    def handler(connected: bool) -> MagicMock:
        handler = MagicMock()
        handler.client.is_connected.return_value = connected
        return handler

    def test_connected_client_is_kept(self) -> None:
        current = self.handler(True)
        connection, now = self.connection(current)
        for now[0] in (0, 120, 600):
            self.assertTrue(connection.is_connected())
        self.assertIs(connection.handler, current)
        current.close.assert_not_called()

    def test_client_that_stays_down_is_replaced_after_the_grace_period(self) -> None:
        stuck, fresh = self.handler(False), self.handler(True)
        connection, now = self.connection(stuck, fresh)
        with patch.object(slack_socket_agent.threading, "Thread", self.InlineThread), \
                redirect_stdout(io.StringIO()) as output:
            self.assertFalse(connection.is_connected())
            now[0] = 59
            self.assertFalse(connection.is_connected())
            self.assertIs(connection.handler, stuck)
            now[0] = 60
            self.assertTrue(connection.is_connected())

        self.assertIs(connection.handler, fresh)
        fresh.connect.assert_called_once_with()
        stuck.close.assert_called_once_with()
        self.assertIn("replaced it with a fresh one", output.getvalue())

    def test_client_that_recovers_by_itself_restarts_the_grace_period(self) -> None:
        flaky = self.handler(False)
        connection, now = self.connection(flaky)
        self.assertFalse(connection.is_connected())
        now[0] = 50
        flaky.client.is_connected.return_value = True
        self.assertTrue(connection.is_connected())
        flaky.client.is_connected.return_value = False
        now[0] = 100
        self.assertFalse(connection.is_connected())
        now[0] = 159
        self.assertFalse(connection.is_connected())
        self.assertIs(connection.handler, flaky)

    def test_unreachable_slack_keeps_the_current_client_and_retries_later(self) -> None:
        stuck = self.handler(False)
        offline, fresh = self.handler(False), self.handler(True)
        offline.connect.side_effect = OSError("nodename nor servname provided")
        connection, now = self.connection(stuck, offline, fresh)
        with patch.object(slack_socket_agent.threading, "Thread", self.InlineThread), \
                redirect_stdout(io.StringIO()) as output:
            connection.is_connected()
            now[0] = 60
            self.assertFalse(connection.is_connected())
            self.assertIs(connection.handler, stuck)
            offline.close.assert_called_once_with()
            stuck.close.assert_not_called()
            now[0] = 119
            self.assertFalse(connection.is_connected())
            now[0] = 120
            self.assertTrue(connection.is_connected())

        self.assertIs(connection.handler, fresh)
        stuck.close.assert_called_once_with()
        self.assertIn("still unreachable", output.getvalue())

    def test_close_closes_the_current_client(self) -> None:
        current = self.handler(True)
        connection, _now = self.connection(current)
        connection.close()
        current.close.assert_called_once_with()


class SlackAnswerStreamTests(unittest.TestCase):
    def test_activity_starts_grouped_plan_and_final_answer_uses_same_message(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )
        start = {"type": "activity_start", "activity_id": "item-1", "label": "Searching the web…"}
        stream.activity(start)
        stream.activity({**start, "type": "activity_complete", "status": "completed"})

        self.assertTrue(stream.finish("Here is the answer"))
        start_args = client.chat_startStream.call_args.kwargs
        self.assertEqual(start_args["task_display_mode"], "plan")
        self.assertEqual(start_args["chunks"][0], {"type": "plan_update", "title": "Agent activity"})
        self.assertEqual(start_args["chunks"][1]["status"], "in_progress")
        self.assertEqual(client.chat_appendStream.call_args_list[0].kwargs["chunks"][0]["status"], "complete")
        self.assertEqual(client.chat_appendStream.call_args_list[1].kwargs["chunks"], [
            {"type": "markdown_text", "text": "Here is the answer"},
        ])
        client.chat_stopStream.assert_called_once_with(
            channel="C123", ts="3.45",
            chunks=[{"type": "plan_update", "title": "Agent activity"}],
        )

    def test_activity_rejects_raw_labels_and_missing_completion(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )
        stream.activity({"type": "activity_complete", "activity_id": "unknown", "label": "secret"})
        client.chat_startStream.assert_not_called()
        stream.activity({"type": "activity_start", "activity_id": "item-1", "label": "secret path"})
        self.assertEqual(client.chat_startStream.call_args.kwargs["chunks"][1]["title"], "Using a connected tool")
        self.assertTrue(stream.finish("Done"))
        self.assertEqual(client.chat_appendStream.call_args_list[0].kwargs["chunks"][0]["status"], "complete")

    def test_shared_task_chunks_show_short_identity_but_not_inputs_or_results(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )
        stream.activity({
            "type": "activity_start", "activity_id": "item-1", "label": "Reading files…",
            "details": {"tool": "cat secret-plan.md", "input": "private input"},
        })
        stream.activity({
            "type": "activity_complete", "activity_id": "item-1", "label": "Reading files…",
            "status": "completed", "details": {"output": "private result"},
        })
        stream.finish("Done")

        calls = json.dumps([
            call.kwargs for call in client.chat_startStream.call_args_list
            + client.chat_appendStream.call_args_list + client.chat_stopStream.call_args_list
        ])
        self.assertIn("Reading secret-plan.md", calls)
        self.assertNotIn("private input", calls)
        self.assertNotIn("private result", calls)

    def test_task_start_failure_does_not_prevent_answer_stream(self) -> None:
        client = MagicMock()
        client.chat_startStream.side_effect = [RuntimeError("unsupported"), {"ts": "3.45"}]
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )
        stream.activity({"type": "activity_start", "activity_id": "item-1", "label": "Searching the web…"})
        self.assertFalse(stream.failed)
        stream.append("a" * slack_socket_agent.STREAM_START_CHARS)
        self.assertTrue(stream.finish(stream.received))
        self.assertEqual(client.chat_startStream.call_count, 2)

    def test_activity_after_text_stream_does_not_switch_modes(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )
        stream.append("a" * slack_socket_agent.STREAM_START_CHARS)
        stream.activity({"type": "activity_start", "activity_id": "item-1", "label": "Reading files…"})

        self.assertFalse(stream.task_updates_available)
        self.assertEqual(client.chat_startStream.call_count, 1)
        self.assertTrue(stream.finish(stream.received))
        client.chat_stopStream.assert_called_once_with(channel="C123", ts="3.45")

    def test_repeated_activity_uses_one_counted_card(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )
        for index in range(8):
            event = {"type": "activity_start", "activity_id": f"item-{index}",
                     "label": "Running a command…"}
            stream.activity(event)
            stream.activity({**event, "type": "activity_complete", "status": "completed"})

        self.assertTrue(stream.finish("Done"))
        self.assertEqual(len(stream.task_groups), 1)
        self.assertEqual(client.chat_appendStream.call_args_list[-2].kwargs["chunks"], [
            {"type": "task_update", "id": next(iter(stream.task_groups.values()))["id"],
             "title": "Ran a command · 8 steps", "status": "complete"},
        ])

    def test_mixed_activity_has_one_card_per_safe_label(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )
        for index, label in enumerate((
            "Reading files…", "Running a command…", "Running a command…",
            "Reading files…", "Running a command…",
        )):
            event = {"type": "activity_start", "activity_id": f"item-{index}", "label": label}
            stream.activity(event)
            stream.activity({**event, "type": "activity_complete", "status": "completed"})

        self.assertTrue(stream.finish("Done"))
        self.assertEqual(len(stream.task_groups), 2)
        self.assertEqual([chunk["title"] for chunk in client.chat_appendStream.call_args_list[-2].kwargs["chunks"]], [
            "Read files · 2 steps", "Ran a command · 3 steps",
        ])

    def test_task_groups_are_bounded(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )
        labels = list(slack_socket_agent.PUBLIC_LABELS)[:slack_socket_agent.MAX_STREAM_TASKS + 1]
        for index, label in enumerate(labels):
            stream.activity({"type": "activity_start", "activity_id": f"item-{index}", "label": label})

        self.assertEqual(len(stream.task_groups), slack_socket_agent.MAX_STREAM_TASKS)
        self.assertEqual(client.chat_appendStream.call_count, len(labels) - 1)
        self.assertTrue(client.chat_appendStream.call_args.kwargs["chunks"][-1]["title"].startswith("More steps · "))

    def test_live_rows_distinguish_commands_and_update_file_change_names(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )
        examples = (("cat runtime-agent.md", "Reading runtime-agent.md"),
                    ("cat SKILL.md", "Reading SKILL.md"),
                    ("python script.py", "Running script.py"),
                    ("File change", "Updating files"))
        for index, (tool, title) in enumerate(examples):
            event = {"type": "activity_start", "activity_id": f"item-{index}",
                     "label": "Running a command…", "details": {"tool": tool}}
            stream.activity(event)
            chunks = (client.chat_startStream.call_args.kwargs["chunks"] if index == 0
                      else client.chat_appendStream.call_args.kwargs["chunks"])
            self.assertEqual(chunks[-1]["title"], title)
            self.assertEqual(chunks[-1]["status"], "in_progress")
            if index:
                self.assertEqual(chunks[0]["status"], "complete")
            if tool == "File change":
                event["details"] = {"tool": "File change · app.py"}
            stream.activity({**event, "type": "activity_complete", "status": "completed"})

        stream.finish("Done")
        self.assertEqual(len(stream.task_groups), 4)
        final_cards = client.chat_appendStream.call_args_list[-2].kwargs["chunks"]
        self.assertEqual(final_cards[-1]["title"], "Updated app.py")
        self.assertTrue(all(card["status"] == "complete" for card in final_cards))

    def test_live_tool_names_escape_mentions_and_redact_credentials(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )
        stream.activity({"type": "activity_start", "activity_id": "item-1",
                         "label": "Running a command…",
                         "details": {"tool": "cat <@U123> token=secret-value"}})
        title = client.chat_startStream.call_args.kwargs["chunks"][-1]["title"]
        self.assertNotIn("<@U123>", title)
        self.assertNotIn("secret-value", title)
        stream.abort()

    def test_successful_run_closes_activity_complete_after_tool_level_errors(self) -> None:
        for outcome, prefix, status in (("completed", "Read app.py", "complete"),
                                         ("failed", "Failed · Reading app.py", "error"),
                                         ("declined", "Declined · Reading app.py", "error"),
                                         ("interrupted", "Stopped · Reading app.py", "error")):
            with self.subTest(outcome=outcome):
                client = MagicMock()
                client.chat_startStream.return_value = {"ts": "3.45"}
                stream = slack_socket_agent.SlackAnswerStream(client, "C123", "1.23", "U123", "T123", MagicMock())
                event = {"type": "activity_start", "activity_id": "item-1", "label": "Reading files…",
                         "details": {"tool": "cat app.py"}}
                stream.activity(event)
                stream.activity({**event, "type": "activity_complete", "status": outcome})
                stream.activity({"type": "activity_start", "activity_id": "item-2",
                                 "label": "Running a command…", "details": {"tool": "git status"}})
                live_card = client.chat_appendStream.call_args.kwargs["chunks"][0]
                self.assertEqual((live_card["title"], live_card["status"]), (prefix, status))
                stream.finish("Done")
                cards = client.chat_appendStream.call_args_list[-2].kwargs["chunks"]
                self.assertTrue(all(card["status"] == "complete" for card in cards))
                self.assertEqual(cards[0]["title"], "Read app.py" if outcome == "completed" else "Finished · Reading app.py")

    def test_aborting_stream_closes_active_cards_without_claiming_completion(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        stream = slack_socket_agent.SlackAnswerStream(client, "C123", "1.23", "U123", "T123", MagicMock())
        stream.activity({"type": "activity_start", "activity_id": "item-1", "label": "Reading files…",
                         "details": {"tool": "cat app.py"}})
        stream.abort()
        card = client.chat_stopStream.call_args.kwargs["chunks"][-1]
        self.assertEqual(card["title"], "Stopped · Reading app.py")
        self.assertEqual(card["status"], "error")

    def test_successful_run_closes_missing_completion_and_repeated_steps_resume_running(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        stream = slack_socket_agent.SlackAnswerStream(client, "C123", "1.23", "U123", "T123", MagicMock())
        event = {"type": "activity_start", "activity_id": "item-1", "label": "Reading files…",
                 "details": {"tool": "cat app.py"}}
        stream.activity(event)
        stream.activity({**event, "type": "activity_complete", "status": "completed"})
        stream.activity({**event, "activity_id": "item-2"})
        card = client.chat_appendStream.call_args.kwargs["chunks"][-1]
        self.assertEqual(card["title"], "Reading app.py · 2 steps")
        self.assertEqual(card["status"], "in_progress")
        stream.finish("Done")
        card = client.chat_appendStream.call_args_list[-2].kwargs["chunks"][0]
        self.assertEqual(card["title"], "Finished · Reading app.py · 2 steps")
        self.assertEqual(card["status"], "complete")

    def test_batches_deltas_and_finishes_stream(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )

        stream.append("a" * slack_socket_agent.STREAM_START_CHARS)
        stream.append("b" * slack_socket_agent.STREAM_APPEND_CHARS)
        finished = stream.finish(stream.received)

        self.assertTrue(finished)
        client.chat_startStream.assert_called_once()
        self.assertEqual(
            ("U123", "T123"),
            (
                client.chat_startStream.call_args.kwargs["recipient_user_id"],
                client.chat_startStream.call_args.kwargs["recipient_team_id"],
            ),
        )
        client.chat_appendStream.assert_called_once()
        client.chat_stopStream.assert_called_once_with(channel="C123", ts="3.45")

    def test_no_deltas_uses_normal_message_fallback(self) -> None:
        client = MagicMock()
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )

        self.assertFalse(stream.finish("Complete Codex answer"))
        client.chat_startStream.assert_not_called()

    def test_start_failure_preserves_complete_answer_fallback(self) -> None:
        client = MagicMock()
        client.chat_startStream.side_effect = RuntimeError("not supported")
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )

        stream.append("a" * slack_socket_agent.STREAM_START_CHARS)

        self.assertTrue(stream.failed)
        self.assertFalse(stream.finish(stream.received))

    def test_append_failure_replaces_partial_stream_instead_of_duplicating(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        client.chat_appendStream.side_effect = RuntimeError("temporary append failure")
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )
        stream.append("a" * slack_socket_agent.STREAM_START_CHARS)
        stream.append("b" * slack_socket_agent.STREAM_APPEND_CHARS)

        self.assertTrue(stream.failed)
        self.assertTrue(stream.finish(stream.received))
        client.chat_stopStream.assert_called_once_with(
            channel="C123",
            ts="3.45",
            markdown_text=("a" * slack_socket_agent.STREAM_START_CHARS)
            + ("b" * slack_socket_agent.STREAM_APPEND_CHARS),
        )

    def test_finishes_stream_with_settings_blocks(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        blocks = [{"type": "actions", "elements": []}]
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )

        stream.append("a" * slack_socket_agent.STREAM_START_CHARS)

        self.assertTrue(stream.finish(stream.received, blocks))
        client.chat_stopStream.assert_called_once_with(channel="C123", ts="3.45", blocks=blocks)

    def test_short_delta_flushes_on_time_threshold(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        started = MagicMock()
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock(), on_start=started
        )

        stream.append("short")
        stream.flush()

        client.chat_startStream.assert_called_once()
        started.assert_called_once_with()

    def test_authoritative_final_can_replace_divergent_deltas(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )
        stream.append("a" * slack_socket_agent.STREAM_START_CHARS)

        self.assertTrue(stream.finish("Corrected final"))
        client.chat_stopStream.assert_called_once_with(
            channel="C123", ts="3.45", markdown_text="Corrected final"
        )

    def test_chunk_stream_recovery_uses_chunks(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )
        stream.activity({"type": "activity_start", "activity_id": "item-1", "label": "Reading files…"})
        client.chat_appendStream.side_effect = RuntimeError("temporary failure")
        stream.append("a" * slack_socket_agent.STREAM_APPEND_CHARS)

        self.assertTrue(stream.failed)
        self.assertTrue(stream.finish(stream.received))
        client.chat_stopStream.assert_called_once()
        chunks = client.chat_stopStream.call_args.kwargs["chunks"]
        self.assertEqual(chunks[-1], {"type": "markdown_text", "text": stream.received})
        self.assertEqual(chunks[0]["status"], "complete")

    def test_chunk_stream_replaces_divergent_deltas_with_chunks(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        stream = slack_socket_agent.SlackAnswerStream(
            client, "C123", "1.23", "U123", "T123", MagicMock()
        )
        stream.activity({"type": "activity_start", "activity_id": "item-1", "label": "Reading files…"})
        stream.append("partial")

        self.assertTrue(stream.finish("Corrected final"))
        self.assertEqual(client.chat_stopStream.call_args.kwargs["chunks"], [
            {"type": "markdown_text", "text": "Corrected final"},
        ])


class BackendEventRunnerTests(unittest.TestCase):
    def run_with_events(
        self,
        events: list[dict[str, object]],
        *,
        return_code: int = 0,
        register_side_effect: object | None = None,
        on_answer_start: object | None = None,
        on_status: object | None = None,
        on_trace_event: object | None = None,
    ) -> tuple[str, bool]:
        process = MagicMock()
        process.stdout = iter(json.dumps(event) + "\n" for event in events)
        process.wait.return_value = return_code
        process.poll.return_value = None
        register_patch = patch.object(slack_socket_agent, "register_active_run")
        with tempfile.TemporaryDirectory() as raw_dir, patch.object(
            slack_socket_agent.subprocess, "Popen", return_value=process
        ), patch.object(slack_socket_agent.threading, "Timer"), register_patch as register:
            register.side_effect = register_side_effect
            return slack_socket_agent.run_backend_events(
                "codex",
                "T123",
                "C123",
                "1.23",
                "U123",
                "question",
                "thread",
                Path(raw_dir),
                30,
                MagicMock(),
                on_answer_start=on_answer_start,
                on_status=on_status,
                on_trace_event=on_trace_event,
            )

    def test_continued_conversation_gets_new_thread_messages_and_reports_its_size(self) -> None:
        process = MagicMock()
        process.stdout = iter(json.dumps(event) + "\n" for event in [
            {"type": "context", "tokens": 4200},
            {"type": "turn_complete", "status": "completed"},
        ])
        process.wait.return_value = 0
        process.poll.return_value = None
        sizes: list[int] = []
        written: list[str] = []
        with tempfile.TemporaryDirectory() as raw_dir, patch.object(
            slack_socket_agent.subprocess, "Popen", return_value=process
        ) as popen, patch.object(slack_socket_agent.threading, "Timer"), patch.object(
            slack_socket_agent, "register_active_run"
        ):
            def capture(*args, **kwargs):
                command = args[0]
                written.append(Path(command[command.index("--thread-new-file") + 1]).read_text(encoding="utf-8"))
                return process
            popen.side_effect = capture
            slack_socket_agent.run_backend_events(
                "claude", "T123", "C123", "1.23", "U123", "question", "thread", Path(raw_dir), 30,
                MagicMock(), resume_session="s-1", thread_new_text="U2: only this", on_context=sizes.append,
            )
            new_file = Path(popen.call_args.args[0][popen.call_args.args[0].index("--thread-new-file") + 1])
        self.assertEqual(["U2: only this"], written)
        self.assertFalse(new_file.exists())
        self.assertEqual([4200], sizes)

    def test_resumes_the_threads_conversation_and_reports_the_new_one(self) -> None:
        process = MagicMock()
        process.stdout = iter(json.dumps(event) + "\n" for event in [
            {"type": "session", "session_id": "thread-9"},
            {"type": "message_complete", "phase": "final_answer", "text": "Done"},
            {"type": "turn_complete", "status": "completed"},
        ])
        process.wait.return_value = 0
        process.poll.return_value = None
        sessions: list[str] = []
        with tempfile.TemporaryDirectory() as raw_dir, patch.object(
            slack_socket_agent.subprocess, "Popen", return_value=process
        ) as popen, patch.object(slack_socket_agent.threading, "Timer"), patch.object(
            slack_socket_agent, "register_active_run"
        ):
            slack_socket_agent.run_backend_events(
                "codex", "T123", "C123", "1.23", "U123", "question", "thread", Path(raw_dir), 30,
                MagicMock(), resume_session="thread-8", on_session=sessions.append,
            )
        command = popen.call_args.args[0]
        self.assertEqual("thread-8", command[command.index("--resume-session") + 1])
        self.assertEqual(["thread-9"], sessions)

    def test_forwards_lifecycle_activity_to_private_record(self) -> None:
        callback = MagicMock()
        start = {"type": "activity_start", "activity_id": "one", "label": "Searching the web…"}
        complete = {"type": "activity_complete", "activity_id": "one", "label": "Searching the web…", "status": "completed"}
        self.run_with_events([start, complete], on_trace_event=callback)
        self.assertEqual([start, complete], [call.args[0] for call in callback.call_args_list])

    def test_forwards_backend_retry_status(self) -> None:
        callback = MagicMock()

        self.run_with_events(
            [{"type": "status", "text": "Backend busy — retrying (2/3)…"}],
            on_status=callback,
        )

        callback.assert_called_once_with("Backend busy — retrying (2/3)…")

    def test_only_explicit_final_answer_start_signals_preparation(self) -> None:
        callback = MagicMock()
        self.run_with_events([
            {"type": "message_start", "phase": "commentary"},
            {"type": "message_start", "phase": None},
            {"type": "activity_complete", "activity_id": "one", "label": "Working…"},
        ], on_answer_start=callback)
        callback.assert_not_called()
        self.run_with_events([
            {"type": "message_start", "phase": "final_answer"},
        ], on_answer_start=callback)
        callback.assert_called_once()

    def test_multiple_final_messages_are_reconciled_in_order(self) -> None:
        answer, succeeded = self.run_with_events([
            {"type": "message_complete", "phase": "final_answer", "text": "First. "},
            {"type": "message_complete", "phase": "final_answer", "text": "Second."},
            {"type": "turn_complete", "status": "completed"},
        ])

        self.assertTrue(succeeded)
        self.assertEqual("First. Second.", answer)

    def test_forced_cleanup_is_not_reported_as_confirmed_stop(self) -> None:
        def request_cancel(_key: object, run: slack_socket_agent.ActiveBackendRun) -> None:
            run.cancel_requested = True

        answer, succeeded = self.run_with_events(
            [], return_code=137, register_side_effect=request_cancel
        )

        self.assertFalse(succeeded)
        self.assertIn("did not confirm interruption", answer)


class ClaudeBackendParityTests(unittest.TestCase):
    def test_both_backends_select_rich_events_by_default_with_rollbacks(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(slack_socket_agent.rich_events_selected("codex"))
            self.assertTrue(slack_socket_agent.rich_events_selected("claude"))
        with patch.dict(os.environ, {"OPENTAG_CLAUDE_TRANSPORT": "print",
                                     "OPENTAG_CODEX_TRANSPORT": "exec"}, clear=True):
            self.assertFalse(slack_socket_agent.rich_events_selected("claude"))
            self.assertFalse(slack_socket_agent.rich_events_selected("codex"))

    def test_claude_run_receives_model_effort_and_fast_mode(self) -> None:
        process = MagicMock()
        process.stdout = iter([json.dumps({"type": "turn_complete", "status": "completed"}) + "\n"])
        process.wait.return_value = 0
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as raw_dir, patch.object(
            slack_socket_agent.subprocess, "Popen", return_value=process
        ) as popen, patch.object(slack_socket_agent.threading, "Timer"), patch.object(
            slack_socket_agent, "register_active_run"
        ):
            slack_socket_agent.run_backend_events(
                "claude", "T123", "C123", "1.23", "U123", "question", "thread",
                Path(raw_dir), 30, MagicMock(), model="opus", reasoning_effort="max", fast_mode=True,
            )
        command = popen.call_args.args[0]
        self.assertEqual("claude", command[command.index("--backend") + 1])
        self.assertEqual("opus", command[command.index("--model") + 1])
        self.assertEqual("max", command[command.index("--reasoning-effort") + 1])
        self.assertEqual("on", command[command.index("--fast-mode") + 1])

    def test_claude_models_come_from_the_signed_in_account(self) -> None:
        catalog = [
            {"model": "default", "displayName": "Default (recommended)", "isDefault": True,
             "supportedEfforts": ["low", "medium", "high", "max"], "supportsFastMode": False},
            {"model": "opus", "displayName": "Opus", "isDefault": False,
             "supportedEfforts": ["low", "high", "xhigh"], "supportsFastMode": True},
            {"model": "haiku", "displayName": "Haiku", "isDefault": False,
             "supportedEfforts": [], "supportsFastMode": False},
        ]
        with patch.dict(os.environ, {}, clear=True), patch.object(
            agent_models, "fetch_claude_model_catalog", return_value=catalog
        ):
            models = agent_models.discover_models("claude")
        self.assertEqual(["default", "opus", "haiku"], [item.model_id for item in models])
        defaults = slack_socket_agent.default_agent_settings(models)
        self.assertEqual(("default", "high", False), (defaults.model, defaults.reasoning_effort, defaults.fast_mode))
        self.assertTrue(slack_socket_agent.fast_mode_available("opus", models))
        self.assertEqual((), slack_socket_agent.efforts_for_model("haiku", models))

    def test_unavailable_claude_catalog_still_offers_cli_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch.object(
            agent_models, "fetch_claude_model_catalog", return_value=None
        ):
            models = agent_models.discover_models("claude")
        self.assertEqual([("default", True)], [(item.model_id, item.is_default) for item in models])


    def test_claude_approval_names_the_backend_and_tool_label(self) -> None:
        blocks = slack_socket_agent.approval_button_blocks(
            team="T1", channel="C1", thread_ts="1.2", user_id="U1", approval_id="a" * 32,
            label="use a tool that requires approval", backend="claude",
        )
        self.assertEqual(
            "*Claude needs approval* to use a tool that requires approval. Approve only if you expect this request.",
            blocks[0]["text"]["text"],
        )


class ModelSwitchingTests(unittest.TestCase):
    CODEX = [slack_socket_agent.ModelOption("gpt-5", "GPT-5", ("low", "high"), is_default=True)]
    CLAUDE = [
        slack_socket_agent.ModelOption("default", "Default", ("low", "high"), is_default=True, backend="claude"),
        slack_socket_agent.ModelOption("opus", "Opus", ("low", "max"), supports_fast_mode=True, backend="claude"),
    ]

    def discover(self, backend: str) -> list[slack_socket_agent.ModelOption]:
        return {"codex": self.CODEX, "claude": self.CLAUDE}[backend]

    def test_tag_models_combine_signed_in_backends_with_default_first(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch.object(
            agent_models, "discover_models", side_effect=self.discover
        ), patch.object(agent_models, "backend_signed_in", return_value=True):
            models = slack_socket_agent.discover_tag_models("codex")
        self.assertEqual(["codex:gpt-5", "claude:default", "claude:opus"], [item.value for item in models])
        self.assertEqual(["codex:gpt-5"], [item.value for item in models if item.is_default])

    def test_tag_default_model_can_select_another_backend(self) -> None:
        with patch.dict(os.environ, {"OPENTAG_DEFAULT_MODEL": "claude:opus"}, clear=True), patch.object(
            agent_models, "discover_models", side_effect=self.discover
        ), patch.object(agent_models, "backend_signed_in", return_value=True):
            models = slack_socket_agent.discover_tag_models("codex")
        defaults = slack_socket_agent.default_agent_settings(models)
        self.assertEqual(("claude", "opus"), (defaults.backend, defaults.model))
        self.assertEqual(["codex:gpt-5"], [item.value for item in models if item.backend == "codex"])

    def test_tag_thinking_level_reaches_codex_and_claude(self) -> None:
        for default_model, backend in (("codex:gpt-5", "codex"), ("claude:opus", "claude")):
            with self.subTest(backend=backend), patch.dict(os.environ, {
                "OPENTAG_DEFAULT_MODEL": default_model, "OPENTAG_DEFAULT_EFFORT": "low",
            }, clear=True), patch.object(agent_models, "discover_models", side_effect=self.discover), patch.object(
                agent_models, "backend_signed_in", return_value=True
            ):
                models = slack_socket_agent.discover_tag_models("codex")
            defaults = slack_socket_agent.default_agent_settings(models)
            self.assertEqual((backend, "low"), (defaults.backend, defaults.reasoning_effort))
            # Catalog normalization retains explicit settings for backend validation.
            own = next(item for item in models if item.is_default).reasoning_efforts[-1]
            chosen = slack_socket_agent.normalize_settings(
                slack_socket_agent.AgentSettings(model=defaults.model, reasoning_effort=own, backend=backend), models)
            self.assertEqual(own, chosen.reasoning_effort)
            self.assertNotEqual("low", own)
            unset = slack_socket_agent.normalize_settings(slack_socket_agent.AgentSettings(), models)
            self.assertEqual("low", unset.reasoning_effort)
            # The cache keeps the model's own default, not the Tag's level.
            with tempfile.TemporaryDirectory() as raw:
                names = Path(raw) / "state/model-names.json"
                slack_socket_agent.remember_model_names(models, names)
                saved = agent_models.load_model_efforts(names.with_name("model-efforts.json"))
            self.assertIsNone(saved[f"{backend}:{defaults.model}"]["default"])

    def test_tag_thinking_level_is_ignored_when_the_model_lacks_it(self) -> None:
        with patch.dict(os.environ, {"OPENTAG_DEFAULT_MODEL": "claude:opus", "OPENTAG_DEFAULT_EFFORT": "high"},
                        clear=True), patch.object(agent_models, "discover_models", side_effect=self.discover), \
                patch.object(agent_models, "backend_signed_in", return_value=True):
            models = slack_socket_agent.discover_tag_models("codex")
        defaults = slack_socket_agent.default_agent_settings(models)
        self.assertEqual(("opus", "low"), (defaults.model, defaults.reasoning_effort))
        # Without the setting, as on every Tag set up before it existed, nothing changes.
        with patch.dict(os.environ, {"OPENTAG_DEFAULT_MODEL": "codex:gpt-5"}, clear=True), patch.object(
            agent_models, "discover_models", side_effect=self.discover
        ), patch.object(agent_models, "backend_signed_in", return_value=True):
            models = slack_socket_agent.discover_tag_models("codex")
        self.assertEqual(self.CODEX[0].default_reasoning_effort, next(item for item in models if item.is_default).default_reasoning_effort)
        self.assertFalse(any(item.tag_effort_applied for item in models))

    def test_unavailable_or_disallowed_backends_are_not_offered(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch.object(
            agent_models, "discover_models", side_effect=self.discover
        ), patch.object(agent_models, "backend_signed_in", side_effect=lambda name: name == "codex"):
            self.assertEqual({"codex"}, {item.backend for item in slack_socket_agent.discover_tag_models("codex")})
        with patch.dict(os.environ, {"OPENTAG_BACKENDS": "codex"}, clear=True), patch.object(
            agent_models, "discover_models", side_effect=self.discover
        ), patch.object(agent_models, "backend_signed_in", return_value=True) as signed_in:
            self.assertEqual({"codex"}, {item.backend for item in slack_socket_agent.discover_tag_models("codex")})
        signed_in.assert_called_once_with("codex")

    def test_disconnected_default_is_excluded_and_connected_account_becomes_default(self):
        for disconnected, connected in (("claude", "codex"), ("codex", "claude")):
            with self.subTest(disconnected=disconnected), patch.dict(os.environ, {
                "OPENTAG_DEFAULT_MODEL": f"{disconnected}:saved-model",
            }, clear=True), patch.object(agent_models, "discover_models", side_effect=self.discover), patch.object(
                agent_models, "backend_signed_in", side_effect=lambda name: name == connected
            ):
                models = agent_models.discover_tag_models(disconnected)
                self.assertEqual({connected}, {item.backend for item in models})
                self.assertEqual(connected, slack_socket_agent.default_agent_settings(models).backend)
                self.assertTrue(any(item.is_default for item in models))

    def test_no_connected_accounts_offer_no_models(self):
        with patch.object(agent_models, "backend_signed_in", return_value=False), patch.object(
            agent_models, "discover_models"
        ) as discover:
            self.assertEqual([], agent_models.discover_tag_models("codex"))
            discover.assert_not_called()


    def test_same_model_name_resolves_by_backend(self) -> None:
        models = [
            slack_socket_agent.ModelOption("shared", "Codex shared", ("low",), is_default=True),
            slack_socket_agent.ModelOption("shared", "Claude shared", ("max",), backend="claude"),
        ]
        normalized = slack_socket_agent.normalize_settings(
            slack_socket_agent.AgentSettings(model="shared", backend="claude"), models,
        )
        self.assertEqual(("claude", "max"), (normalized.backend, normalized.reasoning_effort))


class RunSummaryTests(unittest.TestCase):
    MODELS = [slack_socket_agent.ModelOption("opus", "Opus 5.5", ("high",), backend="claude")]

    def test_durations_are_short_and_readable(self) -> None:
        self.assertEqual(
            ["0s", "42s", "1m", "1m 12s", "59m 59s", "1h", "1h 3m"],
            [slack_socket_agent.format_duration(value) for value in (0.2, 42, 60, 72, 3599, 3600, 3780)],
        )

    def test_summary_names_backend_model_thinking_and_duration(self) -> None:
        settings = slack_socket_agent.AgentSettings("opus", "high", True, backend="claude")
        blocks = slack_socket_agent.run_summary_blocks(settings, self.MODELS, "claude", 72)
        self.assertEqual(
            [{"type": "context", "elements": [{"type": "mrkdwn",
                                               "text": "Claude · Opus 5.5 · high thinking · Fast mode · 1m 12s"}]}],
            blocks,
        )

    def test_summary_names_the_model_the_backend_reported(self) -> None:
        models = [
            slack_socket_agent.ModelOption("default", "Default (recommended)", ("low", "high"), backend="claude",
                                           resolved_model="claude-sonnet-5-5"),
            slack_socket_agent.ModelOption("sonnet", "Sonnet 5.5", ("low", "high"), backend="claude",
                                           resolved_model="claude-sonnet-5-5"),
        ]
        settings = slack_socket_agent.AgentSettings("default", None, False, backend="claude")
        text = lambda reported: slack_socket_agent.run_summary_blocks(
            settings, models, "claude", 5, reported_model=reported)[0]["elements"][0]["text"]
        self.assertEqual("Claude · Sonnet 5.5 · default thinking · 5s", text("claude-sonnet-5-5"))
        self.assertEqual("Claude · claude-new-model · default thinking · 5s", text("claude-new-model"))
        self.assertEqual("Claude · default model · default thinking · 5s", text(None))

    def test_summary_thinking_level_falls_back_to_reported_then_catalog_default(self) -> None:
        models = [
            slack_socket_agent.ModelOption("gpt-6-astra", "GPT-6-Astra", ("low", "high"), default_reasoning_effort="high"),
            slack_socket_agent.ModelOption("haiku", "Haiku 4.5", (), backend="claude"),
        ]
        default = slack_socket_agent.AgentSettings(None, None, False, backend="codex")
        text = lambda settings, backend, **kwargs: slack_socket_agent.run_summary_blocks(
            settings, models, backend, 42, **kwargs)[0]["elements"][0]["text"]
        self.assertEqual("Codex · GPT-6-Astra · high thinking · 42s",
                         text(default, "codex", reported_model="gpt-6-astra"))
        self.assertEqual("Codex · GPT-6-Astra · xhigh thinking · 42s",
                         text(default, "codex", reported_model="gpt-6-astra", reported_effort="xhigh"))
        self.assertEqual("Codex · GPT-6-Astra · low thinking · 42s",
                         text(replace(default, reasoning_effort="low"), "codex",
                              reported_model="gpt-6-astra", reported_effort="xhigh"))
        haiku = slack_socket_agent.AgentSettings("haiku", None, False, backend="claude")
        self.assertEqual("Claude · Haiku 4.5 · 42s", text(haiku, "claude"))

    def test_bridge_forwards_reported_model_and_usage(self) -> None:
        process = MagicMock()
        process.stdout = iter([json.dumps(event) + "\n" for event in (
            {"type": "run_info", "model": "gpt-6-astra", "reasoning_effort": "xhigh"},
            {"type": "usage", "usage": {"input_tokens": 100, "output_tokens": 20}},
            {"type": "turn_complete", "status": "completed"},
        )])
        process.wait.return_value = 0
        process.poll.return_value = None
        reported: list[dict[str, str]] = []
        trace = []
        with tempfile.TemporaryDirectory() as raw_dir, patch.object(
            slack_socket_agent.subprocess, "Popen", return_value=process
        ), patch.object(slack_socket_agent.threading, "Timer"), patch.object(
            slack_socket_agent, "register_active_run"
        ):
            slack_socket_agent.run_backend_events(
                "codex", "T1", "C1", "1.0", "U1", "q", "thread", Path(raw_dir), 30, MagicMock(),
                on_run_info=reported.append, on_trace_event=trace.append,
            )
        self.assertEqual([{"model": "gpt-6-astra", "reasoning_effort": "xhigh"}], reported)
        self.assertEqual(trace, [{"type": "usage", "usage": {"input_tokens": 100, "output_tokens": 20}}])

    def test_summary_marks_stopped_and_failed_runs_and_default_models(self) -> None:
        settings = slack_socket_agent.AgentSettings(None, None, False, backend="codex")
        text = lambda outcome: slack_socket_agent.run_summary_blocks(
            settings, [], "codex", 45, outcome=outcome)[0]["elements"][0]["text"]
        self.assertEqual("Codex · default model · default thinking · stopped after 45s", text("stopped"))
        self.assertEqual("Codex · default model · default thinking · failed after 45s", text("failed"))


class SlackCancellationTests(unittest.TestCase):
    def tearDown(self) -> None:
        with slack_socket_agent.ACTIVE_RUNS_LOCK:
            slack_socket_agent.ACTIVE_RUNS.clear()

    def test_old_run_cannot_unregister_or_cancel_new_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir, patch(
            "scripts.slack_socket_agent.threading.Timer"
        ):
            key = slack_socket_agent.RunKey("T123", "C123", "1.23")
            old_process = MagicMock()
            old_process.poll.return_value = None
            new_process = MagicMock()
            new_process.poll.return_value = None
            old = slack_socket_agent.ActiveBackendRun(
                old_process, Path(raw_dir) / "old", "old"
            )
            new = slack_socket_agent.ActiveBackendRun(
                new_process, Path(raw_dir) / "new", "new"
            )
            slack_socket_agent.register_active_run(key, old)
            slack_socket_agent.register_active_run(key, new)
            slack_socket_agent.unregister_active_run(key, old)

            self.assertTrue(slack_socket_agent.cancel_active_run(key))
            self.assertEqual("new", new.control_file.read_text(encoding="utf-8"))
            self.assertFalse(old.control_file.exists())

    def test_delayed_stop_event_cannot_cancel_a_newer_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            key = slack_socket_agent.RunKey("T123", "C123", "1.23")
            process = MagicMock()
            process.poll.return_value = None
            run = slack_socket_agent.ActiveBackendRun(
                process,
                Path(raw_dir) / "control",
                "new-run",
                started_at_epoch=200.0,
            )
            slack_socket_agent.register_active_run(key, run)

            self.assertFalse(slack_socket_agent.cancel_active_run(key, "100.0"))
            self.assertFalse(run.cancel_requested)
            self.assertFalse(run.control_file.exists())

    def test_stop_event_targets_team_channel_and_thread(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ,
            {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C123"},
            clear=True,
        ), patch.object(slack_socket_agent, "cancel_active_run", return_value=True) as cancel:
            slack_socket_agent.create_app("claude", 30, frozenset({"UOWNER"}))
            fake_app.events["agent_session_stopped"](
                {
                    "channel": "C123",
                    "thread_ts": "1.23",
                    "user": "UOWNER",
                    "event_ts": "100.25",
                },
                {"team_id": "T123"},
                client,
                MagicMock(),
            )

        cancel.assert_called_once_with(
            slack_socket_agent.RunKey("T123", "C123", "1.23"),
            "100.25",
        )
        client.api_call.assert_not_called()

    def test_orphaned_stop_event_clears_slack_session(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        journal = MagicMock()
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ,
            {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C123"},
            clear=True,
        ), patch.object(slack_socket_agent, "cancel_active_run", return_value=False):
            slack_socket_agent.create_app(
                "claude",
                30,
                frozenset({"UOWNER"}),
                session_journal=journal,
            )
            fake_app.events["agent_session_stopped"](
                {
                    "channel": "C123",
                    "thread_ts": "1.23",
                    "user": "UOWNER",
                    "event_ts": "100.25",
                },
                {"team_id": "T123"},
                client,
                MagicMock(),
            )

        client.api_call.assert_called_once_with(
            "agents.sessions.setStatus",
            json={"channel_id": "C123", "thread_ts": "1.23", "status": "active"},
        )
        client.assistant_threads_setStatus.assert_called_once_with(
            channel_id="C123", thread_ts="1.23", status=""
        )
        journal.remove.assert_called_once_with("T123", "C123", "1.23")


class SlackAgentSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        summary = patch.object(slack_socket_agent, "queue_reply_summary")
        self.queue_summary = summary.start()
        self.addCleanup(summary.stop)
        # Legacy Codex fixtures must not read the operator's ChatGPT account.
        auth = patch("scripts.tag_chatgpt.enabled", return_value=False)
        auth.start()
        self.addCleanup(auth.stop)
        connected = patch.object(agent_models, "backend_signed_in", side_effect=lambda name: name == "codex")
        connected.start()
        self.addCleanup(connected.stop)
        catalog = patch.object(agent_models, "fetch_codex_model_catalog", return_value=None)
        self.live_catalog = catalog.start()
        self.addCleanup(catalog.stop)

    def test_live_account_default_overrides_an_incompatible_shared_cache(self) -> None:
        self.live_catalog.return_value = [{
            "model": "gpt-6-astra", "displayName": "Astra", "isDefault": True,
            "defaultReasoningEffort": "medium", "additionalSpeedTiers": ["fast"],
            "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "medium"}],
        }]
        with tempfile.TemporaryDirectory() as raw_dir, patch.dict(os.environ, {"CODEX_HOME": raw_dir, "OPENTAG_WORKDIR": raw_dir}, clear=True):
            (Path(raw_dir) / "models_cache.json").write_text(json.dumps({"models": [
                {"slug": "gpt-6.1-sol", "visibility": "list", "priority": 1},
            ]}), encoding="utf-8")
            models = agent_models.discover_codex_models()
            self.assertEqual([model.model_id for model in models], ["gpt-6-astra"])
            self.assertEqual(slack_socket_agent.default_agent_settings(models).model, "gpt-6-astra")
            old_saved_choice = slack_socket_agent.AgentSettings("gpt-6.1-sol", "low", False)
            repaired = slack_socket_agent.normalize_settings(old_saved_choice, models)
            self.assertEqual((repaired.model, repaired.reasoning_effort), ("gpt-6-astra", "low"))
            explicit = slack_socket_agent.AgentSettings("gpt-6-astra", "low", False, backend="codex")
            self.assertEqual(slack_socket_agent.normalize_settings(explicit, models), explicit)
            # A later cache rewrite cannot bring back the rejected model.
            self.assertEqual(slack_socket_agent.default_agent_settings(agent_models.discover_codex_models()).model, "gpt-6-astra")

    def test_malformed_live_reasoning_efforts_preserve_catalog(self) -> None:
        """Malformed optional metadata must not discard other account models."""
        for efforts in (None, 42, "medium", {"reasoningEffort": "high"},
                        [None, "high", {}, {"reasoningEffort": "low"}]):
            with self.subTest(efforts=efforts), patch.dict(os.environ, {}, clear=True):
                self.live_catalog.return_value = [
                    {"model": "malformed", "supportedReasoningEfforts": efforts},
                    {"model": "valid", "isDefault": True,
                     "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]},
                ]
                models = agent_models.discover_codex_models()
                self.assertEqual([model.model_id for model in models], ["malformed", "valid"])
                self.assertEqual(models[0].reasoning_efforts,
                                 ("low",) if isinstance(efforts, list)
                                 else slack_socket_agent.DEFAULT_REASONING_EFFORTS)
                self.assertEqual(models[1].reasoning_efforts, ("medium",))
                self.assertEqual(slack_socket_agent.default_agent_settings(models).model, "valid")

    def test_unavailable_live_catalog_never_promotes_cached_priority_to_default(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir, patch.dict(os.environ, {"CODEX_HOME": raw_dir, "OPENTAG_WORKDIR": raw_dir}, clear=True):
            (Path(raw_dir) / "models_cache.json").write_text(json.dumps({"models": [
                {"slug": "gpt-6.1-sol", "visibility": "list", "priority": 1},
            ]}), encoding="utf-8")
            models = agent_models.discover_codex_models()
            self.assertIsNone(slack_socket_agent.default_agent_settings(models).model)

    def test_failed_app_server_replies_hide_activity(self) -> None:
        for backend, outcome in ((b, o) for b in ("codex", "claude") for o in ("backend_error", "exception")):
            with self.subTest(backend=backend, outcome=outcome), tempfile.TemporaryDirectory() as raw_dir:
                store = ActivityStore(Path(raw_dir) / "activity")
                fake_app = FakeApp()
                client = MagicMock()

                def run_events(*_args: object, **kwargs: object) -> tuple[str, bool]:
                    kwargs["on_trace_event"]({
                        "type": "activity_start", "activity_id": "item-1",
                        "label": "Searching the web…",
                    })
                    if outcome == "exception":
                        raise RuntimeError("backend crashed")
                    return "backend failed", False

                with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
                    os.environ, {
                        "SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C1",
                        "OPENTAG_SLACK_STREAMING": "0",
                    }, clear=True,
                ), patch.object(agent_models, "discover_codex_models", return_value=[]), patch.object(
                    slack_socket_agent, "build_thread_text", return_value="thread"
                ), patch.object(slack_socket_agent, "run_backend_events", side_effect=run_events), patch.object(
                    slack_socket_agent, "collect_health_checks", return_value=[]
                ):
                    slack_socket_agent.create_app(
                        backend, 30, frozenset({"UOWNER"}),
                        activity_store=store, report_store=MagicMock(),
                    )
                    fake_app.events["app_mention"](
                        {"channel": "C1", "ts": "1.23", "user": "UOWNER", "text": "<@BOT> do it"},
                        {"team_id": "T1"}, client, MagicMock(),
                    )

                action_ids = [
                    element["action_id"]
                    for call in client.chat_postEphemeral.call_args_list
                    for block in call.kwargs.get("blocks") or []
                    if block.get("type") == "actions"
                    for element in block["elements"]
                ]
                record = store.get(next(store.root.glob("*.json")).stem)
                self.assertRegex(record["error_reference"], r"^[A-F0-9]{8}$")
                self.assertIn(slack_socket_agent.RETRY_ACTION_ID, action_ids)
                self.assertNotIn("opentag_view_activity", action_ids)
                self.queue_summary.assert_not_called()

    def test_app_server_request_hides_activity_and_persists_trace(self) -> None:
        for backend in ("codex", "claude"):
            with tempfile.TemporaryDirectory() as raw_dir:
                store = ActivityStore(Path(raw_dir) / "activity")
                fake_app = FakeApp()
                client = MagicMock()

                def run_events(*_args: object, **kwargs: object) -> tuple[str, bool]:
                    kwargs["on_run_info"]({"model": "actual-model"})
                    callback = kwargs["on_trace_event"]
                    callback({"type": "activity_start", "activity_id": "item-1", "label": "Searching the web…",
                              "details": {"tool": "Web search", "input": "refund policy"}})
                    callback({"type": "activity_complete", "activity_id": "item-1", "label": "Searching the web…", "status": "completed",
                              "details": {"output": "found two pages"}})
                    return "Done.", True

                with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
                    os.environ, {
                        "SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C1",
                        "OPENTAG_SLACK_STREAMING": "0",
                    }, clear=True,
                ), patch.object(agent_models, "discover_codex_models", return_value=[]), patch.object(
                    slack_socket_agent, "build_thread_text", return_value="thread"
                ), patch.object(slack_socket_agent, "run_backend_events", side_effect=run_events):
                    slack_socket_agent.create_app(
                        backend, 30, frozenset({"UOWNER"}), activity_store=store,
                    )
                    fake_app.events["app_mention"](
                        {"channel": "C1", "ts": "1.23", "user": "UOWNER", "text": "<@BOT> do it"},
                        {"team_id": "T1"}, client, MagicMock(),
                    )

                footer = next(
                    call.kwargs["blocks"] for call in client.chat_postMessage.call_args_list
                    if call.kwargs.get("blocks") and
                    any(block.get("type") == "context" for block in call.kwargs["blocks"])
                )
                buttons = [element for block in footer if block.get("type") == "actions"
                           for element in block["elements"]]
                self.assertEqual(
                    [],
                    [button["action_id"] for button in buttons],
                )
                run_id = next(store.root.glob("*.json")).stem
                record = store.get(run_id)
                self.assertEqual("completed", record["outcome"])
                self.assertEqual("Searching the web…", record["events"][0]["label"])
                self.assertEqual("refund policy", record["events"][0]["details"]["input"])
                self.assertEqual("found two pages", record["events"][0]["details"]["output"])
                self.assertEqual("Done.", record["reply_preview"])
                self.assertEqual("actual-model", record["model"])
                self.assertEqual(backend, record["backend"])
                self.assertEqual("actual-model", self.queue_summary.call_args.args[4])
                self.assertEqual((store, run_id, "Done.", backend), self.queue_summary.call_args.args[:4])
                client.chat_postEphemeral.assert_not_called()
                self.assertNotIn("refund policy", json.dumps(footer))

    def test_activity_button_opens_only_for_original_requester(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            store = ActivityStore(Path(raw_dir) / "activity")
            run_id = store.create(
                team="T1", channel="C1", thread_ts="1.23",
                request_ts="1.23", requester="UOWNER",
            )
            store.observe(run_id, {
                "type": "activity_start", "activity_id": "tool-1",
                "label": "Searching the web…",
            })
            store.finish(run_id, "completed")
            button = {"value": json.dumps({
                "team": "T1", "channel": "C1", "thread_ts": "1.23", "run_id": run_id,
            })}

            fake_app = FakeApp()
            client = MagicMock()
            with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
                os.environ, {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C1"}, clear=True,
            ), patch.object(agent_models, "discover_codex_models", return_value=[]):
                slack_socket_agent.create_app(
                    "codex", 30, frozenset({"UOWNER", "UOTHER"}), activity_store=store,
                )
                handler = fake_app.actions[slack_socket_agent.ACTIVITY_ACTION_ID]
                body = {
                    "actions": [{"value": button["value"]}],
                    "trigger_id": "trigger", "user": {"id": "UOTHER"},
                    "team": {"id": "T1"}, "channel": {"id": "C1"},
                }
                handler(MagicMock(), body, client, MagicMock())
                client.views_open.assert_not_called()
                body["user"]["id"] = "UOWNER"
                body["channel"]["id"] = "C2"
                handler(MagicMock(), body, client, MagicMock())
                client.views_open.assert_not_called()
                body["channel"]["id"] = "C1"
                handler(MagicMock(), body, client, MagicMock())
                with patch.dict(os.environ, {"SLACK_CHANNEL_IDS": "C2"}):
                    handler(MagicMock(), body, client, MagicMock())
                root_view = client.views_open.call_args.kwargs["view"]
                detail_body = {
                    "actions": [{"value": "0"}],
                    "trigger_id": "detail-trigger", "user": {"id": "UOTHER"},
                    "team": {"id": "T1"}, "view": root_view,
                }
                detail_handler = fake_app.actions[slack_socket_agent.ACTIVITY_DETAIL_ACTION_ID]
                detail_handler(MagicMock(), detail_body, client, MagicMock())
                client.views_push.assert_not_called()
                detail_body["user"]["id"] = "UOWNER"
                detail_handler(MagicMock(), detail_body, client, MagicMock())
            client.views_open.assert_called_once()
            client.views_push.assert_called_once()
            self.assertEqual("trigger", client.views_open.call_args.kwargs["trigger_id"])
            self.assertIn("Searching the web", json.dumps(client.views_open.call_args.kwargs["view"]))

    def test_activity_details_are_preserved_but_hidden(self) -> None:
        metadata = dict(team="T1", channel="C1", thread_ts="1.23")
        self.assertEqual([], slack_socket_agent.activity_button_blocks(**metadata, run_id="run-1"))
        with patch.object(slack_socket_agent, "SHOW_ACTIVITY_DETAILS", True):
            self.assertEqual("Activity", slack_socket_agent.activity_button_blocks(
                **metadata, run_id="run-1",
            )[0]["elements"][0]["text"]["text"])


    def test_settings_action_value_supports_new_and_existing_messages(self) -> None:
        self.assertEqual(
            "new",
            slack_socket_agent.settings_action_value(
                {"selected_option": {"value": "new"}}
            ),
        )
        self.assertEqual("old", slack_socket_agent.settings_action_value({"value": "old"}))

    def test_discovers_visible_codex_models_and_reasoning_levels(self) -> None:
        payload = {
            "models": [
                {
                    "slug": "gpt-visible",
                    "display_name": "GPT Visible",
                    "visibility": "list",
                    "default_reasoning_level": "medium",
                    "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}],
                    "additional_speed_tiers": ["fast"],
                },
                {
                    "slug": "gpt-hidden",
                    "display_name": "GPT Hidden",
                    "visibility": "hide",
                    "supported_reasoning_levels": [{"effort": "high"}],
                },
            ]
        }
        with tempfile.TemporaryDirectory() as raw_dir:
            cache = Path(raw_dir) / "models_cache.json"
            cache.write_text(json.dumps(payload), encoding="utf-8")
            with patch(
                "scripts.agent_models.codex_models_cache_path",
                return_value=cache,
            ), patch.dict(os.environ, {}, clear=True):
                models = agent_models.discover_codex_models()

        self.assertEqual(["gpt-visible"], [model.model_id for model in models])
        self.assertEqual(("low", "medium"), models[0].reasoning_efforts)
        self.assertTrue(models[0].supports_fast_mode)
        self.assertFalse(models[0].is_default)
        self.assertEqual("medium", models[0].default_reasoning_effort)


    def test_configured_codex_model_and_thinking_are_request_defaults(self) -> None:
        payload = {
            "models": [
                {
                    "slug": "gpt-first",
                    "display_name": "GPT First",
                    "visibility": "list",
                    "priority": 1,
                    "default_reasoning_level": "low",
                    "supported_reasoning_levels": [{"effort": "low"}],
                },
                {
                    "slug": "gpt-configured",
                    "display_name": "GPT Configured",
                    "visibility": "list",
                    "priority": 2,
                    "default_reasoning_level": "medium",
                    "additional_speed_tiers": ["fast"],
                    "supported_reasoning_levels": [
                        {"effort": "medium"},
                        {"effort": "high"},
                    ],
                },
            ]
        }
        with tempfile.TemporaryDirectory() as raw_dir:
            codex_home = Path(raw_dir)
            (codex_home / "models_cache.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            (codex_home / "config.toml").write_text(
                'model = "gpt-configured"\n'
                'model_reasoning_effort = "high"\n'
                'service_tier = "priority"\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEX_HOME": raw_dir}, clear=True):
                models = agent_models.discover_codex_models()

        configured = next(model for model in models if model.is_default)
        self.assertEqual("gpt-configured", configured.model_id)
        self.assertEqual("high", configured.default_reasoning_effort)
        self.assertEqual(
            slack_socket_agent.AgentSettings("gpt-configured", "high", fast_mode=True, backend="codex"),
            slack_socket_agent.default_agent_settings(models),
        )

    def test_tag_local_codex_defaults_override_global_defaults_for_requests(self) -> None:
        payload = {
            "models": [
                {
                    "slug": "gpt-global",
                    "display_name": "GPT Global",
                    "visibility": "list",
                    "supported_reasoning_levels": [
                        {"effort": "medium"},
                        {"effort": "high"},
                    ],
                },
                {
                    "slug": "gpt-tag",
                    "display_name": "GPT Tag",
                    "visibility": "list",
                    "additional_speed_tiers": ["fast"],
                    "supported_reasoning_levels": [
                        {"effort": "medium"},
                        {"effort": "high"},
                    ],
                },
            ]
        }
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            codex_home = root / "codex-home"
            workdir = root / "tag-workspace"
            codex_home.mkdir()
            (workdir / ".codex").mkdir(parents=True)
            (codex_home / "models_cache.json").write_text(json.dumps(payload), encoding="utf-8")
            (codex_home / "config.toml").write_text(
                'model = "gpt-global"\n'
                'model_reasoning_effort = "medium"\n'
                'service_tier = "priority"\n',
                encoding="utf-8",
            )
            (workdir / ".codex/config.toml").write_text(
                'model = "gpt-tag"\n'
                'model_reasoning_effort = "high"\n'
                'service_tier = "default"\n',
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"CODEX_HOME": str(codex_home), "OPENTAG_WORKDIR": str(workdir)},
                clear=True,
            ):
                models = agent_models.discover_codex_models()
                defaults = slack_socket_agent.default_agent_settings(models)

        self.assertEqual(("codex", "gpt-tag", "high", False),
                         (defaults.backend, defaults.model, defaults.reasoning_effort, defaults.fast_mode))

    def test_tag_local_codex_defaults_inherit_missing_global_values(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            codex_home = root / "codex-home"
            workdir = root / "tag-workspace"
            codex_home.mkdir()
            (workdir / ".codex").mkdir(parents=True)
            (codex_home / "config.toml").write_text(
                'model = "gpt-global"\nmodel_reasoning_effort = "medium"\n',
                encoding="utf-8",
            )
            (workdir / ".codex/config.toml").write_text(
                'model_reasoning_effort = "high"\nservice_tier = "fast"\n',
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"CODEX_HOME": str(codex_home), "OPENTAG_WORKDIR": str(workdir)},
                clear=True,
            ):
                defaults = agent_models.configured_codex_defaults()

        self.assertEqual(("gpt-global", "high", True), defaults)

    def test_tag_local_codex_defaults_inherit_global_service_tier_when_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            codex_home = root / "codex-home"
            workdir = root / "tag-workspace"
            codex_home.mkdir()
            (workdir / ".codex").mkdir(parents=True)
            (codex_home / "config.toml").write_text(
                'service_tier = "fast"\n', encoding="utf-8"
            )
            (workdir / ".codex/config.toml").write_text(
                'service_tier = "typo"\n', encoding="utf-8"
            )
            with patch.dict(
                os.environ,
                {"CODEX_HOME": str(codex_home), "OPENTAG_WORKDIR": str(workdir)},
                clear=True,
            ):
                defaults = agent_models.configured_codex_defaults()

        self.assertEqual((None, None, True), defaults)


    def test_normalize_settings_turns_fast_mode_off_for_unsupported_model(self) -> None:
        models = [
            slack_socket_agent.CodexModelOption(
                "gpt-standard",
                "GPT Standard",
                ("medium",),
            )
        ]

        normalized = slack_socket_agent.normalize_settings(
            slack_socket_agent.AgentSettings(
                "gpt-standard",
                "medium",
                fast_mode=True,
            ),
            models,
        )

        self.assertFalse(normalized.fast_mode)

    def test_normalize_settings_does_not_transfer_fast_mode_from_removed_model(self) -> None:
        normalized = slack_socket_agent.normalize_settings(
            slack_socket_agent.AgentSettings(
                "gpt-removed",
                "medium",
                fast_mode=True,
            ),
            [],
        )

        self.assertIsNone(normalized.model)
        self.assertFalse(normalized.fast_mode)


class SlackStreamingConfigurationTests(unittest.TestCase):
    def test_streaming_defaults_on_and_can_be_disabled(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(slack_socket_agent.env_enabled("OPENTAG_SLACK_STREAMING", default=True))
        with patch.dict(os.environ, {"OPENTAG_SLACK_STREAMING": "0"}, clear=True):
            self.assertFalse(slack_socket_agent.env_enabled("OPENTAG_SLACK_STREAMING", default=True))

class BackendEffortIsolationTests(unittest.TestCase):
    def test_codex_allowlist_does_not_remove_claude_efforts_or_default(self):
        models = [slack_socket_agent.ModelOption(
            "opus", "Opus", ("high", "max"), backend="claude",
            default_reasoning_effort="high", is_default=True,
        ), slack_socket_agent.ModelOption("gpt", "GPT", ("low", "high"))]
        with patch.dict(os.environ, {"OPENTAG_CODEX_REASONING_EFFORTS": "low,medium"}):
            self.assertEqual(("high", "max"), slack_socket_agent.efforts_for_model("opus", models, "claude"))
            self.assertEqual(("high", "max"), slack_socket_agent.efforts_for_model(None, models, "claude"))
            self.assertEqual("high", slack_socket_agent.default_agent_settings(models).reasoning_effort)
            self.assertEqual(("low",), slack_socket_agent.efforts_for_model("gpt", models, "codex"))


class RetiredSlackSettingsTests(unittest.TestCase):
    def test_legacy_overrides_cannot_change_requests_for_either_backend(self):
        for backend in ("codex", "claude"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                path = root / "settings.json"
                legacy = {"T1:U1": {"backend": "claude" if backend == "codex" else "codex",
                                    "model": "old-model", "reasoning_effort": "high", "fast_mode": True}}
                path.write_text(json.dumps(legacy), encoding="utf-8")
                original = path.read_bytes()
                fake_app = FakeApp()
                models = [slack_socket_agent.ModelOption("tag-model", "Tag model", ("low", "high"),
                          is_default=True, default_reasoning_effort="low", backend=backend)]
                with patch.dict(os.environ, {"SLACK_BOT_TOKEN": "fixture", "SLACK_CHANNEL_IDS": "C1,C2",
                        "OPENTAG_SLACK_SETTINGS_FILE": str(path), "OPENTAG_SLACK_STREAMING": "0"}, clear=True), \
                     patch.object(slack_socket_agent, "App", return_value=fake_app), \
                     patch.object(slack_socket_agent, "discover_tag_models", return_value=models), \
                     patch.object(slack_socket_agent, "build_thread_text", return_value="thread"), \
                     patch.object(slack_socket_agent, "run_backend_events", return_value=("Done.", True)) as run:
                    slack_socket_agent.create_app(backend, 30, frozenset({"U1", "U2"}),
                                                  activity_store=ActivityStore(root / "activity"))
                    # An already-open form from the old version cannot save anything.
                    ack = MagicMock()
                    fake_app.views[slack_socket_agent.SETTINGS_VIEW_ID](ack)
                    self.assertEqual("update", ack.call_args.kwargs["response_action"])
                    self.assertNotIn("submit", ack.call_args.kwargs["view"])
                    for user, channel in (("U1", "C1"), ("U1", "C2"), ("U2", "C1")):
                        client = MagicMock()
                        fake_app.events["app_mention"]({"channel": channel, "ts": "1.1", "user": user,
                            "text": "<@BOT> help"}, {"team_id": "T1"}, client, MagicMock())
                        self.assertEqual(backend, run.call_args.args[0])
                        self.assertEqual(("tag-model", "low", False), tuple(
                            run.call_args.kwargs[key] for key in ("model", "reasoning_effort", "fast_mode")))
                        self.assertNotIn(slack_socket_agent.SETTINGS_ACTION_ID, json.dumps(
                            [call.kwargs for call in client.chat_postMessage.call_args_list]))
                self.assertFalse(path.exists())
                self.assertEqual(original, path.with_name(path.name + ".retired-v1").read_bytes())

    def test_migration_is_idempotent_and_preserves_malformed_legacy_bytes(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "settings.json"
            path.write_bytes(b"old malformed preferences")
            backup = path.with_name(path.name + ".retired-v1")
            marker = path.with_name(path.name + ".retired-v1.complete.json")
            slack_socket_agent.retire_slack_settings(path)
            stamp = backup.stat().st_mtime_ns
            slack_socket_agent.retire_slack_settings(path)
            self.assertFalse(path.exists())
            self.assertEqual(b"old malformed preferences", backup.read_bytes())
            self.assertEqual(stamp, backup.stat().st_mtime_ns)
            self.assertEqual({"version": "1"}, json.loads(marker.read_text(encoding="utf-8")))

    def test_failed_archive_and_completion_write_can_retry(self):
        from scripts import tag_config
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "settings.json"
            path.write_bytes(b"saved preferences")
            marker = path.with_name(path.name + ".retired-v1.complete.json")
            with patch.object(Path, "replace", side_effect=OSError("disk")), self.assertRaises(OSError):
                slack_socket_agent.retire_slack_settings(path)
            self.assertEqual(b"saved preferences", path.read_bytes())
            self.assertFalse(marker.exists())
            with patch.object(tag_config, "save_config", side_effect=OSError("disk")), self.assertRaises(OSError):
                slack_socket_agent.retire_slack_settings(path)
            self.assertFalse(marker.exists())
            self.assertFalse(path.exists())
            slack_socket_agent.retire_slack_settings(path)
            self.assertTrue(marker.exists())
            self.assertEqual(b"saved preferences", path.with_name(path.name + ".retired-v1").read_bytes())

    def test_conflicting_backup_is_preserved_and_not_marked_complete(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "settings.json"
            backup = path.with_name(path.name + ".retired-v1")
            path.write_bytes(b"current")
            backup.write_bytes(b"previous")
            with self.assertRaisesRegex(RuntimeError, "conflicting backups"):
                slack_socket_agent.retire_slack_settings(path)
            self.assertEqual(b"current", path.read_bytes())
            self.assertEqual(b"previous", backup.read_bytes())
            self.assertFalse(path.with_name(path.name + ".retired-v1.complete.json").exists())
