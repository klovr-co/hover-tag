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
    from .tag_paths import tag_temp_dir
    from . import slack_channels
except ImportError:  # Direct script execution does not create a package context.
    from opentag_process_env import backend_environment
    from slack_mrkdwn import to_mrkdwn
    from tag_paths import tag_temp_dir
    import slack_channels


MENTION_RE = re.compile(r"<@[^>]+>")
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_ATTACHMENT_TEXT_CHARS = 12_000
MAX_OUTPUT_FILE_BYTES = 15 * 1024 * 1024
MAX_OUTPUT_ARTIFACTS = 10
OUTPUT_ARTIFACT_MANIFEST_PREFIX = ".opentag-output-artifacts-"
MAX_GENERATED_IMAGES = 10
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
SETTINGS_FAST_ACTION_ID = "opentag_settings_fast_mode"
SETTINGS_RESET_ACTION_ID = "opentag_settings_reset"
RETRY_ACTION_ID = "opentag_retry_request"
OPEN_LOCAL_ARTIFACT_ACTION_ID = "opentag_open_local_artifact"
OPEN_LOCAL_ARTIFACT_ACTION_PATTERN = re.compile(
    rf"^{re.escape(OPEN_LOCAL_ARTIFACT_ACTION_ID)}_[0-9]+$"
)
OPEN_LOCAL_ARTIFACT_DIRECTORY_ACTION_ID = "opentag_open_local_artifact_directory"
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
GENERATED_IMAGE_MIME_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


@dataclass(frozen=True)
class CodexModelOption:
    model_id: str
    label: str
    reasoning_efforts: tuple[str, ...]
    supports_fast_mode: bool = False
    default_reasoning_effort: str | None = None
    is_default: bool = False
    default_fast_mode: bool = False


@dataclass(frozen=True)
class AgentSettings:
    model: str | None = None
    reasoning_effort: str | None = None
    fast_mode: bool | None = None


def codex_models_cache_path() -> Path:
    codex_home = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    return codex_home / "models_cache.json"


def tag_codex_config_path() -> Path:
    """Return the project-local Codex configuration owned by Tag."""
    workdir = Path(os.getenv("OPENTAG_WORKDIR", str(Path.cwd()))).expanduser()
    return workdir / ".codex" / "config.toml"


