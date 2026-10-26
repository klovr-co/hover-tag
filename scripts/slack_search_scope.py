#!/usr/bin/env python3
"""Deterministic resolution and authorization for live Slack search.

The Slack bridge and the ``slack_search`` helper deliberately share this
policy. Natural-language parsing can request a broader scope, but only this
module may turn that request into the stable channel IDs a run may read.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Protocol

try:
    from .slack_channels import parse_channel_ids, slack_api
except ImportError:  # Direct execution: python3 scripts/slack_search_scope.py
    from slack_channels import parse_channel_ids, slack_api


CHANNEL_ID_RE = re.compile(r"^[CG][A-Z0-9]+$")
CHANNEL_REF_RE = re.compile(r"(?<![\w])#([\w][\w-]{0,79})", re.IGNORECASE)
SLACK_CHANNEL_REF_RE = re.compile(
    r"<#(?P<id>[CG][A-Z0-9]+)(?:\|(?P<name>[^>]+))?>", re.IGNORECASE
)
CHANNEL_NAME = r"[\w][\w-]{0,79}"
NAMED_CHANNELS_RE = (
    re.compile(
        rf"\b(?:search|look|check|find|scan|review|summari[sz]e)\b\s+"
        rf"(?:in|through)\s+(?:the\s+)?channels?\s+"
        rf"(?P<names>{CHANNEL_NAME}(?:(?:\s*,\s*|\s+and\s+){CHANNEL_NAME})*)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:search|look|check|find|scan|review|summari[sz]e)\b\s+"
        rf"(?P<names>{CHANNEL_NAME}(?:(?:\s*,\s*|\s+and\s+){CHANNEL_NAME})*)\s+channels?\b",
        re.IGNORECASE,
    ),
)
SEARCH_VERB_RE = re.compile(
    r"\b(?:search|look|check|find|scan|review|summari[sz]e|investigate)\b",
    re.IGNORECASE,
)
ALL_CHANNELS_RE = re.compile(
    r"(?:"
    r"\b(?:search|look|check|find|scan|review|summari[sz]e)\b.{0,45}"
    r"\b(?:all|every)\s+(?:the\s+)?(?:slack\s+)?channels?\b"
    r"|\b(?:search|look|check|find|scan|review|summari[sz]e)\b.{0,35}"
    r"\bacross\s+(?:all\s+)?(?:of\s+)?slack\b"
    r"|\b(?:search|look|check|find|scan|review|summari[sz]e)\b.{0,35}"
    r"\bacross\s+(?:the\s+)?workspace\b"
    r"|\b(?:workspace[- ]wide|all[- ]channel)\s+(?:slack\s+)?search\b"
    r"|\b(?:search|look|check|find|scan|review|summari[sz]e)\b.{0,45}"
    r"\bchannels?\s+(?:that\s+)?i\s+can\s+access\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class SearchIntent:
    mode: str
    channel_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class VisibleChannel:
    channel_id: str
    name: str
    private: bool = False


@dataclass(frozen=True)
class ScopePlan:
    mode: str
    channels: tuple[VisibleChannel, ...] = ()
    skipped_names: tuple[str, ...] = ()
    clarification: str = ""
    notice: str = ""
    # Whether the requester may have the Tag search public channels workspace-wide
    # (Slack's Real-Time Search). Slack doesn't let apps search for guests.
    public_search: bool = False

    @property
    def channel_ids(self) -> tuple[str, ...]:
        return tuple(channel.channel_id for channel in self.channels)

    @property
    def channel_labels(self) -> dict[str, str]:
        return {channel.channel_id: channel.name for channel in self.channels}

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "channels": [
                {"id": channel.channel_id, "name": channel.name, "private": channel.private}
                for channel in self.channels
            ],
            "public_search": self.public_search,
            "skipped": list(self.skipped_names),
            "clarification": self.clarification or None,
            "notice": self.notice or None,
        }


class SlackClient(Protocol):
    def users_info(self, *, user: str) -> dict[str, Any]: ...

    def conversations_info(self, *, channel: str) -> dict[str, Any]: ...

    def conversations_members(
        self, *, channel: str, limit: int, cursor: str | None = None
    ) -> dict[str, Any]: ...


class HttpSlackClient:
    """Small Web API adapter used only by the backend-neutral CLI."""

    def __init__(self, token: str) -> None:
        self.token = token

    def users_info(self, *, user: str) -> dict[str, Any]:
        return slack_api(self.token, "users.info", {"user": user})

    def conversations_info(self, *, channel: str) -> dict[str, Any]:
        return slack_api(self.token, "conversations.info", {"channel": channel})

    def conversations_members(
        self, *, channel: str, limit: int, cursor: str | None = None
    ) -> dict[str, Any]:
        parameters = {"channel": channel, "limit": str(limit)}
        if cursor:
            parameters["cursor"] = cursor
        return slack_api(self.token, "conversations.members", parameters)


def normalize_channel_name(value: str) -> str:
    return value.strip().lstrip("#").casefold()


def explicit_channel_names(text: str) -> tuple[str, ...]:
    """Return explicit channel names without mistaking Slack IDs for names."""
    names = [
        normalize_channel_name(match.group("name"))
        for match in SLACK_CHANNEL_REF_RE.finditer(text)
        if match.group("name")
    ]
    plain_text = SLACK_CHANNEL_REF_RE.sub(" ", text)
    names.extend(normalize_channel_name(name) for name in CHANNEL_REF_RE.findall(plain_text))
    return tuple(dict.fromkeys(name for name in names if name))


def _plain_channel_names(text: str) -> tuple[str, ...]:
    for pattern in NAMED_CHANNELS_RE:
        match = pattern.search(text)
        if match:
            return tuple(
                dict.fromkeys(
                    normalize_channel_name(name)
                    for name in re.split(r"\s*,\s*|\s+and\s+", match.group("names"), flags=re.IGNORECASE)
                )
            )
    return ()


def parse_search_intent(text: str) -> SearchIntent:
    """Recognize only explicit broadening; uncertain wording asks for clarification."""
    names = explicit_channel_names(text)
    if not names:
        names = _plain_channel_names(text)
    if names and all(name in {"all", "every"} for name in names):
        names = ()
    has_retrieval_cue = bool(SEARCH_VERB_RE.search(text))
    requests_all = bool(ALL_CHANNELS_RE.search(text))
    if names and requests_all:
        return SearchIntent("clarify")
    if names and has_retrieval_cue:
        return SearchIntent("named", names)
    if requests_all:
        return SearchIntent("all")

    broad_terms = sum(
        bool(re.search(pattern, text, re.IGNORECASE))
        for pattern in (r"\ball\b", r"\bacross\b", r"\bworkspace\b", r"\bslack\b", r"\bchannels?\b")
    )
    if has_retrieval_cue and broad_terms >= 2:
        return SearchIntent("clarify")
    return SearchIntent("current")


def _response_mapping(response: Any, key: str) -> dict[str, Any]:
    value = response.get(key, {}) if hasattr(response, "get") else {}
    return value if isinstance(value, dict) else {}


def _caller_is_member(client: SlackClient, channel_id: str, caller_id: str) -> bool:
    cursor: str | None = None
    while True:
        response = client.conversations_members(channel=channel_id, limit=200, cursor=cursor)
        members = response.get("members", []) if hasattr(response, "get") else []
        if caller_id in members:
            return True
        metadata = _response_mapping(response, "response_metadata")
        next_cursor = metadata.get("next_cursor")
        cursor = next_cursor if isinstance(next_cursor, str) and next_cursor else None
        if cursor is None:
            return False


def _caller(client: SlackClient, team_id: str, caller_id: str) -> tuple[bool, bool]:
    """Whether the caller is a person in this workspace, and whether they are a guest."""
    user = _response_mapping(client.users_info(user=caller_id), "user")
    enterprise_user = user.get("enterprise_user") or {}
    enterprise_id = os.getenv("SLACK_ENTERPRISE_ID", "")
    member_of_workspace = user.get("team_id") == team_id or (
        bool(enterprise_id) and isinstance(enterprise_user, dict)
        and enterprise_user.get("enterprise_id") == enterprise_id
        and team_id in (enterprise_user.get("teams") or [])
    )
    if (
        user.get("id") != caller_id
        or not member_of_workspace
        or user.get("deleted") is True
        or user.get("is_bot") is True
    ):
        return False, True
    restricted = bool(user.get("is_restricted") or user.get("is_ultra_restricted") or user.get("is_stranger"))
    return True, restricted


def _visible_channels(
    client: SlackClient,
    *,
    caller_id: str,
    restricted: bool,
    configured_channel_ids: tuple[str, ...],
) -> tuple[VisibleChannel, ...]:
    """The Tag's channels this caller may have it read: the Tag is in them, they
    aren't shared outside the workspace, and private ones (or any, for a guest)
    have the caller as a member."""
    visible: list[VisibleChannel] = []
    for channel_id in configured_channel_ids:
        try:
            response = client.conversations_info(channel=channel_id)
            channel = _response_mapping(response, "channel")
            name = channel.get("name")
            if (
                channel.get("id") != channel_id
                or not isinstance(name, str)
                or not name
                or channel.get("is_archived") is True
                or channel.get("is_member") is not True
                or channel.get("is_shared") is True
                or channel.get("is_ext_shared") is True
                or channel.get("is_org_shared") is True
                or bool(channel.get("pending_shared"))
            ):
                continue
            is_private = bool(channel.get("is_private"))
            if (is_private or restricted) and not _caller_is_member(client, channel_id, caller_id):
                continue
        except Exception:  # Slack/API failures deny this channel without leaking details.
            continue
        visible.append(VisibleChannel(channel_id, name, is_private))
    return tuple(visible)


def _safe_suggestions(name: str, visible: tuple[VisibleChannel, ...]) -> list[str]:
    by_normalized = {normalize_channel_name(channel.name): channel.name for channel in visible}
    matches = difflib.get_close_matches(name, sorted(by_normalized), n=3, cutoff=0.82)
    return [by_normalized[match] for match in matches]


def plan_search_scopes(
    *,
    request_text: str,
    current_channel_id: str,
    caller_id: str,
    team_id: str,
    configured_channels: str,
    client: SlackClient,
    intent: SearchIntent | None = None,
) -> ScopePlan:
    """Resolve an explicit request to the least set of channels the caller may have the Tag read."""
    intent = intent or parse_search_intent(request_text)
    if intent.mode == "current":
        return ScopePlan("current")
    if intent.mode == "clarify":
        return ScopePlan(
            "clarify",
            clarification="Please clarify the Slack search scope: name channels like #support, or say ‘search all channels I can access’.",
        )
    if not team_id or not caller_id:
        return ScopePlan("denied", clarification="I couldn't verify an authorized Slack search scope.")
    try:
        person, restricted = _caller(client, team_id, caller_id)
        if not person:
            return ScopePlan("denied", clarification="I couldn't verify an authorized Slack search scope.")
        visible = _visible_channels(
            client,
            caller_id=caller_id,
            restricted=restricted,
            configured_channel_ids=parse_channel_ids(configured_channels),
        )
    except Exception:
        return ScopePlan("denied", clarification="I couldn't verify an authorized Slack search scope.")

    if intent.mode == "all":
        candidate_count = len(parse_channel_ids(configured_channels))
        notice = (
            "Some of the Tag's channels were left out because access could not be verified."
            if len(visible) < candidate_count
            else ""
        )
        return ScopePlan("all", visible, notice=notice, public_search=not restricted)

    by_name: dict[str, list[VisibleChannel]] = {}
    for channel in visible:
        by_name.setdefault(normalize_channel_name(channel.name), []).append(channel)
    selected: list[VisibleChannel] = []
    skipped: list[str] = []
    suggestion_lines: list[str] = []
    for requested_name in intent.channel_names:
        exact = by_name.get(requested_name, [])
        if len(exact) == 1:
            if exact[0] not in selected:
                selected.append(exact[0])
            continue
        skipped.append(requested_name)
        suggestions = _safe_suggestions(requested_name, visible)
        if suggestions:
            suggestion_lines.append(
                f"For #{requested_name}, did you mean "
                + " or ".join(f"#{name}" for name in suggestions)
                + "?"
            )
    if skipped:
        detail = " ".join(suggestion_lines)
        generic = "I couldn't uniquely verify every requested channel."
        return ScopePlan(
            "clarify",
            skipped_names=tuple(skipped),
            clarification=f"{generic}{(' ' + detail) if detail else ' Please check the channel names and try again.'}",
        )
    if not selected:
        return ScopePlan("denied", clarification="I couldn't verify any requested channels the Tag may read.")
    return ScopePlan("named", tuple(selected))


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve which Slack channels a request may have the Tag read.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--current-channel", required=True)
    parser.add_argument("--caller-id", required=True)
    parser.add_argument("--team-id", required=True)
    parser.add_argument("--configured-channels", default=os.getenv("SLACK_CHANNEL_IDS", ""))
    args = parser.parse_args()
    token = os.getenv("SLACK_BOT_TOKEN", "")
    if not token:
        print(json.dumps({"error": "SLACK_BOT_TOKEN is required"}))
        return 2
    plan = plan_search_scopes(
        request_text=args.request,
        current_channel_id=args.current_channel,
        caller_id=args.caller_id,
        team_id=args.team_id,
        configured_channels=args.configured_channels,
        client=HttpSlackClient(token),
    )
    print(json.dumps(plan.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if plan.mode not in {"clarify", "denied"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
