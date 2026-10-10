"""Environment isolation for OpenTag backend processes."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

try:
    from mfs_scope_policy import canonical_uri, is_scope_allowed, parse_scopes
except ImportError:
    from scripts.mfs_scope_policy import canonical_uri, is_scope_allowed, parse_scopes


SLACK_BRIDGE_ONLY_ENV = {
    "SLACK_APP_TOKEN",
    "SLACK_APP_ID",
    "SLACK_TEAM_ID",
    "SLACK_ENTERPRISE_ID",
    "SLACK_CHANNEL_ID",
    "SLACK_CHANNEL_IDS",
    "SLACK_CHANNEL_POLICY",
    "SLACK_ALLOWED_USER_IDS",
    "SLACK_ALLOWED_BOT_IDS",
    "OPENTAG_SLACK_DM_ENABLED",
    "MFS_SLACK_TOKEN",
    "OPENTAG_SLACK_SEARCH_GRANT",
}

TELEMETRY_ENV_NAMES = {
    "TAG_TELEMETRY",
    "TAG_POSTHOG_HOST",
    "TAG_POSTHOG_PROJECT_TOKEN",
}


def without_telemetry_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Keep telemetry controls and transport settings out of child processes."""
    return {
        key: value for key, value in source.items()
        if key not in TELEMETRY_ENV_NAMES
        and not key.upper().startswith("TAG_TELEMETRY_")
        and not key.upper().startswith("TAG_POSTHOG_")
    }


def text_only_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Preserve provider auth while blanking task/bridge data, also for SDK env merges."""
    clean = without_telemetry_environment(source)
    return {key: "" if key not in clean or key.startswith(("SLACK_", "MFS_"))
            or key in {"OPENTAG_CALLER_ID", "OPENTAG_CURRENT_CHANNEL_ID",
                       "OPENTAG_SLACK_SEARCH_GRANT", "OPENTAG_SLACK_CHANNEL_LABELS"}
            else value for key, value in source.items()}


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


def parse_channel_scopes(raw: str) -> dict[str, list[str]]:
    """Read MFS_CHANNEL_SCOPES: a JSON object from Slack channel ID to extra MFS scopes.

    Raises ValueError when the value is not that shape, so settings can reject it.
    """
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("MFS_CHANNEL_SCOPES must be a JSON object")
    out: dict[str, list[str]] = {}
    for channel, scopes in data.items():
        # A comma would let one entry smuggle a second scope into the comma-joined list.
        if not isinstance(channel, str) or not re.fullmatch(r"[CG][A-Z0-9]+", channel) or not isinstance(
            scopes, list
        ) or not all(
            isinstance(scope, str) and "," not in scope and canonical_uri(scope) is not None
            for scope in scopes
        ):
            raise ValueError("MFS_CHANNEL_SCOPES maps each channel ID to a list of source URIs")
        out[channel] = [scope.strip() for scope in scopes]
    return out


def with_channel_scopes(scopes: str, conversation_id: str, source: Mapping[str, str]) -> str:
    """Add the owner's extra MFS scopes for this channel, each within the saved MFS_ALLOWED_SCOPES.

    An invalid map adds nothing, so a bad setting never widens what a run may read.
    """
    try:
        extra = parse_channel_scopes(source.get("MFS_CHANNEL_SCOPES", "")).get(conversation_id, [])
    except ValueError:
        return scopes
    allowed = parse_scopes(source.get("MFS_ALLOWED_SCOPES", ""))
    merged = parse_scopes(scopes)
    for scope in extra:
        if scope not in merged and is_scope_allowed(scope, allowed):
            merged.append(scope)
    return ",".join(merged)


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
    slack_search_grant: str | None = None,
    memory_receipts: str | None = None,
    handoff_requests: str | None = None,
    handoff_depth: int = 0,
) -> dict[str, str]:
    clean = without_telemetry_environment(
        isolated_environment(source, transport=transport)
    )
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
    # Non-Slack sources (for example a customer's chat archive) reach a run only
    # through the channel they are mapped to.
    if source.get("MFS_CHANNEL_SCOPES"):
        clean["MFS_ALLOWED_SCOPES"] = with_channel_scopes(
            clean.get("MFS_ALLOWED_SCOPES", ""), conversation_id, source
        )
    clean.pop("MFS_CHANNEL_SCOPES", None)
    if channel_labels:
        clean["OPENTAG_SLACK_CHANNEL_LABELS"] = channel_labels
    else:
        clean.pop("OPENTAG_SLACK_CHANNEL_LABELS", None)
    if slack_search_grant:
        clean["OPENTAG_SLACK_SEARCH_GRANT"] = slack_search_grant
    else:
        clean.pop("OPENTAG_SLACK_SEARCH_GRANT", None)
    if memory_receipts:
        clean["OPENTAG_MEMORY_RECEIPTS"] = memory_receipts
    else:
        clean.pop("OPENTAG_MEMORY_RECEIPTS", None)
    if handoff_requests:
        clean["OPENTAG_HANDOFF_REQUESTS"] = handoff_requests
    else:
        clean.pop("OPENTAG_HANDOFF_REQUESTS", None)
    clean["OPENTAG_HANDOFF_DEPTH"] = str(handoff_depth)
    return clean
