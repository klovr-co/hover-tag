"""Environment isolation for OpenTag backend processes."""

from __future__ import annotations

from collections.abc import Mapping


SLACK_BRIDGE_ONLY_ENV = {
    "SLACK_APP_TOKEN",
    "SLACK_CHANNEL_ID",
    "SLACK_ALLOWED_USER_IDS",
    "OPENTAG_SLACK_DM_ENABLED",
}


def isolated_environment(source: Mapping[str, str], *, transport: str) -> dict[str, str]:
    if transport != "slack":
        raise ValueError("transport must be slack")
    return dict(source)


def backend_environment(
    source: Mapping[str, str],
    *,
    transport: str,
    conversation_id: str,
    caller_id: str,
) -> dict[str, str]:
    clean = isolated_environment(source, transport=transport)
    # The backend may use SLACK_BOT_TOKEN through channel-restricted helpers,
    # but never needs Socket Mode or bridge access-control configuration.
    for name in SLACK_BRIDGE_ONLY_ENV:
        clean.pop(name, None)
    clean["OPENTAG_CURRENT_CHANNEL_ID"] = conversation_id
    clean["OPENTAG_CALLER_ID"] = caller_id
    return clean
