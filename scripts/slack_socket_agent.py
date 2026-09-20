#!/usr/bin/env python3
# Modified by klovr.co in 2026 for Tag. See NOTICE and repository history.
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

try:
    from .opentag_process_env import backend_environment
    from .slack_mrkdwn import to_mrkdwn
    from . import slack_channels
except ImportError:  # Direct script execution does not create a package context.
    from opentag_process_env import backend_environment
    from slack_mrkdwn import to_mrkdwn
    import slack_channels


MENTION_RE = re.compile(r"<@[^>]+>")
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_ATTACHMENT_TEXT_CHARS = 12_000
MAX_REPLY_CHARS = 3_800
STREAM_START_CHARS = 40
STREAM_APPEND_CHARS = 200
STREAM_FLUSH_SECONDS = 0.35
ACTIVITY_DEBOUNCE_SECONDS = 0.25
ACTIVITY_HOLD_SECONDS = 1.5
ACTIVITY_WAIT_SECONDS = 12.0
CANCEL_GRACE_SECONDS = 6.0
STATUS_REFRESH_SECONDS = 90
SUPPORTED_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")
DEFAULT_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
DEFAULT_CONFIG_VALUE = "__opentag_default__"
SETTINGS_ACTION_ID = "opentag_change_agent_settings"
SETTINGS_MODEL_ACTION_ID = "opentag_settings_model"
SETTINGS_EFFORT_ACTION_ID = "opentag_settings_effort"
SETTINGS_VIEW_ID = "opentag_agent_settings"
HOME_CHANNEL_ACTION_ID = "opentag_home_channel"
UNAUTHORIZED_USER_MESSAGE = "Sorry, only users authorized by the Tag owner can use this bot."
LOADING_MESSAGES = [
    "Working on the request…",
]
TEXT_FILE_MIME_TYPES = {
    "application/json",
    "application/javascript",
    "application/xml",
    "application/x-yaml",
}
TEXT_FILE_TYPES = {
    "bash",
    "c",
    "cpp",
    "csv",
    "html",
    "java",
    "javascript",
    "json",
    "markdown",
    "md",
    "php",
    "python",
    "ruby",
    "sql",
    "text",
    "txt",
    "typescript",
    "xml",
    "yaml",
}


@dataclass(frozen=True)
class CodexModelOption:
    model_id: str
    label: str
    reasoning_efforts: tuple[str, ...]


@dataclass(frozen=True)
class AgentSettings:
    model: str | None = None
    reasoning_effort: str | None = None


def codex_models_cache_path() -> Path:
    codex_home = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    return codex_home / "models_cache.json"


