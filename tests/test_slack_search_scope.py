from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from scripts.slack_search_scope import (
    explicit_channel_names,
    parse_search_intent,
    plan_search_scopes,
)


class FakeSlackClient:
    def __init__(self, *, restricted: bool = False) -> None:
        self.user = {
            "id": "U1",
            "team_id": "T1",
            "is_restricted": restricted,
            "is_ultra_restricted": False,
        }
        self.channels = {
            "C1": {
                "id": "C1",
                "name": "general",
                "is_private": False,
                "is_member": True,
            },
            "C2": {
                "id": "C2",
                "name": "support",
                "is_private": False,
                "is_member": True,
            },
            "G3": {
                "id": "G3",
                "name": "leadership",
                "is_private": True,
                "is_member": True,
            },
        }
        self.members = {"C1": ["U1"], "C2": ["U1"], "G3": ["U1"]}
        self.info_calls: list[str] = []
        self.member_calls: list[tuple[str, str | None]] = []

    def users_info(self, *, user: str):
        return {"ok": True, "user": self.user}

    def conversations_info(self, *, channel: str):
        self.info_calls.append(channel)
        value = self.channels[channel]
        if isinstance(value, Exception):
            raise value
        return {"ok": True, "channel": value}

    def conversations_members(self, *, channel: str, limit: int, cursor: str | None = None):
        self.member_calls.append((channel, cursor))
        values = self.members[channel]
        if values and isinstance(values[0], list):
            page = 0 if cursor is None else int(cursor)
            pages = values
            return {
                "members": pages[page],
                "response_metadata": {"next_cursor": str(page + 1) if page + 1 < len(pages) else ""},
            }
        return {"members": values, "response_metadata": {"next_cursor": ""}}


class SearchIntentTests(unittest.TestCase):
    def test_explicit_names_support_slack_markup_without_treating_ids_as_names(self) -> None:
        self.assertEqual(
            ("general", "support"),
            explicit_channel_names(
                "search <#C123|general> and #support, but not the ID-only <#C999>"
            ),
        )

    def test_ordinary_request_keeps_current_channel(self) -> None:
        self.assertEqual("current", parse_search_intent("What did we decide yesterday?").mode)

    def test_named_channels_require_explicit_search_language(self) -> None:
        intent = parse_search_intent("Please search #support and #general for the launch")
        self.assertEqual(("support", "general"), intent.channel_names)
        self.assertEqual("current", parse_search_intent("Post this in #support").mode)

    def test_named_channels_can_use_ordinary_channel_list_wording(self) -> None:
        self.assertEqual(
            ("support", "engineering"),
            parse_search_intent("search in channels support and engineering for launch notes").channel_names,
        )
        self.assertEqual(
            ("support", "engineering"),
            parse_search_intent("check support and engineering channels").channel_names,
        )
        self.assertEqual(
            ("équipe",), parse_search_intent("search #équipe for the update").channel_names
        )

    def test_explicit_workspace_wide_phrases_are_supported(self) -> None:
        for request in (
            "look across Slack for this customer",
            "check all channels I can access",
            "run a workspace-wide search",
        ):
            with self.subTest(request=request):
                self.assertEqual("all", parse_search_intent(request).mode)

    def test_distribution_request_does_not_expand_read_scope(self) -> None:
        self.assertEqual(
            "current", parse_search_intent("Post this announcement across all channels").mode
        )

    def test_vague_broad_words_require_clarification(self) -> None:
        self.assertEqual("clarify", parse_search_intent("search general workspace all").mode)

    def test_mixed_named_and_all_scope_requires_clarification(self) -> None:
        self.assertEqual(
            "clarify", parse_search_intent("search #support and all channels").mode
        )


