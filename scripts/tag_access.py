# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""Who may give a Tag information, and who may make it run work.

These are two separate permissions, always named explicitly:

- "Who can give it information" (``OPENTAG_WHO_CAN_INFORM``): who may answer
  the Tag's questions and add context in a thread where it waits. Their words
  reach the agent as "<name> said: …", never as instructions.
- "Who can make it run work" (``OPENTAG_WHO_CAN_RUN``): who may start or add
  tasks. This is the earlier "Who can ask it", backed by
  ``SLACK_ALLOWED_USER_IDS`` (the first ID is the owner).

``OPENTAG_CHANNEL_ACCESS`` overrides either one per channel, as JSON:
``{"C123": {"inform": "owner", "run": "owner"}}``. Unknown or broken values
fail closed to "Only me".
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping

INFORM_KEY = "OPENTAG_WHO_CAN_INFORM"
RUN_KEY = "OPENTAG_WHO_CAN_RUN"
CHANNELS_KEY = "OPENTAG_CHANNEL_ACCESS"
INFORM_LEVELS = ("anyone", "authorized", "owner")
RUN_LEVELS = ("authorized", "owner")
DEFAULT_INFORM = "anyone"
INFORM_LABELS = {"anyone": "Anyone in the thread", "authorized": "People I've authorised", "owner": "Only me"}
RUN_LABELS = {"authorized": "People I've authorised", "owner": "Only me"}
CHANNEL_RE = re.compile(r"^[CGD][A-Z0-9]{2,}$")


def authorized_ids(values: Mapping[str, str]) -> list[str]:
    """The owner first, then everyone the owner authorised, in saved order."""
    seen: list[str] = []
    for item in values.get("SLACK_ALLOWED_USER_IDS", "").split(","):
        if item.strip() and item.strip() not in seen:
            seen.append(item.strip())
    return seen


def owner_id(values: Mapping[str, str]) -> str:
    ids = authorized_ids(values)
    return ids[0] if ids else ""


def channel_overrides(values: Mapping[str, str]) -> dict[str, dict[str, str]]:
    raw = values.get(CHANNELS_KEY, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for channel, levels in parsed.items():
        if not isinstance(channel, str) or not CHANNEL_RE.fullmatch(channel) or not isinstance(levels, dict):
            continue
        kept = {}
        if levels.get("inform") is not None:
            kept["inform"] = levels["inform"] if levels["inform"] in INFORM_LEVELS else "owner"
        if levels.get("run") is not None:
            kept["run"] = levels["run"] if levels["run"] in RUN_LEVELS else "owner"
        if kept:
            result[channel] = kept
    return result


def default_inform(values: Mapping[str, str]) -> str:
    value = values.get(INFORM_KEY, "").strip()
    if not value:
        return DEFAULT_INFORM
    return value if value in INFORM_LEVELS else "owner"


def default_run(values: Mapping[str, str]) -> str:
    """Unset keeps the earlier rule: everyone in SLACK_ALLOWED_USER_IDS may run work."""
    value = values.get(RUN_KEY, "").strip()
    if not value:
        return "owner" if len(authorized_ids(values)) <= 1 else "authorized"
    return value if value in RUN_LEVELS else "owner"


def levels(values: Mapping[str, str], channel: str = "") -> tuple[str, str]:
    """The (information, run work) levels that apply in one channel."""
    override = channel_overrides(values).get(channel, {})
    return override.get("inform", default_inform(values)), override.get("run", default_run(values))


def can_run(values: Mapping[str, str], user: str, channel: str = "", *,
            thread_runners: Iterable[str] = ()) -> bool:
    """Whether ``user`` may start or add work in this channel (or this thread)."""
    if not user:
        return False
    owner = owner_id(values)
    if user == owner:
        return True
    if user in set(thread_runners):
        return True
    return levels(values, channel)[1] == "authorized" and user in authorized_ids(values)


def can_inform(values: Mapping[str, str], user: str, channel: str = "", *, requester: str = "",
               thread_runners: Iterable[str] = ()) -> bool:
    """Whether ``user`` may answer the Tag's question or add context in this thread."""
    if not user:
        return False
    if user in {owner_id(values), requester} or user in set(thread_runners):
        return True
    inform = levels(values, channel)[0]
    if inform == "anyone":
        return True
    return inform == "authorized" and user in authorized_ids(values)


def validation_error(key: str, value: str) -> str | None:
    if key == INFORM_KEY and value and value not in INFORM_LEVELS:
        return "Use anyone, authorized, or owner"
    if key == RUN_KEY and value and value not in RUN_LEVELS:
        return "Use authorized or owner"
    if key == CHANNELS_KEY and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return "Use a JSON object of channel IDs to {\"inform\": …, \"run\": …}"
        if not isinstance(parsed, dict):
            return "Use a JSON object of channel IDs to {\"inform\": …, \"run\": …}"
        for channel, entry in parsed.items():
            if not CHANNEL_RE.fullmatch(str(channel)) or not isinstance(entry, dict) or not entry \
                    or set(entry) - {"inform", "run"} \
                    or entry.get("inform", "anyone") not in INFORM_LEVELS \
                    or entry.get("run", "owner") not in RUN_LEVELS:
                return f"{channel}: use {{\"inform\": anyone|authorized|owner, \"run\": authorized|owner}}"
    return None


def describe(values: Mapping[str, str]) -> dict:
    """The protocol view used by `tag NAME settings access --json` and Tag.app."""
    return {
        "inform": default_inform(values),
        "run": default_run(values),
        "inform_options": [{"id": level, "label": INFORM_LABELS[level]} for level in INFORM_LEVELS],
        "run_options": [{"id": level, "label": RUN_LABELS[level]} for level in RUN_LEVELS],
        "channels": channel_overrides(values),
        "owner": owner_id(values) or None,
        "authorized": authorized_ids(values)[1:],
    }


def changes(values: Mapping[str, str], *, inform: str | None = None, run: str | None = None,
            channel: str | None = None) -> dict[str, str]:
    """The settings to save for one change. ``default`` clears a channel's override."""
    if channel is None:
        result = {}
        if inform is not None:
            if inform not in INFORM_LEVELS:
                raise ValueError("inform must be anyone, authorized, or owner")
            result[INFORM_KEY] = inform
        if run is not None:
            if run not in RUN_LEVELS:
                raise ValueError("run must be authorized or owner")
            result[RUN_KEY] = run
        return result
    if not CHANNEL_RE.fullmatch(channel):
        raise ValueError("Use a Slack channel ID such as C0123ABCDE")
    overrides = channel_overrides(values)
    entry = dict(overrides.get(channel, {}))
    for name, value, allowed in (("inform", inform, INFORM_LEVELS), ("run", run, RUN_LEVELS)):
        if value is None:
            continue
        if value == "default":
            entry.pop(name, None)
        elif value in allowed:
            entry[name] = value
        else:
            raise ValueError(f"{name} must be {', '.join(allowed)}, or default")
    if entry:
        overrides[channel] = entry
    else:
        overrides.pop(channel, None)
    return {CHANNELS_KEY: json.dumps(overrides, separators=(",", ":"), sort_keys=True) if overrides else ""}