def discover_codex_models() -> list[CodexModelOption]:
    """Read Codex's local model metadata, optionally constrained by an operator allowlist."""
    configured = [
        value.strip()
        for value in os.getenv("OPENTAG_CODEX_MODELS", "").split(",")
        if value.strip()
    ]
    discovered: dict[str, CodexModelOption] = {}
    try:
        payload = json.loads(codex_models_cache_path().read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            payload = {}
        for raw_model in payload.get("models", []):
            if not isinstance(raw_model, dict) or raw_model.get("visibility") == "hide":
                continue
            model_id = raw_model.get("slug")
            if not isinstance(model_id, str) or not model_id:
                continue
            efforts = tuple(
                item["effort"]
                for item in raw_model.get("supported_reasoning_levels", [])
                if isinstance(item, dict) and isinstance(item.get("effort"), str)
            )
            discovered[model_id] = CodexModelOption(
                model_id=model_id,
                label=str(raw_model.get("display_name") or model_id),
                reasoning_efforts=efforts or DEFAULT_REASONING_EFFORTS,
            )
    except (OSError, ValueError, TypeError):
        pass

    if configured:
        return [
            discovered.get(
                model_id,
                CodexModelOption(model_id, model_id, DEFAULT_REASONING_EFFORTS),
            )
            for model_id in configured
        ]
    return list(discovered.values())


def configured_reasoning_efforts() -> tuple[str, ...]:
    configured = tuple(
        value.strip().lower()
        for value in os.getenv("OPENTAG_CODEX_REASONING_EFFORTS", "").split(",")
        if value.strip().lower() in SUPPORTED_REASONING_EFFORTS
    )
    return configured or DEFAULT_REASONING_EFFORTS


def efforts_for_model(model: str | None, models: list[CodexModelOption]) -> tuple[str, ...]:
    allowed = set(configured_reasoning_efforts())
    if model:
        selected = next((item for item in models if item.model_id == model), None)
        if selected:
            return tuple(effort for effort in selected.reasoning_efforts if effort in allowed)
    discovered = {effort for item in models for effort in item.reasoning_efforts}
    return tuple(effort for effort in configured_reasoning_efforts() if not discovered or effort in discovered)


def normalize_settings(
    settings: AgentSettings,
    models: list[CodexModelOption],
) -> AgentSettings:
    known_models = {item.model_id for item in models}
    model = settings.model if settings.model in known_models else None
    efforts = efforts_for_model(model, models)
    effort = settings.reasoning_effort if settings.reasoning_effort in efforts else None
    return AgentSettings(model=model, reasoning_effort=effort)


class ThreadAgentSettingsStore:
    """Persist model choices by Slack thread so bridge restarts retain them."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(
            os.getenv(
                "OPENTAG_SLACK_SETTINGS_FILE",
                str(skill_dir() / ".runtime" / "slack-thread-settings.json"),
            )
        ).expanduser()
        self.lock = threading.Lock()

    @staticmethod
    def key(team: str, channel: str, thread_ts: str) -> str:
        return f"{team}:{channel}:{thread_ts}"

    def _read(self) -> dict[str, dict[str, str | None]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def get(self, team: str, channel: str, thread_ts: str) -> AgentSettings:
        with self.lock:
            raw = self._read().get(self.key(team, channel, thread_ts), {})
        if not isinstance(raw, dict):
            raw = {}
        model = raw.get("model")
        effort = raw.get("reasoning_effort")
        return AgentSettings(
            model=model if isinstance(model, str) else None,
            reasoning_effort=effort if isinstance(effort, str) else None,
        )

    def set(self, team: str, channel: str, thread_ts: str, settings: AgentSettings) -> None:
        with self.lock:
            payload = self._read()
            payload[self.key(team, channel, thread_ts)] = {
                "model": settings.model,
                "reasoning_effort": settings.reasoning_effort,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(temporary, self.path)


def attachment_text(value: Any) -> str:
    """Normalize a legacy Slack attachment value without letting it dominate a prompt."""
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) > MAX_ATTACHMENT_TEXT_CHARS:
        return text[:MAX_ATTACHMENT_TEXT_CHARS] + "\n[Attachment text truncated]"
    return text


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def skill_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def default_workdir() -> Path:
    return Path(os.getenv("OPENTAG_WORKDIR", str(Path.cwd())))


def strip_mention(text: str) -> str:
    stripped = MENTION_RE.sub("", text).strip()
    return stripped or "Find the most relevant context for this thread and summarize it."


def format_message_attachments(message: dict[str, Any]) -> list[str]:
    """Expose integration previews (including Slack Email) to the backend prompt."""
    lines: list[str] = []
    for attachment in message.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        service = attachment_text(attachment.get("service_name")) or "Slack attachment"
        parts = [f"[{service}]"]
        for label, key in (("Title", "title"), ("From", "author_name"), ("Preview", "pretext"), ("Body", "text")):
            value = attachment_text(attachment.get(key))
            if value:
                parts.append(f"{label}: {value}")
        for field in attachment.get("fields") or []:
            if not isinstance(field, dict):
                continue
            title = attachment_text(field.get("title")) or "Field"
            value = attachment_text(field.get("value"))
            if value:
                parts.append(f"{title}: {value}")
        if len(parts) > 1:
            lines.append("\n".join(parts))
    return lines


def attachment_name(file: dict[str, Any], index: int) -> str:
    raw_name = file.get("name") or f"slack-image-{index}"
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_name).strip(".-")
    return safe_name or f"slack-image-{index}"


def is_text_file(file: dict[str, Any]) -> bool:
    """Return whether a Slack file is safe to include as bounded prompt text."""
    mime_type = (file.get("mimetype") or "").lower()
    file_type = (file.get("filetype") or "").lower()
    return mime_type.startswith("text/") or mime_type in TEXT_FILE_MIME_TYPES or file_type in TEXT_FILE_TYPES


def download_file_bytes(url: str, token: str, expected_content_prefix: str | None = None) -> bytes:
    """Download one private Slack file while enforcing the attachment size limit."""
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    chunks: list[bytes] = []
    total = 0
    with urllib.request.urlopen(request, timeout=30) as response:
        if expected_content_prefix:
            content_type = response.headers.get_content_type()
            if content_type and not content_type.startswith(expected_content_prefix):
                raise ValueError(f"Slack returned {content_type}, not {expected_content_prefix.rstrip('/')}")
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_ATTACHMENT_BYTES:
                raise ValueError("file exceeds the 15 MB safety limit")
            chunks.append(chunk)
    return b"".join(chunks)


def download_thread_images(messages: list[dict[str, Any]], attachment_dir: Path) -> list[str]:
    """Download Slack image attachments for the current invocation only."""
    lines: list[str] = []
    seen_file_ids: set[str] = set()
    token = require_env("SLACK_BOT_TOKEN")

    for message in messages:
        for file in message.get("files") or []:
            file_id = file.get("id")
            if not file_id or file_id in seen_file_ids:
                continue
            seen_file_ids.add(file_id)
            mime_type = file.get("mimetype") or ""
            if not mime_type.startswith("image/"):
                continue
            url = file.get("url_private_download") or file.get("url_private")
            name = attachment_name(file, len(seen_file_ids))
            if not url:
                lines.append(f"[Slack image attachment could not be downloaded: {name}]")
                continue

            target = attachment_dir / name
            try:
                data = download_file_bytes(url, token, expected_content_prefix="image/")
                with target.open("wb") as output:
                    output.write(data)
                detected_type = mimetypes.guess_type(target.name)[0] or mime_type
                lines.append(f"[Slack image attachment: {name} ({detected_type}) at {target}]")
            except (OSError, urllib.error.URLError, ValueError) as exc:
                target.unlink(missing_ok=True)
                lines.append(f"[Could not retrieve Slack image {name}: {exc}]")
    return lines


def download_thread_text_files(messages: list[dict[str, Any]]) -> list[str]:
    """Read Slack snippets and text attachments into the current prompt only."""
    lines: list[str] = []
    seen_file_ids: set[str] = set()
    token = require_env("SLACK_BOT_TOKEN")

    for message in messages:
        for file in message.get("files") or []:
            file_id = file.get("id")
            if not file_id or file_id in seen_file_ids or not is_text_file(file):
                continue
            seen_file_ids.add(file_id)
            name = attachment_name(file, len(seen_file_ids))
            url = file.get("url_private_download") or file.get("url_private")
            if not url:
                lines.append(f"[Slack text attachment could not be downloaded: {name}]")
                continue
            try:
                text = download_file_bytes(url, token).decode("utf-8", errors="replace").strip()
                if len(text) > MAX_ATTACHMENT_TEXT_CHARS:
                    text = text[:MAX_ATTACHMENT_TEXT_CHARS] + "\n[Attachment text truncated]"
                if text:
                    lines.append(f"[Slack text attachment: {name}]\n{text}")
                else:
                    lines.append(f"[Slack text attachment was empty: {name}]")
            except (OSError, UnicodeError, urllib.error.URLError, ValueError) as exc:
                lines.append(f"[Could not retrieve Slack text attachment {name}: {exc}]")
    return lines


def build_thread_text(client: Any, channel: str, thread_ts: str, attachment_dir: Path) -> str:
    response = client.conversations_replies(channel=channel, ts=thread_ts, limit=30)
    messages = response.get("messages", [])
    lines = []
    for message in messages:
        user = message.get("user") or message.get("bot_id") or "unknown"
        text = message.get("text", "")
        lines.append(f"{user}: {text}")
        lines.extend(format_message_attachments(message))
    lines.extend(download_thread_text_files(messages))
    lines.extend(download_thread_images(messages, attachment_dir))
    return "\n".join(lines)


def split_reply(text: str, max_chars: int = MAX_REPLY_CHARS) -> list[str]:
    """Split a Slack reply at readable boundaries without losing generated text."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        split_at = max(
            remaining.rfind("\n\n", 0, max_chars + 1),
            remaining.rfind("\n", 0, max_chars + 1),
            remaining.rfind(" ", 0, max_chars + 1),
        )
        if split_at <= 0:
            split_at = max_chars
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    chunks.append(remaining)
    return chunks


def env_enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def friendly_effort(effort: str | None) -> str:
    return {
        None: "Default thinking",
        "minimal": "Minimal",
        "low": "Fast",
        "medium": "Balanced",
        "high": "Deep",
        "xhigh": "Extra deep",
        "max": "Maximum",
        "ultra": "Ultra",
    }.get(effort, effort or "Default thinking")


def model_label(model: str | None, models: list[CodexModelOption]) -> str:
    if model is None:
        return "Default model"
    option = next((item for item in models if item.model_id == model), None)
    return option.label if option else model


def settings_context(
    settings: AgentSettings,
    models: list[CodexModelOption],
) -> str:
    return f"Codex · {model_label(settings.model, models)} · {friendly_effort(settings.reasoning_effort)}"


def settings_button_blocks(
    *,
    team: str,
    channel: str,
    thread_ts: str,
    settings: AgentSettings,
    models: list[CodexModelOption],
) -> list[dict[str, Any]]:
    value = json.dumps(
        {"team": team, "channel": channel, "thread_ts": thread_ts},
        separators=(",", ":"),
    )
    return [
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": settings_context(settings, models)}],
        },
        {
            "type": "actions",
            "block_id": f"opentag_settings_{thread_ts}",
            "elements": [
                {
                    "type": "button",
                    "action_id": SETTINGS_ACTION_ID,
                    "text": {"type": "plain_text", "text": "Change model & thinking"},
                    "value": value,
                }
            ],
        },
    ]


