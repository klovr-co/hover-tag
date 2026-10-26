"""Environment isolation for OpenTag backend processes."""

from __future__ import annotations

from collections.abc import Mapping


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
    "OPENTAG_SLACK_SEARCH_GRANT",
    "OPENTAG_SLACK_ACTION_TOKEN",
}
# Settings an earlier Tag used for its local Slack index; a run never sees them.
RETIRED_PREFIXES = ("MFS_",)

RUN_LIMIT_ENV = {
    "OPENTAG_RUN_MAX_TURNS": "max_turns",
    "OPENTAG_RUN_MAX_BUDGET_USD": "max_budget_usd",
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
            or key in {"OPENTAG_CALLER_ID", "OPENTAG_CURRENT_CHANNEL_ID", "OPENTAG_CURRENT_THREAD_TS",
                       "OPENTAG_ROUTINE_ID", "OPENTAG_SLACK_SEARCH_GRANT", "OPENTAG_SLACK_CHANNEL_LABELS",
                       "OPENTAG_SLACK_ACTION_TOKEN"}
            else value for key, value in source.items()}


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
    channel_labels: str | None = None,
    slack_search_grant: str | None = None,
    memory_receipts: str | None = None,
    handoff_requests: str | None = None,
    handoff_depth: int = 0,
    run_limits: Mapping[str, float | int] | None = None,
    routine_id: str | None = None,
    thread_ts: str | None = None,
    action_token: str | None = None,
) -> dict[str, str]:
    clean = without_telemetry_environment(
        isolated_environment(source, transport=transport)
    )
    # The backend may use SLACK_BOT_TOKEN through channel-restricted helpers,
    # but never needs Socket Mode or bridge access-control configuration.
    for name in SLACK_BRIDGE_ONLY_ENV:
        clean.pop(name, None)
    for name in [key for key in clean if key.startswith(RETIRED_PREFIXES)]:
        clean.pop(name)
    clean["OPENTAG_CURRENT_CHANNEL_ID"] = conversation_id
    clean["OPENTAG_CALLER_ID"] = caller_id
    # The memory helper links what it saves to this Slack thread.
    if thread_ts:
        clean["OPENTAG_CURRENT_THREAD_TS"] = thread_ts
    else:
        clean.pop("OPENTAG_CURRENT_THREAD_TS", None)
    # Slack's permission to search for this one request (Real-Time Search).
    if action_token:
        clean["OPENTAG_SLACK_ACTION_TOKEN"] = action_token
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
    # Set only for a routine's own run, so tag routine commands can refuse to change routines from it.
    if routine_id:
        clean["OPENTAG_ROUTINE_ID"] = routine_id
    else:
        clean.pop("OPENTAG_ROUTINE_ID", None)
    # Per-run limits come only from the bridge (routine runs), never from the environment.
    for name, key in RUN_LIMIT_ENV.items():
        value = (run_limits or {}).get(key)
        if value is None:
            clean.pop(name, None)
        else:
            clean[name] = str(value)
    return clean
