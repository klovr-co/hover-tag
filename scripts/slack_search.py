#!/usr/bin/env python3
# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""Search Slack live with the Tag's own bot connection, and store nothing.

- The current conversation, and channels the request is authorized for: read
  their history with ``conversations.history`` / ``conversations.replies`` and
  scan it in memory.
- Public channels across the workspace, only when the person asked to search
  all of Slack: Slack's Real-Time Search API (``assistant.search.context``),
  with the bot token and the ``action_token`` of the event that started this
  run. Slack allows about 10 of these calls a minute and 20 results a call.

Results are printed for the agent and never written to disk: Slack's terms
forbid keeping copies of what this API returns. The only file this helper
writes is a list of call times, to stay inside Slack's rate limit.

The Slack bridge decides which channels a request may read
(``OPENTAG_SLACK_SEARCH_GRANT``, see ``slack_search_scope``); this helper only
narrows that grant to the channels the person named.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .slack_search_scope import ALL_CHANNELS_RE, SLACK_CHANNEL_REF_RE, explicit_channel_names, normalize_channel_name
except ImportError:  # Direct execution: python3 scripts/slack_search.py
    from slack_search_scope import ALL_CHANNELS_RE, SLACK_CHANNEL_REF_RE, explicit_channel_names, normalize_channel_name

SLACK_API = "https://slack.com/api"
RTS_LIMIT_PER_MINUTE = 10
RTS_PAGE = 20
HISTORY_PAGE = 200
DEFAULT_DAYS = 30
MAX_DAYS = 365
MAX_MESSAGES = 1_000
MAX_THREADS = 15
SHOWN_CHARS = 600
STOP_WORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "what", "when", "where", "about", "into", "was", "were",
    "are", "have", "has", "our", "you", "your", "did", "does", "any", "all", "who", "how", "why", "not",
})


class SearchError(Exception):
    """A refusal or failure the agent should report in plain words."""


@dataclass
class Hit:
    channel: str
    channel_name: str
    ts: str
    user: str
    text: str
    thread_ts: str = ""
    score: int = 0
    source: str = "history"
    permalink: str = ""

    def as_json(self) -> dict[str, Any]:
        return {"channel": self.channel, "channel_name": self.channel_name, "ts": self.ts, "thread_ts": self.thread_ts or None,
                "user": self.user, "when": when(self.ts), "text": self.text[:SHOWN_CHARS], "link": self.permalink,
                "source": self.source}


@dataclass
class Grant:
    """What the bridge allowed for this request."""

    current: str
    channels: dict[str, str] = field(default_factory=dict)  # id -> name
    public_search: bool = False
    request_text: str = ""

    @classmethod
    def from_env(cls) -> "Grant":
        current = os.getenv("OPENTAG_CURRENT_CHANNEL_ID", "")
        if not re.fullmatch(r"[CDG][A-Z0-9]+", current):
            raise SearchError("Slack search is only available inside a Tag's Slack run.")
        grant = cls(current=current)
        try:
            payload = json.loads(os.getenv("OPENTAG_SLACK_SEARCH_GRANT", "") or "{}")
        except ValueError:
            payload = {}
        if isinstance(payload, dict) and payload.get("mode") == "all":
            for channel in payload.get("channels") or []:
                if isinstance(channel, dict) and isinstance(channel.get("id"), str) and isinstance(channel.get("name"), str):
                    grant.channels[channel["id"]] = channel["name"]
            grant.public_search = payload.get("public_search") is True
            grant.request_text = payload.get("request_text") if isinstance(payload.get("request_text"), str) else ""
        return grant


# Slack Web API ----------------------------------------------------------------------


class Slack:
    def __init__(self, token: str):
        if not token:
            raise SearchError("Slack search needs the Tag's Slack connection.")
        self.token = token

    def call(self, method: str, parameters: dict[str, str], *, post: bool = False) -> dict[str, Any]:
        data = urllib.parse.urlencode(parameters)
        request = urllib.request.Request(
            f"{SLACK_API}/{method}" + ("" if post else f"?{data}"),
            data=data.encode() if post else None,
            headers={"Authorization": f"Bearer {self.token}"},
            method="POST" if post else "GET",
        )
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    payload = json.loads(response.read())
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt == 0:
                    wait = min(int(exc.headers.get("Retry-After", "5") or 5), 20)
                    time.sleep(wait)
                    continue
                if exc.code == 429:
                    raise SearchError("Slack is limiting searches right now; try again in a minute.") from None
                raise SearchError(f"Slack could not be reached ({exc.code}).") from None
            except (OSError, ValueError):
                raise SearchError("Slack could not be reached; check the connection and try again.") from None
        if not isinstance(payload, dict) or not payload.get("ok"):
            error = payload.get("error", "unknown_error") if isinstance(payload, dict) else "invalid_response"
            raise SlackAPIError(method, str(error), str(payload.get("needed", "")) if isinstance(payload, dict) else "")
        return payload