def select_option(value: str, text: str, description: str | None = None) -> dict[str, Any]:
    option: dict[str, Any] = {
        "text": {"type": "plain_text", "text": text[:75]},
        "value": value,
    }
    if description:
        option["description"] = {"type": "plain_text", "text": description[:75]}
    return option


def settings_modal(
    *,
    metadata: dict[str, str],
    settings: AgentSettings,
    models: list[CodexModelOption],
) -> dict[str, Any]:
    model_options = [select_option(DEFAULT_CONFIG_VALUE, "Codex default")]
    model_options.extend(select_option(item.model_id, item.label, item.model_id) for item in models)
    selected_model = settings.model if settings.model in {item.model_id for item in models} else None
    efforts = efforts_for_model(selected_model, models)
    effort_options = [select_option(DEFAULT_CONFIG_VALUE, "Codex default")]
    effort_options.extend(
        select_option(effort, friendly_effort(effort), effort) for effort in efforts
    )
    selected_effort = settings.reasoning_effort if settings.reasoning_effort in efforts else None
    return {
        "type": "modal",
        "callback_id": SETTINGS_VIEW_ID,
        "private_metadata": json.dumps(metadata, separators=(",", ":")),
        "title": {"type": "plain_text", "text": "Task settings"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "model",
                "label": {"type": "plain_text", "text": "Model"},
                "element": {
                    "type": "static_select",
                    "action_id": SETTINGS_MODEL_ACTION_ID,
                    "options": model_options,
                    "initial_option": next(
                        option
                        for option in model_options
                        if option["value"] == (selected_model or DEFAULT_CONFIG_VALUE)
                    ),
                },
            },
            {
                "type": "input",
                "block_id": "reasoning_effort",
                "label": {"type": "plain_text", "text": "Thinking"},
                "element": {
                    "type": "radio_buttons",
                    "action_id": SETTINGS_EFFORT_ACTION_ID,
                    "options": effort_options,
                    "initial_option": next(
                        option
                        for option in effort_options
                        if option["value"] == (selected_effort or DEFAULT_CONFIG_VALUE)
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "These settings apply to the next request in this Slack thread.",
                    }
                ],
            },
        ],
    }


def selected_setting(view: dict[str, Any], block_id: str, action_id: str) -> str | None:
    selected = view["state"]["values"][block_id][action_id].get("selected_option")
    value = selected.get("value") if isinstance(selected, dict) else None
    return None if value == DEFAULT_CONFIG_VALUE else value


class WorkingIndicator:
    """Prefer Slack's native agent status, with the old message as a fallback."""

    def __init__(self, client: Any, channel: str, thread_ts: str, logger: Any) -> None:
        self.client = client
        self.channel = channel
        self.thread_ts = thread_ts
        self.logger = logger
        self.native = False
        self.session_api = False
        self.legacy_status = False
        self.message_ts: str | None = None
        self.refresh_timer: threading.Timer | None = None
        self.activity_timer: threading.Timer | None = None
        self.activity_due = float("inf")
        self.activities: dict[str, str] = {}
        self.activity_started: dict[str, float] = {}
        self.wait_labels: dict[str, str] = {}
        self.preparing_answer = False
        self.last_status: str | None = None
        self.last_status_at = float("-inf")
        self.lock = threading.RLock()

    def set_native_status(self, *, force: bool = False) -> None:
        if self.activities:
            latest = next(reversed(self.activities))
            elapsed = time.monotonic() - self.activity_started[latest]
            status = self.wait_labels[latest] if elapsed >= ACTIVITY_WAIT_SECONDS else self.activities[latest]
            if len(self.activities) > 1:
                status += f" (+{len(self.activities) - 1} other active)"
        elif self.preparing_answer:
            status = "Preparing your answer…"
        else:
            status = "is working on this…"
        if not force and status == self.last_status:
            return
        self.client.assistant_threads_setStatus(
            channel_id=self.channel,
            thread_ts=self.thread_ts,
            status=status,
            loading_messages=LOADING_MESSAGES,
        )
        self.last_status = status
        self.last_status_at = time.monotonic()

    def set_session_status(self, status: str) -> None:
        """Use the Agent Sessions lifecycle API independently of display copy."""
        self.client.api_call(
            "agents.sessions.setStatus",
            json={
                "channel_id": self.channel,
                "thread_ts": self.thread_ts,
                "status": status,
            },
        )

    def schedule_refresh(self) -> None:
        """Schedule while the caller holds ``lock`` and native status is active."""
        self.refresh_timer = threading.Timer(STATUS_REFRESH_SECONDS, self.refresh)
        self.refresh_timer.daemon = True
        self.refresh_timer.start()

    def refresh(self) -> None:
        with self.lock:
            self.refresh_timer = None
            if not self.native:
                return
            if self.session_api:
                try:
                    self.set_session_status("processing")
                except Exception as exc:  # noqa: BLE001 - legacy status may still work
                    self.logger.warning("Could not refresh Slack agent session: %s", exc)
                    self.session_api = False
            if self.legacy_status:
                try:
                    self.set_native_status(force=True)
                except Exception as exc:  # noqa: BLE001 - Agent Sessions may still work
                    self.logger.warning("Could not refresh native Slack loading copy: %s", exc)
                    self.legacy_status = False
            self.native = self.session_api or self.legacy_status
            if self.native:
                self.schedule_refresh()

    def activity(self, event_type: str, activity_id: str, label: str, wait_label: str = "This operation is still running…") -> None:
        """Debounce truthful tool lifecycle updates and count concurrent work."""
        with self.lock:
            if event_type == "activity_start":
                self.activities[activity_id] = label
                self.activity_started.setdefault(activity_id, time.monotonic())
                self.wait_labels[activity_id] = wait_label
                self.preparing_answer = False
            else:
                self.activities.pop(activity_id, None)
                self.activity_started.pop(activity_id, None)
                self.wait_labels.pop(activity_id, None)
            self.schedule_activity()

    def answer_started(self) -> None:
        with self.lock:
            self.preparing_answer = True
            self.schedule_activity()

    def schedule_activity(self) -> None:
        """Coalesce events and hold the displayed copy briefly; caller holds lock."""
        if not self.native or not self.legacy_status:
            return
        delay = max(ACTIVITY_DEBOUNCE_SECONDS,
                    self.last_status_at + ACTIVITY_HOLD_SECONDS - time.monotonic())
        due = time.monotonic() + delay
        if self.activity_timer is not None:
            if self.activity_due <= due:
                return
            self.activity_timer.cancel()
        self.activity_due = due
        self.activity_timer = threading.Timer(delay, self.flush_activity)
        self.activity_timer.daemon = True
        self.activity_timer.start()

    def flush_activity(self) -> None:
        with self.lock:
            self.activity_timer = None
            self.activity_due = float("inf")
            if not self.native or not self.legacy_status:
                return
            try:
                self.set_native_status()
                if self.activities:
                    latest = next(reversed(self.activities))
                    remaining = self.activity_started[latest] + ACTIVITY_WAIT_SECONDS - time.monotonic()
                    if remaining > 0:
                        remaining = max(remaining, self.last_status_at + ACTIVITY_HOLD_SECONDS - time.monotonic())
                        self.activity_due = time.monotonic() + remaining
                        self.activity_timer = threading.Timer(remaining, self.flush_activity)
                        self.activity_timer.daemon = True
                        self.activity_timer.start()
            except Exception as exc:  # noqa: BLE001 - answer delivery remains primary
                self.logger.warning("Could not update native Slack activity: %s", exc)
                self.legacy_status = False

    def start(self) -> None:
        with self.lock:
            try:
                self.set_session_status("processing")
                self.session_api = True
            except Exception as exc:  # noqa: BLE001 - compatibility bridge may still work
                self.logger.warning("Slack Agent Sessions status is unavailable: %s", exc)
            try:
                self.set_native_status()
                self.legacy_status = True
            except Exception as exc:  # noqa: BLE001 - Agent Sessions may still work
                self.logger.warning("Custom Slack loading copy is unavailable: %s", exc)
            self.native = self.session_api or self.legacy_status
            if self.native:
                self.schedule_refresh()
            else:
                response = self.client.chat_postMessage(
                    channel=self.channel,
                    thread_ts=self.thread_ts,
                    text="Open Tag is working on this.",
                )
                self.message_ts = response["ts"]

    def clear(self, *, complete_session: bool = True) -> None:
        with self.lock:
            if self.activity_timer is not None:
                self.activity_timer.cancel()
                self.activity_timer = None
            if self.refresh_timer is not None:
                self.refresh_timer.cancel()
                self.refresh_timer = None
            if not self.native and not self.session_api and not self.legacy_status:
                return
            # Flip state before the API call so an already-running timer cannot
            # restore a status after final-answer streaming has begun.
            self.native = False
            if not complete_session:
                return
            if self.session_api:
                try:
                    self.set_session_status("active")
                except Exception as exc:  # noqa: BLE001 - final replies must still be delivered
                    self.logger.warning("Could not complete Slack agent session status: %s", exc)
            elif self.legacy_status:
                try:
                    self.client.assistant_threads_setStatus(
                        channel_id=self.channel,
                        thread_ts=self.thread_ts,
                        status="",
                    )
                except Exception as exc:  # noqa: BLE001 - final replies must still be delivered
                    self.logger.warning("Could not clear native Slack loading status: %s", exc)
            self.session_api = False
            self.legacy_status = False


