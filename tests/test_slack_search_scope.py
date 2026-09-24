from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.slack_search_scope import (
    explicit_channel_names,
    indexed_slack_channels,
    mfs_scope_is_indexed,
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


SCOPES = ",".join(
    (
        "slack://tag-t1/channels/old-general__C1",
        "slack://tag-t1/channels/support__C2",
        "slack://tag-t1/channels/leadership__G3",
        "slack://tag-t2/channels/foreign__C9",
        "file://local/repo",
    )
)


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


class IndexedScopeTests(unittest.TestCase):
    def test_app_scoped_connectors_preserve_exact_workspace_boundary(self) -> None:
        for authority in ("tag-t1-a123", "TAG-T1-A123", "tag-t1"):
            with self.subTest(authority=authority):
                scope = f"slack://{authority}/channels/general__C1"
                self.assertEqual(scope, indexed_slack_channels(scope, "T1")["C1"].scope)
        for authority in (
            "tag-t2-a123", "tag-t10-a123", "tag-t1-other", "tag-t1-a",
            "tag-t1-a123-extra", "tag-t1-a123:80", "user@tag-t1-a123",
        ):
            with self.subTest(authority=authority):
                self.assertEqual({}, indexed_slack_channels(
                    f"slack://{authority}/channels/general__C1", "T1"
                ))

    def test_indexes_only_exact_workspace_channel_scopes(self) -> None:
        indexed = indexed_slack_channels(SCOPES, "T1")
        self.assertEqual({"C1", "C2", "G3"}, set(indexed))
        self.assertEqual("slack://tag-t1/channels/old-general__C1", indexed["C1"].scope)

    def test_live_mfs_check_requires_a_listable_scope(self) -> None:
        with patch(
            "scripts.slack_search_scope.mfs_request_json",
            side_effect=(
                {
                    "entries": [
                        {"path": "slack://tag-t1/channels/renamed-support__C2"}
                    ]
                },
                {
                    "entries": [
                        {"name": "messages.jsonl", "search_status": "indexed"}
                    ]
                },
            ),
        ) as request:
            self.assertTrue(mfs_scope_is_indexed("slack://tag-t1/channels/support__C2"))
        self.assertEqual(
            [
                unittest.mock.call(
                    "/v1/ls", {"path": "slack://tag-t1/channels"}
                ),
                unittest.mock.call(
                    "/v1/ls",
                    {"path": "slack://tag-t1/channels/renamed-support__C2"},
                ),
            ],
            request.call_args_list,
        )
        with patch(
            "scripts.slack_search_scope.mfs_request_json",
            side_effect=OSError("MFS unavailable"),
        ):
            self.assertFalse(mfs_scope_is_indexed("slack://tag-t1/channels/support__C2"))


class ScopePlanningTests(unittest.TestCase):
    def plan(self, request: str, client: FakeSlackClient | None = None, **kwargs):
        return plan_search_scopes(
            request_text=request,
            current_channel_id=kwargs.pop("current_channel_id", "C1"),
            caller_id=kwargs.pop("caller_id", "U1"),
            team_id=kwargs.pop("team_id", "T1"),
            configured_channels=kwargs.pop("configured_channels", "C1,C2,G3,C4"),
            allowed_scopes=kwargs.pop("allowed_scopes", SCOPES),
            client=client or FakeSlackClient(),
            scope_is_indexed=kwargs.pop("scope_is_indexed", lambda _scope: True),
            **kwargs,
        )

    def test_setup_app_scopes_produce_search_grant_with_visibility_checks(self) -> None:
        from scripts.opentag_setup import connector_scope
        from scripts.slack_channels import SlackChannel

        scope = connector_scope("T1", SlackChannel("C1", "general", False, True), "A123")
        plan = self.plan("search all channels", allowed_scopes=scope)
        self.assertEqual("all", plan.mode)
        self.assertEqual((scope,), plan.scopes)
        self.assertEqual({"C1": "general"}, plan.channel_labels)

        client = FakeSlackClient(restricted=True)
        client.members["C1"] = []
        denied = self.plan("search all channels", client, allowed_scopes=scope)
        self.assertEqual("denied", denied.mode)
        self.assertEqual((), denied.scopes)

    def test_default_is_byte_for_byte_current_scope_without_slack_calls(self) -> None:
        client = FakeSlackClient()
        plan = self.plan("What happened?", client)
        self.assertEqual(("slack://tag-t1/channels/old-general__C1",), plan.scopes)
        self.assertEqual([], client.info_calls)

    def test_named_search_uses_stable_id_and_refreshed_name(self) -> None:
        client = FakeSlackClient()
        client.channels["C1"]["name"] = "company-news"
        plan = self.plan("search #company-news and #support", client)
        self.assertEqual("named", plan.mode)
        self.assertEqual(("C1", "C2"), tuple(channel.channel_id for channel in plan.channels))
        self.assertEqual("company-news", plan.channel_labels["C1"])
        self.assertIn("old-general__C1", plan.scopes[0])

    def test_rename_refreshes_mfs_path_by_stable_id(self) -> None:
        client = FakeSlackClient()
        client.channels["C1"]["name"] = "company-news"
        plan = self.plan(
            "search #company-news",
            client,
            resolve_indexed_scope=lambda channel: (
                "slack://tag-t1/channels/company-news__C1"
                if channel.channel_id == "C1"
                else channel.scope
            ),
        )
        self.assertEqual(
            ("slack://tag-t1/channels/company-news__C1",), plan.scopes
        )

    def test_all_search_intersects_configured_indexed_and_visible(self) -> None:
        client = FakeSlackClient()
        client.members["G3"] = []
        plan = self.plan("check all channels I can access", client)
        self.assertEqual(("C1", "C2"), tuple(channel.channel_id for channel in plan.channels))
        self.assertNotIn("C4", client.info_calls)  # configured, but not indexed
        self.assertNotIn("C9", client.info_calls)  # indexed, but another workspace
        self.assertIn("Some channels were omitted", plan.notice)

    def test_stale_or_unreachable_mfs_scope_fails_closed_before_slack_lookup(self) -> None:
        client = FakeSlackClient()
        plan = self.plan(
            "search #support",
            client,
            scope_is_indexed=lambda scope: not scope.endswith("__C2"),
        )
        self.assertEqual("clarify", plan.mode)
        self.assertNotIn("C2", client.info_calls)

    def test_private_channel_requires_membership(self) -> None:
        client = FakeSlackClient()
        client.members["G3"] = []
        plan = self.plan("search #leadership", client)
        self.assertEqual("clarify", plan.mode)
        self.assertEqual((), plan.scopes)

    def test_restricted_user_requires_membership_even_for_public_channel(self) -> None:
        client = FakeSlackClient(restricted=True)
        client.members["C2"] = []
        plan = self.plan("search #support", client)
        self.assertEqual("clarify", plan.mode)
        self.assertEqual((), plan.scopes)

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

    def test_caller_and_workspace_identity_must_match(self) -> None:
        client = FakeSlackClient()
        self.assertEqual("denied", self.plan("look across Slack", client, team_id="T2").mode)
        client = FakeSlackClient()
        self.assertEqual("denied", self.plan("look across Slack", client, caller_id="U2").mode)


if __name__ == "__main__":
    unittest.main()
