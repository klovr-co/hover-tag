from __future__ import annotations

from scripts import setup_ui

import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from scripts import slack_channels


class SlackChannelPickerTests(unittest.TestCase):
    def test_missing_join_scope_opens_settings_without_reinstall_or_retry(self):
        channel = slack_channels.SlackChannel("C1", "general", False, False)
        with patch.object(setup_ui, "choose", side_effect=[0, 3, 2]), patch.object(
            slack_channels, "slack_api_post", side_effect=slack_channels.MissingScope("conversations.join", "channels:join")
        ) as join, patch.object(slack_channels, "open_settings") as browser, redirect_stdout(StringIO()) as output:
            with self.assertRaises(setup_ui.Paused):
                slack_channels.join_selected_channels("fixture", [channel], app_id="A123")
        browser.assert_called_once_with("A123")
        self.assertEqual(join.call_count, 1)
        self.assertIn("channels:join", output.getvalue())
        self.assertIn("reinstall the app", output.getvalue())

    def test_joined_private_channel_requires_explicit_picker_approval(self):
        private = slack_channels.SlackChannel("G1", "private-team", True, True)
        with patch.object(slack_channels, "list_channels", return_value=[private]), patch.object(
            setup_ui, "checklist", return_value={0}
        ), patch.object(setup_ui, "choose") as approve, patch.object(
            slack_channels, "slack_api_post"
        ) as join, redirect_stdout(StringIO()) as output:
            approve.return_value = 0
            self.assertEqual(slack_channels.choose_channels("fixture"), [private])
        self.assertEqual(approve.call_args.args[1], ["Continue with 1 selected channel(s)", "Check again", "Save and exit"])
        join.assert_not_called()
        self.assertIn("Choose which joined channels", output.getvalue())

    def test_no_public_channels_refreshes_after_private_invitation(self):
        private = slack_channels.SlackChannel("G1", "private-team", True, True)
        with patch.object(slack_channels, "list_channels", side_effect=[[], [private]]), patch.object(
            setup_ui, "choose", side_effect=[0, 0]
        ), patch.object(setup_ui, "checklist", return_value={0}) as picker, patch.object(
            slack_channels, "slack_api_post"
        ) as join, redirect_stdout(StringIO()) as output:
            self.assertEqual(slack_channels.choose_channels("fixture"), [private])
        join.assert_not_called()
        picker.assert_called_once()
        self.assertIn("/invite", output.getvalue())

    def test_empty_membership_refreshes_then_selects_without_exiting(self):
        joined = slack_channels.SlackChannel("C1", "team", False, True)
        unjoined = slack_channels.SlackChannel("C2", "other", False, False)
        with patch.object(slack_channels, "list_channels", side_effect=[[], [joined, unjoined]]) as listing, patch.object(
            setup_ui, "choose", side_effect=[0, 0]
        ), patch.object(setup_ui, "checklist", return_value={0}) as picker, redirect_stdout(StringIO()) as output:
            self.assertEqual(slack_channels.choose_channels("fixture"), [joined])
        self.assertEqual(listing.call_count, 2)
        picker.assert_called_once()
        self.assertIn("/invite", output.getvalue())

    def test_empty_membership_can_pause_without_selecting_any_channel(self):
        with patch.object(slack_channels, "list_channels", return_value=[]), patch.object(
            setup_ui, "choose", return_value=1
        ), patch.object(setup_ui, "checklist") as picker, redirect_stdout(StringIO()):
            with self.assertRaises(setup_ui.Paused):
                slack_channels.choose_channels("fixture")
        picker.assert_not_called()

    def test_picker_shows_unjoined_public_and_joins_only_selection(self):
        channels = [
            slack_channels.SlackChannel("C1", "selected", False, False),
            slack_channels.SlackChannel("C2", "not-selected", False, False),
            slack_channels.SlackChannel("G1", "hidden-private", True, False),
        ]
        with patch.object(slack_channels, "list_channels", return_value=channels), patch.object(
            setup_ui, "checklist", return_value={0}
        ) as picker, patch.object(setup_ui, "choose", side_effect=[0, 0]), patch.object(
            slack_channels, "slack_api_post", return_value={"ok": True, "channel": {"id": "C1", "is_member": True}}
        ) as join, redirect_stdout(StringIO()):
            chosen = slack_channels.choose_channels("fixture")
        self.assertEqual([c.channel_id for c in chosen], ["C1"])
        self.assertTrue(chosen[0].is_member)
        self.assertEqual(len(picker.call_args.args[0]), 2)
        join.assert_called_once_with("fixture", "conversations.join", {"channel": "C1"})

    def test_joined_channels_require_selection_and_public_channels_remain_optional(self):
        joined = slack_channels.SlackChannel("G1", "private-team", True, True)
        public = slack_channels.SlackChannel("C1", "announcements", False, False)
        with patch.object(slack_channels, "list_channels", return_value=[joined, public]), patch.object(
            setup_ui, "choose", return_value=0
        ) as choose, patch.object(setup_ui, "checklist", return_value={0}) as picker, redirect_stdout(StringIO()) as output:
            self.assertEqual(slack_channels.choose_channels("fixture"), [joined])
        self.assertEqual(choose.call_args.args[1], ["Continue with 1 selected channel(s)", "Add public channels", "Check again", "Save and exit"])
        picker.assert_called_once_with([joined.label], set())
        self.assertIn("Choose which joined channels", output.getvalue())

    def test_joined_channel_picker_preselects_only_saved_channels(self):
        joined = [
            slack_channels.SlackChannel("C1", "approved", False, True),
            slack_channels.SlackChannel("G2", "not-approved", True, True),
        ]
        with patch.object(slack_channels, "list_channels", return_value=joined), patch.object(
            setup_ui, "checklist", return_value={0}
        ) as picker, patch.object(setup_ui, "choose", return_value=0), redirect_stdout(StringIO()):
            selected = slack_channels.choose_channels("fixture", "C1")
        self.assertEqual([channel.channel_id for channel in selected], ["C1"])
        self.assertEqual(picker.call_args.args, ([channel.label for channel in joined], {0}))

    def test_join_requires_approval_and_skips_existing_members(self):
        channel = slack_channels.SlackChannel("C1", "selected", False, False)
        with patch.object(setup_ui, "choose", side_effect=[1, 2]), patch.object(
            slack_channels, "slack_api_post"
        ) as join, redirect_stdout(StringIO()):
            self.assertIsNone(slack_channels.join_selected_channels("fixture", [channel]))
            with self.assertRaises(setup_ui.Paused):
                slack_channels.join_selected_channels("fixture", [channel])
            member = slack_channels.SlackChannel("C1", "selected", False, True)
            self.assertEqual(slack_channels.join_selected_channels("fixture", [member]), [member])
        join.assert_not_called()

    def test_missing_join_scope_can_recover_after_manual_invite(self):
        channel = slack_channels.SlackChannel("C1", "selected", False, False)
        member = slack_channels.SlackChannel("C1", "selected", False, True)
        with patch.object(setup_ui, "choose", side_effect=[0, 0]), patch.object(
            slack_channels, "slack_api_post", side_effect=slack_channels.MissingScope("conversations.join", "channels:join")
        ) as join, patch.object(slack_channels, "list_channels", return_value=[member]), redirect_stdout(StringIO()) as output:
            self.assertEqual(slack_channels.join_selected_channels("fixture", [channel]), [member])
        self.assertEqual(join.call_count, 1)
        self.assertIn("channels:join", output.getvalue())

    def test_unconfirmed_join_cannot_return_selected_channel(self):
        channel = slack_channels.SlackChannel("C1", "selected", False, False)
        with patch.object(setup_ui, "choose", side_effect=[0, 2]), patch.object(
            slack_channels, "slack_api_post", return_value={"ok": True, "channel": {"id": "COTHER", "is_member": True}}
        ), redirect_stdout(StringIO()):
            with self.assertRaises(setup_ui.Paused):
                slack_channels.join_selected_channels("fixture", [channel])

    def test_join_posts_channel_as_form_data_without_exposing_token(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"ok":true,"channel":{"id":"C1","is_member":true}}'
        with patch.object(slack_channels.urllib.request, "urlopen", return_value=Response()) as request:
            slack_channels.slack_api_post("fixture", "conversations.join", {"channel": "C1"})
        sent = request.call_args.args[0]
        self.assertEqual(sent.get_method(), "POST")
        self.assertEqual(sent.data, b"channel=C1")
        self.assertNotIn("fixture", sent.full_url)

    def test_lists_all_pages_and_prioritizes_joined_channels(self) -> None:
        pages = [
            {
                "ok": True,
                "channels": [
                    {"id": "C2", "name": "general", "is_private": False, "is_member": False},
                ],
                "response_metadata": {"next_cursor": "next"},
            },
            {
                "ok": True,
                "channels": [
                    {"id": "G1", "name": "tag-sandbox", "is_private": True, "is_member": True},
                ],
                "response_metadata": {"next_cursor": ""},
            },
        ]
        with patch.object(slack_channels, "slack_api", side_effect=pages) as api:
            channels = slack_channels.list_channels("xoxb-secret")

        self.assertEqual(["G1", "C2"], [channel.channel_id for channel in channels])
        self.assertEqual("next", api.call_args_list[1].args[2]["cursor"])

    def test_picker_requires_membership_before_returning_channel_id(self) -> None:
        channels = [
            slack_channels.SlackChannel("C1", "team", False, False),
            slack_channels.SlackChannel("G2", "tag-sandbox", True, True),
        ]
        answers = iter(["1", "2"])
        output = StringIO()
        with patch.object(slack_channels, "list_channels", return_value=channels), redirect_stdout(output):
            selected = slack_channels.choose_channel("xoxb-secret", input_fn=lambda _: next(answers))

        self.assertEqual("G2", selected)
        self.assertIn("#team", output.getvalue())
        self.assertIn("Invite the bot to #team", output.getvalue())
        self.assertIn("🔒 #tag-sandbox", output.getvalue())

    def test_picker_can_explicitly_allow_any_joined_channel(self) -> None:
        channels = [slack_channels.SlackChannel("C1", "team", False, True)]
        with patch.object(slack_channels, "list_channels", return_value=channels), redirect_stdout(StringIO()):
            self.assertEqual("", slack_channels.choose_channel("xoxb-secret", input_fn=lambda _: "0"))

    def test_multi_picker_requires_at_least_one_joined_channel(self) -> None:
        channels = [
            slack_channels.SlackChannel("C1", "team", False, True),
            slack_channels.SlackChannel("G2", "private", True, True),
            slack_channels.SlackChannel("C3", "unjoined", False, False),
        ]
        answers = iter(["", "1,2"])
        with patch.object(slack_channels, "list_channels", return_value=channels), redirect_stdout(StringIO()):
            selected = slack_channels.choose_channels(
                "xoxb-secret", input_fn=lambda _: next(answers)
            )

        self.assertEqual(["C1", "G2"], [channel.channel_id for channel in selected])

    def test_api_errors_never_include_the_token(self) -> None:
        response = {"ok": False, "error": "invalid_auth"}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return __import__("json").dumps(response).encode()

        with patch.object(slack_channels.urllib.request, "urlopen", return_value=FakeResponse()):
            with self.assertRaises(slack_channels.SlackChannelError) as raised:
                slack_channels.list_channels("xoxb-super-secret")
        self.assertNotIn("xoxb-super-secret", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