class SlackAnswerStream:
    """Batch answer deltas into Slack's streaming-message APIs."""

    def __init__(
        self,
        client: Any,
        channel: str,
        thread_ts: str,
        recipient_user_id: str,
        recipient_team_id: str,
        logger: Any,
        on_start: Callable[[], None] | None = None,
    ) -> None:
        self.client = client
        self.channel = channel
        self.thread_ts = thread_ts
        self.recipient_user_id = recipient_user_id
        self.recipient_team_id = recipient_team_id
        self.logger = logger
        self.on_start = on_start
        self.pending = ""
        self.received = ""
        self.ts: str | None = None
        self.failed = False
        self.flush_timer: threading.Timer | None = None
        self.lock = threading.RLock()

    def append(self, text: str) -> None:
        if not text:
            return
        with self.lock:
            self.received += text
            self.pending += text
            if self.failed:
                return
            threshold = STREAM_START_CHARS if self.ts is None else STREAM_APPEND_CHARS
            if len(self.pending) >= threshold:
                self._flush_locked()
            elif self.flush_timer is None:
                self.flush_timer = threading.Timer(STREAM_FLUSH_SECONDS, self.flush)
                self.flush_timer.daemon = True
                self.flush_timer.start()

    def flush(self) -> None:
        with self.lock:
            self.flush_timer = None
            self._flush_locked()

    def _flush_locked(self) -> None:
        if self.failed or not self.pending:
            return
        if self.flush_timer is not None:
            self.flush_timer.cancel()
            self.flush_timer = None
        try:
            if self.ts is None:
                response = self.client.chat_startStream(
                    channel=self.channel,
                    thread_ts=self.thread_ts,
                    recipient_user_id=self.recipient_user_id,
                    recipient_team_id=self.recipient_team_id,
                    markdown_text=self.pending,
                )
                self.ts = response["ts"]
                if self.on_start is not None:
                    self.on_start()
            else:
                self.client.chat_appendStream(
                    channel=self.channel,
                    ts=self.ts,
                    markdown_text=self.pending,
                )
            self.pending = ""
        except Exception as exc:  # noqa: BLE001 - preserve the complete final answer
            self.failed = True
            self.logger.warning("Slack answer streaming failed; using a normal reply: %s", exc)

    def finish(self, final_text: str, blocks: list[dict[str, Any]] | None = None) -> bool:
        """Finalize a real delta stream; return False when a normal reply is safer."""
        with self.lock:
            if self.flush_timer is not None:
                self.flush_timer.cancel()
                self.flush_timer = None
            if not self.received:
                return False
            if self.failed:
                return self._replace_and_stop_locked(final_text, blocks)

            # The backend final event is authoritative. Most runs exactly match the
            # deltas; append a missing suffix when a backend omitted its last delta.
            replace_text: str | None = None
            if final_text.startswith(self.received):
                self.pending += final_text[len(self.received):]
                self.received = final_text
            elif final_text != self.received:
                self.logger.warning("Backend final text differed from streamed deltas")
                self.pending = ""
                self.received = final_text
                replace_text = final_text

            try:
                self._flush_locked()
                if self.failed:
                    return self._replace_and_stop_locked(final_text, blocks)
                if self.ts is None:
                    return False
                stop_args: dict[str, Any] = {"channel": self.channel, "ts": self.ts}
                if replace_text is not None:
                    stop_args["markdown_text"] = replace_text
                if blocks:
                    stop_args["blocks"] = blocks
                self.client.chat_stopStream(**stop_args)
                return True
            except Exception as exc:  # noqa: BLE001 - caller posts the full fallback reply
                self.failed = True
                self.logger.warning("Could not finalize Slack answer stream: %s", exc)
                return False

    def _replace_and_stop_locked(
        self,
        final_text: str,
        blocks: list[dict[str, Any]] | None,
    ) -> bool:
        """Recover a partial stream without posting a duplicate normal reply."""
        if self.ts is None:
            return False
        stop_args: dict[str, Any] = {
            "channel": self.channel,
            "ts": self.ts,
            "markdown_text": final_text,
        }
        if blocks:
            stop_args["blocks"] = blocks
        try:
            self.client.chat_stopStream(**stop_args)
            return True
        except Exception as exc:  # noqa: BLE001 - caller still owns the full fallback
            self.logger.warning("Could not recover partial Slack answer stream: %s", exc)
            return False

    def abort(self) -> None:
        with self.lock:
            if self.flush_timer is not None:
                self.flush_timer.cancel()
                self.flush_timer = None
            if self.ts is None:
                return
            try:
                self.client.chat_stopStream(channel=self.channel, ts=self.ts)
            except Exception as exc:  # noqa: BLE001 - interruption cleanup is best effort
                self.logger.warning("Could not stop interrupted Slack answer stream: %s", exc)