class ScopePlanningTests(unittest.TestCase):
    def plan(self, request: str, client: FakeSlackClient | None = None, **kwargs):
        return plan_search_scopes(
            request_text=request,
            current_channel_id=kwargs.pop("current_channel_id", "C1"),
            caller_id=kwargs.pop("caller_id", "U1"),
            team_id=kwargs.pop("team_id", "T1"),
            configured_channels=kwargs.pop("configured_channels", "C1,C2,G3"),
            client=client or FakeSlackClient(),
            **kwargs,
        )

    def test_all_search_grants_the_callers_visible_channels_and_public_search(self) -> None:
        plan = self.plan("search all channels")
        self.assertEqual("all", plan.mode)
        self.assertEqual(("C1", "C2", "G3"), plan.channel_ids)
        self.assertEqual({"C1": "general", "C2": "support", "G3": "leadership"}, plan.channel_labels)
        self.assertTrue(plan.public_search)
        self.assertEqual({"id": "G3", "name": "leadership", "private": True}, plan.as_dict()["channels"][2])

    def test_guests_get_only_channels_they_are_in_and_no_public_search(self) -> None:
        client = FakeSlackClient(restricted=True)
        client.members["C1"] = []
        plan = self.plan("search all channels", client)
        self.assertEqual(("C2", "G3"), plan.channel_ids)
        self.assertFalse(plan.public_search)

    def test_default_is_the_current_conversation_without_slack_calls(self) -> None:
        client = FakeSlackClient()
        plan = self.plan("What happened?", client)
        self.assertEqual(("current", ()), (plan.mode, plan.channels))
        self.assertEqual([], client.info_calls)

    def test_named_search_uses_stable_id_and_refreshed_name(self) -> None:
        client = FakeSlackClient()
        client.channels["C1"]["name"] = "company-news"
        plan = self.plan("search #company-news and #support", client)
        self.assertEqual("named", plan.mode)
        self.assertEqual(("C1", "C2"), plan.channel_ids)
        self.assertEqual("company-news", plan.channel_labels["C1"])

    def test_all_search_leaves_out_what_it_cannot_verify(self) -> None:
        client = FakeSlackClient()
        client.members["G3"] = []
        plan = self.plan("check all channels I can access", client, configured_channels="C1,C2,G3,C4")
        self.assertEqual(("C1", "C2"), plan.channel_ids)
        self.assertIn("left out", plan.notice)

    def test_private_channel_requires_membership(self) -> None:
        client = FakeSlackClient()
        client.members["G3"] = []
        plan = self.plan("search #leadership", client)
        self.assertEqual("clarify", plan.mode)
        self.assertEqual((), plan.channels)

    def test_restricted_user_requires_membership_even_for_public_channel(self) -> None:
        client = FakeSlackClient(restricted=True)
        client.members["C2"] = []
        plan = self.plan("search #support", client)
        self.assertEqual("clarify", plan.mode)
        self.assertEqual((), plan.channels)

    def test_membership_pagination_is_followed(self) -> None:
        client = FakeSlackClient()
        client.members["G3"] = [["UOTHER"], ["U1"]]
        plan = self.plan("search #leadership", client)
        self.assertEqual("named", plan.mode)
        self.assertEqual([("G3", None), ("G3", "1")], client.member_calls)

    def test_archived_shared_and_slack_failures_fail_closed(self) -> None:
        for mutation in (
            {"is_archived": True},
            {"is_shared": True},
            {"is_ext_shared": True},
            {"is_org_shared": True},
            {"pending_shared": ["T2"]},
        ):
            client = FakeSlackClient()
            client.channels["C2"].update(mutation)
            with self.subTest(mutation=mutation):
                self.assertEqual("clarify", self.plan("search #support", client).mode)
        client = FakeSlackClient()
        client.channels["C2"] = RuntimeError("Slack unavailable")
        self.assertEqual("clarify", self.plan("search #support", client).mode)

    def test_typo_suggestion_only_uses_visible_authorized_channels(self) -> None:
        client = FakeSlackClient()
        client.members["G3"] = []
        plan = self.plan("search #suport and #leadershp", client)
        self.assertEqual("clarify", plan.mode)
        self.assertIn("#support", plan.clarification)
        self.assertNotIn("leadership", plan.clarification)

    def test_org_member_can_search_only_the_selected_workspace(self):
        client = FakeSlackClient()
        client.user["team_id"] = "T2"
        client.user["enterprise_user"] = {"enterprise_id": "EORG", "teams": ["T1", "T2"]}
        with patch.dict(os.environ, {"SLACK_ENTERPRISE_ID": "EORG"}):
            self.assertEqual(("C1", "C2", "G3"), self.plan("search all channels", client).channel_ids)
            client.user["enterprise_user"]["teams"] = ["T2"]
            self.assertEqual("denied", self.plan("search all channels", client).mode)
            client.user["enterprise_user"] = {"enterprise_id": "EOTHER", "teams": ["T1"]}
            self.assertEqual("denied", self.plan("search all channels", client).mode)

    def test_caller_and_workspace_identity_must_match(self) -> None:
        client = FakeSlackClient()
        self.assertEqual("denied", self.plan("look across Slack", client, team_id="T2").mode)
        client = FakeSlackClient()
        self.assertEqual("denied", self.plan("look across Slack", client, caller_id="U2").mode)


if __name__ == "__main__":
    unittest.main()
