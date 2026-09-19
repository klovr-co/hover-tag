from __future__ import annotations

import json
import os
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
    def test_tag_is_the_backend_independent_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(slack_socket_agent.suggested_bot_name("codex"), "Tag")
            self.assertEqual(slack_socket_agent.suggested_bot_name("claude"), "Tag")

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


class SlackWorkingIndicatorTests(unittest.TestCase):
    def test_uses_native_slack_loading_status(self) -> None:
        client = MagicMock()
        indicator = slack_socket_agent.WorkingIndicator(client, "C123", "1.23", MagicMock())

        with patch("scripts.slack_socket_agent.threading.Timer") as timer:
            indicator.start()
            indicator.clear()

        first_call = client.assistant_threads_setStatus.call_args_list[0].kwargs
        self.assertEqual("is working on this…", first_call["status"])
        self.assertEqual(slack_socket_agent.LOADING_MESSAGES, first_call["loading_messages"])
        self.assertEqual("", client.assistant_threads_setStatus.call_args_list[1].kwargs["status"])
        timer.assert_called_once_with(
            slack_socket_agent.STATUS_REFRESH_SECONDS,
            indicator.refresh,
        )
        timer.return_value.start.assert_called_once()
        timer.return_value.cancel.assert_called_once()
        client.chat_postMessage.assert_not_called()

    def test_falls_back_to_temporary_message_when_native_status_fails(self) -> None:
        client = MagicMock()
        client.assistant_threads_setStatus.side_effect = RuntimeError("unsupported")
        client.chat_postMessage.return_value = {"ts": "2.34"}
        indicator = slack_socket_agent.WorkingIndicator(client, "C123", "1.23", MagicMock())

        indicator.start()
        indicator.clear()

        self.assertFalse(indicator.native)
        self.assertEqual("2.34", indicator.message_ts)
        client.chat_postMessage.assert_called_once()
        client.assistant_threads_setStatus.assert_called_once()


class SlackAnswerStreamTests(unittest.TestCase):
    def test_batches_deltas_and_finishes_stream(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        stream = slack_socket_agent.SlackAnswerStream(client, "C123", "1.23", MagicMock())

        stream.append("a" * slack_socket_agent.STREAM_START_CHARS)
        stream.append("b" * slack_socket_agent.STREAM_APPEND_CHARS)
        finished = stream.finish(stream.received)

        self.assertTrue(finished)
        client.chat_startStream.assert_called_once()
        client.chat_appendStream.assert_called_once()
        client.chat_stopStream.assert_called_once_with(channel="C123", ts="3.45")

    def test_no_deltas_uses_normal_message_fallback(self) -> None:
        client = MagicMock()
        stream = slack_socket_agent.SlackAnswerStream(client, "C123", "1.23", MagicMock())

        self.assertFalse(stream.finish("Complete Codex answer"))
        client.chat_startStream.assert_not_called()

    def test_start_failure_preserves_complete_answer_fallback(self) -> None:
        client = MagicMock()
        client.chat_startStream.side_effect = RuntimeError("not supported")
        stream = slack_socket_agent.SlackAnswerStream(client, "C123", "1.23", MagicMock())

        stream.append("a" * slack_socket_agent.STREAM_START_CHARS)

        self.assertTrue(stream.failed)
        self.assertFalse(stream.finish(stream.received))

    def test_finishes_stream_with_settings_blocks(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        blocks = [{"type": "actions", "elements": []}]
        stream = slack_socket_agent.SlackAnswerStream(client, "C123", "1.23", MagicMock())

        stream.append("a" * slack_socket_agent.STREAM_START_CHARS)

        self.assertTrue(stream.finish(stream.received, blocks))
        client.chat_stopStream.assert_called_once_with(channel="C123", ts="3.45", blocks=blocks)


class SlackAgentSettingsTests(unittest.TestCase):
    def test_discovers_visible_codex_models_and_reasoning_levels(self) -> None:
        payload = {
            "models": [
                {
                    "slug": "gpt-visible",
                    "display_name": "GPT Visible",
                    "visibility": "list",
                    "default_reasoning_level": "medium",
                    "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}],
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

    def test_thread_settings_survive_a_new_store_instance(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            path = Path(raw_dir) / "settings.json"
            settings = slack_socket_agent.AgentSettings("gpt-visible", "high")
            slack_socket_agent.ThreadAgentSettingsStore(path).set("T1", "C1", "1.23", settings)

            loaded = slack_socket_agent.ThreadAgentSettingsStore(path).get("T1", "C1", "1.23")

        self.assertEqual(settings, loaded)

    def test_modal_limits_reasoning_to_selected_model(self) -> None:
        models = [
            slack_socket_agent.CodexModelOption(
                "gpt-visible",
                "GPT Visible",
                ("low", "high"),
            )
        ]
        modal = slack_socket_agent.settings_modal(
            metadata={"team": "T1", "channel": "C1", "thread_ts": "1.23"},
            settings=slack_socket_agent.AgentSettings("gpt-visible", "high"),
            models=models,
        )

        effort_element = modal["blocks"][1]["element"]
        self.assertEqual(
            [slack_socket_agent.DEFAULT_CONFIG_VALUE, "low", "high"],
            [option["value"] for option in effort_element["options"]],
        )
        self.assertEqual("high", effort_element["initial_option"]["value"])


class SlackStreamingConfigurationTests(unittest.TestCase):
    def test_streaming_defaults_on_and_can_be_disabled(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(slack_socket_agent.env_enabled("OPENTAG_SLACK_STREAMING", default=True))
        with patch.dict(os.environ, {"OPENTAG_SLACK_STREAMING": "0"}, clear=True):
            self.assertFalse(slack_socket_agent.env_enabled("OPENTAG_SLACK_STREAMING", default=True))