@dataclass
class ActiveBackendRun:
    process: subprocess.Popen[str]
    control_file: Path
    run_id: str
    started_at_epoch: float = field(default_factory=time.time)
    cancel_requested: bool = False
    kill_timer: threading.Timer | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def cancel(self) -> None:
        with self.lock:
            if self.cancel_requested or self.process.poll() is not None:
                return
            self.cancel_requested = True
            try:
                self.control_file.write_text(self.run_id, encoding="utf-8")
            except OSError:
                should_force_stop = True
            else:
                should_force_stop = False
                self.kill_timer = threading.Timer(CANCEL_GRACE_SECONDS, self.force_stop)
                self.kill_timer.daemon = True
                self.kill_timer.start()
        if should_force_stop:
            self.force_stop()

    def force_stop(self) -> None:
        if self.process.poll() is not None:
            return
        if os.name != "nt":
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
                return
            except (OSError, ProcessLookupError):
                pass
        self.process.kill()

    def finish(self) -> None:
        with self.lock:
            if self.kill_timer is not None:
                self.kill_timer.cancel()
                self.kill_timer = None


@dataclass(frozen=True)
class RunKey:
    team: str
    channel: str
    thread_ts: str


ACTIVE_RUNS: dict[RunKey, ActiveBackendRun] = {}
ACTIVE_RUNS_LOCK = threading.Lock()


def register_active_run(key: RunKey, run: ActiveBackendRun) -> None:
    with ACTIVE_RUNS_LOCK:
        ACTIVE_RUNS[key] = run


def unregister_active_run(key: RunKey, run: ActiveBackendRun) -> None:
    with ACTIVE_RUNS_LOCK:
        if ACTIVE_RUNS.get(key) is run:
            ACTIVE_RUNS.pop(key, None)


def cancel_active_run(key: RunKey, event_ts: str | None = None) -> bool:
    with ACTIVE_RUNS_LOCK:
        run = ACTIVE_RUNS.get(key)
    if run is None:
        return False
    try:
        event_epoch = float(event_ts) if event_ts else None
    except ValueError:
        return False
    if event_epoch is not None and event_epoch < run.started_at_epoch:
        return False
    run.cancel()
    return True


