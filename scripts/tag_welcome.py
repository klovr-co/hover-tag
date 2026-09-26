"""Versioned, retryable welcome delivery after a successful Tag start."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

try:
    from . import slack_channels, tag_config
    from .tag_error_reporting import COMMUNITY_INVITE_URL
except ImportError:
    import slack_channels
    import tag_config
    from tag_error_reporting import COMMUNITY_INVITE_URL



def welcome_message(values: dict[str, str]) -> dict[str, str]:
    """Slack blocks plus complete notification/screen-reader fallback text."""
    introduction = (
        "Send me a task here, or mention me in a connected channel."
        if values.get("OPENTAG_SLACK_DM_ENABLED", "1") == "1"
        else "Mention me in a connected Slack channel to send me a task."
    )
    example = "Try: “Help me plan my week. Ask me what’s on my plate.”"
    help_text = f"Need a hand? <{COMMUNITY_INVITE_URL}|Join the Hover Community> to ask questions or share feedback."
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "👋 *You’re all set!*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": introduction + "\n\n" + example}},
        {"type": "section", "text": {"type": "mrkdwn", "text": help_text}},
    ]
    return {
        "text": f"👋 You’re all set!\n\n{introduction}\n\n{example}\n\n{help_text}",
        "blocks": json.dumps(blocks, ensure_ascii=False),
        "unfurl_links": "false",
        "unfurl_media": "false",
    }


def send_once(home: Path, values: dict[str, str]) -> str | None:
    """Called under the startup lock, after all readiness checks have passed.

    Missing v1 state also upgrades existing installations without setup or a
    new Slack grant. A pending receipt survives interrupted/failed delivery;
    only Slack's confirmed message ID commits completion.
    """
    users = list(dict.fromkeys(
        user.strip() for user in values.get("SLACK_ALLOWED_USER_IDS", "").split(",")
        if user.strip()
    ))
    if len(users) != 1:
        return "Skipped: a single setup user is required for the welcome DM."
    user = users[0]
    team = values.get("SLACK_TEAM_ID", "")
    app = values.get("SLACK_APP_ID", "")
    if not (re.fullmatch(r"[UW][A-Z0-9]+", user)
            and re.fullmatch(r"T[A-Z0-9]+", team)
            and re.fullmatch(r"A[A-Z0-9]+", app)):
        raise ValueError("Welcome recipient identity is incomplete")
    path = home / "state/welcome-v1" / f"{team}-{app}-{user}.json"
    message_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"tag:welcome:v1:{team}:{app}:{user}"))
    if path.exists():
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(receipt, dict) or receipt.get("version") != "1"
                or receipt.get("client_msg_id") != message_id
                or receipt.get("status") not in {"pending", "sent"}):
            raise ValueError("Welcome receipt needs repair")
        if receipt["status"] == "sent":
            if not receipt.get("channel") or not receipt.get("ts"):
                raise ValueError("Welcome delivery receipt is incomplete")
            return None

    receipt = {"version": "1", "status": "pending", "client_msg_id": message_id}
    # Check durable storage before sending, and reuse the message ID on retry.
    tag_config.save_config(path, receipt)
    # chat.postMessage accepts a user ID and opens the app's DM using chat:write.
    # https://docs.slack.dev/reference/methods/chat.postMessage/
    response = slack_channels.slack_api_post(values["SLACK_BOT_TOKEN"], "chat.postMessage", {
        "channel": user,
        "client_msg_id": message_id,
        **welcome_message(values),
    })
    channel, timestamp = response.get("channel"), response.get("ts")
    if (response.get("ok") is not True or not isinstance(channel, str)
            or not re.fullmatch(r"D[A-Z0-9]+", channel)
            or not isinstance(timestamp, str) or not re.fullmatch(r"\d+\.\d+", timestamp)):
        raise ValueError("Slack did not confirm welcome delivery")
    tag_config.save_config(path, {**receipt, "status": "sent", "channel": channel, "ts": timestamp})
    return "Sent · check your Slack direct messages."
