"""Environment isolation for OpenTag backend processes."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit


SLACK_BRIDGE_ONLY_ENV = {
    "SLACK_APP_TOKEN",
    "OPENTAG_RELAY_URL",
    "OPENTAG_RELAY_TOKEN",
    "OPENTAG_RELAY_APP_ID",
    "OPENTAG_SLACK_CONNECTION",
    "SLACK_APP_ID",
    "SLACK_TEAM_ID",
    "SLACK_CHANNEL_ID",
    "SLACK_CHANNEL_IDS",
    "SLACK_CHANNEL_POLICY",
    "SLACK_ALLOWED_USER_IDS",
    "OPENTAG_SLACK_DM_ENABLED",
    "MFS_SLACK_TOKEN",
}


def current_channel_scopes(raw_scopes: str, conversation_id: str) -> str:
    """Narrow Slack connector scopes to this invocation's channel directory."""
    scopes = [scope.strip() for scope in raw_scopes.split(",") if scope.strip()]
    narrowed: list[str] = []
    for scope in scopes:
        parsed = urlsplit(scope)
        marker = f"__{conversation_id}"
        if parsed.scheme == "slack" and any(
            segment.endswith(marker) for segment in parsed.path.split("/")
        ):
            narrowed.append(scope)
    return ",".join(narrowed)


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
    authorized_scopes: str | None = None,
    channel_labels: str | None = None,
) -> dict[str, str]:
    clean = isolated_environment(source, transport=transport)
    # The backend may use SLACK_BOT_TOKEN through channel-restricted helpers,
    # but never needs Socket Mode or bridge access-control configuration.
    for name in SLACK_BRIDGE_ONLY_ENV:
        clean.pop(name, None)
    clean["OPENTAG_CURRENT_CHANNEL_ID"] = conversation_id
    clean["OPENTAG_CALLER_ID"] = caller_id
    if authorized_scopes is not None:
        clean["MFS_ALLOWED_SCOPES"] = authorized_scopes
    elif clean.get("MFS_ALLOWED_SCOPES"):
        clean["MFS_ALLOWED_SCOPES"] = current_channel_scopes(
            clean["MFS_ALLOWED_SCOPES"], conversation_id
        )
    if channel_labels:
        clean["OPENTAG_SLACK_CHANNEL_LABELS"] = channel_labels
    else:
        clean.pop("OPENTAG_SLACK_CHANNEL_LABELS", None)
    return clean
