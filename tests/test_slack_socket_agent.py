from __future__ import annotations

import json
import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    import slack_bolt  # noqa: F401
except ModuleNotFoundError:
    raise unittest.SkipTest("slack_bolt is installed by the Slack bridge runtime")

from scripts import slack_socket_agent


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
    def test_openmax_is_the_backend_independent_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(slack_socket_agent.suggested_bot_name("codex"), "OpenMax")
            self.assertEqual(slack_socket_agent.suggested_bot_name("claude"), "OpenMax")

    def test_operator_can_override_the_display_name(self) -> None:
        with patch.dict(os.environ, {"OPENTAG_BOT_NAME": "TeamBot"}, clear=True):
            self.assertEqual(slack_socket_agent.suggested_bot_name("codex"), "TeamBot")


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

        client.chat_postMessage.assert_called_once_with(
            channel="C123",
            thread_ts="1.23",
            text="Here is the chart.",
            mrkdwn=True,
            blocks=None,
        )
        upload = client.files_upload_v2.call_args.kwargs
        self.assertEqual("C123", upload["channel"])
        self.assertEqual("1.23", upload["thread_ts"])
        self.assertEqual("chart.png", upload["filename"])
        self.assertTrue(Path(upload["file"]).is_relative_to(Path(raw_home) / "tmp"))

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
    def test_failure_copy_does_not_expose_backend_diagnostics(self) -> None:
        reply = slack_socket_agent.user_facing_failure(
            "RuntimeError: secret backend detail", 420, "ABC12345"
        )

        self.assertNotIn("secret backend detail", reply)
        self.assertIn("ABC12345", reply)
        self.assertIn("Please retry", reply)

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
        ), patch.object(slack_socket_agent, "discover_codex_models", return_value=[]), patch.object(
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
        ), patch.object(slack_socket_agent, "discover_codex_models", return_value=[]):
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

    def test_authorized_user_can_open_settings_from_configure_button(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        ack = MagicMock()
        metadata = {"team": "T123", "channel": "C123", "thread_ts": "1.23"}
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ,
            {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C123"},
            clear=True,
        ), patch.object(slack_socket_agent, "discover_codex_models", return_value=[]):
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
        self.assertEqual(
            metadata,
            json.loads(client.views_open.call_args.kwargs["view"]["private_metadata"]),
        )

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

        journal.add.assert_called_once_with("T123", "C123", "1.23")
        journal.remove.assert_called_once_with("T123", "C123", "1.23")

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


class SlackAnswerStreamTests(unittest.TestCase):
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


class BackendEventRunnerTests(unittest.TestCase):
    def run_with_events(
        self,
        events: list[dict[str, object]],
        *,
        return_code: int = 0,
        register_side_effect: object | None = None,
        on_answer_start: object | None = None,
        on_status: object | None = None,
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
            )

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
    def test_settings_footer_uses_a_compact_configure_button(self) -> None:
        blocks = slack_socket_agent.settings_button_blocks(
            team="T1",
            channel="C1",
            thread_ts="1.23",
        )

        self.assertEqual(1, len(blocks))
        self.assertEqual("actions", blocks[0]["type"])
        button = blocks[0]["elements"][0]
        self.assertEqual("button", button["type"])
        self.assertEqual("Configure", button["text"]["text"])
        self.assertEqual(
            {"team": "T1", "channel": "C1", "thread_ts": "1.23"},
            json.loads(button["value"]),
        )

        client = MagicMock()
        slack_socket_agent.post_final_reply(
            client,
            "C1",
            "1.23",
            "A concise answer.",
            footer_blocks=blocks,
        )

        rendered = client.chat_postMessage.call_args.kwargs["blocks"]
        self.assertEqual(2, len(rendered))
        self.assertEqual("A concise answer.", rendered[0]["text"]["text"])
        self.assertEqual("Configure", rendered[1]["elements"][0]["text"]["text"])

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
                "scripts.slack_socket_agent.codex_models_cache_path",
                return_value=cache,
            ), patch.dict(os.environ, {}, clear=True):
                models = slack_socket_agent.discover_codex_models()

        self.assertEqual(["gpt-visible"], [model.model_id for model in models])
        self.assertEqual(("low", "medium"), models[0].reasoning_efforts)
        self.assertTrue(models[0].supports_fast_mode)
        self.assertTrue(models[0].is_default)
        self.assertEqual("medium", models[0].default_reasoning_effort)

    def test_user_settings_survive_a_new_store_instance(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            path = Path(raw_dir) / "settings.json"
            settings = slack_socket_agent.AgentSettings("gpt-visible", "high", fast_mode=True)
            slack_socket_agent.UserAgentSettingsStore(path).set("T1", "U1", settings)

            loaded = slack_socket_agent.UserAgentSettingsStore(path).get("T1", "U1")

        self.assertEqual(settings, loaded)

    def test_configured_codex_model_and_thinking_are_the_reset_defaults(self) -> None:
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
                models = slack_socket_agent.discover_codex_models()

        configured = next(model for model in models if model.is_default)
        self.assertEqual("gpt-configured", configured.model_id)
        self.assertEqual("high", configured.default_reasoning_effort)
        self.assertEqual(
            slack_socket_agent.AgentSettings("gpt-configured", "high", fast_mode=True),
            slack_socket_agent.default_agent_settings(models),
        )

    def test_user_settings_without_fast_mode_leave_it_unset(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            path = Path(raw_dir) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "T1:U1": {
                            "model": "gpt-visible",
                            "reasoning_effort": "medium",
                        }
                    }
                ),
                encoding="utf-8",
            )

            loaded = slack_socket_agent.UserAgentSettingsStore(path).get("T1", "U1")

        self.assertIsNone(loaded.fast_mode)

    def test_user_settings_are_isolated_between_users(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            path = Path(raw_dir) / "settings.json"
            store = slack_socket_agent.UserAgentSettingsStore(path)
            settings = slack_socket_agent.AgentSettings("gpt-visible", "high", fast_mode=True)
            store.set("T1", "U1", settings)

            self.assertEqual(settings, store.get("T1", "U1"))
            self.assertEqual(slack_socket_agent.AgentSettings(), store.get("T1", "U2"))

    def test_saved_user_settings_follow_user_across_channels_and_threads(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        models = [
            slack_socket_agent.CodexModelOption(
                "gpt-visible",
                "GPT Visible",
                ("low", "high"),
                supports_fast_mode=True,
            )
        ]
        with tempfile.TemporaryDirectory() as raw_dir, patch.object(
            slack_socket_agent, "App", return_value=fake_app
        ), patch.dict(
            os.environ,
            {
                "SLACK_BOT_TOKEN": "xoxb-test",
                "SLACK_CHANNEL_IDS": "C123,C456",
                "OPENTAG_SLACK_SETTINGS_FILE": str(Path(raw_dir) / "settings.json"),
            },
            clear=True,
        ), patch.object(slack_socket_agent, "discover_codex_models", return_value=models):
            slack_socket_agent.create_app("codex", 30, frozenset({"UOWNER"}))
            save = fake_app.views[slack_socket_agent.SETTINGS_VIEW_ID]
            save(
                MagicMock(),
                {
                    "user": {"id": "UOWNER"},
                    "view": {
                        "private_metadata": json.dumps(
                            {"team": "T123", "channel": "C123", "thread_ts": "1.23"}
                        ),
                        "state": {
                            "values": {
                                "model": {
                                    slack_socket_agent.SETTINGS_MODEL_ACTION_ID: {
                                        "selected_option": {"value": "gpt-visible"}
                                    }
                                },
                                "reasoning_effort": {
                                    slack_socket_agent.SETTINGS_EFFORT_ACTION_ID: {
                                        "selected_option": {"value": "high"}
                                    }
                                },
                                "fast_mode": {
                                    slack_socket_agent.SETTINGS_FAST_ACTION_ID: {
                                        "selected_options": [{"value": "on"}]
                                    }
                                },
                            }
                        },
                    },
                },
                client,
                MagicMock(),
            )

            open_settings = fake_app.actions[slack_socket_agent.SETTINGS_ACTION_ID]
            open_settings(
                MagicMock(),
                {
                    "actions": [
                        {
                            "value": json.dumps(
                                {
                                    "team": "T123",
                                    "channel": "C456",
                                    "thread_ts": "9.87",
                                }
                            )
                        }
                    ],
                    "trigger_id": "trigger",
                    "user": {"id": "UOWNER"},
                },
                client,
                MagicMock(),
            )

        modal = client.views_open.call_args.kwargs["view"]
        self.assertEqual(
            "gpt-visible", modal["blocks"][0]["element"]["initial_option"]["value"]
        )
        self.assertEqual("high", modal["blocks"][1]["element"]["initial_option"]["value"])
        self.assertEqual(
            "on", modal["blocks"][2]["accessory"]["initial_options"][0]["value"]
        )
        self.assertIn(
            "Applied to your future Slack requests",
            client.chat_postEphemeral.call_args.kwargs["text"],
        )

    def test_modal_limits_reasoning_to_selected_model(self) -> None:
        models = [
            slack_socket_agent.CodexModelOption(
                "gpt-visible",
                "GPT Visible",
                ("low", "high"),
                supports_fast_mode=True,
            )
        ]
        modal = slack_socket_agent.settings_modal(
            metadata={"team": "T1", "channel": "C1", "thread_ts": "1.23"},
            settings=slack_socket_agent.AgentSettings(
                "gpt-visible",
                "high",
                fast_mode=True,
            ),
            models=models,
        )

        effort_element = modal["blocks"][1]["element"]
        self.assertTrue(modal["blocks"][0]["dispatch_action"])
        self.assertEqual("Codex settings", modal["title"]["text"])
        self.assertEqual("static_select", effort_element["type"])
        self.assertEqual(
            ["low", "high"],
            [option["value"] for option in effort_element["options"]],
        )
        self.assertTrue(
            all("description" not in option for option in effort_element["options"])
        )
        self.assertEqual("high", effort_element["initial_option"]["value"])
        model_element = modal["blocks"][0]["element"]
        self.assertEqual(
            ["gpt-visible"],
            [option["value"] for option in model_element["options"]],
        )
        self.assertTrue(
            all("description" not in option for option in model_element["options"])
        )
        fast_block = modal["blocks"][2]
        self.assertEqual("section", fast_block["type"])
        self.assertEqual("*Speed*", fast_block["text"]["text"])
        self.assertNotIn("optional", fast_block)
        fast_element = fast_block["accessory"]
        self.assertEqual("checkboxes", fast_element["type"])
        self.assertEqual(
            ["on"],
            [option["value"] for option in fast_element["options"]],
        )
        self.assertEqual("on", fast_element["initial_options"][0]["value"])
        reset_block = modal["blocks"][3]
        self.assertEqual("section", reset_block["type"])
        self.assertEqual(
            "_Applies to your future Slack requests._",
            reset_block["text"]["text"],
        )
        reset_button = reset_block["accessory"]
        self.assertEqual(slack_socket_agent.SETTINGS_RESET_ACTION_ID, reset_button["action_id"])
        self.assertEqual("Reset to default", reset_button["text"]["text"])

    def test_reset_button_restores_default_form_without_saving(self) -> None:
        fake_app = FakeApp()
        client = MagicMock()
        ack = MagicMock()
        metadata = {"team": "T123", "channel": "C123", "thread_ts": "1.23"}
        models = [
            slack_socket_agent.CodexModelOption(
                "gpt-visible",
                "GPT Visible",
                ("low", "high"),
                supports_fast_mode=True,
                default_fast_mode=True,
            )
        ]
        with patch.object(slack_socket_agent, "App", return_value=fake_app), patch.dict(
            os.environ,
            {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C123"},
            clear=True,
        ), patch.object(slack_socket_agent, "discover_codex_models", return_value=models):
            slack_socket_agent.create_app("codex", 30, frozenset({"UOWNER"}))
            handler = fake_app.actions[slack_socket_agent.SETTINGS_RESET_ACTION_ID]
            handler(
                ack,
                {
                    "user": {"id": "UOWNER"},
                    "view": {
                        "id": "V123",
                        "hash": "hash",
                        "private_metadata": json.dumps(metadata),
                    },
                },
                client,
                MagicMock(),
            )

        ack.assert_called_once_with()
        reset_view = client.views_update.call_args.kwargs["view"]
        self.assertEqual(
            "gpt-visible",
            reset_view["blocks"][0]["element"]["initial_option"]["value"],
        )
        self.assertEqual(
            "low",
            reset_view["blocks"][1]["element"]["initial_option"]["value"],
        )
        self.assertEqual(
            "on",
            reset_view["blocks"][2]["accessory"]["initial_options"][0]["value"],
        )
        self.assertRegex(reset_view["blocks"][0]["block_id"], r"^model_reset_\d+$")
        self.assertRegex(
            reset_view["blocks"][1]["block_id"],
            r"^reasoning_effort_reset_\d+$",
        )
        self.assertRegex(
            reset_view["blocks"][2]["block_id"],
            r"^fast_mode_reset_\d+$",
        )

    def test_modal_disables_fast_mode_for_unsupported_explicit_model(self) -> None:
        models = [
            slack_socket_agent.CodexModelOption(
                "gpt-standard",
                "GPT Standard",
                ("medium",),
            )
        ]

        modal = slack_socket_agent.settings_modal(
            metadata={"team": "T1", "channel": "C1", "thread_ts": "1.23"},
            settings=slack_socket_agent.AgentSettings(
                "gpt-standard",
                "medium",
                fast_mode=True,
            ),
            models=models,
        )

        fast_block = modal["blocks"][2]
        self.assertEqual("context", fast_block["type"])
        self.assertIn("unavailable", fast_block["elements"][0]["text"])

    def test_reads_fast_mode_checkbox_state(self) -> None:
        view = {
            "state": {
                "values": {
                    "fast_mode_reset_123": {
                        slack_socket_agent.SETTINGS_FAST_ACTION_ID: {
                            "selected_options": [{"value": "on"}]
                        }
                    }
                }
            }
        }

        self.assertTrue(slack_socket_agent.selected_fast_mode(view))
        self.assertFalse(slack_socket_agent.selected_fast_mode({"state": {"values": {}}}))

    def test_reads_select_state_after_modal_reset(self) -> None:
        view = {
            "state": {
                "values": {
                    "model_reset_123": {
                        slack_socket_agent.SETTINGS_MODEL_ACTION_ID: {
                            "selected_option": {"value": "gpt-visible"}
                        }
                    },
                    "reasoning_effort_reset_123": {
                        slack_socket_agent.SETTINGS_EFFORT_ACTION_ID: {
                            "selected_option": {"value": "high"}
                        }
                    },
                }
            }
        }

        self.assertEqual(
            "gpt-visible",
            slack_socket_agent.selected_setting(
                view,
                slack_socket_agent.SETTINGS_MODEL_ACTION_ID,
            ),
        )
        self.assertEqual(
            "high",
            slack_socket_agent.selected_setting(
                view,
                slack_socket_agent.SETTINGS_EFFORT_ACTION_ID,
            ),
        )

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
