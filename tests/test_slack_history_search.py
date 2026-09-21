from __future__ import annotations

import json
import unittest

from scripts.slack_history_search import (
    authorized_channels,
    requested_channel_names,
    select_channels,
    select_channels_for_request,
)


GRANT = json.dumps(
    {
        "mode": "all",
        "channels": [
            {"id": "C1", "name": "general", "scope": "slack://tag-t1/channels/general__C1"},
            {"id": "C2", "name": "marketing", "scope": "slack://tag-t1/channels/marketing__C2"},
        ],
    }
)


class SlackHistorySearchTests(unittest.TestCase):
    def test_uses_only_channels_in_bridge_grant(self) -> None:
        channels = authorized_channels(GRANT)
        selected = select_channels(channels, ["#Marketing"])
        self.assertEqual(("C2",), tuple(channel["id"] for channel in selected))

    def test_no_names_selects_all_authorized_channels(self) -> None:
        channels = authorized_channels(GRANT)
        self.assertEqual(channels, select_channels(channels, []))

    def test_ungranted_channel_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not uniquely authorized"):
            select_channels(authorized_channels(GRANT), ["leadership"])

    def test_malformed_grant_fails_closed(self) -> None:
        self.assertEqual((), authorized_channels("not json"))
        self.assertEqual(
            (),
            authorized_channels(
                '{"mode":"all","channels":[{"id":"C1","name":"general"}]}'
            ),
        )

    def test_original_slack_request_names_are_bound_to_the_grant(self) -> None:
        grant = json.dumps(
            {
                **json.loads(GRANT),
                "request_text": "Search <#C1|general> and #marketing for launch notes",
            }
        )
        self.assertEqual(("general", "marketing"), requested_channel_names(grant))

    def test_issue_references_are_not_treated_as_channel_names(self) -> None:
        grant = json.dumps(
            {
                **json.loads(GRANT),
                "request_text": "Search #1234 and #marketing for launch notes",
            }
        )
        self.assertEqual(("marketing",), requested_channel_names(grant))

    def test_typo_cannot_be_silently_corrected_by_the_agent(self) -> None:
        channels = authorized_channels(GRANT)
        with self.assertRaisesRegex(ValueError, r"did you mean #general"):
            select_channels_for_request(channels, ["general"], ("genral",))
        with self.assertRaisesRegex(ValueError, r"did you mean #general"):
            select_channels_for_request(channels, ["genral"], ("genral",))

    def test_named_request_cannot_fall_back_to_all_channels(self) -> None:
        with self.assertRaisesRegex(ValueError, "confirm the channel names"):
            select_channels_for_request(
                authorized_channels(GRANT), [], ("general", "marketing")
            )