class SlackAPIError(SearchError):
    def __init__(self, method: str, error: str, needed: str = ""):
        self.method, self.error, self.needed = method, error, needed
        super().__init__(f"Slack refused {method} ({error}).")


# Matching -----------------------------------------------------------------------------


def terms(query: str) -> list[str]:
    words = [w for w in re.findall(r"[\w][\w'.-]*", query.casefold()) if len(w) > 1]
    kept = [w.strip(".'") for w in words if w not in STOP_WORDS]
    return list(dict.fromkeys(w for w in kept if w)) or list(dict.fromkeys(words))


def score(text: str, wanted: list[str]) -> int:
    """How many search terms the text contains; every term must appear for a match."""
    folded = text.casefold()
    found = sum(1 for term in wanted if term in folded)
    return found if found == len(wanted) else 0


def when(ts: str) -> str:
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return ""


def link(channel: str, ts: str, thread_ts: str = "") -> str:
    team = os.getenv("SLACK_TEAM_ID", "")
    query = {"team": team, "id": channel, "message": ts} if team else {"id": channel, "message": ts}
    if thread_ts and thread_ts != ts:
        query["thread_ts"] = thread_ts
    return "slack://channel?" + urllib.parse.urlencode(query)


# Reading history live -------------------------------------------------------------------


def channel_name(slack: Slack, channel: str, grant: Grant) -> str:
    if channel in grant.channels:
        return grant.channels[channel]
    try:
        labels = json.loads(os.getenv("OPENTAG_SLACK_CHANNEL_LABELS", "") or "{}")
    except ValueError:
        labels = {}
    if isinstance(labels, dict) and isinstance(labels.get(channel), str):
        return labels[channel]
    if channel.startswith("D"):
        return "this direct message"
    try:
        info = slack.call("conversations.info", {"channel": channel}).get("channel") or {}
    except SearchError:
        return channel
    return str(info.get("name") or channel)


def read_history(slack: Slack, channel: str, *, days: int, limit: int = MAX_MESSAGES) -> list[dict[str, Any]]:
    oldest = f"{time.time() - days * 86_400:.6f}"
    messages: list[dict[str, Any]] = []
    cursor = ""
    while len(messages) < limit:
        parameters = {"channel": channel, "limit": str(min(HISTORY_PAGE, limit - len(messages))), "oldest": oldest}
        if cursor:
            parameters["cursor"] = cursor
        try:
            payload = slack.call("conversations.history", parameters)
        except SlackAPIError as exc:
            if exc.error in {"not_in_channel", "channel_not_found"}:
                raise SearchError(f"The Tag isn't in {channel}, so it can't read that channel's history.") from None
            raise
        messages.extend(m for m in payload.get("messages") or [] if isinstance(m, dict))
        cursor = str((payload.get("response_metadata") or {}).get("next_cursor") or "")
        if not cursor or not payload.get("has_more"):
            break
    return messages


def read_replies(slack: Slack, channel: str, thread_ts: str) -> list[dict[str, Any]]:
    payload = slack.call("conversations.replies", {"channel": channel, "ts": thread_ts, "limit": str(HISTORY_PAGE)})
    return [m for m in payload.get("messages") or [] if isinstance(m, dict)][1:]


def message_text(message: dict[str, Any]) -> str:
    parts = [str(message.get("text") or "")]
    for attachment in message.get("attachments") or []:
        if isinstance(attachment, dict):
            parts.extend(str(attachment.get(key) or "") for key in ("title", "text", "fallback"))
    for upload in message.get("files") or []:
        if isinstance(upload, dict):
            parts.append(str(upload.get("title") or upload.get("name") or ""))
    return "\n".join(p for p in parts if p).strip()


def scan_channel(slack: Slack, channel: str, name: str, wanted: list[str], *, days: int) -> list[Hit]:
    hits: list[Hit] = []
    history = read_history(slack, channel, days=days)
    threads = sorted((m for m in history if int(m.get("reply_count") or 0) > 0),
                     key=lambda m: float(m.get("latest_reply") or m.get("ts") or 0), reverse=True)[:MAX_THREADS]
    for message in history:
        text = message_text(message)
        if (points := score(text, wanted)):
            hits.append(Hit(channel, name, str(message.get("ts")), str(message.get("user") or message.get("bot_id") or ""),
                            text, str(message.get("thread_ts") or ""), points))
    for parent in threads:
        try:
            replies = read_replies(slack, channel, str(parent["ts"]))
        except SearchError:
            continue
        for reply in replies:
            text = message_text(reply)
            if (points := score(text, wanted)):
                hits.append(Hit(channel, name, str(reply.get("ts")), str(reply.get("user") or reply.get("bot_id") or ""),
                                text, str(parent["ts"]), points))
    for hit in hits:
        hit.permalink = link(hit.channel, hit.ts, hit.thread_ts)
    return hits