def read_codex_config(path: Path) -> dict[str, Any]:
    try:
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 runtime
            import tomli as tomllib
        with path.open("rb") as handle:
            config = tomllib.load(handle)
        return config if isinstance(config, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


def configured_codex_defaults() -> tuple[str | None, str | None, bool]:
    """Layer Tag's model defaults over the user's global Codex defaults."""
    global_config = read_codex_config(codex_models_cache_path().with_name("config.toml"))
    tag_config = read_codex_config(tag_codex_config_path())

    def layered_value(key: str, validator: Callable[[Any], bool]) -> Any:
        local = tag_config.get(key)
        if validator(local):
            return local
        global_value = global_config.get(key)
        return global_value if validator(global_value) else None

    model = layered_value("model", lambda value: isinstance(value, str) and bool(value))
    effort = layered_value(
        "model_reasoning_effort",
        lambda value: isinstance(value, str) and value in SUPPORTED_REASONING_EFFORTS,
    )
    service_tier = layered_value(
        "service_tier",
        lambda value: isinstance(value, str)
        and value in {"default", "fast", "priority"},
    )
    return (
        model,
        effort,
        service_tier in {"fast", "priority"},
    )


def discover_codex_models() -> list[CodexModelOption]:
    """Read Codex's local model metadata, optionally constrained by an operator allowlist."""
    configured = [
        value.strip()
        for value in os.getenv("OPENTAG_CODEX_MODELS", "").split(",")
        if value.strip()
    ]
    configured_model, configured_effort, configured_fast_mode = configured_codex_defaults()
    discovered: dict[str, CodexModelOption] = {}
    try:
        payload = json.loads(codex_models_cache_path().read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            payload = {}
        raw_models = [
            raw_model
            for raw_model in payload.get("models", [])
            if isinstance(raw_model, dict)
            and raw_model.get("visibility") != "hide"
            and isinstance(raw_model.get("slug"), str)
            and raw_model.get("slug")
        ]
        fallback_default = min(
            raw_models,
            key=lambda item: item.get("priority")
            if isinstance(item.get("priority"), (int, float))
            else float("inf"),
            default={},
        ).get("slug")
        default_model = configured_model or fallback_default
        for raw_model in raw_models:
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
            speed_tiers = raw_model.get("additional_speed_tiers", [])
            if not isinstance(speed_tiers, list):
                speed_tiers = []
            catalog_effort = raw_model.get("default_reasoning_level")
            is_default = model_id == default_model
            default_effort = configured_effort if is_default and configured_effort else catalog_effort
            discovered[model_id] = CodexModelOption(
                model_id=model_id,
                label=str(raw_model.get("display_name") or model_id),
                reasoning_efforts=efforts or DEFAULT_REASONING_EFFORTS,
                supports_fast_mode="fast" in speed_tiers,
                default_reasoning_effort=(
                    default_effort
                    if isinstance(default_effort, str)
                    and default_effort in SUPPORTED_REASONING_EFFORTS
                    else None
                ),
                is_default=is_default,
                default_fast_mode=configured_fast_mode,
            )
    except (OSError, ValueError, TypeError):
        pass

    if configured:
        return [
            discovered.get(
                model_id,
                CodexModelOption(
                    model_id,
                    model_id,
                    DEFAULT_REASONING_EFFORTS,
                    default_reasoning_effort=(
                        configured_effort if model_id == configured_model else None
                    ),
                    is_default=model_id == configured_model,
                    default_fast_mode=configured_fast_mode,
                ),
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


def fast_mode_available(model: str | None, models: list[CodexModelOption]) -> bool:
    """Let Codex validate its configured default; validate explicit models locally."""
    if model is None:
        return True
    selected = next((item for item in models if item.model_id == model), None)
    return bool(selected and selected.supports_fast_mode)


def default_agent_settings(models: list[CodexModelOption]) -> AgentSettings:
    if not models:
        return AgentSettings()
    selected = next((item for item in models if item.is_default), models[0])
    efforts = efforts_for_model(selected.model_id, models)
    effort = selected.default_reasoning_effort
    if effort not in efforts:
        effort = efforts[0] if efforts else None
    return AgentSettings(
        model=selected.model_id,
        reasoning_effort=effort,
        fast_mode=selected.default_fast_mode and selected.supports_fast_mode,
    )


def default_effort_for_model(
    model: str | None,
    models: list[CodexModelOption],
) -> str | None:
    selected = next((item for item in models if item.model_id == model), None)
    efforts = efforts_for_model(model, models)
    if selected and selected.default_reasoning_effort in efforts:
        return selected.default_reasoning_effort
    return efforts[0] if efforts else None


def normalize_settings(
    settings: AgentSettings,
    models: list[CodexModelOption],
) -> AgentSettings:
    known_models = {item.model_id for item in models}
    defaults = default_agent_settings(models)
    model_is_valid = settings.model is None or settings.model in known_models
    model = settings.model if settings.model in known_models else defaults.model
    efforts = efforts_for_model(model, models)
    effort = (
        settings.reasoning_effort
        if settings.reasoning_effort in efforts
        else default_effort_for_model(model, models)
    )
    requested_fast_mode = defaults.fast_mode if settings.fast_mode is None else settings.fast_mode
    fast_mode = bool(
        requested_fast_mode
        and model_is_valid
        and fast_mode_available(model, models)
    )
    return AgentSettings(model=model, reasoning_effort=effort, fast_mode=fast_mode)


class UserAgentSettingsStore:
    """Persist model choices by Slack user so they follow future requests."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(
            os.getenv(
                "OPENTAG_SLACK_SETTINGS_FILE",
                str(skill_dir() / ".runtime" / "slack-user-settings.json"),
            )
        ).expanduser()
        self.lock = threading.Lock()

    @staticmethod
    def key(team: str, user_id: str) -> str:
        return f"{team}:{user_id}"

    def _read(self) -> dict[str, dict[str, str | bool | None]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def get(self, team: str, user_id: str) -> AgentSettings:
        with self.lock:
            raw = self._read().get(self.key(team, user_id), {})
        if not isinstance(raw, dict):
            raw = {}
        model = raw.get("model")
        effort = raw.get("reasoning_effort")
        fast_mode = raw.get("fast_mode")
        return AgentSettings(
            model=model if isinstance(model, str) else None,
            reasoning_effort=effort if isinstance(effort, str) else None,
            fast_mode=fast_mode if isinstance(fast_mode, bool) else None,
        )

    def set(self, team: str, user_id: str, settings: AgentSettings) -> None:
        with self.lock:
            payload = self._read()
            payload[self.key(team, user_id)] = {
                "model": settings.model,
                "reasoning_effort": settings.reasoning_effort,
                "fast_mode": settings.fast_mode,
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


def generated_images_dir(attachment_dir: Path) -> Path:
    """Return the backend/bridge handoff directory for generated images."""
    return attachment_dir / "results" / "images"


def collect_generated_images(results_dir: Path) -> tuple[list[Path], list[str]]:
    """Validate bounded image results before giving their paths to Slack."""
    images: list[Path] = []
    errors: list[str] = []
    for path in sorted(results_dir.iterdir(), key=lambda item: item.name.lower()):
        if path.is_symlink() or not path.is_file():
            errors.append(f"{path.name}: not a regular file")
            continue
        mime_type = mimetypes.guess_type(path.name)[0]
        if mime_type not in GENERATED_IMAGE_MIME_TYPES:
            errors.append(f"{path.name}: unsupported image type")
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        if size == 0:
            errors.append(f"{path.name}: empty file")
            continue
        if size > MAX_ATTACHMENT_BYTES:
            errors.append(f"{path.name}: exceeds the 15 MB safety limit")
            continue
        if len(images) >= MAX_GENERATED_IMAGES:
            errors.append(f"{path.name}: exceeds the {MAX_GENERATED_IMAGES}-image result limit")
            continue
        images.append(path)
    return images, errors


def upload_generated_images(
    client: Any,
    channel: str,
    thread_ts: str,
    results_dir: Path,
) -> list[str]:
    """Upload validated backend image results into the originating Slack thread."""
    images, errors = collect_generated_images(results_dir)
    for path in images:
        try:
            client.files_upload_v2(
                channel=channel,
                thread_ts=thread_ts,
                file=str(path),
                filename=path.name,
                title=path.stem,
            )
        except Exception as exc:  # noqa: BLE001 - upload failures must not hide the text answer
            errors.append(f"{path.name}: {exc}")
    return errors


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
    return effort or "Default thinking"


def model_label(model: str | None, models: list[CodexModelOption]) -> str:
    if model is None:
        return "Default model"
    option = next((item for item in models if item.model_id == model), None)
    return option.label if option else model


def settings_context(
    settings: AgentSettings,
    models: list[CodexModelOption],
) -> str:
    fast_label = "Fast mode on" if settings.fast_mode else "Fast mode off"
    return (
        f"Codex · {model_label(settings.model, models)} · "
        f"{friendly_effort(settings.reasoning_effort)} · {fast_label}"
    )


def settings_button_blocks(
    *,
    team: str,
    channel: str,
    thread_ts: str,
    direct_message: bool = False,
) -> list[dict[str, Any]]:
    metadata = {"team": team, "channel": channel, "thread_ts": thread_ts}
    if direct_message:
        metadata["direct_message"] = True
    value = json.dumps(metadata, separators=(",", ":"))
    return [
        {
            "type": "actions",
            "block_id": f"opentag_settings_{thread_ts}",
            "elements": [
                {
                    "type": "button",
                    "action_id": SETTINGS_ACTION_ID,
                    "text": {"type": "plain_text", "text": "Configure"},
                    "value": value,
                }
            ],
        },
    ]


def output_artifact_button_blocks(
    paths: list[Path],
    workdir: Path,
    *,
    user_id: str,
    channel: str,
    thread_ts: str,
) -> list[dict[str, Any]]:
    """Build one compact row of host-local actions for validated output artifacts."""
    root = workdir.expanduser().resolve()
    elements: list[dict[str, Any]] = []
    valid_paths: list[Path] = []
    for index, raw_path in enumerate(paths):
        try:
            path = raw_path.expanduser().resolve(strict=True)
            relative_path = path.relative_to(root)
            if not path.is_file():
                continue
        except (OSError, ValueError):
            continue
        valid_paths.append(path)
        metadata = {
            "user": user_id,
            "channel": channel,
            "thread_ts": thread_ts,
            "path": str(relative_path),
        }
        value = json.dumps(metadata, separators=(",", ":"))
        if len(value.encode("utf-8")) > 2_000:
            continue
        label = f"↗ {path.name}"
        if len(label) > 75:
            label = label[:74] + "…"
        elements.append(
            {
                "type": "button",
                "action_id": f"{OPEN_LOCAL_ARTIFACT_ACTION_ID}_{index}",
                "text": {"type": "plain_text", "text": label},
                "accessibility_label": f"Open {path.name} on the Tag host"[:75],
                "value": value,
            }
        )
    if valid_paths:
        common_directory = Path(
            os.path.commonpath([str(path.parent) for path in valid_paths])
        )
        relative_directory = common_directory.relative_to(root)
        directory_metadata = {
            "user": user_id,
            "channel": channel,
            "thread_ts": thread_ts,
            "path": str(relative_directory),
        }
        directory_value = json.dumps(directory_metadata, separators=(",", ":"))
        if len(directory_value.encode("utf-8")) <= 2_000:
            elements.append(
                {
                    "type": "button",
                    "action_id": OPEN_LOCAL_ARTIFACT_DIRECTORY_ACTION_ID,
                    "text": {"type": "plain_text", "text": "📁 Open folder"},
                    "accessibility_label": "Open the output folder on the Tag host",
                    "value": directory_value,
                }
            )
    if not elements:
        return []
    return [
        {
            "type": "actions",
            "block_id": f"opentag_artifacts_{thread_ts}",
            "elements": elements,
        }
    ]


def resolve_local_artifact(raw_path: str, workdir: Path) -> Path:
    """Resolve an action path while keeping it inside the configured workspace."""
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise ValueError("artifact action path must be relative")
    root = workdir.expanduser().resolve(strict=True)
    path = (root / candidate).resolve(strict=True)
    path.relative_to(root)
    if not path.is_file():
        raise ValueError("artifact action path must be a regular file")
    return path


def resolve_local_artifact_directory(raw_path: str, workdir: Path) -> Path:
    """Resolve an output directory while keeping it inside the configured workspace."""
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise ValueError("artifact directory path must be relative")
    root = workdir.expanduser().resolve(strict=True)
    path = (root / candidate).resolve(strict=True)
    path.relative_to(root)
    if not path.is_dir():
        raise ValueError("artifact directory path must be a directory")
    return path


def open_local_artifact(path: Path) -> None:
    """Open a file with the desktop application on the machine running Tag."""
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
        return
    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )


def open_local_artifact_directory(path: Path) -> None:
    """Open an output directory on the machine running Tag."""
    open_local_artifact(path)


def settings_action_value(action: dict[str, Any]) -> str:
    """Read compact overflow actions and button actions on older messages."""
    selected_option = action.get("selected_option")
    if isinstance(selected_option, dict) and isinstance(selected_option.get("value"), str):
        return selected_option["value"]
    value = action.get("value")
    if isinstance(value, str):
        return value
    raise ValueError("Settings action has no value")


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
    metadata: dict[str, Any],
    settings: AgentSettings,
    models: list[CodexModelOption],
    revision: str = "",
) -> dict[str, Any]:
    block_suffix = f"_{revision}" if revision else ""
    normalized = normalize_settings(settings, models)
    model_options = [select_option(item.model_id, item.label) for item in models]
    if not model_options:
        model_options = [select_option(DEFAULT_CONFIG_VALUE, "No models available")]
    selected_model = normalized.model
    efforts = efforts_for_model(selected_model, models)
    effort_options = [select_option(effort, friendly_effort(effort)) for effort in efforts]
    if not effort_options:
        effort_options = [select_option(DEFAULT_CONFIG_VALUE, "No thinking levels available")]
    selected_effort = normalized.reasoning_effort
    fast_available = fast_mode_available(selected_model, models)
    fast_option = select_option(
        "on",
        "Enable Fast mode",
        "Faster responses with increased usage",
    )
    fast_element: dict[str, Any] = {
        "type": "checkboxes",
        "action_id": SETTINGS_FAST_ACTION_ID,
        "options": [fast_option],
    }
    if normalized.fast_mode and fast_available:
        fast_element["initial_options"] = [fast_option]
    fast_block: dict[str, Any]
    if fast_available:
        fast_block = {
            "type": "section",
            "block_id": f"fast_mode{block_suffix}",
            "text": {"type": "mrkdwn", "text": "*Speed*"},
            "accessory": fast_element,
        }
    else:
        fast_block = {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Fast mode is unavailable for this model.",
                }
            ],
        }
    return {
        "type": "modal",
        "callback_id": SETTINGS_VIEW_ID,
        "private_metadata": json.dumps(metadata, separators=(",", ":")),
        "title": {"type": "plain_text", "text": "Codex settings"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": f"model{block_suffix}",
                "dispatch_action": True,
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
                "block_id": f"reasoning_effort{block_suffix}",
                "label": {"type": "plain_text", "text": "Thinking"},
                "element": {
                    "type": "static_select",
                    "action_id": SETTINGS_EFFORT_ACTION_ID,
                    "options": effort_options,
                    "initial_option": next(
                        option
                        for option in effort_options
                        if option["value"] == (selected_effort or DEFAULT_CONFIG_VALUE)
                    ),
                },
            },
            fast_block,
            {
                "type": "section",
                "block_id": "reset_settings",
                "text": {
                    "type": "mrkdwn",
                    "text": "_Applies to your future Slack requests._",
                },
                "accessory": {
                    "type": "button",
                    "action_id": SETTINGS_RESET_ACTION_ID,
                    "text": {"type": "plain_text", "text": "Reset to default"},
                    "value": "reset",
                },
            },
        ],
    }


def setting_state(view: dict[str, Any], action_id: str) -> tuple[str, dict[str, Any]]:
    """Find a modal control even when a refresh gave its block a fresh ID."""
    values = view.get("state", {}).get("values", {})
    if not isinstance(values, dict):
        return "", {}
    for block_id, actions in values.items():
        if not isinstance(block_id, str) or not isinstance(actions, dict):
            continue
        state = actions.get(action_id)
        if isinstance(state, dict):
            return block_id, state
    return "", {}


def selected_setting(view: dict[str, Any], action_id: str) -> str | None:
    _, action = setting_state(view, action_id)
    selected = action.get("selected_option")
    value = selected.get("value") if isinstance(selected, dict) else None
    return None if value == DEFAULT_CONFIG_VALUE else value


def selected_fast_mode(view: dict[str, Any]) -> bool:
    _, action = setting_state(view, SETTINGS_FAST_ACTION_ID)
    selected = action.get("selected_options", [])
    return any(
        isinstance(option, dict) and option.get("value") == "on"
        for option in selected
    )


def clear_slack_session(client: Any, channel: str, thread_ts: str, logger: Any) -> bool:
    """Best-effort completion for an active or orphaned Slack agent session."""
    succeeded = False
    try:
        client.api_call(
            "agents.sessions.setStatus",
            json={
                "channel_id": channel,
                "thread_ts": thread_ts,
                "status": "active",
            },
        )
        succeeded = True
    except Exception as exc:  # noqa: BLE001 - legacy cleanup may still work
        logger.warning("Could not close Slack agent session: %s", exc)
    try:
        client.assistant_threads_setStatus(
            channel_id=channel,
            thread_ts=thread_ts,
            status="",
        )
        succeeded = True
    except Exception as exc:  # noqa: BLE001 - Agent Sessions may have worked
        logger.warning("Could not clear Slack loading status: %s", exc)
    return succeeded


class SlackSessionJournal:
    """Persist enough identity to clear Slack statuses after an unclean exit."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.sessions = self._load()

    @staticmethod
    def key(team: str, channel: str, thread_ts: str) -> str:
        return f"{team}:{channel}:{thread_ts}"

    def _load(self) -> dict[str, dict[str, str]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            key: value
            for key, value in payload.items()
            if isinstance(key, str)
            and isinstance(value, dict)
            and all(
                isinstance(value.get(field), str) and value[field]
                for field in ("team", "channel", "thread_ts")
            )
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.sessions:
            self.path.unlink(missing_ok=True)
            return
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.sessions, handle, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def add(self, team: str, channel: str, thread_ts: str) -> None:
        with self.lock:
            self.sessions[self.key(team, channel, thread_ts)] = {
                "team": team,
                "channel": channel,
                "thread_ts": thread_ts,
            }
            self._save()

    def remove(self, team: str, channel: str, thread_ts: str) -> None:
        with self.lock:
            self.sessions.pop(self.key(team, channel, thread_ts), None)
            self._save()

    def reconcile(self, client: Any, logger: Any) -> int:
        """Clear recorded sessions with Slack, retaining entries that still fail."""
        with self.lock:
            pending = list(self.sessions.items())
        cleared = 0
        for key, session in pending:
            if clear_slack_session(
                client, session["channel"], session["thread_ts"], logger
            ):
                with self.lock:
                    self.sessions.pop(key, None)
                    try:
                        self._save()
                    except OSError as exc:
                        logger.warning("Could not update the Slack session journal: %s", exc)
                cleared += 1
        return cleared


def slack_session_journal_path() -> Path:
    return Path(
        os.getenv(
            "OPENTAG_SLACK_SESSIONS_FILE",
            str(skill_dir() / ".runtime" / "slack-active-sessions.json"),
        )
    ).expanduser()


class WorkingIndicator:
    """Prefer Slack's native agent status, with the old message as a fallback."""

    def __init__(
        self,
        client: Any,
        channel: str,
        thread_ts: str,
        logger: Any,
        *,
        journal: SlackSessionJournal | None = None,
        team: str = "",
    ) -> None:
        self.client = client
        self.channel = channel
        self.thread_ts = thread_ts
        self.logger = logger
        self.journal = journal
        self.team = team
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
        self.public_status: str | None = None
        self.last_status: str | None = None
        self.last_status_at = float("-inf")
        self.lock = threading.RLock()

    def journal_add(self) -> None:
        if self.journal is None:
            return
        try:
            self.journal.add(self.team, self.channel, self.thread_ts)
        except OSError as exc:
            self.logger.warning("Could not record the active Slack session: %s", exc)

    def journal_remove(self) -> None:
        if self.journal is None:
            return
        try:
            self.journal.remove(self.team, self.channel, self.thread_ts)
        except OSError as exc:
            self.logger.warning("Could not update the Slack session journal: %s", exc)

    def set_native_status(self, *, force: bool = False) -> None:
        if self.public_status:
            status = self.public_status
        elif self.activities:
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
                self.public_status = None
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
            self.public_status = None
            self.preparing_answer = True
            self.schedule_activity()

    def status(self, text: str) -> None:
        """Display a backend-provided public lifecycle status such as a retry."""
        with self.lock:
            self.public_status = text
            if self.native and self.legacy_status:
                try:
                    self.set_native_status(force=True)
                except Exception as exc:  # noqa: BLE001 - processing remains active
                    self.logger.warning("Could not update native Slack status: %s", exc)
                    self.legacy_status = False

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
            self.journal_add()
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
                self.journal_remove()
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
                    self.journal_remove()
                except Exception as exc:  # noqa: BLE001 - final replies must still be delivered
                    self.logger.warning("Could not complete Slack agent session status: %s", exc)
            elif self.legacy_status:
                try:
                    self.client.assistant_threads_setStatus(
                        channel_id=self.channel,
                        thread_ts=self.thread_ts,
                        status="",
                    )
                    self.journal_remove()
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
    fast_mode: bool = False,
    output_manifest: Path | None = None,
    max_timeout: int | None = None,
) -> tuple[str, bool]:
    if max_timeout is None:
        max_timeout = int(os.getenv("OPENTAG_MAX_TIMEOUT_SECONDS", "3600"))
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", encoding="utf-8", delete=False, dir=tag_temp_dir()
    ) as f:
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
        str(max_timeout),
        "--max-timeout",
        str(max_timeout),
    ]
    if output_manifest is not None:
        cmd.extend(["--output-manifest", str(output_manifest)])
    if backend == "codex" and model:
        cmd.extend(["--model", model])
    if backend == "codex" and reasoning_effort:
        cmd.extend(["--reasoning-effort", reasoning_effort])
    if backend == "codex":
        cmd.extend(["--fast-mode", "on" if fast_mode else "off"])
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
            timeout=max_timeout + 10,
            env=child_env,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            if result.returncode == 124:
                return f"Tag backend exceeded its maximum runtime of {max_timeout}s", False
            detail = output[-3000:] or f"Open Tag backend failed with exit code {result.returncode}."
            return detail, False
        return output or "Open Tag finished without output.", True
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
    on_status: Callable[[str], None] | None = None,
    fast_mode: bool = False,
    output_manifest: Path | None = None,
    max_timeout: int | None = None,
) -> tuple[str, bool]:
    """Consume normalized lifecycle events and forward only final-answer text."""
    if max_timeout is None:
        max_timeout = int(os.getenv("OPENTAG_MAX_TIMEOUT_SECONDS", "3600"))
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", encoding="utf-8", delete=False, dir=tag_temp_dir()
    ) as f:
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
        "--max-timeout",
        str(max_timeout),
        "--event-stream",
    ]
    if output_manifest is not None:
        cmd.extend(["--output-manifest", str(output_manifest)])
    if backend == "codex" and model:
        cmd.extend(["--model", model])
    if backend == "codex" and reasoning_effort:
        cmd.extend(["--reasoning-effort", reasoning_effort])
    if backend == "codex":
        cmd.extend(["--fast-mode", "on" if fast_mode else "off"])
    run_id = uuid.uuid4().hex
    with tempfile.NamedTemporaryFile(
        "w", suffix=".control", delete=False, dir=tag_temp_dir()
    ) as control:
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

    timer = threading.Timer(max_timeout + 10, stop_process)
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
            elif event_type == "status" and isinstance(text, str) and on_status:
                on_status(text)
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
        return f"Tag backend exceeded its maximum runtime of {max_timeout}s", False
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

    def blocks_with_accessory(text: str) -> list[dict[str, Any]]:
        section: dict[str, Any] = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        }
        if (
            footer_blocks
            and len(footer_blocks) == 1
            and isinstance(footer_blocks[0].get("accessory"), dict)
        ):
            section["accessory"] = footer_blocks[0]["accessory"]
            return [section]
        return [section, *(footer_blocks or [])]

    if placeholder_ts is not None:
        first_blocks = None
        if footer_blocks and len(chunks) == 1:
            first_blocks = blocks_with_accessory(chunks[0])
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
            first_blocks = blocks_with_accessory(chunks[0])
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
            blocks = blocks_with_accessory(chunk)
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=chunk,
            mrkdwn=True,
            blocks=blocks,
        )


