"""Discover Slack channels and present safe interactive channel choices."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable
try:
    from slack_permissions import MissingScope, guidance, open_settings
except ImportError:
    from scripts.slack_permissions import MissingScope, guidance, open_settings


SLACK_API = "https://slack.com/api"


@dataclass(frozen=True)
class SlackChannel:
    channel_id: str
    name: str
    is_private: bool
    is_member: bool

    @property
    def label(self) -> str:
        privacy = "private" if self.is_private else "public"
        membership = "ready" if self.is_member else "invite the bot first"
        return f"{'🔒 ' if self.is_private else ''}#{self.name} ({privacy}; {membership})"


class SlackChannelError(RuntimeError):
    """A safe, actionable channel-discovery failure."""


def slack_api(token: str, method: str, parameters: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{SLACK_API}/{method}?{urllib.parse.urlencode(parameters)}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read())
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError) as exc:
        raise SlackChannelError("Slack channel list is unavailable; check your connection and try again") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        error = payload.get("error", "unknown_error") if isinstance(payload, dict) else "invalid_response"
        if error == "missing_scope":
            raise MissingScope(method, payload.get("needed", ""))
        guidance = {
            "invalid_auth": "Slack rejected the bot token; reinstall the app or copy a fresh xoxb token",
            "not_authed": "Add the Slack bot token before choosing a channel",
            "missing_scope": "Reinstall the Slack app with channels:read and groups:read",
            "token_revoked": "The Slack bot token was revoked; reinstall the app",
        }.get(str(error), f"Slack could not list channels ({error})")
        raise SlackChannelError(guidance)
    return payload


def slack_api_post(token: str, method: str, parameters: dict[str, str] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{SLACK_API}/{method}",
        data=urllib.parse.urlencode(parameters or {}).encode(),
        headers={"Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read())
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError) as exc:
        raise SlackChannelError("Slack credential validation is unavailable; check your connection") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        error = payload.get("error", "unknown_error") if isinstance(payload, dict) else "invalid_response"
        if error == "missing_scope":
            raise MissingScope(method, payload.get("needed", ""))
        raise SlackChannelError(f"Slack rejected the credential ({error})")
    return payload


def list_channels(token: str) -> list[SlackChannel]:
    channels: list[SlackChannel] = []
    cursor = ""
    while True:
        parameters = {
            "types": "public_channel,private_channel",
            "exclude_archived": "true",
            "limit": "200",
        }
        if cursor:
            parameters["cursor"] = cursor
        payload = slack_api(token, "conversations.list", parameters)
        for item in payload.get("channels", []):
            if not isinstance(item, dict):
                continue
            channel_id, name = item.get("id"), item.get("name")
            if isinstance(channel_id, str) and isinstance(name, str) and channel_id and name:
                channels.append(
                    SlackChannel(
                        channel_id=channel_id,
                        name=name,
                        is_private=bool(item.get("is_private")),
                        is_member=bool(item.get("is_member")),
                    )
                )
        metadata = payload.get("response_metadata") or {}
        cursor = metadata.get("next_cursor", "") if isinstance(metadata, dict) else ""
        if not cursor:
            break
    return sorted(channels, key=lambda item: (not item.is_member, item.is_private, item.name.casefold()))


def channel_label(token: str, channel_id: str) -> str:
    if not channel_id:
        return "Any joined channel"
    try:
        payload = slack_api(token, "conversations.info", {"channel": channel_id})
        channel = payload.get("channel") or {}
        name = channel.get("name") if isinstance(channel, dict) else None
        return f"#{name}" if isinstance(name, str) and name else channel_id
    except (SlackChannelError, MissingScope):
        return channel_id


def choose_channel(
    token: str,
    current: str = "",
    *,
    input_fn: Callable[[str], str] | None = None,
) -> str:
    try:
        import setup_ui as ui
    except ImportError:
        from scripts import setup_ui as ui
    reader = input_fn or input
    channels = list_channels(token)
    if not channels:
        raise SlackChannelError("No Slack channels are visible to the bot; invite it to a channel and try again")
    while True:
        print()
        ui.message("Choose where Tag should respond:")
        for index, channel in enumerate(channels, 1):
            selected = " ← current" if channel.channel_id == current else ""
            ui.message(f"{index}. {channel.label}{selected}")
        ui.message("0. Any joined channel (less restrictive)")
        answer = reader("Channel: ").strip()
        if answer == "0":
            return ""
        if answer.isdigit() and 1 <= int(answer) <= len(channels):
            channel = channels[int(answer) - 1]
            if channel.is_member:
                return channel.channel_id
            ui.message(f"Invite the bot to #{channel.name}, then choose it again.")
            continue
        ui.message("Choose one of the displayed channel numbers.")


def parse_channel_ids(value: str) -> tuple[str, ...]:
    """Return a stable, de-duplicated explicit Slack channel allowlist."""
    result: list[str] = []
    for raw in value.split(","):
        channel_id = raw.strip()
        if channel_id and channel_id not in result:
            result.append(channel_id)
    return tuple(result)


def join_selected_channels(token: str, selected: list[SlackChannel], *, app_id: str = "") -> list[SlackChannel] | None:
    """Join only explicitly approved public channels; verify before returning."""
    try:
        import setup_ui as ui
    except ImportError:
        from scripts import setup_ui as ui
    pending = [channel for channel in selected if not channel.is_member]
    if not pending:
        return selected
    if any(channel.is_private for channel in pending):
        raise SlackChannelError("Private channels require an invitation before selection.")
    print()
    ui.message("Add Tag to: " + ", ".join(f"#{channel.name}" for channel in pending))
    ui.message("Only these selected public channels will be joined. No history is indexed yet.")
    action = ui.choose("Add Tag to selected channels?", ["Add Tag and continue", "Back to channels", "Save and exit"], default=1)
    if action == 1:
        return None
    if action == 2:
        raise ui.Paused()
    confirmed: list[SlackChannel] = []
    for channel in selected:
        while not channel.is_member:
            try:
                payload = slack_api_post(token, "conversations.join", {"channel": channel.channel_id})
                result = payload.get("channel") or {}
                if not isinstance(result, dict) or result.get("id") != channel.channel_id or result.get("is_member") is not True:
                    raise SlackChannelError("Slack did not confirm channel membership.")
                channel = SlackChannel(channel.channel_id, channel.name, channel.is_private, True)
            except (SlackChannelError, MissingScope) as error:
                print()
                ui.message(f"Could not add Tag to #{channel.name}: {error}")
                if isinstance(error, MissingScope):
                    guidance(error, app_id)
                ui.message("You can also use /invite in this channel to add your connected app.")
                options = ["Check again", "Back to channels", "Save and exit"]
                if isinstance(error, MissingScope):
                    options.append("Open app settings")
                while True:
                    action = ui.choose("Keep your channel selection", options)
                    if action != 3:
                        break
                    open_settings(app_id)
                if action == 1:
                    return None
                if action == 2:
                    raise ui.Paused()
                # A manual invitation or an earlier partial join may now be visible.
                refreshed = list_channels(token)
                channel = next((item for item in refreshed if item.channel_id == channel.channel_id), channel)
        confirmed.append(channel)
    return confirmed


def choose_channels(
    token: str,
    current: str = "",
    *,
    input_fn: Callable[[str], str] | None = None,
    app_id: str = "",
) -> list[SlackChannel]:
    """Require explicit approval for joined channels and optional public joins."""
    reader = input_fn or input
    try:
        import setup_ui as ui
    except ImportError:
        from scripts import setup_ui as ui
    channels = list_channels(token)
    if input_fn is None:
        while True:
            joined = [channel for channel in channels if channel.is_member]
            public_options = [channel for channel in channels if not channel.is_private and not channel.is_member]
            if joined:
                print()
                ui.message("Choose which joined channels Tag may reply to and index.")
                approved = ui.checklist(
                    [channel.label for channel in joined],
                    {index for index, channel in enumerate(joined) if channel.channel_id in parse_channel_ids(current)},
                )
                selected_joined = [channel for index, channel in enumerate(joined) if index in approved]
                options = [f"Continue with {len(selected_joined)} selected channel(s)"]
                if public_options:
                    options.append("Add public channels")
                options.extend(["Check again", "Save and exit"])
                action = ui.choose("Channel access", options)
                if action == 0:
                    return selected_joined
                if public_options and action == 1:
                    ui.message("Optional: select public channels for Tag to join.")
                    indices = ui.checklist([f"#{channel.name} (public; Tag will join)" for channel in public_options], set())
                    requested = [channel for index, channel in enumerate(public_options) if index in indices]
                    added = join_selected_channels(token, requested, app_id=app_id)
                    if added is not None:
                        return selected_joined + added
                    channels = list_channels(token)
                    continue
                if (public_options and action == 2) or (not public_options and action == 1):
                    channels = list_channels(token)
                    continue
                raise ui.Paused()

            if public_options:
                print()
                ui.message("Tag has not joined a channel yet.")
                action = ui.choose("Add Tag to a channel", ["Choose public channels", "Check after a private invitation", "Save and exit"])
                if action == 0:
                    ui.message("Select public channels for Tag to join.")
                    indices = ui.checklist([f"#{channel.name} (public; Tag will join)" for channel in public_options], set())
                    requested = [channel for index, channel in enumerate(public_options) if index in indices]
                    added = join_selected_channels(token, requested, app_id=app_id)
                    if added is not None:
                        return added
                elif action == 2:
                    raise ui.Paused()
                channels = list_channels(token)
                continue

            print()
            ui.message("Add your app to a Slack channel")
            ui.message("No public channels are visible, and Tag has not been invited to a private channel.")
            ui.message("In Slack, open a channel you want Tag to use, type /invite, and select this app.")
            ui.message("Then choose Check again below. Nothing is indexed yet.")
            if ui.choose("Ready to check membership?", ["Check again", "Save and exit"]) == 1:
                raise ui.Paused()
            channels = list_channels(token)

    available = [channel for channel in channels if not channel.is_private or channel.is_member]
    if not available:
        raise SlackChannelError(
            "No joined Slack channels are visible to the bot; invite it to a channel and try again"
        )
    selected = set(parse_channel_ids(current))
    while True:
        print()
        ui.message("Choose one or more channels for replies and Slack memory:")
        for index, channel in enumerate(available, 1):
            marker = "x" if channel.channel_id in selected else " "
            ui.message(f"{index}. [{marker}] {channel.label}")
        answer = reader("Channels (comma-separated numbers): ").strip()
        if not answer:
            ui.message("Select at least one channel.")
            continue
        pieces = [piece.strip() for piece in answer.split(",") if piece.strip()]
        if not pieces or any(not piece.isdigit() for piece in pieces):
            ui.message("Use comma-separated channel numbers, such as 1,3.")
            continue
        indices = [int(piece) for piece in pieces]
        if any(index < 1 or index > len(available) for index in indices):
            ui.message("Choose only displayed channel numbers.")
            continue
        chosen: list[SlackChannel] = []
        for index in indices:
            channel = available[index - 1]
            if channel not in chosen:
                chosen.append(channel)
        result = join_selected_channels(token, chosen, app_id=app_id)
        if result is not None:
            return result


def validate_app_id(value: str) -> bool:
    return bool(re.fullmatch(r"A[A-Z0-9]+", value.strip()))