# Real-Time Search ------------------------------------------------------------------------


def limiter_path() -> Path:
    return Path(tempfile.gettempdir()) / "tag-slack-search-calls.json"


def reserve_search_call(now: float | None = None) -> None:
    """Stay inside Slack's limit of about 10 searches a minute; keeps only call times."""
    now = time.time() if now is None else now
    path = limiter_path()
    try:
        calls = [float(t) for t in json.loads(path.read_text(encoding="utf-8")) if now - float(t) < 60]
    except (OSError, ValueError, TypeError):
        calls = []
    if len(calls) >= RTS_LIMIT_PER_MINUTE:
        wait = int(60 - (now - min(calls))) + 1
        raise SearchError(f"Slack allows about {RTS_LIMIT_PER_MINUTE} searches a minute; try again in {wait} seconds.")
    calls.append(now)
    try:
        path.write_text(json.dumps(calls), encoding="utf-8")
    except OSError:
        pass


def search_public(slack: Slack, query: str, *, action_token: str, limit: int, after: float | None) -> list[Hit]:
    if not action_token:
        raise SearchError("Searching all public channels needs a request from Slack itself, such as a mention; "
                          "routines can search only their own channels.")
    reserve_search_call()
    parameters = {"query": query, "action_token": action_token, "channel_types": "public_channel",
                  "content_types": "messages", "limit": str(min(limit, RTS_PAGE)), "include_bots": "false"}
    if after:
        parameters["after"] = str(int(after))
    try:
        payload = slack.call("assistant.search.context", parameters, post=True)
    except SlackAPIError as exc:
        if exc.error == "missing_scope":
            raise SearchError("Searching all public channels needs the Tag's Slack app update (search:read.public). "
                              "The Tag's owner approves it when they next start the Tag.") from None
        if exc.error in {"invalid_action_token", "action_token_expired"}:
            raise SearchError("Slack's permission to search for this request has expired; mention the Tag again.") from None
        if exc.error == "enterprise_is_restricted":
            raise SearchError("Slack doesn't let this Tag's app search public channels across the organization; "
                              "it read only the channels it is in.") from None
        raise
    results = payload.get("results") if isinstance(payload.get("results"), dict) else {}
    hits = []
    for item in results.get("messages") or []:
        if not isinstance(item, dict):
            continue
        channel = str(item.get("channel_id") or "")
        ts = str(item.get("message_ts") or "")
        hits.append(Hit(channel, str(item.get("channel_name") or channel), ts,
                        str(item.get("author_user_id") or ""), str(item.get("content") or ""),
                        source="slack search", permalink=str(item.get("permalink") or link(channel, ts))))
    return hits


# Choosing channels ---------------------------------------------------------------------------


def requested_in_request(grant: Grant) -> tuple[set[str], set[str]]:
    """Channel IDs and names the original Slack request named."""
    ids = {m.group("id") for m in SLACK_CHANNEL_REF_RE.finditer(grant.request_text)}
    names = set(explicit_channel_names(SLACK_CHANNEL_REF_RE.sub(" ", grant.request_text)))
    return ids, names


def select(grant: Grant, names: list[str]) -> dict[str, str]:
    """The named channels, each one the request named and the bridge allowed."""
    if not names:
        return {grant.current: ""}
    asked_ids, asked_names = requested_in_request(grant)
    by_name: dict[str, list[str]] = {}
    for channel_id, name in grant.channels.items():
        by_name.setdefault(normalize_channel_name(name), []).append(channel_id)
    chosen: dict[str, str] = {}
    for raw in names:
        reference = SLACK_CHANNEL_REF_RE.fullmatch(raw)
        if reference:
            matches = [reference.group("id")] if reference.group("id") in grant.channels else []
            asked = reference.group("id") in asked_ids
        else:
            normalized = normalize_channel_name(raw)
            matches = by_name.get(normalized, [])
            asked = normalized in asked_names or any(grant.channels.get(i) and normalize_channel_name(grant.channels[i]) == normalized
                                                     for i in asked_ids)
        if len(matches) != 1 or not asked:
            raise SearchError(f"#{raw.lstrip('#')} isn't one of the channels this request may read. "
                              "Ask the person to confirm the channel name; don't retry with another name.")
        chosen[matches[0]] = grant.channels[matches[0]]
    return chosen


