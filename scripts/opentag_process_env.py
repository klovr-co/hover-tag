"""Environment isolation shared by OpenTag chat adapters."""

from __future__ import annotations

from collections.abc import Mapping


TRANSPORT_ENV = {
    "slack": {
        "SLACK_APP_TOKEN",
        "SLACK_BOT_TOKEN",
        "SLACK_CHANNEL_ID",
        "SLACK_ALLOWED_USER_IDS",
    },
    "zulip": {
        "ZULIP_CONFIG_FILE",
        "ZULIP_ADMIN_CONFIG_FILE",
        "ZULIP_RC_PATH",
        "BOT_ALLOWED_PRIVATE_STREAMS",
        "BOT_ALLOWED_WRITE_STREAMS",
    },
}


def isolated_environment(source: Mapping[str, str], *, transport: str) -> dict[str, str]:
    if transport not in TRANSPORT_ENV:
        raise ValueError("transport must be slack or zulip")
    clean = dict(source)
    for other_transport, names in TRANSPORT_ENV.items():
        if other_transport == transport:
            continue
        for name in names:
            clean.pop(name, None)
    return clean


def backend_environment(
    source: Mapping[str, str],
    *,
    transport: str,
    conversation_id: str,
    caller_id: str,
) -> dict[str, str]:
    clean = isolated_environment(source, transport=transport)
    if transport == "slack":
        # The backend may use SLACK_BOT_TOKEN through the channel-restricted
        # Canvas helper, but never needs the Socket Mode app token.
        clean.pop("SLACK_APP_TOKEN", None)
        clean.pop("SLACK_CHANNEL_ID", None)
        clean.pop("SLACK_ALLOWED_USER_IDS", None)
    else:
        # The native Zulip backend returns text to its bridge and does not call
        # Zulip directly, so neither bot nor administrator credentials belong
        # in the agent process.
        for name in TRANSPORT_ENV["zulip"]:
            clean.pop(name, None)
    clean["OPENTAG_CURRENT_CHANNEL_ID"] = conversation_id
    clean["OPENTAG_CALLER_ID"] = caller_id
    return clean