def run_backend(
    backend: str,
    channel: str,
    caller_id: str,
    question: str,
    thread_text: str,
    attachment_dir: Path,
    timeout: int,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as f:
        f.write(thread_text)
        thread_file = Path(f.name)

    cmd = [
        sys.executable,
        str(skill_dir() / "scripts" / "opentag_agent.py"),
        "--backend",
        backend,
        "--channel-id",
        channel,
        "--question",
        question,
        "--thread-file",
        str(thread_file),
        "--attachments-dir",
        str(attachment_dir),
        "--skill-dir",
        str(skill_dir()),
        "--workdir",
        str(default_workdir()),
        "--timeout",
        str(timeout),
    ]
    if backend == "codex" and model:
        cmd.extend(["--model", model])
    if backend == "codex" and reasoning_effort:
        cmd.extend(["--reasoning-effort", reasoning_effort])
    try:
        child_env = backend_environment(
            os.environ,
            transport="slack",
            conversation_id=channel,
            caller_id=caller_id,
        )
        result = subprocess.run(
            cmd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout + 10,
            env=child_env,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            return f"Open Tag backend failed with exit code {result.returncode}:\n```text\n{output[-3000:]}\n```"
        return output or "Open Tag finished without output."
    finally:
        try:
            thread_file.unlink()
        except OSError:
            pass


def run_backend_events(
    backend: str,
    team: str,
    channel: str,
    thread_ts: str,
    caller_id: str,
    question: str,
    thread_text: str,
    attachment_dir: Path,
    timeout: int,
    on_delta: Callable[[str], None],
    on_activity: Callable[[str, str, str, str], None] | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    on_answer_start: Callable[[], None] | None = None,
) -> tuple[str, bool]:
    """Consume normalized lifecycle events and forward only final-answer text."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as f:
        f.write(thread_text)
        thread_file = Path(f.name)

    cmd = [
        sys.executable,
        str(skill_dir() / "scripts" / "opentag_agent.py"),
        "--backend",
        backend,
        "--channel-id",
        channel,
        "--question",
        question,
        "--thread-file",
        str(thread_file),
        "--attachments-dir",
        str(attachment_dir),
        "--skill-dir",
        str(skill_dir()),
        "--workdir",
        str(default_workdir()),
        "--timeout",
        str(timeout),
        "--event-stream",
    ]
    if backend == "codex" and model:
        cmd.extend(["--model", model])
    if backend == "codex" and reasoning_effort:
        cmd.extend(["--reasoning-effort", reasoning_effort])
    run_id = uuid.uuid4().hex
    with tempfile.NamedTemporaryFile("w", suffix=".control", delete=False) as control:
        control_file = Path(control.name)
    cmd.extend(["--control-file", str(control_file), "--run-id", run_id])
    child_env = backend_environment(
        os.environ,
        transport="slack",
        conversation_id=channel,
        caller_id=caller_id,
    )
    try:
        process = subprocess.Popen(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            env=child_env,
            start_new_session=os.name != "nt",
        )
    except BaseException:
        thread_file.unlink(missing_ok=True)
        control_file.unlink(missing_ok=True)
        raise
    run_key = RunKey(team, channel, thread_ts)
    active_run = ActiveBackendRun(process, control_file, run_id)
    register_active_run(run_key, active_run)
    timed_out = threading.Event()

    def stop_process() -> None:
        timed_out.set()
        active_run.force_stop()

    timer = threading.Timer(timeout + 10, stop_process)
    timer.start()
    final_messages: list[str] = []
    delta_text: list[str] = []
    error_text = ""
    terminal_status = ""
    diagnostics: list[str] = []
    try:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                diagnostics.append(line)
                continue
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            text = event.get("text")
            if event_type == "message_start" and event.get("phase") == "final_answer" and on_answer_start:
                on_answer_start()
            if event_type in {"delta", "message_delta"} and isinstance(text, str):
                if event_type == "message_delta" and event.get("phase") != "final_answer":
                    continue
                delta_text.append(text)
                on_delta(text)
            elif event_type in {"final", "message_complete"} and isinstance(text, str):
                if event_type == "message_complete" and event.get("phase") != "final_answer":
                    continue
                final_messages.append(text)
            elif event_type == "error" and isinstance(text, str):
                error_text = text
            elif event_type in {"activity_start", "activity_complete"} and on_activity:
                activity_id = event.get("activity_id")
                label = event.get("label")
                if isinstance(activity_id, str) and isinstance(label, str):
                    wait_label = event.get("wait_label")
                    on_activity(event_type, activity_id, label,
                                wait_label if isinstance(wait_label, str) else "This operation is still running…")
            elif event_type == "turn_complete":
                status = event.get("status")
                if isinstance(status, str):
                    terminal_status = status
        return_code = process.wait()
    finally:
        timer.cancel()
        active_run.finish()
        unregister_active_run(run_key, active_run)
        thread_file.unlink(missing_ok=True)
        control_file.unlink(missing_ok=True)

    if active_run.cancel_requested and terminal_status == "interrupted":
        return "Stopped. Actions completed before the stop were not rolled back.", False
    if active_run.cancel_requested:
        return "Stop requested, but the backend did not confirm interruption before cleanup.", False
    if timed_out.is_set():
        return f"Tag backend timed out after {timeout}s", False
    if return_code != 0:
        details = error_text or "\n".join(diagnostics)[-3000:].strip()
        return details or f"Open Tag backend failed with exit code {return_code}.", False
    answer = "".join(final_messages) or "".join(delta_text)
    return (answer or "Open Tag finished without output."), True


def post_final_reply(
    client: Any,
    channel: str,
    thread_ts: str,
    answer: str,
    placeholder_ts: str | None = None,
    footer_blocks: list[dict[str, Any]] | None = None,
) -> None:
    chunks = split_reply(to_mrkdwn(answer), max_chars=2_900 if footer_blocks else MAX_REPLY_CHARS)
    if placeholder_ts is not None:
        first_blocks = None
        if footer_blocks and len(chunks) == 1:
            first_blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": chunks[0]}},
                *footer_blocks,
            ]
        client.chat_update(
            channel=channel,
            ts=placeholder_ts,
            text=chunks[0],
            mrkdwn=True,
            blocks=first_blocks,
        )
    else:
        first_blocks = None
        if footer_blocks and len(chunks) == 1:
            first_blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": chunks[0]}},
                *footer_blocks,
            ]
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=chunks[0],
            mrkdwn=True,
            blocks=first_blocks,
        )
    for index, chunk in enumerate(chunks[1:], start=1):
        blocks = None
        if footer_blocks and index == len(chunks) - 1:
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": chunk}},
                *footer_blocks,
            ]
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=chunk,
            mrkdwn=True,
            blocks=blocks,
        )


def suggested_bot_name(backend: str) -> str:
    if os.getenv("OPENTAG_BOT_NAME"):
        return os.environ["OPENTAG_BOT_NAME"]
    return "OpenMax"


def slack_channel_allowed(channel: str) -> bool:
    """Restrict Slack execution to the explicit configured channel allowlist."""
    configured = os.getenv("SLACK_CHANNEL_IDS", "").strip()
    if not configured:
        configured = os.getenv("SLACK_CHANNEL_ID", "").strip()
    allowed_channels = set(slack_channels.parse_channel_ids(configured))
    return bool(allowed_channels) and channel in allowed_channels


def parse_slack_user_ids(value: str) -> frozenset[str]:
    """Parse a comma-separated Slack user allowlist into exact member IDs."""
    return frozenset(user_id.strip() for user_id in value.split(",") if user_id.strip())


def configured_slack_user_ids() -> frozenset[str]:
    """Load the required caller allowlist, failing closed when it is empty."""
    user_ids = parse_slack_user_ids(require_env("SLACK_ALLOWED_USER_IDS"))
    if not user_ids:
        raise RuntimeError("SLACK_ALLOWED_USER_IDS must contain at least one Slack member ID")
    return user_ids


def slack_user_allowed(user_id: str, allowed_user_ids: frozenset[str]) -> bool:
    return bool(user_id) and user_id in allowed_user_ids


def app_home_view(
    channel_ids: str = "",
    *,
    authorized: bool = True,
    notice: str | None = None,
) -> dict[str, Any]:
    """Build the visual Slack App Home channel configuration surface."""
    if not authorized:
        return {
            "type": "home",
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": "Tag"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": UNAUTHORIZED_USER_MESSAGE}},
            ],
        }
    if os.getenv("SLACK_CHANNEL_POLICY") == "invited":
        return {"type": "home", "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "Tag"}},
            {"type": "section", "text": {"type": "mrkdwn", "text":
                "Invite Tag to a channel to enable replies and automatic channel memory. "
                "Invitations are checked about every minute while Tag runs. "
                "Only authorized users can request tasks; replies use this channel’s memory only."}},
        ]}
    selector: dict[str, Any] = {
        "type": "multi_conversations_select",
        "action_id": HOME_CHANNEL_ACTION_ID,
        "placeholder": {"type": "plain_text", "text": "Select a Slack channel"},
        "filter": {
            "include": ["public", "private"],
            "exclude_bot_users": True,
            "exclude_external_shared_channels": True,
        },
    }
    selected = list(slack_channels.parse_channel_ids(channel_ids))
    if selected:
        selector["initial_conversations"] = selected
    current = (
        "Current channels: " + ", ".join(f"<#{channel_id}>" for channel_id in selected)
        if selected else "No channels are configured."
    )
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "Set up Tag"}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Choose where Tag should respond*\n{current}",
            },
            "accessory": selector,
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Tag saves the channel ID, so this keeps working if the channel is renamed. The bot must already be invited.",
                }
            ],
        },
    ]
    if notice:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": notice}})
    return {"type": "home", "blocks": blocks}


def save_home_channels(channel_ids: list[str]) -> None:
    """Persist authorized App Home choices and apply them to this live bridge."""
    try:
        import tag_config as settings
        from tag_paths import tag_home
    except ImportError:
        from scripts import tag_config as settings
        from scripts.tag_paths import tag_home
    value = ",".join(channel_ids)
    settings.update_config(settings.config_path(tag_home()), {"SLACK_CHANNEL_IDS": value})
    os.environ["SLACK_CHANNEL_IDS"] = value


def print_live_summary(backend: str, allowed_user_ids: frozenset[str]) -> None:
    bot = suggested_bot_name(backend)
    scopes = [s.strip() for s in os.getenv("MFS_ALLOWED_SCOPES", "").split(",") if s.strip()]
    channel_ids = os.getenv("SLACK_CHANNEL_IDS", "").strip() or os.getenv("SLACK_CHANNEL_ID", "").strip()
    channel = ", ".join(
        slack_channels.channel_label(require_env("SLACK_BOT_TOKEN"), channel_id)
        for channel_id in slack_channels.parse_channel_ids(channel_ids)
    ) or "(none configured)"
    invoke = {
        "claude": "claude -p --dangerously-skip-permissions",
        "codex": (
            "codex app-server --stdio"
            if os.getenv("OPENTAG_CODEX_TRANSPORT", "app-server").strip().lower() == "app-server"
            else "codex exec --approve-for-me"
        ),
    }[backend]

    print("=" * 64)
    print(f"Open Tag is live as @{bot}")
    print(f"  Brain   : {backend}  ->  {invoke}")
    print(f"  Memory  : {len(scopes)} permitted MFS scope(s):")
    for scope in scopes or ["(none — set MFS_ALLOWED_SCOPES)"]:
        print(f"            - {scope}")
    print(f"  Slack   : listening for @mentions in channel {channel}")
    print(f"  Access  : {len(allowed_user_ids)} authorized Slack user(s)")
    print("")
    print("  Only explicitly authorized Slack users can drive the backend,")
    print("  which runs with your shell and inherited environment.")
    print("  Slack text flows into the prompt, so treat every mention as")
    print("  untrusted input: use an isolated channel on a non-production host.")
    print(f"  Invite the bot only where it should respond: /invite @{bot}")
    print("=" * 64)


def create_app(backend: str, timeout: int, allowed_user_ids: frozenset[str]) -> App:
    app = App(token=require_env("SLACK_BOT_TOKEN"))

    @app.event("agent_session_stopped")
    def handle_agent_session_stopped(
        event: dict[str, Any],
        body: dict[str, Any],
        logger: Any,
    ) -> None:
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts", "")
        user_id = event.get("user", "")
        team = body.get("team_id") or event.get("team_id") or ""
        if not (
            isinstance(channel, str)
            and isinstance(thread_ts, str)
            and slack_channel_allowed(channel)
            and slack_user_allowed(user_id, allowed_user_ids)
        ):
            logger.warning("Ignoring unauthorized or malformed agent stop event")
            return
        if not cancel_active_run(
            RunKey(str(team), channel, thread_ts),
            event.get("event_ts") if isinstance(event.get("event_ts"), str) else None,
        ):
            logger.info("No active Tag run matched the Slack stop event")
    settings_store = ThreadAgentSettingsStore()
    models = discover_codex_models() if backend == "codex" else []

    @app.event("app_home_opened")
    def show_app_home(event: dict[str, Any], client: Any, logger: Any) -> None:
        if event.get("tab") != "home":
            return
        user_id = event.get("user", "")
        try:
            client.views_publish(
                user_id=user_id,
                view=app_home_view(
                    os.getenv("SLACK_CHANNEL_IDS", "").strip()
                    or os.getenv("SLACK_CHANNEL_ID", "").strip(),
                    authorized=slack_user_allowed(user_id, allowed_user_ids),
                ),
            )
        except Exception as exc:  # noqa: BLE001 - keep the Socket Mode listener alive
            logger.warning("Could not publish Tag App Home: %s", exc)

    @app.action(HOME_CHANNEL_ACTION_ID)
    def select_home_channel(ack: Any, body: dict[str, Any], client: Any, logger: Any) -> None:
        ack()
        if os.getenv("SLACK_CHANNEL_POLICY") == "invited":
            return  # Ignore stale manual-picker actions in membership-driven mode.
        user_id = body.get("user", {}).get("id", "")
        if not slack_user_allowed(user_id, allowed_user_ids):
            return
        try:
            channel_ids = body["actions"][0]["selected_conversations"]
            if not isinstance(channel_ids, list) or not channel_ids:
                raise ValueError("select at least one channel")
            denied = []
            for channel_id in channel_ids:
                response = client.conversations_info(channel=channel_id)
                channel = response.get("channel") or {}
                if not channel.get("is_member"):
                    denied.append(channel_id)
            if denied:
                notice = ":warning: Invite the Tag bot to every selected channel first."
                selected = os.getenv("SLACK_CHANNEL_IDS", "").strip()
            else:
                save_home_channels(channel_ids)
                notice = ":white_check_mark: Tag will respond only in the selected channels."
                selected = ",".join(channel_ids)
            client.views_publish(
                user_id=user_id,
                view=app_home_view(selected, notice=notice),
            )
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            logger.warning("Could not save the Tag App Home channel: %s", exc)
            client.views_publish(
                user_id=user_id,
                view=app_home_view(
                    os.getenv("SLACK_CHANNEL_IDS", "").strip()
                    or os.getenv("SLACK_CHANNEL_ID", "").strip(),
                    notice=":warning: Tag could not save that channel. Check the local Tag logs.",
                ),
            )

    @app.action(SETTINGS_ACTION_ID)
    def open_agent_settings(ack: Any, body: dict[str, Any], client: Any, logger: Any) -> None:
        ack()
        try:
            raw_value = body["actions"][0]["value"]
            metadata = json.loads(raw_value)
            channel = metadata["channel"]
            if not slack_channel_allowed(channel):
                return
            user_id = body.get("user", {}).get("id", "")
            if not slack_user_allowed(user_id, allowed_user_ids):
                client.chat_postEphemeral(
                    channel=channel,
                    user=user_id,
                    thread_ts=metadata["thread_ts"],
                    text=UNAUTHORIZED_USER_MESSAGE,
                )
                return
            settings = normalize_settings(
                settings_store.get(
                    metadata.get("team", ""),
                    channel,
                    metadata["thread_ts"],
                ),
                models,
            )
            client.views_open(
                trigger_id=body["trigger_id"],
                view=settings_modal(metadata=metadata, settings=settings, models=models),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Could not open Open Tag settings modal: %s", exc)

    @app.action(SETTINGS_MODEL_ACTION_ID)
    def refresh_reasoning_options(
        ack: Any,
        body: dict[str, Any],
        client: Any,
        logger: Any,
    ) -> None:
        ack()
        if not slack_user_allowed(body.get("user", {}).get("id", ""), allowed_user_ids):
            return
        try:
            view = body["view"]
            metadata = json.loads(view["private_metadata"])
            selected = body["actions"][0]["selected_option"]["value"]
            model = None if selected == DEFAULT_CONFIG_VALUE else selected
            effort = selected_setting(view, "reasoning_effort", SETTINGS_EFFORT_ACTION_ID)
            if effort not in efforts_for_model(model, models):
                effort = None
            client.views_update(
                view_id=view["id"],
                hash=view.get("hash"),
                view=settings_modal(
                    metadata=metadata,
                    settings=AgentSettings(model=model, reasoning_effort=effort),
                    models=models,
                ),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Could not refresh Open Tag reasoning options: %s", exc)

    @app.action(SETTINGS_EFFORT_ACTION_ID)
    def acknowledge_reasoning_choice(ack: Any) -> None:
        ack()

    @app.view(SETTINGS_VIEW_ID)
    def save_agent_settings(ack: Any, body: dict[str, Any], client: Any, logger: Any) -> None:
        if not slack_user_allowed(body.get("user", {}).get("id", ""), allowed_user_ids):
            ack(response_action="errors", errors={"model": UNAUTHORIZED_USER_MESSAGE})
            return
        try:
            view = body["view"]
            metadata = json.loads(view["private_metadata"])
            model = selected_setting(view, "model", SETTINGS_MODEL_ACTION_ID)
            effort = selected_setting(view, "reasoning_effort", SETTINGS_EFFORT_ACTION_ID)
            errors: dict[str, str] = {}
            if model and model not in {item.model_id for item in models}:
                errors["model"] = "Choose an available model."
            if effort and effort not in efforts_for_model(model, models):
                errors["reasoning_effort"] = "Choose a thinking level supported by this model."
            if errors:
                ack(response_action="errors", errors=errors)
                return
            if not slack_channel_allowed(metadata["channel"]):
                ack(response_action="errors", errors={"model": "This channel is not allowed."})
                return
            settings = AgentSettings(model=model, reasoning_effort=effort)
            settings_store.set(
                metadata.get("team", ""),
                metadata["channel"],
                metadata["thread_ts"],
                settings,
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Could not save Open Tag thread settings: %s", exc)
            ack(response_action="errors", errors={"model": "Could not save these settings."})
            return

        ack()
        try:
            client.chat_postEphemeral(
                channel=metadata["channel"],
                user=body["user"]["id"],
                thread_ts=metadata["thread_ts"],
                text=f"Updated this thread to {settings_context(settings, models)}. It applies to the next request.",
            )
        except Exception as exc:  # noqa: BLE001 - the setting is already durably saved
            logger.warning("Could not post Open Tag settings confirmation: %s", exc)

    @app.event("app_mention")
    def handle_mention(
        event: dict[str, Any],
        body: dict[str, Any],
        client: Any,
        logger: Any,
    ) -> None:
        channel = event["channel"]
        if not slack_channel_allowed(channel):
            logger.warning("Ignoring Open Tag mention from unapproved Slack channel %s", channel)
            return
        thread_ts = event.get("thread_ts") or event["ts"]
        user_id = event.get("user", "")
        if not slack_user_allowed(user_id, allowed_user_ids):
            logger.warning(
                "Rejecting Open Tag mention from unauthorized Slack user %s in channel %s",
                user_id or "(missing)",
                channel,
            )
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=UNAUTHORIZED_USER_MESSAGE,
            )
            return
        team = body.get("team_id") or event.get("team") or ""
        question = strip_mention(event.get("text", ""))
        agent_settings = normalize_settings(
            settings_store.get(team, channel, thread_ts),
            models,
        )

        indicator = WorkingIndicator(client, channel, thread_ts, logger)
        indicator.start()
        answer_stream: SlackAnswerStream | None = None

        try:
            with tempfile.TemporaryDirectory(prefix="opentag-slack-") as raw_attachment_dir:
                attachment_dir = Path(raw_attachment_dir)
                thread_text = build_thread_text(client, channel, thread_ts, attachment_dir)
                stream_available = env_enabled("OPENTAG_SLACK_STREAMING", default=True) and indicator.native
                app_server_selected = (
                    backend == "codex"
                    and os.getenv("OPENTAG_CODEX_TRANSPORT", "app-server").strip().lower() == "app-server"
                )
                if stream_available:
                    answer_stream = SlackAnswerStream(
                        client,
                        channel,
                        thread_ts,
                        user_id,
                        team,
                        logger,
                        on_start=lambda: indicator.clear(complete_session=False),
                    )
                if stream_available or app_server_selected:
                    answer, succeeded = run_backend_events(
                        backend,
                        team,
                        channel,
                        thread_ts,
                        user_id,
                        question,
                        thread_text,
                        attachment_dir,
                        timeout,
                        answer_stream.append if answer_stream is not None else lambda _text: None,
                        indicator.activity,
                        model=agent_settings.model,
                        reasoning_effort=agent_settings.reasoning_effort,
                        on_answer_start=indicator.answer_started,
                    )
                else:
                    answer = run_backend(
                        backend,
                        channel,
                        user_id,
                        question,
                        thread_text,
                        attachment_dir,
                        timeout,
                        model=agent_settings.model,
                        reasoning_effort=agent_settings.reasoning_effort,
                    )
                    succeeded = True
            indicator.clear()
            footer_blocks = (
                settings_button_blocks(
                    team=team,
                    channel=channel,
                    thread_ts=thread_ts,
                    settings=agent_settings,
                    models=models,
                )
                if backend == "codex" and succeeded
                else None
            )
            if (
                answer_stream is not None
                and succeeded
                and answer_stream.finish(answer, footer_blocks)
            ):
                return
            if answer_stream is not None:
                answer_stream.abort()
            post_final_reply(
                client,
                channel,
                thread_ts,
                answer,
                indicator.message_ts,
                footer_blocks,
            )
        except Exception as exc:
            logger.exception("Open Tag failed")
            indicator.clear()
            if answer_stream is not None:
                answer_stream.abort()
            answer = f"Open Tag failed: `{type(exc).__name__}: {exc}`"
            post_final_reply(client, channel, thread_ts, answer, indicator.message_ts)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Open Tag Slack Socket Mode bridge.")
    parser.add_argument(
        "--backend",
        choices=["claude", "codex"],
        default=os.getenv("OPENTAG_BACKEND"),
    )
    parser.add_argument(
        "--timeout", type=int, default=int(os.getenv("OPENTAG_TIMEOUT_SECONDS", "420"))
    )
    parser.add_argument(
        "--ready-file",
        type=Path,
        help="Write a short-lived Socket Mode connection heartbeat to this path.",
    )
    parser.add_argument("--process-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.backend:
        parser.error("--backend or OPENTAG_BACKEND is required")

    allowed_user_ids = configured_slack_user_ids()
    app = create_app(args.backend, args.timeout, allowed_user_ids)
    print_live_summary(args.backend, allowed_user_ids)
    handler = SocketModeHandler(app, require_env("SLACK_APP_TOKEN"))
    invitation_memory = None
    if os.getenv("SLACK_CHANNEL_POLICY") == "invited":
        try:
            from .slack_invitation_memory import InvitationMemory
            from .tag_paths import tag_home
        except ImportError:
            from slack_invitation_memory import InvitationMemory
            from tag_paths import tag_home
        invitation_memory = InvitationMemory(tag_home())
        invitation_memory.start()
    try:
        handler.connect()
        while True:
            if args.ready_file:
                if handler.client.is_connected():
                    instance_id = args.process_id or require_env("OPENTAG_PROCESS_ID")
                    temporary = args.ready_file.with_name(
                        f"{args.ready_file.name}.tmp.{os.getpid()}"
                    )
                    temporary.write_text(
                        f"{instance_id} {os.getpid()} {int(time.time())}\n",
                        encoding="utf-8",
                    )
                    temporary.replace(args.ready_file)
                else:
                    args.ready_file.unlink(missing_ok=True)
            time.sleep(1)
    finally:
        if invitation_memory:
            invitation_memory.stop()
        if args.ready_file:
            args.ready_file.unlink(missing_ok=True)
        handler.close()


if __name__ == "__main__":
    main()