# Command line -----------------------------------------------------------------------------------


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search Slack live for this Tag run; nothing is stored.")
    actions = parser.add_subparsers(dest="action", required=True)
    find = actions.add_parser("search", help="Find messages.")
    find.add_argument("query")
    find.add_argument("--channel", action="append", default=[], help="a channel the person named (repeat)")
    find.add_argument("--all", action="store_true", help="every channel this request may read, and public channels")
    find.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"how far back to read history (default {DEFAULT_DAYS})")
    find.add_argument("--limit", type=int, default=20)
    find.add_argument("--json", action="store_true")
    show = actions.add_parser("history", help="Read a channel's recent messages, for a summary.")
    show.add_argument("--channel", help="a channel the person named (default: this conversation)")
    show.add_argument("--days", type=int, default=7)
    show.add_argument("--limit", type=int, default=200)
    show.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        grant = Grant.from_env()
        slack = Slack(os.getenv("SLACK_BOT_TOKEN", ""))
        days = max(1, min(args.days, MAX_DAYS))
        if args.action == "history":
            channels = select(grant, [args.channel] if args.channel else [])
            channel = next(iter(channels))
            name = channels[channel] or channel_name(slack, channel, grant)
            messages = list(reversed(read_history(slack, channel, days=days, limit=max(1, min(args.limit, MAX_MESSAGES)))))
            hits = [Hit(channel, name, str(m.get("ts")), str(m.get("user") or m.get("bot_id") or ""), message_text(m),
                        str(m.get("thread_ts") or ""), permalink=link(channel, str(m.get("ts")))) for m in messages]
            return report(hits, args.json, header=f"{len(hits)} messages from {label(name)} in the last {days} days, oldest first:")
        wanted = terms(args.query)
        if not wanted:
            raise SearchError("Give some words to search for.")
        notes: list[str] = []
        if args.all:
            if not ALL_CHANNELS_RE.search(grant.request_text):
                raise SearchError("Search all channels only when the person asked for that; ask them which channels to search.")
            channels = {grant.current: "", **grant.channels}
        else:
            channels = select(grant, args.channel)
        hits: list[Hit] = []
        for channel, name in channels.items():
            hits.extend(scan_channel(slack, channel, name or channel_name(slack, channel, grant), wanted, days=days))
        if args.all:
            if grant.public_search:
                try:
                    after = time.time() - days * 86_400 if args.days != DEFAULT_DAYS else None
                    found = search_public(slack, args.query, action_token=os.getenv("OPENTAG_SLACK_ACTION_TOKEN", ""),
                                          limit=args.limit, after=after)
                    seen = {(h.channel, h.ts) for h in hits}
                    hits.extend(h for h in found if (h.channel, h.ts) not in seen)
                except SearchError as exc:
                    notes.append(str(exc))
            else:
                notes.append("Public channels beyond the Tag's own were not searched: Slack doesn't let apps search for guests.")
        hits.sort(key=lambda h: (-h.score, -float(h.ts or 0)))
        searched = ", ".join(label(n or channel_name(slack, c, grant)) for c, n in channels.items())
        header = f"{min(len(hits), args.limit)} of {len(hits)} matches for {args.query!r} in {searched}" + \
            (" and public channels" if args.all and grant.public_search else "") + f" (last {days} days):"
        return report(hits[: max(1, min(args.limit, 100))], args.json, header=header, notes=notes)
    except SearchError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def label(name: str) -> str:
    return name if name.startswith("this ") or not name else f"#{name}"


def report(hits: list[Hit], as_json: bool, *, header: str, notes: list[str] | None = None) -> int:
    if as_json:
        print(json.dumps({"results": [h.as_json() for h in hits], "notes": notes or []}, ensure_ascii=False))
        return 0
    print(header)
    for hit in hits:
        text = " ".join(hit.text.split())
        if len(text) > SHOWN_CHARS:
            text = text[: SHOWN_CHARS - 1] + "…"
        who = f"<@{hit.user}>" if re.fullmatch(r"[UW][A-Z0-9]+", hit.user) else "a bot"
        print(f"- {label(hit.channel_name)} · {when(hit.ts)} · {who}{' · in a thread' if hit.thread_ts and hit.thread_ts != hit.ts else ''}"
              f" · {hit.permalink}\n  {text}")
    for note in notes or []:
        print(f"Note: {note}")
    if not hits:
        print("No matching messages. Say so; don't guess what was said.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
