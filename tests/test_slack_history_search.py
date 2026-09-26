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
        self.assertEqual(("<#C1>", "marketing"), requested_channel_names(grant))

    def test_id_only_slack_mentions_resolve_through_the_authorized_grant(self) -> None:
        grant = json.dumps(
            {
                **json.loads(GRANT),
                "request_text": "Search <#C1> and <#C2> for launch notes",
            }
        )
        channels = authorized_channels(grant)
        original_names = requested_channel_names(grant)

        self.assertEqual(("<#C1>", "<#C2>"), original_names)
        self.assertEqual(
            ("C1", "C2"),
            tuple(
                channel["id"]
                for channel in select_channels_for_request(
                    channels, ["general", "marketing"], original_names
                )
            ),
        )

    def test_unknown_id_only_slack_mention_still_fails_closed(self) -> None:
        grant = json.dumps(
            {
                **json.loads(GRANT),
                "request_text": "Search <#C9> for launch notes",
            }
        )
        with self.assertRaisesRegex(ValueError, "confirm the channel names"):
            requested_channel_names(grant)

    def test_channel_reference_ids_are_case_sensitive(self) -> None:
        grant = json.dumps(
            {
                **json.loads(GRANT),
                "request_text": "Search <#c1> for launch notes",
            }
        )
        with self.assertRaisesRegex(ValueError, "not authorized"):
            requested_channel_names(grant)

    def test_case_distinct_authorized_ids_resolve_independently(self) -> None:
        grant = json.dumps(
            {
                "mode": "all",
                "channels": [
                    *json.loads(GRANT)["channels"],
                    {
                        "id": "c1",
                        "name": "lowercase-id",
                        "scope": "slack://tag-t1/channels/lowercase-id__c1",
                    },
                ],
                "request_text": "Search <#C1> and <#c1> for launch notes",
            }
        )
        self.assertEqual(
            ("<#C1>", "<#c1>"), requested_channel_names(grant)
        )

    def test_unknown_reference_id_cannot_match_an_authorized_channel_name(self) -> None:
        grant = json.dumps(
            {
                "mode": "all",
                "channels": [
                    {
                        "id": "C1",
                        "name": "c9",
                        "scope": "slack://tag-t1/channels/c9__C1",
                    }
                ],
                "request_text": "Search <#C9> for launch notes",
            }
        )
        with self.assertRaisesRegex(ValueError, "not authorized"):
            requested_channel_names(grant)

    def test_issue_references_are_not_treated_as_channel_names(self) -> None:
        grant = json.dumps(
            {
                **json.loads(GRANT),
                "request_text": "Search #1234 and #marketing for launch notes",
            }
        )
        self.assertEqual(("marketing",), requested_channel_names(grant))

    def test_authorized_numeric_channel_name_is_preserved_and_selected(self) -> None:
        grant = json.dumps(
            {
                "mode": "all",
                "channels": [
                    *json.loads(GRANT)["channels"],
                    {
                        "id": "C3",
                        "name": "1234",
                        "scope": "slack://tag-t1/channels/1234__C3",
                    },
                ],
                "request_text": "Search #1234 for launch notes",
            }
        )
        channels = authorized_channels(grant)
        original_names = requested_channel_names(grant)

        self.assertEqual(("1234",), original_names)
        self.assertEqual(
            ("C3",),
            tuple(
                channel["id"]
                for channel in select_channels_for_request(
                    channels, ["1234"], original_names
                )
            ),
        )

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

    def test_id_references_select_exact_channels_with_duplicate_names(self) -> None:
        channels = [
            {"id": "C1", "name": "general", "scope": "slack://one/general"},
            {"id": "C2", "name": "General", "scope": "slack://two/general"},
        ]
        for request, expected in (
            ("Search <#C2> for notes", ("C2",)),
            ("Search <#C2|stale-name> for notes", ("C2",)),
            ("Search <#C1> and <#C2> for notes", ("C1", "C2")),
            ("Search <#C2> and <#C2> for notes", ("C2",)),
        ):
            with self.subTest(request=request):
                grant = json.dumps(
                    {"mode": "all", "channels": channels, "request_text": request}
                )
                selected = select_channels_for_request(
                    authorized_channels(grant), ["general"], requested_channel_names(grant)
                )
                self.assertEqual(expected, tuple(channel["id"] for channel in selected))

    def test_plain_duplicate_name_remains_ambiguous_even_with_id_reference(self) -> None:
        for request in ("Search #general", "Search <#C1> and #general"):
            with self.subTest(request=request):
                grant = json.dumps({
                    "mode": "all",
                    "channels": [
                        {"id": "C1", "name": "general", "scope": "slack://one/general"},
                        {"id": "C2", "name": "general", "scope": "slack://two/general"},
                    ],
                    "request_text": request,
                })
                with self.assertRaisesRegex(ValueError, "confirm the channel names"):
                    select_channels_for_request(
                        authorized_channels(grant), ["general"], requested_channel_names(grant)
                    )

    def test_id_selection_never_falls_back_to_names(self) -> None:
        channels = ({"id": "C1", "name": "c9", "scope": "slack://one/c9"},)
        for reference in ("<#C9>", "<#c1>"):
            with self.subTest(reference=reference):
                with self.assertRaisesRegex(ValueError, "not uniquely authorized"):
                    select_channels(channels, [reference])
