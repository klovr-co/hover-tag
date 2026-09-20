#!/usr/bin/env python3
"""Deterministic resolution and authorization for Slack history search scopes.

The Slack bridge and this module's CLI adapter deliberately share this policy.
Natural-language parsing can request a broader scope, but only this module may
turn that request into stable channel IDs and MFS scopes.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

try:
    from .mfs_scope_policy import parse_scopes
    from .mfs_ls import request_json as mfs_request_json
    from .opentag_process_env import current_channel_scopes
    from .slack_channels import parse_channel_ids, slack_api
except ImportError:  # Direct execution: python3 scripts/slack_search_scope.py
    from mfs_scope_policy import parse_scopes
    from mfs_ls import request_json as mfs_request_json
    from opentag_process_env import current_channel_scopes
    from slack_channels import parse_channel_ids, slack_api


CHANNEL_ID_RE = re.compile(r"^[CG][A-Z0-9]+$")
CHANNEL_REF_RE = re.compile(r"(?<![\w])#([\w][\w-]{0,79})", re.IGNORECASE)
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
class IndexedChannel:
    channel_id: str
    scope: str


@dataclass(frozen=True)
class VisibleChannel:
    channel_id: str
    name: str
    scope: str


@dataclass(frozen=True)
class ScopePlan:
    mode: str
    scopes: tuple[str, ...]
    channels: tuple[VisibleChannel, ...] = ()
    skipped_names: tuple[str, ...] = ()
    clarification: str = ""
    notice: str = ""

    @property
    def allowed_scopes(self) -> str:
        return ",".join(self.scopes)

    @property
    def channel_labels(self) -> dict[str, str]:
        return {channel.channel_id: channel.name for channel in self.channels}

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "allowed_scopes": list(self.scopes),
            "channels": [
                {"id": channel.channel_id, "name": channel.name, "scope": channel.scope}
                for channel in self.channels
            ],
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
    names = tuple(dict.fromkeys(normalize_channel_name(name) for name in CHANNEL_REF_RE.findall(text)))
    if not names:
        names = _plain_channel_names(text)
    if names and all(name in {"all", "every"} for name in names):
        names = ()
    has_search_verb = bool(SEARCH_VERB_RE.search(text))
    requests_all = bool(ALL_CHANNELS_RE.search(text))
    if names and requests_all:
        return SearchIntent("clarify")
    if names and has_search_verb:
        return SearchIntent("named", names)
    if requests_all:
        return SearchIntent("all")

    broad_terms = sum(
        bool(re.search(pattern, text, re.IGNORECASE))
        for pattern in (r"\ball\b", r"\bacross\b", r"\bworkspace\b", r"\bslack\b", r"\bchannels?\b")
    )
    if has_search_verb and broad_terms >= 2:
        return SearchIntent("clarify")
    return SearchIntent("current")


def indexed_slack_channels(raw_scopes: str, team_id: str) -> dict[str, IndexedChannel]:
    """Return indexed channel scopes belonging to exactly this Slack workspace."""
    expected_authority = f"tag-{team_id.casefold()}"
    indexed: dict[str, IndexedChannel] = {}
    for scope in parse_scopes(raw_scopes):
        parsed = urlsplit(scope)
        if parsed.scheme.casefold() != "slack" or parsed.netloc.casefold() != expected_authority:
            continue
        final_segment = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        _, marker, channel_id = final_segment.rpartition("__")
        if marker and CHANNEL_ID_RE.fullmatch(channel_id):
            indexed.setdefault(channel_id, IndexedChannel(channel_id, scope))
    return indexed


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


def _visible_channels(
    client: SlackClient,
    *,
    team_id: str,
    caller_id: str,
    configured_channel_ids: tuple[str, ...],
    indexed: dict[str, IndexedChannel],
    resolve_indexed_scope: Callable[[IndexedChannel], str | None],
) -> tuple[VisibleChannel, ...]:
    user_response = client.users_info(user=caller_id)
    user = _response_mapping(user_response, "user")
    if (
        user.get("id") != caller_id
        or user.get("team_id") != team_id
        or user.get("deleted") is True
        or user.get("is_bot") is True
    ):
        return ()
    restricted = bool(
        user.get("is_restricted")
        or user.get("is_ultra_restricted")
        or user.get("is_stranger")
    )

    visible: list[VisibleChannel] = []
    for channel_id in configured_channel_ids:
        indexed_channel = indexed.get(channel_id)
        if indexed_channel is None:
            continue
        try:
            current_scope = resolve_indexed_scope(indexed_channel)
            if current_scope is None:
                continue
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
        visible.append(VisibleChannel(channel_id, name, current_scope))
    return tuple(visible)


def resolve_mfs_channel_scope(indexed: IndexedChannel) -> str | None:
    """Resolve current MFS metadata by stable ID, surviving channel renames."""
    parsed = urlsplit(indexed.scope)
    parent_path = parsed.path.rstrip("/").rsplit("/", 1)[0] or "/"
    parent = parsed._replace(path=parent_path, query="", fragment="").geturl()
    try:
        response = mfs_request_json("/v1/ls", {"path": parent})
    except Exception:
        return None
    entries = response.get("entries") if isinstance(response, dict) else None
    if not isinstance(entries, list):
        return None
    marker = f"__{indexed.channel_id}"
    for entry in entries:
        candidate = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(candidate, str):
            continue
        candidate_parsed = urlsplit(candidate)
        if (
            candidate_parsed.scheme.casefold() == parsed.scheme.casefold()
            and candidate_parsed.netloc.casefold() == parsed.netloc.casefold()
            and candidate_parsed.path.rstrip("/").rsplit("/", 1)[-1].endswith(marker)
        ):
            current_scope = candidate.rstrip("/")
            try:
                channel_listing = mfs_request_json("/v1/ls", {"path": current_scope})
            except Exception:
                return None
            children = (
                channel_listing.get("entries")
                if isinstance(channel_listing, dict)
                else None
            )
            if not isinstance(children, list):
                return None
            if any(
                isinstance(child, dict)
                and child.get("name") == "messages.jsonl"
                and child.get("search_status") == "indexed"
                for child in children
            ):
                return current_scope
            return None
    return None


def mfs_scope_is_indexed(scope: str) -> bool:
    """Compatibility predicate for callers that only need availability."""
    final_segment = urlsplit(scope).path.rstrip("/").rsplit("/", 1)[-1]
    _, marker, channel_id = final_segment.rpartition("__")
    return bool(
        marker
        and CHANNEL_ID_RE.fullmatch(channel_id)
        and resolve_mfs_channel_scope(IndexedChannel(channel_id, scope))
    )


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
    allowed_scopes: str,
    client: SlackClient,
    scope_is_indexed: Callable[[str], bool] | None = None,
    resolve_indexed_scope: Callable[[IndexedChannel], str | None] | None = None,
) -> ScopePlan:
    """Resolve an explicit request to the least set of proven channel scopes."""
    intent = parse_search_intent(request_text)
    if intent.mode == "current":
        narrowed = current_channel_scopes(allowed_scopes, current_channel_id)
        return ScopePlan("current", tuple(parse_scopes(narrowed)))
    if intent.mode == "clarify":
        return ScopePlan(
            "clarify",
            (),
            clarification="Please clarify the Slack search scope: name channels like #support, or say ‘search all channels I can access’.",
        )
    if not team_id or not caller_id:
        return ScopePlan("denied", (), clarification="I couldn't verify an authorized Slack search scope.")

    indexed = indexed_slack_channels(allowed_scopes, team_id)
    if resolve_indexed_scope is None:
        resolve_indexed_scope = (
            (lambda channel: channel.scope if scope_is_indexed(channel.scope) else None)
            if scope_is_indexed is not None
            else resolve_mfs_channel_scope
        )
    try:
        visible = _visible_channels(
            client,
            team_id=team_id,
            caller_id=caller_id,
            configured_channel_ids=parse_channel_ids(configured_channels),
            indexed=indexed,
            resolve_indexed_scope=resolve_indexed_scope,
        )
    except Exception:
        return ScopePlan("denied", (), clarification="I couldn't verify an authorized Slack search scope.")

    if intent.mode == "all":
        if not visible:
            return ScopePlan("denied", (), clarification="I couldn't verify any permitted indexed Slack channels.")
        candidate_count = len(set(parse_channel_ids(configured_channels)) & set(indexed))
        notice = (
            "Some channels were omitted because access or current indexing could not be verified."
            if len(visible) < candidate_count
            else ""
        )
        return ScopePlan(
            "all",
            tuple(channel.scope for channel in visible),
            visible,
            notice=notice,
        )

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
            (),
            skipped_names=tuple(skipped),
            clarification=f"{generic}{(' ' + detail) if detail else ' Please check the channel names and try again.'}",
        )
    if not selected:
        return ScopePlan("denied", (), clarification="I couldn't verify any requested permitted indexed Slack channels.")
    return ScopePlan("named", tuple(channel.scope for channel in selected), tuple(selected))


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve a permission-aware Slack history search scope.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--current-channel", required=True)
    parser.add_argument("--caller-id", required=True)
    parser.add_argument("--team-id", required=True)
    parser.add_argument("--configured-channels", default=os.getenv("SLACK_CHANNEL_IDS", ""))
    parser.add_argument("--allowed-scopes", default=os.getenv("MFS_ALLOWED_SCOPES", ""))
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
        allowed_scopes=args.allowed_scopes,
        client=HttpSlackClient(token),
    )
    print(json.dumps(plan.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if plan.mode not in {"clarify", "denied"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