def load_output_artifact_entries(
    manifest: Path,
    workdir: Path,
) -> tuple[list[tuple[Path, bool]], list[str]]:
    """Load validated deliverables and their explicit Slack attachment intent."""
    if not manifest.exists():
        return [], []
    try:
        raw_artifacts = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return [], ["The requested output list was unreadable, so no files were attached."]
    if not isinstance(raw_artifacts, list) or not all(
        isinstance(item, str)
        or (
            isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and isinstance(item.get("attach"), bool)
        )
        for item in raw_artifacts
    ):
        return [], ["The requested output list was invalid, so no files were attached."]
    if len(raw_artifacts) > MAX_OUTPUT_ARTIFACTS:
        return [], [
            f"The request listed more than {MAX_OUTPUT_ARTIFACTS} outputs, so no files were attached."
        ]

    root = workdir.expanduser().resolve()
    artifacts: list[tuple[Path, bool]] = []
    errors: list[str] = []
    seen: set[Path] = set()
    for item in raw_artifacts:
        # String entries were produced by the first artifact implementation and
        # retain its upload behavior for in-flight/backward-compatible manifests.
        raw_path = item if isinstance(item, str) else item["path"]
        attach = True if isinstance(item, str) else item["attach"]
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            path = candidate.resolve(strict=True)
            path.relative_to(root)
            if not path.is_file():
                raise ValueError("not a regular file")
            size = path.stat().st_size
        except (OSError, ValueError):
            errors.append(
                f"Could not attach `{candidate.name or 'requested output'}` because it is missing, "
                "inaccessible, or outside the workspace."
            )
            continue
        if size > MAX_OUTPUT_FILE_BYTES:
            errors.append(
                f"Could not attach `{path.name}` because it exceeds Tag’s "
                f"{MAX_OUTPUT_FILE_BYTES // (1024 * 1024)} MB output limit."
            )
            continue
        if path not in seen:
            artifacts.append((path, attach))
            seen.add(path)
    return artifacts, errors


