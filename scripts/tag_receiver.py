"""Automatic per-app registration with Tag's shared Slack receiver."""
from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request
from pathlib import Path

try:
    import tag_config as settings
except ImportError:
    from scripts import tag_config as settings

RECEIVER_URL = "https://tag-offline-receiver.maxine-1ef.workers.dev"


class ReceiverError(RuntimeError):
    pass


def endpoint(app_id: str, action: str = "registration") -> str:
    import re
    if not re.fullmatch(r"A[A-Z0-9]+", app_id):
        raise ReceiverError("Tag needs a valid Slack app. Run tag setup.")
    return f"{RECEIVER_URL}/v1/apps/{app_id}/{action}"


def request(method: str, app_id: str, token: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(endpoint(app_id), method=method, data=data,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "Tag/1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as result:
            body = json.load(result)
            if not isinstance(body, dict):
                raise ValueError()
            return body
    except urllib.error.HTTPError as error:
        try:
            detail = json.load(error).get("error", "")
        except (ValueError, AttributeError):
            detail = ""
        if error.code == 403 and detail != "This app is registered to another Tag installation":
            raise ReceiverError("Tag could not verify this Slack app. Check its app and bot credentials in tag setup.") from None
        messages = {
            400: "Slack credentials or permissions need updating. Run tag setup.",
            401: "The saved Tag connection is no longer authorized. Run tag setup.",
            403: "This Slack app is registered to another Tag installation. Disconnect it there first.",
            409: "This Slack app already has a different workspace registration.",
            429: "Tag's connection service is busy. Retry shortly.",
        }
        raise ReceiverError(messages.get(error.code, "Tag's connection service is unavailable. Retry tag start.")) from None
    except (OSError, ValueError):
        raise ReceiverError("Tag's connection service is unavailable. Your setup is saved; retry tag start.") from None


def ensure_registered(config_path: Path) -> dict[str, str]:
    values = settings.load_config(config_path)
    if values.get("OPENTAG_SLACK_CONNECTION") != "hosted":
        return values
    app = values.get("SLACK_APP_ID", "")
    previous = values.get("OPENTAG_RELAY_APP_ID", "")
    if previous and previous != app:
        remove_registration(values)
        values = settings.update_config(config_path, {"OPENTAG_RELAY_TOKEN": "", "OPENTAG_RELAY_URL": "", "OPENTAG_RELAY_APP_ID": ""})
    # Save ownership before the network call. Retrying an interrupted response
    # must authenticate as the same owner, not create an inaccessible registration.
    if not values.get("OPENTAG_RELAY_TOKEN") or values.get("OPENTAG_RELAY_APP_ID") != app:
        values = settings.update_config(config_path, {
            "OPENTAG_RELAY_TOKEN": secrets.token_urlsafe(48), "OPENTAG_RELAY_APP_ID": app,
        })
    payload = {
        "team": values["SLACK_TEAM_ID"], "app_token": values["SLACK_APP_TOKEN"],
        "bot_token": values["SLACK_BOT_TOKEN"],
        "users": [s.strip() for s in values["SLACK_ALLOWED_USER_IDS"].split(",") if s.strip()],
        "channels": [s.strip() for s in values.get("SLACK_CHANNEL_IDS", values.get("SLACK_CHANNEL_ID", "")).split(",") if s.strip()],
        "policy": values.get("SLACK_CHANNEL_POLICY", "selected"),
    }
    result = request("PUT", app, values["OPENTAG_RELAY_TOKEN"], payload)
    if not result.get("registered") or result.get("app") != app or result.get("team") != values["SLACK_TEAM_ID"]:
        raise ReceiverError("Tag's connection service returned an unexpected app identity. Registration was not activated.")
    return settings.update_config(config_path, {"OPENTAG_RELAY_URL": endpoint(app, "connect").replace("https://", "wss://", 1)})


def remove_registration(values: dict[str, str]) -> None:
    app = values.get("OPENTAG_RELAY_APP_ID")
    token = values.get("OPENTAG_RELAY_TOKEN")
    if not app:
        return
    if not token:
        raise ReceiverError("The saved connection credential is missing; restore it before disconnecting.")
    result = request("DELETE", app, token)
    if not result.get("removed"):
        raise ReceiverError("Hosted credential removal was not confirmed. Your local setup was kept.")


def disconnect(config_path: Path) -> dict[str, str]:
    values = settings.load_config(config_path)
    remove_registration(values)
    return settings.update_config(config_path, {
        "OPENTAG_SLACK_CONNECTION": "direct", "OPENTAG_RELAY_URL": "",
        "OPENTAG_RELAY_TOKEN": "", "OPENTAG_RELAY_APP_ID": "",
    })
