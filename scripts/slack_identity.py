"""Keep CLI organization authorization separate from a Tag's workspace boundary."""
from __future__ import annotations

import re
from typing import Callable, NamedTuple


class WorkspaceSelection(NamedTuple):
    team_id: str
    name: str
    enterprise_id: str = ""
    organization_name: str = ""
    sign_in_id: str = ""  # The Slack CLI sign-in: the workspace, or its organization.
    user_id: str = ""  # The signed-in member, who becomes the Tag's owner.
    user_name: str = ""


def cli_team(values: dict[str, str]) -> str:
    return values.get("SLACK_ENTERPRISE_ID", "") or values.get("SLACK_TEAM_ID", "")


def grant_flags(team_id: str, enterprise_id: str = "") -> list[str]:
    if not re.fullmatch(r"T[A-Z0-9]+", team_id):
        raise RuntimeError("Select a Slack workspace Team ID starting with T.")
    if enterprise_id and not re.fullmatch(r"E[A-Z0-9]+", enterprise_id):
        raise RuntimeError("Select a Slack organization ID starting with E.")
    return ["--org-workspace-grant", team_id] if enterprise_id else []


def validate(token: str, *, team_id: str, app_id: str = "", enterprise_id: str = "",
             label: str, api: Callable) -> dict:
    """An org token must prove both its organization and its workspace grant."""
    payload = api(token, "auth.test", {})
    actual_app = payload.get("app_id")
    if enterprise_id and app_id and not actual_app:
        bot_id = payload.get("bot_id")
        if not bot_id:
            raise RuntimeError(f"{label} did not identify the selected Slack app")
        actual_app = (api(token, "bots.info", {"bot": bot_id, "team_id": team_id}).get("bot") or {}).get("app_id")
        if actual_app != app_id:
            raise RuntimeError(f"{label} belongs to a different Slack app")
    if app_id and actual_app and actual_app != app_id:
        raise RuntimeError(f"{label} belongs to a different Slack app")
    if enterprise_id and payload.get("enterprise_id") != enterprise_id:
        raise RuntimeError(f"{label} belongs to a different Slack organization")
    if payload.get("is_enterprise_install"):
        if not enterprise_id or not re.fullmatch(r"T[A-Z0-9]+", team_id):
            raise RuntimeError(f"{label} requires a selected organization and workspace")
        cursor = ""
        seen: set[str] = set()
        while True:
            parameters = {"limit": "200"}
            if cursor:
                parameters["cursor"] = cursor
            page = api(token, "auth.teams.list", parameters)
            if any(isinstance(team, dict) and team.get("id") == team_id
                   for team in page.get("teams", [])):
                return payload
            cursor = (page.get("response_metadata") or {}).get("next_cursor", "")
            if not cursor or cursor in seen:
                break
            seen.add(cursor)
        raise RuntimeError(
            f"{label} has no grant for workspace {team_id}. Ask an organization admin "
            "to add the app to that workspace, then retry setup or start."
        )
    if team_id and payload.get("team_id") != team_id:
        raise RuntimeError(f"{label} belongs to a different Slack workspace")
    return payload


def event_allowed(body: dict, team_id: str, enterprise_id: str = "", app_id: str = "") -> bool:
    """Use the receiving workspace, never an author's team or an org-wide grant."""
    if app_id and body.get("api_app_id") and body["api_app_id"] != app_id:
        return False
    team = body.get("team")
    view = body.get("view") or {}
    user = body.get("user") or {}
    view = view if isinstance(view, dict) else {}
    user = user if isinstance(user, dict) else {}
    candidates = [body.get("team_id"), body.get("context_team_id"),
                  team.get("id") if isinstance(team, dict) else team,
                  view.get("app_installed_team_id"), view.get("team_id")]
    recipients = [value for value in candidates if value]
    if any(value != recipients[0] for value in recipients):
        return False
    recipient = recipients[0] if recipients else None
    if not recipient and body.get("type") in {
        "block_actions", "view_submission", "view_closed", "shortcut", "message_action",
    }:
        recipient = user.get("team_id")
    if team_id and recipient != team_id:
        return False
    enterprise = body.get("enterprise")
    event_enterprise = body.get("enterprise_id") or (
        enterprise.get("id") if isinstance(enterprise, dict) else enterprise
    )
    return not (enterprise_id and event_enterprise and event_enterprise != enterprise_id)