def load_output_artifacts(manifest: Path, workdir: Path) -> tuple[list[Path], list[str]]:
    """Load all outputs that should receive host-local Open actions."""
    entries, errors = load_output_artifact_entries(manifest, workdir)
    return [path for path, _attach in entries], errors


def deliver_output_artifacts(
    client: Any,
    channel: str,
    thread_ts: str,
    manifest: Path,
    workdir: Path,
    logger: Any,
) -> list[str]:
    """Attach validated outputs to the authorized originating Slack thread."""
    entries, messages = load_output_artifact_entries(manifest, workdir)
    for path, attach in entries:
        if not attach:
            continue
        try:
            response = client.files_upload_v2(
                channel=channel,
                thread_ts=thread_ts,
                file=str(path),
                filename=path.name,
                title=path.name,
            )
            uploaded_files: list[dict[str, Any]] = []
            if hasattr(response, "get"):
                plural = response.get("files")
                if isinstance(plural, list):
                    uploaded_files.extend(item for item in plural if isinstance(item, dict))
                singular = response.get("file")
                if isinstance(singular, dict) and singular not in uploaded_files:
                    uploaded_files.append(singular)

            permalink = next(
                (
                    item["permalink"]
                    for item in uploaded_files
                    if isinstance(item.get("permalink"), str) and item["permalink"]
                ),
                None,
            )
            if permalink is None:
                file_id = next(
                    (
                        item["id"]
                        for item in uploaded_files
                        if isinstance(item.get("id"), str) and item["id"]
                    ),
                    None,
                )
                if file_id is not None:
                    try:
                        info = client.files_info(file=file_id)
                        info_file = info.get("file") if hasattr(info, "get") else None
                        if isinstance(info_file, dict) and isinstance(
                            info_file.get("permalink"), str
                        ):
                            permalink = info_file["permalink"] or None
                    except Exception:  # noqa: BLE001 - upload still succeeded
                        logger.warning(
                            "Slack file permalink lookup failed for %s",
                            file_id,
                            exc_info=True,
                        )
            if permalink:
                messages.append(f"Download [{path.name}]({permalink}).")
            else:
                messages.append(f"Attached `{path.name}` to this thread.")
        except Exception:  # noqa: BLE001 - report saved and delivered outcomes separately
            reference = uuid.uuid4().hex[:8].upper()
            logger.exception("Slack output upload failed [%s] for %s", reference, path)
            messages.append(
                f"`{path.name}` was saved locally, but Slack delivery failed "
                f"(reference {reference}). Ask Tag to attach it again; an operator may need "
                "to add `files:write`, reinstall the Slack app, or check Slack’s file limits."
            )
    return messages


