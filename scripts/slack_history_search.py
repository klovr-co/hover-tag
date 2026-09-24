#!/usr/bin/env python3
"""Search only the Slack channels authorized for this runtime invocation."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from .slack_search_scope import (
        SLACK_CHANNEL_REF_RE,
        explicit_channel_names,
        normalize_channel_name,
    )
except ImportError:  # Direct execution: python3 scripts/slack_history_search.py
    from slack_search_scope import (
        SLACK_CHANNEL_REF_RE,
        explicit_channel_names,
        normalize_channel_name,
    )


def authorized_channels(raw_grant: str) -> tuple[dict[str, str], ...]:
    try:
        payload: Any = json.loads(raw_grant)
    except (json.JSONDecodeError, TypeError):
        return ()
    if not isinstance(payload, dict) or payload.get("mode") != "all":
        return ()
    channels = payload.get("channels")
    if not isinstance(channels, list):
        return ()
    validated: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for channel in channels:
        if not isinstance(channel, dict):
            return ()
        channel_id = channel.get("id")
        name = channel.get("name")
        scope = channel.get("scope")
        if not all(isinstance(value, str) and value for value in (channel_id, name, scope)):
            return ()
        if channel_id in seen_ids:
            continue
        seen_ids.add(channel_id)
        validated.append({"id": channel_id, "name": name, "scope": scope})
    return tuple(validated)


def requested_channel_names(raw_grant: str) -> tuple[str, ...]:
    """Read explicit names from the bridge-bound original Slack request."""
    try:
        payload: Any = json.loads(raw_grant)
    except (json.JSONDecodeError, TypeError):
        return ()
    request_text = payload.get("request_text") if isinstance(payload, dict) else None
    if not isinstance(request_text, str):
        return ()
    authorized_by_id = {
        channel["id"].casefold(): normalize_channel_name(channel["name"])
        for channel in authorized_channels(raw_grant)
    }
    authorized_names = set(authorized_by_id.values())
    names = [
        authorized_by_id.get(
            match.group("id").casefold(),
            normalize_channel_name(match.group("id")),
        )
        for match in SLACK_CHANNEL_REF_RE.finditer(request_text)
    ]
    plain_text = SLACK_CHANNEL_REF_RE.sub(" ", request_text)
    names.extend(
        name
        for name in explicit_channel_names(plain_text)
        if not name.isdigit() or name in authorized_names
    )
    return tuple(dict.fromkeys(name for name in names if name))


def _selection_error(
    requested_names: tuple[str, ...], channels: tuple[dict[str, str], ...]
) -> ValueError:
    authorized = {
        normalize_channel_name(channel["name"]): channel["name"] for channel in channels
    }
    suggestions: list[str] = []
    for requested_name in requested_names:
        matches = difflib.get_close_matches(
            normalize_channel_name(requested_name), sorted(authorized), n=1, cutoff=0.82
        )
        if matches:
            suggestions.append(
                f"For #{requested_name.lstrip('#')}, did you mean #{authorized[matches[0]]}?"
            )
    detail = " ".join(suggestions)
    return ValueError(
        (detail + " " if detail else "")
        + "Ask the user to confirm the channel names before searching; do not retry with corrected names."
    )


def select_channels(
    channels: tuple[dict[str, str], ...], requested_names: list[str]
) -> tuple[dict[str, str], ...]:
    if not requested_names:
        return channels
    by_name: dict[str, list[dict[str, str]]] = {}
    for channel in channels:
        by_name.setdefault(normalize_channel_name(channel["name"]), []).append(channel)
    selected: list[dict[str, str]] = []
    for requested_name in requested_names:
        matches = by_name.get(normalize_channel_name(requested_name), [])
        if len(matches) != 1:
            raise ValueError(f"Channel is not uniquely authorized and indexed: #{requested_name.lstrip('#')}")
        if matches[0] not in selected:
            selected.append(matches[0])
    return tuple(selected)


def select_channels_for_request(
    channels: tuple[dict[str, str], ...],
    requested_names: list[str],
    original_names: tuple[str, ...],
) -> tuple[dict[str, str], ...]:
    """Bind explicitly named requests to the user's exact channel spellings."""
    if original_names:
        supplied = tuple(
            dict.fromkeys(normalize_channel_name(name) for name in requested_names)
        )
        if set(supplied) != set(original_names):
            raise _selection_error(original_names, channels)
    try:
        return select_channels(channels, requested_names)
    except ValueError:
        raise _selection_error(original_names or tuple(requested_names), channels) from None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search permitted indexed Slack history across channels."
    )
    parser.add_argument("query")
    parser.add_argument("--channel", action="append", default=[])
    parser.add_argument("--mode", choices=["hybrid", "semantic", "keyword"], default="hybrid")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    raw_grant = os.getenv("OPENTAG_SLACK_SEARCH_GRANT", "")
    channels = authorized_channels(raw_grant)
    if not channels:
        print("No permitted indexed Slack channel history is available.", file=sys.stderr)
        return 2
    try:
        selected = select_channels_for_request(
            channels, args.channel, requested_channel_names(raw_grant)
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    child_env = dict(os.environ)
    child_env["MFS_ALLOWED_SCOPES"] = ",".join(channel["scope"] for channel in selected)
    child_env["OPENTAG_SLACK_CHANNEL_LABELS"] = json.dumps(
        {channel["id"]: channel["name"] for channel in selected}, sort_keys=True
    )
    command = [
        sys.executable,
        str(Path(__file__).with_name("mfs_search.py")),
        args.query,
        "--mode",
        args.mode,
        "--top-k",
        str(args.top_k),
    ]
    if args.json:
        command.append("--json")
    return subprocess.run(command, check=False, env=child_env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