def retry_button_blocks(
    *,
    team: str,
    channel: str,
    thread_ts: str,
    request_ts: str,
    direct_message: bool = False,
) -> list[dict[str, Any]]:
    metadata = {
        "team": team,
        "channel": channel,
        "thread_ts": thread_ts,
        "request_ts": request_ts,
    }
    if direct_message:
        metadata["direct_message"] = True
    return [{
        "type": "actions",
        "elements": [{
            "type": "button",
            "action_id": RETRY_ACTION_ID,
            "text": {"type": "plain_text", "text": "Retry"},
            "value": json.dumps(metadata, separators=(",", ":")),
        }],
    }]


def user_facing_failure(
    detail: str,
    timeout: int,
    error_reference: str,
    max_timeout: int | None = None,
) -> str:
    """Turn private backend diagnostics into stable, actionable Slack copy."""
    if detail.startswith("Stopped.") or detail.startswith("Stop requested"):
        return detail
    lowered = detail.lower()
    if "no backend activity" in lowered:
        return f"Tag stopped after {timeout} seconds without backend activity. Please retry."
    if "maximum runtime" in lowered and max_timeout is not None:
        return f"Tag reached its maximum runtime of {max_timeout} seconds. Please retry."
    if "timed out" in lowered:
        return f"Tag timed out after {timeout} seconds. Please retry."
    if any(
        marker in lowered
        for marker in (
            "capacity",
            "rate limit",
            "rate_limit",
            "too many requests",
            "http 429",
            "backend is busy",
        )
    ):
        return "Tag couldn’t complete this request because the backend is busy. Please retry."
    return (
        "Tag couldn’t complete this request. Please retry. If it keeps happening, "
        f"ask the Tag owner to check the local logs with error reference `{error_reference}`."
    )


def suggested_bot_name(backend: str) -> str:
    if os.getenv("OPENTAG_BOT_NAME"):
        return os.environ["OPENTAG_BOT_NAME"]
    return "Tag"


def slack_channel_allowed(channel: str) -> bool:
    """Restrict Slack execution to the explicit configured channel allowlist."""
    configured = os.getenv("SLACK_CHANNEL_IDS", "").strip()
    if not configured:
        configured = os.getenv("SLACK_CHANNEL_ID", "").strip()
    allowed_channels = set(slack_channels.parse_channel_ids(configured))
    return bool(allowed_channels) and channel in allowed_channels


def direct_messages_enabled() -> bool:
    """Enable authorized DM invocation unless the operator explicitly disables it."""
    return env_enabled("OPENTAG_SLACK_DM_ENABLED", default=True)


def slack_conversation_allowed(channel: str, *, direct_message: bool = False) -> bool:
    """Apply the channel allowlist to channels and the DM switch to direct messages."""
    return direct_messages_enabled() if direct_message else slack_channel_allowed(channel)


def is_direct_message_channel(channel: str) -> bool:
    """Recognize Slack's stable DM conversation ID prefix for events without channel_type."""
    return channel.startswith("D")


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
    dm_status = "enabled" if direct_messages_enabled() else "disabled"
    print(f"  Slack   : listening for @mentions in channel {channel}")
    print(f"  DMs     : {dm_status}")
    print(f"  Access  : {len(allowed_user_ids)} authorized Slack user(s)")
    print("")
    print("  Only explicitly authorized Slack users can drive the backend,")
    print("  which runs with your shell and inherited environment.")
    print("  Slack text flows into the prompt, so treat every invocation as")
    print("  untrusted input: use an isolated channel on a non-production host.")
    print(f"  Invite the bot only where it should respond: /invite @{bot}")
    print("=" * 64)


def create_app(
    backend: str,
    timeout: int,
    allowed_user_ids: frozenset[str],
    *,
    max_timeout: int | None = None,
    session_journal: SlackSessionJournal | None = None,
) -> App:
    if max_timeout is None:
        max_timeout = int(os.getenv("OPENTAG_MAX_TIMEOUT_SECONDS", "3600"))
    app = App(token=require_env("SLACK_BOT_TOKEN"))

    @app.event("agent_session_stopped")
    def handle_agent_session_stopped(
        event: dict[str, Any],
        body: dict[str, Any],
        client: Any,
        logger: Any,
    ) -> None:
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts", "")
        user_id = event.get("user", "")
        team = body.get("team_id") or event.get("team_id") or ""
        if not (
            isinstance(channel, str)
            and isinstance(thread_ts, str)
            and slack_conversation_allowed(
                channel,
                direct_message=is_direct_message_channel(channel),
            )
            and slack_user_allowed(user_id, allowed_user_ids)
        ):
            logger.warning("Ignoring unauthorized or malformed agent stop event")
            return
        if not cancel_active_run(
            RunKey(str(team), channel, thread_ts),
            event.get("event_ts") if isinstance(event.get("event_ts"), str) else None,
        ):
            logger.info("No active Tag run matched the Slack stop event")
            if clear_slack_session(client, channel, thread_ts, logger):
                if session_journal is not None:
                    try:
                        session_journal.remove(str(team), channel, thread_ts)
                    except OSError as exc:
                        logger.warning("Could not update the Slack session journal: %s", exc)
            elif session_journal is not None:
                try:
                    session_journal.add(str(team), channel, thread_ts)
                except OSError as exc:
                    logger.warning("Could not record the orphaned Slack session: %s", exc)
    settings_store = UserAgentSettingsStore()
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
            raw_value = settings_action_value(body["actions"][0])
            metadata = json.loads(raw_value)
            channel = metadata["channel"]
            if not slack_conversation_allowed(
                channel,
                direct_message=metadata.get("direct_message") is True,
            ):
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
                    user_id,
                ),
                models,
            )
            client.views_open(
                trigger_id=body["trigger_id"],
                view=settings_modal(metadata=metadata, settings=settings, models=models),
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Could not open Open Tag settings modal: %s", exc)

    # Keep the exact listener for buttons posted by older Tag versions while
    # accepting the indexed IDs required for multiple actions in one block.
    @app.action(OPEN_LOCAL_ARTIFACT_ACTION_ID)
    @app.action(OPEN_LOCAL_ARTIFACT_ACTION_PATTERN)
    def open_output_artifact(ack: Any, body: dict[str, Any], client: Any, logger: Any) -> None:
        ack()
        user_id = body.get("user", {}).get("id", "")
        channel = body.get("channel", {}).get("id", "")
        metadata: dict[str, Any] = {}
        try:
            metadata = json.loads(body["actions"][0]["value"])
            expected_user = metadata["user"]
            expected_channel = metadata["channel"]
            thread_ts = metadata["thread_ts"]
            raw_path = metadata["path"]
            if not all(
                isinstance(value, str) and value
                for value in (
                    user_id,
                    channel,
                    expected_user,
                    expected_channel,
                    thread_ts,
                    raw_path,
                )
            ):
                raise ValueError("invalid local artifact metadata")
            if (
                user_id != expected_user
                or channel != expected_channel
                or not slack_conversation_allowed(
                    channel,
                    direct_message=is_direct_message_channel(channel),
                )
                or not slack_user_allowed(user_id, allowed_user_ids)
            ):
                raise PermissionError("local artifact action is not authorized")
            path = resolve_local_artifact(raw_path, default_workdir())
            open_local_artifact(path)
            client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                thread_ts=thread_ts,
                text=f"Opened `{path.name}` on the machine running Tag.",
            )
        except Exception as exc:  # noqa: BLE001 - keep the action listener alive
            logger.warning("Could not open local output artifact: %s", exc)
            expected_channel = metadata.get("channel")
            thread_ts = metadata.get("thread_ts")
            if (
                isinstance(channel, str)
                and channel
                and channel == expected_channel
                and user_id == metadata.get("user")
                and slack_conversation_allowed(
                    channel,
                    direct_message=is_direct_message_channel(channel),
                )
                and slack_user_allowed(user_id, allowed_user_ids)
            ):
                client.chat_postEphemeral(
                    channel=channel,
                    user=user_id,
                    thread_ts=thread_ts if isinstance(thread_ts, str) else None,
                    text=(
                        "Tag couldn’t open that local file. It may have been moved or deleted, "
                        "or the Tag host may not have a desktop application for it."
                    ),
                )

    @app.action(OPEN_LOCAL_ARTIFACT_DIRECTORY_ACTION_ID)
    def open_output_artifact_directory(
        ack: Any,
        body: dict[str, Any],
        client: Any,
        logger: Any,
    ) -> None:
        ack()
        user_id = body.get("user", {}).get("id", "")
        channel = body.get("channel", {}).get("id", "")
        metadata: dict[str, Any] = {}
        try:
            metadata = json.loads(body["actions"][0]["value"])
            expected_user = metadata["user"]
            expected_channel = metadata["channel"]
            thread_ts = metadata["thread_ts"]
            raw_path = metadata["path"]
            if not all(
                isinstance(value, str) and value
                for value in (
                    user_id,
                    channel,
                    expected_user,
                    expected_channel,
                    thread_ts,
                    raw_path,
                )
            ):
                raise ValueError("invalid local artifact directory metadata")
            if (
                user_id != expected_user
                or channel != expected_channel
                or not slack_conversation_allowed(
                    channel,
                    direct_message=is_direct_message_channel(channel),
                )
                or not slack_user_allowed(user_id, allowed_user_ids)
            ):
                raise PermissionError("local artifact directory action is not authorized")
            path = resolve_local_artifact_directory(raw_path, default_workdir())
            open_local_artifact_directory(path)
            client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                thread_ts=thread_ts,
                text="Opened the output folder on the machine running Tag.",
            )
        except Exception as exc:  # noqa: BLE001 - keep the action listener alive
            logger.warning("Could not open local output directory: %s", exc)
            expected_channel = metadata.get("channel")
            thread_ts = metadata.get("thread_ts")
            if (
                isinstance(channel, str)
                and channel
                and channel == expected_channel
                and user_id == metadata.get("user")
                and slack_conversation_allowed(
                    channel,
                    direct_message=is_direct_message_channel(channel),
                )
                and slack_user_allowed(user_id, allowed_user_ids)
            ):
                client.chat_postEphemeral(
                    channel=channel,
                    user=user_id,
                    thread_ts=thread_ts if isinstance(thread_ts, str) else None,
                    text=(
                        "Tag couldn’t open that output folder. It may have been moved or "
                        "deleted, or the Tag host may not have a desktop file browser."
                    ),
                )

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
            effort = selected_setting(view, SETTINGS_EFFORT_ACTION_ID)
            if effort not in efforts_for_model(model, models):
                effort = default_effort_for_model(model, models)
            fast_mode = selected_fast_mode(view) and fast_mode_available(model, models)
            client.views_update(
                view_id=view["id"],
                hash=view.get("hash"),
                view=settings_modal(
                    metadata=metadata,
                    settings=AgentSettings(
                        model=model,
                        reasoning_effort=effort,
                        fast_mode=fast_mode,
                    ),
                    models=models,
                    revision=f"model_{time.time_ns()}",
                ),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Could not refresh Open Tag reasoning options: %s", exc)

    @app.action(SETTINGS_EFFORT_ACTION_ID)
    def acknowledge_reasoning_choice(ack: Any) -> None:
        ack()

    @app.action(SETTINGS_FAST_ACTION_ID)
    def acknowledge_fast_mode_choice(ack: Any) -> None:
        ack()

    @app.action(SETTINGS_RESET_ACTION_ID)
    def reset_agent_settings_form(
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
            client.views_update(
                view_id=view["id"],
                hash=view.get("hash"),
                view=settings_modal(
                    metadata=metadata,
                    settings=default_agent_settings(models),
                    models=models,
                    revision=f"reset_{time.time_ns()}",
                ),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Could not reset Open Tag settings form: %s", exc)

    @app.view(SETTINGS_VIEW_ID)
    def save_agent_settings(ack: Any, body: dict[str, Any], client: Any, logger: Any) -> None:
        if not slack_user_allowed(body.get("user", {}).get("id", ""), allowed_user_ids):
            ack(response_action="errors", errors={"model": UNAUTHORIZED_USER_MESSAGE})
            return
        try:
            view = body["view"]
            metadata = json.loads(view["private_metadata"])
            model_block_id, _ = setting_state(view, SETTINGS_MODEL_ACTION_ID)
            effort_block_id, _ = setting_state(view, SETTINGS_EFFORT_ACTION_ID)
            fast_block_id, _ = setting_state(view, SETTINGS_FAST_ACTION_ID)
            model = selected_setting(view, SETTINGS_MODEL_ACTION_ID)
            effort = selected_setting(view, SETTINGS_EFFORT_ACTION_ID)
            fast_mode = selected_fast_mode(view)
            errors: dict[str, str] = {}
            if model and model not in {item.model_id for item in models}:
                errors[model_block_id or "model"] = "Choose an available model."
            if effort and effort not in efforts_for_model(model, models):
                errors[effort_block_id or "reasoning_effort"] = (
                    "Choose a thinking level supported by this model."
                )
            if fast_mode and not fast_mode_available(model, models):
                errors[fast_block_id or "fast_mode"] = (
                    "Fast mode is not supported by this model."
                )
            if errors:
                ack(response_action="errors", errors=errors)
                return
            if not slack_conversation_allowed(
                metadata["channel"],
                direct_message=metadata.get("direct_message") is True,
            ):
                ack(
                    response_action="errors",
                    errors={model_block_id or "model": "This channel is not allowed."},
                )
                return
            settings = AgentSettings(
                model=model,
                reasoning_effort=effort,
                fast_mode=fast_mode,
            )
            settings_store.set(
                metadata.get("team", ""),
                body["user"]["id"],
                settings,
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Could not save Open Tag user settings: %s", exc)
            ack(response_action="errors", errors={"model": "Could not save these settings."})
            return

        ack()
        try:
            client.chat_postEphemeral(
                channel=metadata["channel"],
                user=body["user"]["id"],
                thread_ts=metadata["thread_ts"],
                text=f"Applied to your future Slack requests: {settings_context(settings, models)}.",
            )
        except Exception as exc:  # noqa: BLE001 - the setting is already durably saved
            logger.warning("Could not post Open Tag settings confirmation: %s", exc)

    def handle_invocation(
        event: dict[str, Any],
        body: dict[str, Any],
        client: Any,
        logger: Any,
        *,
        direct_message: bool,
    ) -> None:
        channel = event["channel"]
        thread_ts = event.get("thread_ts") or event["ts"]
        user_id = event.get("user", "")
        if not slack_user_allowed(user_id, allowed_user_ids):
            logger.warning(
                "Rejecting Open Tag invocation from unauthorized Slack user %s in channel %s",
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
            settings_store.get(team, user_id),
            models,
        )

        indicator = WorkingIndicator(
            client,
            channel,
            thread_ts,
            logger,
            journal=session_journal,
            team=team,
        )
        indicator.start()
        answer_stream: SlackAnswerStream | None = None
        output_manifest = (
            default_workdir() / f"{OUTPUT_ARTIFACT_MANIFEST_PREFIX}{uuid.uuid4().hex}.json"
        )

        try:
            with tempfile.TemporaryDirectory(
                prefix="slack-invocation-",
                dir=tag_temp_dir(),
            ) as raw_attachment_dir:
                attachment_dir = Path(raw_attachment_dir)
                image_results_dir = generated_images_dir(attachment_dir)
                image_results_dir.mkdir(parents=True)
                (attachment_dir / "results" / "artifacts").mkdir()
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
                        on_status=indicator.status,
                        fast_mode=agent_settings.fast_mode,
                        output_manifest=output_manifest,
                        max_timeout=max_timeout,
                    )
                else:
                    answer, succeeded = run_backend(
                        backend,
                        channel,
                        user_id,
                        question,
                        thread_text,
                        attachment_dir,
                        timeout,
                        model=agent_settings.model,
                        reasoning_effort=agent_settings.reasoning_effort,
                        fast_mode=agent_settings.fast_mode,
                        output_manifest=output_manifest,
                        max_timeout=max_timeout,
                    )
                artifact_button_blocks: list[dict[str, Any]] = []
                if succeeded:
                    artifact_paths, _artifact_errors = load_output_artifacts(
                        output_manifest,
                        default_workdir(),
                    )
                    delivery_messages = deliver_output_artifacts(
                        client,
                        channel,
                        thread_ts,
                        output_manifest,
                        default_workdir(),
                        logger,
                    )
                    if delivery_messages:
                        answer = f"{answer.rstrip()}\n\n" + "\n".join(delivery_messages)
                    artifact_button_blocks = output_artifact_button_blocks(
                        artifact_paths,
                        default_workdir(),
                        user_id=user_id,
                        channel=channel,
                        thread_ts=thread_ts,
                    )
                indicator.clear()
                if succeeded:
                    footer_blocks = artifact_button_blocks
                    if backend == "codex":
                        footer_blocks += settings_button_blocks(
                            team=team,
                            channel=channel,
                            thread_ts=thread_ts,
                            direct_message=direct_message,
                        )
                    footer_blocks = footer_blocks or None
                elif answer.startswith("Stopped.") or answer.startswith("Stop requested"):
                    footer_blocks = None
                else:
                    error_reference = uuid.uuid4().hex[:8].upper()
                    logger.error("Tag backend failure [%s]: %s", error_reference, answer)
                    answer = user_facing_failure(answer, timeout, error_reference, max_timeout)
                    footer_blocks = retry_button_blocks(
                        team=team,
                        channel=channel,
                        thread_ts=thread_ts,
                        request_ts=event["ts"],
                        direct_message=direct_message,
                    )
                streamed = (
                    answer_stream is not None
                    and succeeded
                    and answer_stream.finish(answer, footer_blocks)
                )
                if not streamed:
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
                if succeeded:
                    upload_errors = upload_generated_images(
                        client,
                        channel,
                        thread_ts,
                        image_results_dir,
                    )
                    if upload_errors:
                        logger.warning(
                            "Generated-image upload failed: %s",
                            "; ".join(upload_errors),
                        )
                        client.chat_postMessage(
                            channel=channel,
                            thread_ts=thread_ts,
                            text=(
                                "I couldn't attach every generated image: "
                                + "; ".join(upload_errors)
                            ),
                        )
        except Exception as exc:
            error_reference = uuid.uuid4().hex[:8].upper()
            logger.exception("Open Tag failed [%s]", error_reference)
            indicator.clear()
            if answer_stream is not None:
                answer_stream.abort()
            answer = user_facing_failure(
                f"{type(exc).__name__}: {exc}", timeout, error_reference, max_timeout
            )
            post_final_reply(
                client,
                channel,
                thread_ts,
                answer,
                indicator.message_ts,
                retry_button_blocks(
                    team=team,
                    channel=channel,
                    thread_ts=thread_ts,
                    request_ts=event["ts"],
                    direct_message=direct_message,
                ),
            )
        finally:
            output_manifest.unlink(missing_ok=True)

    @app.action(RETRY_ACTION_ID)
    def retry_request(ack: Any, body: dict[str, Any], client: Any, logger: Any) -> None:
        ack()
        user_id = body.get("user", {}).get("id", "")
        if not slack_user_allowed(user_id, allowed_user_ids):
            return
        metadata: dict[str, Any] = {}
        try:
            metadata = json.loads(body["actions"][0]["value"])
            channel = metadata["channel"]
            thread_ts = metadata["thread_ts"]
            request_ts = metadata["request_ts"]
            team = metadata.get("team", "")
            direct_message = metadata.get("direct_message") is True
            if not all(
                isinstance(value, str) and value
                for value in (channel, thread_ts, request_ts, team)
            ) or not slack_conversation_allowed(
                channel,
                direct_message=direct_message,
            ):
                raise ValueError("invalid retry metadata")
            response = client.conversations_replies(channel=channel, ts=thread_ts)
            original = next(
                (
                    message
                    for message in response.get("messages", [])
                    if message.get("ts") == request_ts
                    and isinstance(message.get("text"), str)
                ),
                None,
            )
            if original is None:
                raise ValueError("original Slack request is unavailable")
            handle_invocation(
                {
                    "channel": channel,
                    "thread_ts": thread_ts,
                    "ts": request_ts,
                    "user": user_id,
                    "text": original["text"],
                    "team": team,
                },
                {"team_id": team},
                client,
                logger,
                direct_message=direct_message,
            )
        except Exception as exc:  # noqa: BLE001 - keep the Slack action listener alive
            logger.warning("Could not retry Tag request: %s", exc)
            channel = metadata.get("channel")
            if isinstance(channel, str) and slack_conversation_allowed(
                channel,
                direct_message=metadata.get("direct_message") is True,
            ):
                client.chat_postEphemeral(
                    channel=channel,
                    user=user_id,
                    text="Tag couldn’t retry that request. Send it again instead.",
                )

    @app.event("app_mention")
    def handle_mention(
        event: dict[str, Any],
        body: dict[str, Any],
        client: Any,
        logger: Any,
    ) -> None:
        channel = event["channel"]
        if not slack_conversation_allowed(channel):
            logger.warning("Ignoring Open Tag mention from unapproved Slack channel %s", channel)
            return
        handle_invocation(event, body, client, logger, direct_message=False)

    @app.event("message")
    def handle_direct_message(
        event: dict[str, Any],
        body: dict[str, Any],
        client: Any,
        logger: Any,
    ) -> None:
        if event.get("channel_type") != "im" or not direct_messages_enabled():
            return
        # Ignore bot output and Slack's message lifecycle events to prevent reply loops.
        if event.get("bot_id") or event.get("subtype") not in {None, "file_share"}:
            return
        handle_invocation(event, body, client, logger, direct_message=True)

    return app


def install_shutdown_handlers(shutdown_requested: threading.Event) -> None:
    """Turn process signals into a graceful main-loop exit."""
    if threading.current_thread() is not threading.main_thread():
        return

    def request_shutdown(_signum: int, _frame: Any) -> None:
        shutdown_requested.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)


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
        "--max-timeout",
        type=int,
        default=int(os.getenv("OPENTAG_MAX_TIMEOUT_SECONDS", "3600")),
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
    session_journal = SlackSessionJournal(slack_session_journal_path())
    app = create_app(
        args.backend,
        args.timeout,
        allowed_user_ids,
        max_timeout=args.max_timeout,
        session_journal=session_journal,
    )
    print_live_summary(args.backend, allowed_user_ids)
    handler = SocketModeHandler(app, require_env("SLACK_APP_TOKEN"))
    session_journal.reconcile(app.client, app.logger)
    shutdown_requested = threading.Event()
    install_shutdown_handlers(shutdown_requested)
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
        while not shutdown_requested.is_set():
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
        session_journal.reconcile(app.client, app.logger)


if __name__ == "__main__":
    main()
