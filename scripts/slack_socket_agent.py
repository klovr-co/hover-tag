#!/usr/bin/env python3
# Modified by klovr.co in 2026 for Tag. See NOTICE and repository history.
from __future__ import annotations

import argparse
import hashlib
import html
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
from slack_bolt.response import BoltResponse
from slack_bolt.authorization import AuthorizeResult
from slack_sdk import WebClient
from slack_bolt.adapter.socket_mode import SocketModeHandler

try:
    from . import slack_identity
    from .agent_models import (
        BACKEND_NAMES,
        DEFAULT_REASONING_EFFORTS,
        SUPPORTED_REASONING_EFFORTS,
        CodexModelOption,
        ModelOption,
        backend_display_name,
        discover_tag_models,
        model_names_path,
        remember_model_names,
        rich_events_selected,
    )
    from .opentag_process_env import backend_environment
    from .tag_error_reporting import (
        COMMUNITY_INVITE_URL,
        ErrorReport,
        ErrorReportStore,
        ReportOrigin,
        build_troubleshooting_prompt,
        classify_failure,
        collect_health_checks,
        default_report_store,
        make_error_report,
        new_error_reference,
        sanitize_user_context,
        utc_timestamp,
    )
    from .slack_mrkdwn import to_mrkdwn
    from .slack_search_scope import ScopePlan, SearchIntent, plan_search_scopes
    from .tag_activity import ACTIVITY_DETAIL_ACTION_ID, PUBLIC_LABELS, ActivityStore, activity_detail_modal, activity_modal
    from .agent_summary import queue_reply_summary
    from .tag_activity_details import sanitize_activity_details
    from .tag_approval_choices import sanitize_review_details
    from .tag_activity_labels import activity_title_for_status, readable_activity_title
    from .tag_paths import instance_home, tag_temp_dir
    from . import slack_channels
    from . import tag_memory
    from . import tag_handoff
except ImportError:  # Direct script execution does not create a package context.
    import slack_identity
    from agent_models import (
        BACKEND_NAMES,
        DEFAULT_REASONING_EFFORTS,
        SUPPORTED_REASONING_EFFORTS,
        CodexModelOption,
        ModelOption,
        backend_display_name,
        discover_tag_models,
        model_names_path,
        remember_model_names,
        rich_events_selected,
    )
    from opentag_process_env import backend_environment
    from tag_error_reporting import (
        COMMUNITY_INVITE_URL,
        ErrorReport,
        ErrorReportStore,
        ReportOrigin,
        build_troubleshooting_prompt,
        classify_failure,
        collect_health_checks,
        default_report_store,
        make_error_report,
        new_error_reference,
        sanitize_user_context,
        utc_timestamp,
    )
    from slack_mrkdwn import to_mrkdwn
    from slack_search_scope import ScopePlan, SearchIntent, plan_search_scopes
    from tag_activity import ACTIVITY_DETAIL_ACTION_ID, PUBLIC_LABELS, ActivityStore, activity_detail_modal, activity_modal
    from agent_summary import queue_reply_summary
    from tag_activity_details import sanitize_activity_details
    from tag_approval_choices import sanitize_review_details
    from tag_activity_labels import activity_title_for_status, readable_activity_title
    from tag_paths import instance_home, tag_temp_dir
    import slack_channels
    import tag_memory
    import tag_handoff


MENTION_RE = re.compile(r"<@[^>]+>")
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_ATTACHMENTS_PER_REQUEST = 10
MAX_TOTAL_ATTACHMENT_BYTES = 30 * 1024 * 1024
MAX_ATTACHMENT_TEXT_CHARS = 12_000
MAX_OUTPUT_FILE_BYTES = 15 * 1024 * 1024
MAX_OUTPUT_ARTIFACTS = 10
OUTPUT_ARTIFACT_MANIFEST_PREFIX = ".opentag-output-artifacts-"
MAX_GENERATED_IMAGES = 10
MAX_REPLY_CHARS = 3_800
STREAM_START_CHARS = 40
STREAM_APPEND_CHARS = 200
STREAM_FLUSH_SECONDS = 0.35
MAX_STREAM_TASKS = 12
MAX_STREAM_ACTIVITY_EVENTS = 60
ACTIVITY_DEBOUNCE_SECONDS = 0.25
ACTIVITY_HOLD_SECONDS = 1.5
ACTIVITY_WAIT_SECONDS = 12.0
CANCEL_GRACE_SECONDS = 6.0
STATUS_REFRESH_SECONDS = 90
STATUS_CLEANUP_RETRY_DELAYS = (2, 5, 15, 30, 60, 90, 90, 90)
ACTIVITY_ACTION_ID = "opentag_view_activity"
SHOW_ACTIVITY_DETAILS = False
SETTINGS_ACTION_ID = "opentag_change_agent_settings"
SETTINGS_MODEL_ACTION_ID = "opentag_settings_model"
SETTINGS_EFFORT_ACTION_ID = "opentag_settings_effort"
SETTINGS_FAST_ACTION_ID = "opentag_settings_fast_mode"
SETTINGS_RESET_ACTION_ID = "opentag_settings_reset"
RETRY_ACTION_ID = "opentag_retry_request"
APPROVAL_APPROVE_ACTION_ID = "opentag_approval_approve"
APPROVAL_DENY_ACTION_ID = "opentag_approval_deny"
APPROVAL_CHOICE_ACTION_PREFIX = "opentag_approval_choice_"
REPORT_ISSUE_ACTION_ID = "opentag_report_issue"
FIX_WITH_AGENT_ACTION_ID = "opentag_fix_with_coding_agent"
JOIN_COMMUNITY_ACTION_ID = "opentag_join_hover_community"
REPORT_VIEW_ID = "opentag_report_issue_view"
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


class AttachmentLimitError(ValueError):
    """An expected attachment rejection that should be shown directly to the user."""

    @classmethod
    def for_file(cls, name: str) -> "AttachmentLimitError":
        return cls(
            f"I couldn’t process {name} because it exceeds Tag’s 15 MB attachment "
            "limit. Upload a smaller file or provide a local path/link."
        )

    @classmethod
    def for_count(cls) -> "AttachmentLimitError":
        return cls(
            f"I couldn’t process these attachments because Tag accepts at most "
            f"{MAX_ATTACHMENTS_PER_REQUEST} files per request. Upload fewer files."
        )

    @classmethod
    def for_total(cls) -> "AttachmentLimitError":
        total_mb = MAX_TOTAL_ATTACHMENT_BYTES // (1024 * 1024)
        return cls(
            "I couldn’t process these attachments because together they exceed "
            f"Tag’s {total_mb} MB per-request limit. Upload fewer or smaller files, "
            "or provide local paths/links."
        )


@dataclass
class AttachmentBudget:
    """Track the actual bytes downloaded for one Slack invocation."""

    file_count: int = 0
    total_bytes: int = 0

    def record(self, size: int) -> None:
        self.file_count += 1
        if self.file_count > MAX_ATTACHMENTS_PER_REQUEST:
            raise AttachmentLimitError.for_count()
        self.total_bytes += size
        if self.total_bytes > MAX_TOTAL_ATTACHMENT_BYTES:
            raise AttachmentLimitError.for_total()


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
class AgentSettings:
    model: str | None = None
    reasoning_effort: str | None = None
    fast_mode: bool | None = None
    backend: str | None = None


def configured_reasoning_efforts() -> tuple[str, ...]:
    configured = tuple(
        value.strip().lower()
        for value in os.getenv("OPENTAG_CODEX_REASONING_EFFORTS", "").split(",")
        if value.strip().lower() in SUPPORTED_REASONING_EFFORTS
    )
    return configured or DEFAULT_REASONING_EFFORTS


def find_model(
    model: str | None,
    models: list[ModelOption],
    backend: str | None = None,
) -> ModelOption | None:
    if model is None:
        return None
    return next(
        (item for item in models if item.model_id == model and backend in {None, item.backend}),
        None,
    )


def efforts_for_model(
    model: str | None,
    models: list[ModelOption],
    backend: str | None = None,
) -> tuple[str, ...]:
    available = configured_reasoning_efforts() if backend in {None, "codex"} else SUPPORTED_REASONING_EFFORTS
    allowed = set(available)
    if model:
        selected = find_model(model, models, backend)
        if selected:
            return tuple(effort for effort in selected.reasoning_efforts if effort in allowed)
    scoped = [item for item in models if backend in {None, item.backend}]
    discovered = {effort for item in scoped for effort in item.reasoning_efforts}
    return tuple(effort for effort in available if not discovered or effort in discovered)


def fast_mode_available(
    model: str | None,
    models: list[ModelOption],
    backend: str | None = None,
) -> bool:
    """Let the backend validate its configured default; validate explicit models locally."""
    if model is None:
        return True
    selected = find_model(model, models, backend)
    return bool(selected and selected.supports_fast_mode)


def model_default_effort(selected: ModelOption, efforts: tuple[str, ...]) -> str | None:
    """The Tag's level for its default model, or the model's own when operators don't offer it."""
    effort = selected.default_reasoning_effort
    if effort not in efforts and selected.tag_effort_applied:
        return selected.model_reasoning_effort
    return effort


def default_agent_settings(models: list[ModelOption]) -> AgentSettings:
    if not models:
        return AgentSettings()
    selected = next((item for item in models if item.is_default), None)
    if selected is None:
        return AgentSettings(backend=models[0].backend)
    efforts = efforts_for_model(selected.model_id, models, selected.backend)
    effort = model_default_effort(selected, efforts)
    if effort not in efforts:
        effort = efforts[0] if efforts else None
    return AgentSettings(
        model=selected.model_id,
        reasoning_effort=effort,
        fast_mode=selected.default_fast_mode and selected.supports_fast_mode,
        backend=selected.backend,
    )


def default_effort_for_model(
    model: str | None,
    models: list[ModelOption],
    backend: str | None = None,
) -> str | None:
    selected = find_model(model, models, backend)
    efforts = efforts_for_model(model, models, backend)
    if selected and model_default_effort(selected, efforts) in efforts:
        return model_default_effort(selected, efforts)
    return efforts[0] if efforts else None


def normalize_settings(
    settings: AgentSettings,
    models: list[ModelOption],
) -> AgentSettings:
    defaults = default_agent_settings(models)
    selected = find_model(settings.model, models, settings.backend)
    backend_known = settings.backend is None or any(item.backend == settings.backend for item in models)
    model_is_valid = (settings.model is None and backend_known) or selected is not None
    if selected is not None:
        model, backend = selected.model_id, selected.backend
    else:
        model, backend = defaults.model, defaults.backend
    efforts = efforts_for_model(model, models, backend)
    effort = (
        settings.reasoning_effort
        if settings.reasoning_effort in efforts
        else default_effort_for_model(model, models, backend)
    )
    requested_fast_mode = defaults.fast_mode if settings.fast_mode is None else settings.fast_mode
    fast_mode = bool(
        requested_fast_mode
        and model_is_valid
        and fast_mode_available(model, models, backend)
    )
    return AgentSettings(model=model, reasoning_effort=effort, fast_mode=fast_mode, backend=backend)


def retire_slack_settings(path: Path | None = None) -> None:
    """Migration v1: archive per-user overrides before accepting Slack requests."""
    try:
        from .tag_locks import LifecycleLock
        from .tag_config import save_config
    except ImportError:
        from tag_locks import LifecycleLock
        from tag_config import save_config
    path = path or Path(os.getenv(
        "OPENTAG_SLACK_SETTINGS_FILE",
        str(skill_dir() / ".runtime" / "slack-user-settings.json"),
    )).expanduser()
    backup = path.with_name(path.name + ".retired-v1")
    marker = path.with_name(path.name + ".retired-v1.complete.json")
    if not path.exists() and not backup.exists():
        return
    with LifecycleLock(path.with_name(path.name + ".migration.lock")):
        if path.exists():
            original = path.read_bytes()
            if backup.exists():
                if backup.read_bytes() != original:
                    raise RuntimeError("Slack settings retirement found conflicting backups; preserve both files before retrying.")
                path.unlink()
            else:
                path.replace(backup)
            if path.exists() or backup.read_bytes() != original:
                raise RuntimeError("Slack settings retirement verification failed; retry startup.")
        if not marker.exists():
            save_config(marker, {"version": "1"})


def retired_settings_modal() -> dict[str, Any]:
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "Model settings moved"},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": [{"type": "section", "text": {
            "type": "mrkdwn",
            "text": "Choose this Tag's model and thinking level in Tag.app → Details, or with `tag NAME settings ai`. All Slack requests use those settings.",
        }}],
    }


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


def message_files(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Return direct and forwarded Slack files from one message."""
    files: list[dict[str, Any]] = []
    seen_file_ids: set[str] = set()
    containers: list[dict[str, Any]] = [message]
    containers.extend(
        attachment
        for attachment in message.get("attachments") or []
        if isinstance(attachment, dict)
    )
    for container in containers:
        for file in container.get("files") or []:
            if not isinstance(file, dict):
                continue
            file_id = file.get("id")
            if isinstance(file_id, str) and file_id:
                if file_id in seen_file_ids:
                    continue
                seen_file_ids.add(file_id)
            files.append(file)
    return files


def attachment_name(file: dict[str, Any], index: int) -> str:
    raw_name = file.get("name") or f"slack-image-{index}"
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_name).strip(".-")
    return safe_name or f"slack-image-{index}"


def stored_attachment_name(file: dict[str, Any], index: int) -> str:
    """Return a collision-resistant local name while preserving the extension."""
    name = attachment_name(file, index)
    file_id = (
        re.sub(r"[^A-Za-z0-9_-]+", "-", str(file.get("id") or index)).strip("-")
        or str(index)
    )
    stem, separator, suffix = name.rpartition(".")
    extension = f".{suffix}" if separator and stem else ""
    if not extension:
        stem = name
    # Sanitized names are ASCII, so characters equal bytes for the 255-byte
    # filesystem component limit. Reserve the ID and extension before trimming.
    stem_limit = max(0, 255 - len(file_id) - 1 - len(extension))
    return f"{stem[:stem_limit]}-{file_id}{extension}"


def validate_attachment_metadata(files: list[dict[str, Any]]) -> None:
    """Reject declared attachment limits before creating backend work."""
    if len(files) > MAX_ATTACHMENTS_PER_REQUEST:
        raise AttachmentLimitError.for_count()
    declared_total = 0
    for index, file in enumerate(files, start=1):
        size = file.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            continue
        name = attachment_name(file, index)
        if size > MAX_ATTACHMENT_BYTES:
            raise AttachmentLimitError.for_file(name)
        declared_total += size
    if declared_total > MAX_TOTAL_ATTACHMENT_BYTES:
        raise AttachmentLimitError.for_total()


def is_text_file(file: dict[str, Any]) -> bool:
    """Return whether a Slack file is safe to include as bounded prompt text."""
    mime_type = (file.get("mimetype") or "").lower()
    file_type = (file.get("filetype") or "").lower()
    return mime_type.startswith("text/") or mime_type in TEXT_FILE_MIME_TYPES or file_type in TEXT_FILE_TYPES


def download_file_bytes(
    url: str,
    token: str,
    expected_content_prefix: str | None = None,
    *,
    attachment_label: str = "attachment",
) -> bytes:
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
                raise AttachmentLimitError.for_file(attachment_label)
            chunks.append(chunk)
    return b"".join(chunks)


def download_thread_images(
    messages: list[dict[str, Any]],
    attachment_dir: Path,
    budget: AttachmentBudget | None = None,
) -> list[str]:
    """Download Slack image attachments for the current invocation only."""
    lines: list[str] = []
    seen_file_ids: set[str] = set()
    token = require_env("SLACK_BOT_TOKEN")

    for message in messages:
        for file in message_files(message):
            file_id = file.get("id")
            if not file_id or file_id in seen_file_ids:
                continue
            seen_file_ids.add(file_id)
            mime_type = file.get("mimetype") or ""
            if not mime_type.startswith("image/"):
                continue
            url = file.get("url_private_download") or file.get("url_private")
            name = stored_attachment_name(file, len(seen_file_ids))
            if not url:
                lines.append(f"[Slack image attachment could not be downloaded: {name}]")
                continue

            target = attachment_dir / name
            try:
                data = download_file_bytes(
                    url,
                    token,
                    expected_content_prefix="image/",
                    attachment_label=name,
                )
                if budget is not None:
                    budget.record(len(data))
                with target.open("wb") as output:
                    output.write(data)
                detected_type = mimetypes.guess_type(target.name)[0] or mime_type
                lines.append(f"[Slack image attachment: {name} ({detected_type}) at {target}]")
            except AttachmentLimitError:
                target.unlink(missing_ok=True)
                raise
            except (OSError, urllib.error.URLError, ValueError) as exc:
                target.unlink(missing_ok=True)
                lines.append(f"[Could not retrieve Slack image {name}: {exc}]")
    return lines


def download_thread_text_files(
    messages: list[dict[str, Any]], budget: AttachmentBudget | None = None
) -> list[str]:
    """Read Slack snippets and text attachments into the current prompt only."""
    lines: list[str] = []
    seen_file_ids: set[str] = set()
    token = require_env("SLACK_BOT_TOKEN")

    for message in messages:
        for file in message_files(message):
            file_id = file.get("id")
            if not file_id or file_id in seen_file_ids or not is_text_file(file) or (file.get("mimetype") or "").lower().startswith("image/"):
                continue
            seen_file_ids.add(file_id)
            name = attachment_name(file, len(seen_file_ids))
            url = file.get("url_private_download") or file.get("url_private")
            if not url:
                lines.append(f"[Slack text attachment could not be downloaded: {name}]")
                continue
            try:
                data = download_file_bytes(url, token, attachment_label=name)
                if budget is not None:
                    budget.record(len(data))
                text = data.decode("utf-8", errors="replace").strip()
                if len(text) > MAX_ATTACHMENT_TEXT_CHARS:
                    text = text[:MAX_ATTACHMENT_TEXT_CHARS] + "\n[Attachment text truncated]"
                if text:
                    lines.append(f"[Slack text attachment: {name}]\n{text}")
                else:
                    lines.append(f"[Slack text attachment was empty: {name}]")
            except AttachmentLimitError:
                raise
            except (OSError, UnicodeError, urllib.error.URLError, ValueError) as exc:
                lines.append(f"[Could not retrieve Slack text attachment {name}: {exc}]")
    return lines


def download_thread_binary_files(
    messages: list[dict[str, Any]],
    attachment_dir: Path,
    budget: AttachmentBudget | None = None,
) -> list[str]:
    """Download non-image, non-text Slack files without interpreting them."""
    lines: list[str] = []
    seen_file_ids: set[str] = set()
    token = require_env("SLACK_BOT_TOKEN")

    for message in messages:
        for file in message_files(message):
            file_id = file.get("id")
            if not file_id or file_id in seen_file_ids:
                continue
            seen_file_ids.add(file_id)
            mime_type = (file.get("mimetype") or "application/octet-stream").lower()
            if mime_type.startswith("image/") or is_text_file(file):
                continue
            name = stored_attachment_name(file, len(seen_file_ids))
            url = file.get("url_private_download") or file.get("url_private")
            if not url:
                lines.append(f"[Slack file attachment could not be downloaded: {name}]")
                continue
            target = attachment_dir / name
            try:
                data = download_file_bytes(url, token, attachment_label=name)
                if budget is not None:
                    budget.record(len(data))
                target.write_bytes(data)
                lines.append(
                    f"[Slack file attachment: {name} ({mime_type}) at {target}]"
                )
            except AttachmentLimitError:
                target.unlink(missing_ok=True)
                raise
            except (OSError, urllib.error.URLError, ValueError) as exc:
                target.unlink(missing_ok=True)
                lines.append(f"[Could not retrieve Slack file {name}: {exc}]")
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
    *,
    artifacts: list[dict[str, str]] | None = None,
) -> list[str]:
    """Upload validated backend image results into the originating Slack thread."""
    images, errors = collect_generated_images(results_dir)
    for path in images:
        artifact = {"name": path.name, "kind": "image", "delivery": "upload_failed"}
        if artifacts is not None:
            artifacts.append(artifact)
        try:
            response = client.files_upload_v2(
                channel=channel,
                thread_ts=thread_ts,
                file=str(path),
                filename=path.name,
                title=path.stem,
            )
            artifact["delivery"] = "uploaded"
            if url := uploaded_file_permalink(response):
                artifact["url"] = url
        except Exception as exc:  # noqa: BLE001 - upload failures must not hide the text answer
            errors.append(f"{path.name}: {exc}")
    return errors


def uploaded_file_permalink(response: Any) -> str | None:
    """Use the provider's file link, never a temporary authenticated download URL."""
    if not hasattr(response, "get"):
        return None
    files = response.get("files")
    files = list(files) if isinstance(files, list) else []
    if isinstance(response.get("file"), dict):
        files.append(response.get("file"))
    return next((item["permalink"] for item in files if isinstance(item, dict)
                 and isinstance(item.get("permalink"), str) and item["permalink"]), None)


def select_request_files(
    messages: list[dict[str, Any]], request: dict[str, Any]
) -> list[dict[str, Any]]:
    """Select explicit references, current uploads, or the latest attachment group."""
    unique: dict[str, dict[str, Any]] = {}
    for message in messages:
        for file in message_files(message):
            if file.get("id"):
                unique.setdefault(file["id"], file)
    current = {file["id"]: file for file in message_files(request) if file.get("id")}
    unique.update(current)
    text = request.get("text", "")
    if re.search(r"\ball (?:the )?(?:files|images|attachments) (?:in|from) (?:this|the) thread\b", text, re.I):
        return list(unique.values())
    selected = dict(current)
    explicit_ids = {
        file_id for file_id in unique
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(file_id) + r"(?![A-Za-z0-9])", text)
    }
    for file_id in explicit_ids:
        selected[file_id] = unique[file_id]
    names: dict[str, list[dict[str, Any]]] = {}
    for file in unique.values():
        if file.get("name"):
            names.setdefault(file["name"], []).append(file)
    for name, matches in names.items():
        if not re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w.-])", text, re.I):
            continue
        preferred = [file for file in matches if file["id"] in selected]
        if len(matches) > 1 and not preferred:
            raise AttachmentLimitError(
                f"More than one attachment is named {name}. Please share the Slack file link "
                "or reattach the version you want me to use."
            )
        for file in preferred or matches:
            selected[file["id"]] = file
    if not selected:
        for message in reversed(messages):
            files = message_files(message)
            if files:
                selected = {file["id"]: file for file in files if file.get("id")}
                if selected:
                    break
    return list(selected.values())


def build_thread_text(
    client: Any, channel: str, thread_ts: str, attachment_dir: Path,
    *, request: dict[str, Any] | None = None,
) -> str:
    messages: list[dict[str, Any]] = []
    cursor = ""
    seen_cursors: set[str] = set()
    for _ in range(20):
        kwargs: dict[str, Any] = {"channel": channel, "ts": thread_ts, "limit": 100}
        if request is not None:
            kwargs.update(latest=request["ts"], inclusive=True)
        if cursor:
            kwargs["cursor"] = cursor
        response = client.conversations_replies(**kwargs)
        messages.extend(response.get("messages", []))
        cursor = (response.get("response_metadata") or {}).get("next_cursor", "")
        if not cursor:
            if response.get("has_more"):
                raise AttachmentLimitError("I couldn’t retrieve the complete thread. Please start a new thread with the files you want me to use.")
            break
        if cursor in seen_cursors:
            raise AttachmentLimitError("I couldn’t retrieve the complete thread. Please retry or start a new thread with the files you want me to use.")
        seen_cursors.add(cursor)
    else:
        raise AttachmentLimitError("This thread is too long to select attachments reliably. Please start a new thread with the files you want me to use.")
    if request is not None:
        messages = [message for message in messages if float(message.get("ts", "0")) <= float(request["ts"])]
        # Preserve API-enriched file metadata when the event omits it; event
        # fields win, including uploads not yet returned by replies.
        fetched_request = next((message for message in messages if message.get("ts") == request["ts"]), {})
        request = {**fetched_request, **request}
        messages = [message for message in messages if message.get("ts") != request["ts"]]
        messages.append(request)
    messages.sort(key=lambda message: float(message.get("ts", "0")))
    files = select_request_files(messages, request or (messages[-1] if messages else {}))
    validate_attachment_metadata(files)
    selected_ids = {file["id"] for file in files}
    budget = AttachmentBudget()
    lines = []
    for message in messages[-30:]:
        user = message.get("user") or message.get("bot_id") or "unknown"
        text = message.get("text", "")
        lines.append(f"{user}: {text}")
        lines.extend(format_message_attachments(message))
        for file in message_files(message):
            if file.get("id") not in selected_ids:
                lines.append(f"[Historical attachment, not downloaded: {file.get('name', 'unnamed')} ({file.get('id', 'unknown')})]")
    selected_messages = [{"files": files}]
    lines.extend(download_thread_text_files(selected_messages, budget))
    lines.extend(download_thread_images(selected_messages, attachment_dir, budget))
    lines.extend(download_thread_binary_files(selected_messages, attachment_dir, budget))
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


def model_label(model: str | None, models: list[ModelOption], backend: str | None = None) -> str:
    if model is None:
        return "Default model"
    option = find_model(model, models, backend)
    return option.label if option else model


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def reported_model_option(model: str, models: list[ModelOption], backend: str) -> ModelOption | None:
    """Find the backend-reported model, preferring a catalog name over an alias entry."""
    matches = [
        item for item in models
        if item.backend == backend and model in {item.model_id, item.resolved_model}
    ]
    exact = next((item for item in matches if item.model_id == model), None)
    named = next((item for item in matches if item.model_id != "default"), None)
    return exact or named


def reported_model_label(model: str, models: list[ModelOption], backend: str) -> str:
    option = reported_model_option(model, models, backend)
    return option.label if option else model


def run_summary_blocks(
    settings: AgentSettings,
    models: list[ModelOption],
    backend: str,
    seconds: float,
    *,
    outcome: str = "completed",
    reported_model: str | None = None,
    reported_effort: str | None = None,
) -> list[dict[str, Any]]:
    """Show which model answered, its thinking level, and how long the request took."""
    model = settings.model
    reported_option = reported_model_option(reported_model, models, backend) if reported_model else None
    option = reported_option or (find_model(model, models, backend) if model else None)
    if reported_model:
        label = reported_option.label if reported_option else reported_model
    elif model in {None, "default"}:
        label = "default model"
    else:
        label = model_label(model, models, backend)
    parts = [backend_display_name(backend), label]
    effort = (
        settings.reasoning_effort
        or reported_effort
        or (option.default_reasoning_effort if option else None)
    )
    if effort:
        parts.append(f"{effort} thinking")
    elif option is None or option.reasoning_efforts:
        # Models known to have no thinking levels, such as Haiku, show none.
        parts.append("default thinking")
    if settings.fast_mode:
        parts.append("Fast mode")
    timing = format_duration(seconds)
    parts.append({"stopped": f"stopped after {timing}", "failed": f"failed after {timing}"}.get(outcome, timing))
    return [{"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(parts)}]}]


def activity_button_blocks(
    *, team: str, channel: str, thread_ts: str, run_id: str,
    direct_message: bool = False,
) -> list[dict[str, Any]]:
    if not SHOW_ACTIVITY_DETAILS:
        return []
    metadata: dict[str, Any] = {
        "team": team, "channel": channel, "thread_ts": thread_ts, "run_id": run_id,
    }
    if direct_message:
        metadata["direct_message"] = True
    return [{"type": "actions", "elements": [{
        "type": "button", "action_id": ACTIVITY_ACTION_ID,
        "text": {"type": "plain_text", "text": "Activity"},
        "value": json.dumps(metadata, separators=(",", ":")),
    }]}]


def output_artifact_button_blocks(
    paths: list[Path],
    workdir: Path,
    *,
    user_id: str,
    channel: str,
    thread_ts: str,
    uploaded_paths: set[Path] | None = None,
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
        if uploaded_paths is not None and path in uploaded_paths:
            continue
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


def clear_slack_session(
    client: Any,
    channel: str,
    thread_ts: str,
    logger: Any,
    *,
    pending_session_api: bool = True,
    pending_legacy_status: bool = True,
) -> tuple[bool, bool]:
    """Return the cleanup operations that remain pending after best-effort calls."""
    if pending_session_api:
        try:
            client.api_call(
                "agents.sessions.setStatus",
                json={
                    "channel_id": channel,
                    "thread_ts": thread_ts,
                    "status": "active",
                },
            )
            pending_session_api = False
        except Exception as exc:  # noqa: BLE001 - legacy cleanup may still work
            logger.warning("Could not close Slack agent session: %s", exc)
    if pending_legacy_status:
        try:
            client.assistant_threads_setStatus(
                channel_id=channel,
                thread_ts=thread_ts,
                status="",
            )
            pending_legacy_status = False
        except Exception as exc:  # noqa: BLE001 - Agent Sessions may have worked
            logger.warning("Could not clear Slack loading status: %s", exc)
    return pending_session_api, pending_legacy_status


class SlackSessionJournal:
    """Persist enough identity to clear Slack statuses after an unclean exit."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.sessions = self._load()

    @staticmethod
    def key(team: str, channel: str, thread_ts: str) -> str:
        return f"{team}:{channel}:{thread_ts}"

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        sessions: dict[str, dict[str, Any]] = {}
        for key, value in payload.items():
            if not (
                isinstance(key, str)
                and isinstance(value, dict)
                and all(
                    isinstance(value.get(field), str) and value[field]
                    for field in ("team", "channel", "thread_ts")
                )
            ):
                continue
            pending_session_api = value.get("pending_session_api", True)
            pending_legacy_status = value.get("pending_legacy_status", True)
            if not (
                isinstance(pending_session_api, bool)
                and isinstance(pending_legacy_status, bool)
            ):
                continue
            sessions[key] = {
                "team": value["team"],
                "channel": value["channel"],
                "thread_ts": value["thread_ts"],
                "pending_session_api": pending_session_api,
                "pending_legacy_status": pending_legacy_status,
            }
        return sessions

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

    def add(
        self,
        team: str,
        channel: str,
        thread_ts: str,
        *,
        pending_session_api: bool = True,
        pending_legacy_status: bool = True,
    ) -> None:
        """Record exactly which status transitions must survive a restart."""
        with self.lock:
            self.sessions[self.key(team, channel, thread_ts)] = {
                "team": team,
                "channel": channel,
                "thread_ts": thread_ts,
                "pending_session_api": pending_session_api,
                "pending_legacy_status": pending_legacy_status,
            }
            self._save()

    def set_pending(
        self,
        team: str,
        channel: str,
        thread_ts: str,
        *,
        pending_session_api: bool,
        pending_legacy_status: bool,
    ) -> None:
        """Persist remaining work, or retire the entry when both calls succeeded."""
        if pending_session_api or pending_legacy_status:
            self.add(
                team,
                channel,
                thread_ts,
                pending_session_api=pending_session_api,
                pending_legacy_status=pending_legacy_status,
            )
        else:
            self.remove(team, channel, thread_ts)

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
            pending_session_api, pending_legacy_status = clear_slack_session(
                client,
                session["channel"],
                session["thread_ts"],
                logger,
                pending_session_api=session["pending_session_api"],
                pending_legacy_status=session["pending_legacy_status"],
            )
            with self.lock:
                if pending_session_api or pending_legacy_status:
                    self.sessions[key] = {
                        **session,
                        "pending_session_api": pending_session_api,
                        "pending_legacy_status": pending_legacy_status,
                    }
                else:
                    self.sessions.pop(key, None)
                    cleared += 1
                try:
                    self._save()
                except OSError as exc:
                    logger.warning("Could not update the Slack session journal: %s", exc)
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
        self.cleanup_timer: threading.Timer | None = None
        self.cleanup_retry_index = 0
        self.cleanup_session_api = False
        self.cleanup_legacy_status = False
        self.activity_due = float("inf")
        self.activities: dict[str, str] = {}
        self.activity_started: dict[str, float] = {}
        self.wait_labels: dict[str, str] = {}
        self.preparing_answer = False
        self.public_status: str | None = None
        self.last_status: str | None = None
        self.last_status_at = float("-inf")
        self.lock = threading.RLock()

    def journal_add(
        self,
        *,
        pending_session_api: bool = True,
        pending_legacy_status: bool = True,
    ) -> None:
        if self.journal is None:
            return
        try:
            self.journal.add(
                self.team,
                self.channel,
                self.thread_ts,
                pending_session_api=pending_session_api,
                pending_legacy_status=pending_legacy_status,
            )
        except OSError as exc:
            self.logger.warning("Could not record the active Slack session: %s", exc)

    def journal_set_pending(self) -> None:
        """Persist the cleanup flags before relying on restart recovery."""
        if self.journal is None:
            return
        try:
            self.journal.set_pending(
                self.team,
                self.channel,
                self.thread_ts,
                pending_session_api=self.cleanup_session_api,
                pending_legacy_status=self.cleanup_legacy_status,
            )
        except OSError as exc:
            self.logger.warning("Could not update the Slack session journal: %s", exc)

    def journal_remove(self) -> None:
        if self.journal is None:
            return
        try:
            self.journal.remove(self.team, self.channel, self.thread_ts)
        except OSError as exc:
            self.logger.warning("Could not update the Slack session journal: %s", exc)

    def current_status(self) -> str:
        """Return the most useful truthful progress copy for the current work."""
        if self.public_status:
            return self.public_status
        if self.activities:
            latest = next(reversed(self.activities))
            elapsed = time.monotonic() - self.activity_started[latest]
            status = self.wait_labels[latest] if elapsed >= ACTIVITY_WAIT_SECONDS else self.activities[latest]
            if len(self.activities) > 1:
                status += f" (+{len(self.activities) - 1} other active)"
            return status
        if self.preparing_answer:
            return "Preparing your answer…"
        return "is working on this…"

    def set_display_status(self, *, force: bool = False) -> None:
        """Update either custom Slack status or the progress-message fallback."""
        status = self.current_status()
        if not force and status == self.last_status:
            return
        if self.legacy_status:
            self.client.assistant_threads_setStatus(
                channel_id=self.channel,
                thread_ts=self.thread_ts,
                status=status,
                loading_messages=LOADING_MESSAGES,
            )
        elif self.message_ts is not None:
            self.client.chat_update(
                channel=self.channel,
                ts=self.message_ts,
                text=status,
            )
        else:
            return
        self.last_status = status
        self.last_status_at = time.monotonic()

    def set_native_status(self, *, force: bool = False) -> None:
        """Set legacy custom status while that compatibility API is available."""
        status = self.current_status()
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
            if self.legacy_status or self.message_ts is not None:
                try:
                    self.set_display_status(force=True)
                except Exception as exc:  # noqa: BLE001 - processing remains active
                    self.logger.warning("Could not update Slack progress: %s", exc)

    def schedule_activity(self) -> None:
        """Coalesce events and hold the displayed copy briefly; caller holds lock."""
        if not self.legacy_status and self.message_ts is None:
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
            if not self.legacy_status and self.message_ts is None:
                return
            try:
                self.set_display_status()
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
                self.logger.warning("Could not update Slack progress: %s", exc)

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
                self.cleanup_session_api = self.session_api
                self.cleanup_legacy_status = self.legacy_status
                self.journal_add(
                    pending_session_api=self.cleanup_session_api,
                    pending_legacy_status=self.cleanup_legacy_status,
                )
                self.schedule_refresh()
            if not self.legacy_status:
                try:
                    response = self.client.chat_postMessage(
                        channel=self.channel,
                        thread_ts=self.thread_ts,
                        text="Working on this…",
                    )
                    self.message_ts = response["ts"]
                    self.last_status = self.current_status()
                    self.last_status_at = time.monotonic()
                except Exception as exc:  # noqa: BLE001 - native session may still work
                    self.logger.warning("Could not post Slack progress message: %s", exc)
            if not self.native:
                self.journal_remove()

    def schedule_cleanup_retry(self) -> None:
        """Retry a failed terminal transition without blocking answer delivery."""
        if self.cleanup_timer is not None:
            return
        if self.cleanup_retry_index >= len(STATUS_CLEANUP_RETRY_DELAYS):
            self.logger.warning(
                "Slack loading status cleanup still failed after %s retries; "
                "the session journal will retry on restart",
                self.cleanup_retry_index,
            )
            return
        delay = STATUS_CLEANUP_RETRY_DELAYS[self.cleanup_retry_index]
        self.cleanup_retry_index += 1
        self.cleanup_timer = threading.Timer(delay, self.retry_cleanup)
        self.cleanup_timer.daemon = True
        self.cleanup_timer.start()

    def retry_cleanup(self) -> None:
        """Complete an orphaned processing session after Slack recovers."""
        with self.lock:
            self.cleanup_timer = None
            if self.cleanup_session_api:
                try:
                    self.set_session_status("active")
                    self.cleanup_session_api = False
                except Exception as exc:  # noqa: BLE001 - retry remains pending
                    self.logger.warning(
                        "Could not complete Slack agent session status: %s", exc
                    )
            if self.cleanup_legacy_status:
                try:
                    self.client.assistant_threads_setStatus(
                        channel_id=self.channel,
                        thread_ts=self.thread_ts,
                        status="",
                    )
                    self.cleanup_legacy_status = False
                except Exception as exc:  # noqa: BLE001 - retry remains pending
                    self.logger.warning(
                        "Could not clear native Slack loading status: %s", exc
                    )
            if not self.cleanup_session_api and not self.cleanup_legacy_status:
                self.journal_set_pending()
                return
            self.journal_set_pending()
            self.schedule_cleanup_retry()

    def clear(self, *, complete_session: bool = True) -> None:
        with self.lock:
            if self.activity_timer is not None:
                self.activity_timer.cancel()
                self.activity_timer = None
            if self.refresh_timer is not None:
                self.refresh_timer.cancel()
                self.refresh_timer = None
            if not (
                self.native
                or self.session_api
                or self.legacy_status
                or self.cleanup_session_api
                or self.cleanup_legacy_status
            ):
                return
            # Flip state before the API call so an already-running timer cannot
            # restore a status after final-answer streaming has begun.
            self.native = False
            if not complete_session:
                return
            self.cleanup_session_api = self.cleanup_session_api or self.session_api
            self.cleanup_legacy_status = self.cleanup_legacy_status or self.legacy_status
            if self.cleanup_session_api:
                try:
                    self.set_session_status("active")
                    self.cleanup_session_api = False
                except Exception as exc:  # noqa: BLE001 - final replies must still be delivered
                    self.logger.warning("Could not complete Slack agent session status: %s", exc)
            if self.cleanup_legacy_status:
                try:
                    self.client.assistant_threads_setStatus(
                        channel_id=self.channel,
                        thread_ts=self.thread_ts,
                        status="",
                    )
                    self.cleanup_legacy_status = False
                except Exception as exc:  # noqa: BLE001 - final replies must still be delivered
                    self.logger.warning("Could not clear native Slack loading status: %s", exc)
            cleanup_pending = self.cleanup_session_api or self.cleanup_legacy_status
            self.journal_set_pending()
            if cleanup_pending:
                self.schedule_cleanup_retry()
            self.session_api = False
            self.legacy_status = False


class SlackAnswerStream:
    """Stream public tool steps and final-answer deltas in one Slack message."""

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
        self.chunk_mode = False
        self.failed = False
        self.task_updates_available = True
        self.tasks: dict[str, str] = {}
        self.seen_tasks: set[str] = set()
        self.task_groups: dict[str, dict[str, Any]] = {}
        self.dirty_task_groups: set[str] = set()
        self.flush_timer: threading.Timer | None = None
        self.lock = threading.RLock()

    def activity(self, event: dict[str, Any]) -> None:
        """Show short tool identities live; keep full inputs and results private."""
        if event.get("type") not in {"activity_start", "activity_complete"}:
            return
        activity_id, label = event.get("activity_id"), event.get("label")
        if not isinstance(activity_id, str) or not activity_id or len(activity_id) > 200:
            return
        if not isinstance(label, str) or label not in PUBLIC_LABELS:
            label = "Using a connected tool…"
        tool = sanitize_activity_details(event.get("details")).get("tool", "")
        title = readable_activity_title(tool, label)
        compound = bool(re.search(r" (?:&&|\|\||\|) ", tool))
        item_id = hashlib.sha256(activity_id.encode("utf-8")).hexdigest()
        starting = event["type"] == "activity_start"
        with self.lock:
            if self.failed or not self.task_updates_available:
                return
            if self.ts is not None and not self.chunk_mode:
                self.task_updates_available = False
                return
            if starting:
                if item_id in self.seen_tasks or len(self.seen_tasks) >= MAX_STREAM_ACTIVITY_EVENTS:
                    return
                self.seen_tasks.add(item_id)
                key = f"tool:{label}:{tool or title}"
                if key not in self.task_groups and len(self.task_groups) >= MAX_STREAM_TASKS - 1:
                    key = "overflow"
                self.tasks[item_id] = key
                group = self.task_groups.get(key)
                if group is not None:
                    group["count"] += 1
                    group["active"] += 1
                    if key == "overflow":
                        group["title"] = f"More steps · {title}"
                        group["compound"] = compound
                else:
                    self.task_groups[key] = {
                        "id": hashlib.sha256(key.encode("utf-8")).hexdigest(),
                        "title": f"More steps · {title}" if key == "overflow" else title,
                        "count": 1, "active": 1, "failed": False,
                        "outcome": "completed",
                        "compound": compound,
                    }
                self.dirty_task_groups.add(key)
            elif item_id not in self.tasks:
                return
            else:
                key = self.tasks.pop(item_id)
                group = self.task_groups[key]
                group["active"] -= 1
                if event.get("status") != "completed":
                    group["failed"] = True
                    outcome = event.get("status")
                    group["outcome"] = outcome if isinstance(outcome, str) and outcome in {"failed", "declined", "interrupted"} else "unknown"
                if tool and tool != "Command" and key != "overflow":
                    group["title"] = title
                    group["compound"] = compound
                # Batch completions with the next start or final answer.
                self.dirty_task_groups.add(key)
                return
            chunks = [self._task_chunk(group) for key, group in self.task_groups.items()
                      if key in self.dirty_task_groups]
            try:
                if self.ts is None:
                    response = self.client.chat_startStream(
                        channel=self.channel,
                        thread_ts=self.thread_ts,
                        recipient_user_id=self.recipient_user_id,
                        recipient_team_id=self.recipient_team_id,
                        task_display_mode="plan",
                        chunks=[{"type": "plan_update", "title": "Agent activity"}, *chunks],
                    )
                    self.ts = response["ts"]
                    self.chunk_mode = True
                    if self.on_start is not None:
                        self.on_start()
                else:
                    self.client.chat_appendStream(channel=self.channel, ts=self.ts, chunks=chunks)
                self.dirty_task_groups.clear()
            except Exception as exc:  # noqa: BLE001 - task UI is optional; answer delivery remains primary
                self.task_updates_available = False
                self.logger.warning("Slack task-card update failed: %s", exc)

    @staticmethod
    def _task_chunk(group: dict[str, Any], *, final: bool = False, request_completed: bool = False) -> dict[str, Any]:
        outcome = group.get("outcome", "failed" if group["failed"] else "completed")
        if group["active"]:
            outcome = "unknown" if final else "running"
        title = activity_title_for_status(group["title"], outcome, compound=group.get("compound", False))
        if request_completed and outcome != "completed":
            # The run finished successfully; per-attempt outcomes remain in Activity.
            title = f"Finished · {group['title']}"
        if group["count"] > 1:
            title += f" · {group['count']} steps"
        # Escape Slack links and mentions; only the bounded identity is shared.
        raw_title = title
        title = html.escape(raw_title[:200], quote=False)
        if len(title) > 256:
            title = html.escape(raw_title[:45], quote=False) + "…"
        status = "error" if group["failed"] else "complete"
        if group["active"]:
            status = "error" if final else "in_progress"
        if request_completed:
            status = "complete"
        return {"type": "task_update", "id": group["id"], "title": title, "status": status}

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
                append_args: dict[str, Any] = {"channel": self.channel, "ts": self.ts}
                if self.chunk_mode:
                    append_args["chunks"] = [{"type": "markdown_text", "text": self.pending}]
                else:
                    append_args["markdown_text"] = self.pending
                self.client.chat_appendStream(**append_args)
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
            if not self.received and self.ts is None:
                return False
            if self.failed:
                return self._replace_and_stop_locked(final_text, blocks)

            # Close grouped cards once, with honest counts and terminal status.
            if self.ts is not None and self.task_updates_available:
                chunks = [self._task_chunk(group, final=True, request_completed=True) for group in self.task_groups.values()]
                try:
                    if chunks:
                        self.client.chat_appendStream(channel=self.channel, ts=self.ts, chunks=chunks)
                except Exception as exc:  # noqa: BLE001 - preserve final answer delivery
                    self.logger.warning("Could not close Slack task cards: %s", exc)
                self.tasks.clear()

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
                    if self.chunk_mode:
                        stop_args["chunks"] = [{"type": "markdown_text", "text": replace_text}]
                    else:
                        stop_args["markdown_text"] = replace_text
                elif self.chunk_mode:
                    stop_args["chunks"] = [{"type": "plan_update", "title": "Agent activity"}]
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
        stop_args: dict[str, Any] = {"channel": self.channel, "ts": self.ts}
        if self.chunk_mode:
            stop_args["chunks"] = [
                *(self._task_chunk(group, final=True, request_completed=True) for group in self.task_groups.values()),
                {"type": "markdown_text", "text": final_text},
            ]
        else:
            stop_args["markdown_text"] = final_text
        if blocks:
            stop_args["blocks"] = blocks
        try:
            self.client.chat_stopStream(**stop_args)
            return True
        except Exception as exc:  # noqa: BLE001 - caller still owns the full fallback
            self.logger.warning("Could not recover partial Slack answer stream: %s", exc)
            return False

    def abort(self, outcome: str = "interrupted") -> None:
        with self.lock:
            if self.flush_timer is not None:
                self.flush_timer.cancel()
                self.flush_timer = None
            if self.ts is None:
                return
            try:
                stop_args: dict[str, Any] = {"channel": self.channel, "ts": self.ts}
                if self.chunk_mode:
                    for group in self.task_groups.values():
                        if group["active"]:
                            group.update(active=0, failed=True, outcome=outcome)
                    stop_args["chunks"] = [
                        {"type": "plan_update", "title": "Agent activity"},
                        *(self._task_chunk(group, final=True) for group in self.task_groups.values()),
                    ]
                self.client.chat_stopStream(**stop_args)
            except Exception as exc:  # noqa: BLE001 - interruption cleanup is best effort
                self.logger.warning("Could not stop Slack answer stream: %s", exc)


@dataclass
class ActiveBackendRun:
    process: subprocess.Popen[str]
    control_file: Path
    run_id: str
    approval_dir: Path | None = None
    started_at_epoch: float = field(default_factory=time.time)
    cancel_requested: bool = False
    pending_approvals: set[str] = field(default_factory=set)
    pending_approval_choices: dict[str, set[str]] = field(default_factory=dict)
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

    def register_approval(self, approval_id: str, choices: list[dict[str, Any]] | None = None) -> bool:
        if not re.fullmatch(r"[0-9a-f]{32}", approval_id):
            return False
        if choices is not None and (
            not isinstance(choices, list) or not 1 <= len(choices) <= 20
            or any(not isinstance(c, dict) or not isinstance(c.get("id"), str)
                   or not re.fullmatch(r"[0-9]{1,2}", c["id"]) for c in choices)
            or len({c["id"] for c in choices}) != len(choices)
        ):
            return False
        with self.lock:
            if self.process.poll() is not None or self.approval_dir is None:
                return False
            if approval_id in self.pending_approvals:
                return False
            self.pending_approvals.add(approval_id)
            if choices is not None:
                self.pending_approval_choices[approval_id] = {
                    c["id"] for c in choices
                    if isinstance(c, dict) and isinstance(c.get("id"), str)
                    and re.fullmatch(r"[0-9]{1,2}", c["id"])
                }
            return True

    def resolve_approval(self, approval_id: str, *, approved: bool = False,
                         choice: str | None = None) -> bool:
        with self.lock:
            if (
                self.process.poll() is not None
                or self.approval_dir is None
                or approval_id not in self.pending_approvals
            ):
                return False
            offered = self.pending_approval_choices.get(approval_id)
            if choice is not None:
                if offered is None or choice not in offered:
                    return False
            elif approved and offered is not None:
                return False  # Native choices cannot be replaced with a generic approval.
            self.pending_approvals.remove(approval_id)
            self.pending_approval_choices.pop(approval_id, None)
            target = self.approval_dir / f"{approval_id}.json"
            temporary = self.approval_dir / f".{approval_id}.{uuid.uuid4().hex}.tmp"
            try:
                temporary.write_text(
                    json.dumps({"choice": choice} if choice is not None else
                               {"decision": "approve" if approved else "deny"}),
                    encoding="utf-8",
                )
                os.replace(temporary, target)
            except OSError:
                temporary.unlink(missing_ok=True)
                return False
            return True

    def finish(self) -> None:
        with self.lock:
            self.pending_approvals.clear()
            self.pending_approval_choices.clear()
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


def resolve_active_approval(key: RunKey, approval_id: str, *, approved: bool = False,
                            choice: str | None = None) -> bool:
    with ACTIVE_RUNS_LOCK:
        run = ACTIVE_RUNS.get(key)
    return run.resolve_approval(approval_id, approved=approved, choice=choice) if run is not None else False


def slack_search_grant_json(plan: ScopePlan, request_text: str) -> str:
    """Bind a pre-authorized channel grant to the original Slack request."""
    payload = plan.as_dict()
    payload["request_text"] = request_text
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


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
    scope_plan: ScopePlan | None = None,
    slack_search_grant: ScopePlan | None = None,
    on_error: Callable[[str | None, str], None] | None = None,
    memory_receipts: Path | None = None,
    handoff_requests: Path | None = None,
    handoff_depth: int = 0,
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
    if model:
        cmd.extend(["--model", model])
    if reasoning_effort:
        cmd.extend(["--reasoning-effort", reasoning_effort])
    cmd.extend(["--fast-mode", "on" if fast_mode else "off"])
    try:
        child_env = backend_environment(
            os.environ,
            transport="slack",
            conversation_id=channel,
            caller_id=caller_id,
            authorized_scopes=scope_plan.allowed_scopes if scope_plan else None,
            channel_labels=(
                json.dumps(slack_search_grant.channel_labels, sort_keys=True)
                if slack_search_grant
                else None
            ),
            slack_search_grant=(
                slack_search_grant_json(slack_search_grant, question)
                if slack_search_grant and slack_search_grant.mode == "all"
                else None
            ),
            memory_receipts=str(memory_receipts) if memory_receipts else None,
            handoff_requests=str(handoff_requests) if handoff_requests else None,
            handoff_depth=handoff_depth,
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
                detail = f"Tag backend exceeded its maximum runtime of {max_timeout}s"
                if on_error:
                    on_error("maximum_runtime", detail)
                return detail, False
            detail = output[-3000:] or f"Open Tag backend failed with exit code {result.returncode}."
            if on_error:
                on_error(None, detail)
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
    on_approval: Callable[[dict[str, Any]], None] | None = None,
    fast_mode: bool = False,
    output_manifest: Path | None = None,
    max_timeout: int | None = None,
    scope_plan: ScopePlan | None = None,
    slack_search_grant: ScopePlan | None = None,
    on_error: Callable[[str | None, str], None] | None = None,
    on_trace_event: Callable[[dict[str, Any]], None] | None = None,
    on_run_info: Callable[[dict[str, str]], None] | None = None,
    memory_receipts: Path | None = None,
    handoff_requests: Path | None = None,
    handoff_depth: int = 0,
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
    if model:
        cmd.extend(["--model", model])
    if reasoning_effort:
        cmd.extend(["--reasoning-effort", reasoning_effort])
    cmd.extend(["--fast-mode", "on" if fast_mode else "off"])
    run_id = uuid.uuid4().hex
    with tempfile.NamedTemporaryFile(
        "w", suffix=".control", delete=False, dir=tag_temp_dir()
    ) as control:
        control_file = Path(control.name)
    approval_dir_context = tempfile.TemporaryDirectory(
        prefix="tag-approvals-", dir=tag_temp_dir()
    )
    approval_dir = Path(approval_dir_context.name)
    cmd.extend([
        "--control-file", str(control_file),
        "--run-id", run_id,
        "--approval-dir", str(approval_dir),
    ])
    child_env = backend_environment(
        os.environ,
        transport="slack",
        conversation_id=channel,
        caller_id=caller_id,
        authorized_scopes=scope_plan.allowed_scopes if scope_plan else None,
        channel_labels=(
            json.dumps(slack_search_grant.channel_labels, sort_keys=True)
            if slack_search_grant
            else None
        ),
        slack_search_grant=(
            slack_search_grant_json(slack_search_grant, question)
            if slack_search_grant and slack_search_grant.mode == "all"
            else None
        ),
        memory_receipts=str(memory_receipts) if memory_receipts else None,
        handoff_requests=str(handoff_requests) if handoff_requests else None,
        handoff_depth=handoff_depth,
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
        approval_dir_context.cleanup()
        raise
    run_key = RunKey(team, channel, thread_ts)
    active_run = ActiveBackendRun(process, control_file, run_id, approval_dir)
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
    error_code: str | None = None
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
                candidate_code = event.get("code")
                error_code = candidate_code if isinstance(candidate_code, str) else error_code
                if on_error:
                    on_error(error_code, text)
            elif event_type == "usage" and on_trace_event:
                on_trace_event(event)
            elif event_type == "run_info":
                reported_model = event.get("model")
                reported_effort = event.get("reasoning_effort")
                if isinstance(reported_model, str) and reported_model and on_run_info:
                    info = {"model": reported_model[:120]}
                    if isinstance(reported_effort, str) and reported_effort in SUPPORTED_REASONING_EFFORTS:
                        info["reasoning_effort"] = reported_effort
                    on_run_info(info)
            elif event_type == "status" and isinstance(text, str) and on_status:
                on_status(text)
            elif event_type == "approval_expired":
                approval_id = event.get("approval_id")
                if isinstance(approval_id, str):
                    with active_run.lock:
                        active_run.pending_approvals.discard(approval_id)
                        active_run.pending_approval_choices.pop(approval_id, None)
            elif event_type == "approval_request":
                approval_id = event.get("approval_id")
                label = event.get("label")
                if (
                    isinstance(approval_id, str)
                    and isinstance(label, str)
                    and active_run.register_approval(approval_id, event.get("choices"))
                ):
                    if on_approval is None:
                        active_run.resolve_approval(approval_id, approved=False)
                    else:
                        try:
                            prompt = {"approval_id": approval_id, "label": label}
                            if "choices" in event:
                                prompt["choices"] = event["choices"]
                            if "review_details" in event:
                                prompt["review_details"] = sanitize_review_details(event["review_details"])
                            on_approval(prompt)
                        except Exception as exc:  # noqa: BLE001 - fail closed if Slack cannot ask
                            diagnostics.append(f"Could not present approval: {exc}")
                            active_run.resolve_approval(approval_id, approved=False)
            elif event_type in {"activity_start", "activity_complete"}:
                activity_id = event.get("activity_id")
                label = event.get("label")
                if isinstance(activity_id, str) and isinstance(label, str):
                    if on_trace_event:
                        on_trace_event(event)
                    wait_label = event.get("wait_label")
                    if on_activity:
                        on_activity(event_type, activity_id, label,
                                    wait_label if isinstance(wait_label, str) else "This operation is still running…")
            elif event_type == "turn_complete":
                status = event.get("status")
                if isinstance(status, str):
                    terminal_status = status
                terminal_text = event.get("text")
                if isinstance(terminal_text, str) and terminal_text:
                    error_text = terminal_text
                candidate_code = event.get("code")
                if isinstance(candidate_code, str):
                    error_code = candidate_code
                if on_error and terminal_status not in {"completed", "complete", "interrupted"}:
                    on_error(error_code, error_text or terminal_status)
        return_code = process.wait()
    finally:
        timer.cancel()
        active_run.finish()
        unregister_active_run(run_key, active_run)
        thread_file.unlink(missing_ok=True)
        control_file.unlink(missing_ok=True)
        approval_dir_context.cleanup()

    if active_run.cancel_requested and terminal_status == "interrupted":
        return "Stopped. Actions completed before the stop were not rolled back.", False
    if active_run.cancel_requested:
        return "Stop requested, but the backend did not confirm interruption before cleanup.", False
    if timed_out.is_set():
        detail = f"Tag backend exceeded its maximum runtime of {max_timeout}s"
        if on_error:
            on_error("maximum_runtime", detail)
        return detail, False
    if return_code != 0:
        details = error_text or "\n".join(diagnostics)[-3000:].strip()
        detail = details or f"Open Tag backend failed with exit code {return_code}."
        if on_error:
            on_error(error_code, detail)
        return detail, False
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


def post_private_failure(
    client: Any,
    channel: str,
    thread_ts: str,
    user_id: str,
    answer: str,
    placeholder_ts: str | None = None,
    footer_blocks: list[dict[str, Any]] | None = None,
) -> None:
    """Show a failed request only to its requester in a shared channel."""
    if is_direct_message_channel(channel):
        post_final_reply(client, channel, thread_ts, answer, placeholder_ts, footer_blocks)
        return
    rendered = to_mrkdwn(answer)
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": rendered}},
        *(footer_blocks or []),
    ]
    client.chat_postEphemeral(
        channel=channel,
        user=user_id,
        thread_ts=thread_ts,
        text=rendered,
        blocks=blocks,
    )
    if placeholder_ts is not None:
        try:
            client.chat_delete(channel=channel, ts=placeholder_ts)
        except Exception:  # noqa: BLE001 - the private failure was already delivered
            try:
                client.chat_update(channel=channel, ts=placeholder_ts, text="Request finished.")
            except Exception:
                pass  # The public placeholder has no diagnostic details.


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
        except (OSError, ValueError):
            errors.append(
                f"Could not attach `{candidate.name or 'requested output'}` because it is missing, "
                "inaccessible, or outside the workspace."
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
    on_upload_start: Callable[[], None] | None = None,
    uploaded_paths: set[Path] | None = None,
    artifacts: list[dict[str, str]] | None = None,
) -> list[str]:
    """Attach validated outputs to the authorized originating Slack thread."""
    entries, messages = load_output_artifact_entries(manifest, workdir)
    if on_upload_start is not None and any(attach for _path, attach in entries):
        on_upload_start()
    for path, attach in entries:
        artifact = {"name": path.name, "kind": "image" if (mimetypes.guess_type(path.name)[0] or "").startswith("image/") else "file", "local_path": str(path),
                    "delivery": "upload_failed" if attach else "local"}
        if artifacts is not None:
            artifacts.append(artifact)
        if not attach:
            continue
        try:
            if path.stat().st_size > MAX_OUTPUT_FILE_BYTES:
                messages.append(
                    f"`{path.name}` was saved locally, but exceeds Tag’s "
                    f"{MAX_OUTPUT_FILE_BYTES // (1024 * 1024)} MiB upload limit. "
                    "Use its local Open button, or ask Tag to create a smaller copy."
                )
                continue
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
            if uploaded_paths is not None:
                uploaded_paths.add(path)
            artifact["delivery"] = "uploaded"
            if permalink:
                artifact["url"] = permalink
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
    error_reference: str | None = None,
) -> list[dict[str, Any]]:
    metadata = {
        "team": team,
        "channel": channel,
        "thread_ts": thread_ts,
        "request_ts": request_ts,
    }
    if direct_message:
        metadata["direct_message"] = True
    if error_reference:
        metadata["error_reference"] = error_reference
    return [{
        "type": "actions",
        "elements": [{
            "type": "button",
            "action_id": RETRY_ACTION_ID,
            "text": {"type": "plain_text", "text": "Retry"},
            "value": json.dumps(metadata, separators=(",", ":")),
        }],
    }]


def approval_button_blocks(
    *,
    team: str,
    channel: str,
    thread_ts: str,
    user_id: str,
    approval_id: str,
    label: str,
    backend: str = "codex",
    choices: list[dict[str, Any]] | None = None,
    review_details: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    retry = label == "retry an action denied by automatic review"
    safe_labels = {
        "retry an action denied by automatic review",
        "run a command outside the workspace sandbox",
        "change files outside the workspace sandbox",
        "use additional filesystem or network access",
        "apply a file change that requires approval",
        "run a command that requires approval",
        "use a tool that requires approval",
    }
    action = label if label in safe_labels else "perform an action outside its current permissions"
    metadata = json.dumps(
        {
            "team": team,
            "channel": channel,
            "thread_ts": thread_ts,
            "user": user_id,
            "approval_id": approval_id,
        },
        separators=(",", ":"),
    )
    if choices is not None and not retry:
        blocks = [{"type": "section", "text": {"type": "plain_text", "text":
            f"{backend_display_name(backend)} needs approval to {action}. Choose the scope you want to allow."}}]
        for choice in choices:
            detail = choice.get("detail", "")
            if detail:
                blocks.append({"type": "section", "text": {
                    "type": "plain_text", "text": choice["label"] + ": " + detail,
                }})
            value = json.loads(metadata)
            value["choice"] = choice["id"]
            button = {
                "type": "button", "action_id": APPROVAL_CHOICE_ACTION_PREFIX + choice["id"],
                "text": {"type": "plain_text", "text": choice["label"]},
                "value": json.dumps(value, separators=(",", ":")),
            }
            if choice.get("persistent"):
                button["confirm"] = {
                    "title": {"type": "plain_text", "text": f"Save {backend_display_name(backend)} rule?"},
                    "text": {"type": "plain_text", "text": detail},
                    "confirm": {"type": "plain_text", "text": "Save rule"},
                    "deny": {"type": "plain_text", "text": "Back"},
                }
            # Keep each rule description immediately above its own button.
            blocks.append({"type": "actions", "elements": [button]})
        return blocks
    details = sanitize_review_details(review_details)
    if retry:
        intro = [
            {"type": "header", "text": {"type": "plain_text", "text": "Approval needed"}},
            {"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": [
                {"type": "text", "text": "Requested action\n", "style": {"bold": True}},
                {"type": "text", "text": details["action"]},
            ]}]},
            {"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": [
                {"type": "text", "text": f"Why {backend_display_name(backend)} blocked it\n", "style": {"bold": True}},
                {"type": "text", "text": details["reason"]},
            ]}]},
            {"type": "context", "elements": [{"type": "plain_text", "text":
                "One retry only • Automatic review still applies • Expires when this task ends"}]},
        ]
    else:
        intro = [{"type": "section", "text": {"type": "mrkdwn", "text":
            f"*{backend_display_name(backend)} needs approval* to {action}. Approve only if you expect this request."}}]
    return intro + [
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": APPROVAL_APPROVE_ACTION_ID,
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Approve retry" if retry else "Approve once"},
                    "value": metadata,
                },
                {
                    "type": "button",
                    "action_id": APPROVAL_DENY_ACTION_ID,
                    "style": "danger",
                    "text": {"type": "plain_text", "text": "Dismiss" if retry else "Deny"},
                    "value": metadata,
                },
            ],
        },
    ]


def failure_action_blocks(
    *,
    team: str,
    channel: str,
    thread_ts: str,
    request_ts: str,
    error_reference: str,
    direct_message: bool = False,
) -> list[dict[str, Any]]:
    """Render recovery actions without putting diagnostic content in Slack metadata."""
    retry = retry_button_blocks(
        team=team,
        channel=channel,
        thread_ts=thread_ts,
        request_ts=request_ts,
        direct_message=direct_message,
        error_reference=error_reference,
    )[0]["elements"][0]
    reference = json.dumps({"reference": error_reference}, separators=(",", ":"))
    blocks = [{
        "type": "actions",
        "elements": [
            retry,
            {
                "type": "button",
                "action_id": FIX_WITH_AGENT_ACTION_ID,
                "text": {"type": "plain_text", "text": "Fix with coding agent"},
                "value": reference,
            },
            {
                "type": "button",
                "action_id": REPORT_ISSUE_ACTION_ID,
                "text": {"type": "plain_text", "text": "Report issue"},
                "value": reference,
            },
        ],
    }]
    return blocks


def user_facing_failure(
    detail: str,
    timeout: int,
    error_reference: str,
    max_timeout: int | None = None,
    backend_code: str | None = None,
) -> str:
    """Turn private backend diagnostics into stable, actionable Slack copy."""
    if detail.startswith("Stopped.") or detail.startswith("Stop requested"):
        return detail
    classification = classify_failure(detail, backend_code)
    lowered = detail.lower()
    cause = classification.explanation
    if classification.category == "idle_timeout" and "no backend activity" in lowered:
        cause = f"The coding backend stopped after {timeout} seconds without backend activity."
    elif classification.category == "maximum_runtime" and max_timeout is not None:
        cause = f"The coding backend reached Tag's maximum runtime of {max_timeout} seconds."
    elif classification.category == "idle_timeout" and "timed out" in lowered:
        cause = f"The coding backend timed out after {timeout} seconds."
    return (
        "Tag couldn't complete this request.\n"
        f"*Cause:* {cause}\n\n"
        "Please retry, troubleshoot with your coding agent, or report this in "
        f"<{COMMUNITY_INVITE_URL}|Hover Community> so the developers can help.\n\n"
        f"Error reference: `{error_reference}`"
    )


def _modal_text(value: str, limit: int = 2_900) -> str:
    if len(value) <= limit:
        return value
    suffix = "\n[Text truncated; use the report reference to request it again]"
    return value[: max(0, limit - len(suffix))].rstrip() + suffix


def report_preview_modal(
    report: ErrorReport,
    *,
    private_metadata: dict[str, str],
) -> dict[str, Any]:
    """Build a Slack modal using selectable text; Slack has no clipboard button."""
    return {
        "type": "modal",
        "callback_id": REPORT_VIEW_ID,
        "title": {"type": "plain_text", "text": "Report issue"},
        "submit": {"type": "plain_text", "text": "Review report"},
        "close": {"type": "plain_text", "text": "Close"},
        "private_metadata": json.dumps(private_metadata, separators=(",", ":")),
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": "Copy your report, join Hover Community, and share it with the developers. Review the report before sharing. Nothing has been sent yet."}},
            {
                "type": "input",
                "block_id": "tag_report_text",
                "label": {"type": "plain_text", "text": "Sanitized report"},
                "hint": {
                    "type": "plain_text",
                    "text": "Slack does not provide a clipboard action here. Select the text to copy it.",
                },
                "element": {
                    "type": "plain_text_input",
                    "action_id": "report_text",
                    "multiline": True,
                    "initial_value": _modal_text(report.report_text()),
                    "max_length": 3_000,
                },
            },
            {
                "type": "input",
                "block_id": "tag_user_context",
                "optional": True,
                "label": {"type": "plain_text", "text": "What were you trying to do?"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "user_context",
                    "multiline": True,
                    "max_length": 1_200,
                },
            },
            {
                "type": "actions",
                "elements": [{
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Join Hover Community"},
                    "url": COMMUNITY_INVITE_URL,
                    "action_id": JOIN_COMMUNITY_ACTION_ID,
                }],
            },
        ],
    }


def _view_input_value(view: dict[str, Any], block_id: str, action_id: str) -> str:
    values = view.get("state", {}).get("values", {})
    block = values.get(block_id, {}) if isinstance(values, dict) else {}
    action = block.get(action_id, {}) if isinstance(block, dict) else {}
    value = action.get("value") if isinstance(action, dict) else ""
    return value if isinstance(value, str) else ""


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


def newly_invited_channel_allowed(channel: str, client: Any) -> bool:
    """Confirm a joined channel while invitation-memory polling catches up."""
    if os.getenv("SLACK_CHANNEL_POLICY") != "invited":
        return False
    try:
        conversation = client.conversations_info(channel=channel).get("channel") or {}
        return isinstance(conversation, dict) and conversation.get("is_member") is True
    except Exception:  # noqa: BLE001 - a failed Slack check must fail closed
        return False


def direct_messages_enabled() -> bool:
    """Enable authorized DM invocation unless the operator explicitly disables it."""
    return env_enabled("OPENTAG_SLACK_DM_ENABLED", default=True)


def slack_conversation_allowed(channel: str, *, direct_message: bool = False) -> bool:
    """Apply the channel allowlist to channels and the DM switch to direct messages."""
    return direct_messages_enabled() if direct_message else slack_channel_allowed(channel)


def is_direct_message_channel(channel: str) -> bool:
    """Recognize Slack's stable DM conversation ID prefix for events without channel_type."""
    return channel.startswith("D")


def post_backend_approval(
    client: Any,
    *,
    team: str,
    channel: str,
    thread_ts: str,
    user_id: str,
    approval: dict[str, Any],
    backend: str = "codex",
) -> Any:
    """Show an approval only to its requester, except in an already-private DM."""
    message = {
        "channel": channel,
        "thread_ts": thread_ts,
        "text": f"{backend_display_name(backend)} needs your approval to continue.",
        "blocks": approval_button_blocks(
            team=team,
            channel=channel,
            thread_ts=thread_ts,
            user_id=user_id,
            approval_id=approval["approval_id"],
            label=approval["label"],
            backend=backend,
            choices=approval.get("choices"),
            review_details=approval.get("review_details"),
        ),
    }
    if is_direct_message_channel(channel):
        return client.chat_postMessage(**message)
    return client.chat_postEphemeral(user=user_id, **message)


post_codex_approval = post_backend_approval


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
        from tag_paths import instance_home
    except ImportError:
        from scripts import tag_config as settings
        from scripts.tag_paths import instance_home
    value = ",".join(channel_ids)
    settings.update_config(settings.config_path(instance_home()), {"SLACK_CHANNEL_IDS": value})
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
        "claude": (
            "Claude Agent SDK"
            if rich_events_selected("claude")
            else "claude -p --dangerously-skip-permissions"
        ),
        "codex": (
            "codex app-server --stdio"
            if rich_events_selected("codex")
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
    report_store: ErrorReportStore | None = None,
    activity_store: ActivityStore | None = None,
    peers: list[tag_handoff.Peer] | None = None,
    handoff_store: tag_handoff.HandoffStore | None = None,
) -> App:
    retire_slack_settings()
    if max_timeout is None:
        max_timeout = int(os.getenv("OPENTAG_MAX_TIMEOUT_SECONDS", "3600"))
    selected_team = os.getenv("SLACK_TEAM_ID", "").strip()
    selected_enterprise = os.getenv("SLACK_ENTERPRISE_ID", "").strip()
    selected_app = os.getenv("SLACK_APP_ID", "").strip()

    def workspace_boundary(body, context, next):
        if not slack_identity.event_allowed(body, selected_team, selected_enterprise, selected_app):
            return BoltResponse(status=200, body="")
        if selected_team:
            body["team_id"] = selected_team
            if body.get("type") in {"block_actions", "view_submission", "view_closed", "shortcut", "message_action"} and body.get("team") is None:
                body["team"] = {"id": selected_team}
        if selected_enterprise:
            context["team_id"] = selected_team
            context.client.default_params["team_id"] = selected_team
        return next()

    token = require_env("SLACK_BOT_TOKEN")
    if selected_enterprise:
        identity = slack_identity.validate(token, team_id=selected_team, app_id=selected_app,
            enterprise_id=selected_enterprise, label="Bot token", api=slack_channels.slack_api)

        def authorize(enterprise_id, team_id, user_id):
            if enterprise_id not in (None, selected_enterprise) or team_id not in (None, selected_team):
                return None
            return AuthorizeResult(enterprise_id=selected_enterprise, team_id=selected_team,
                bot_token=token, bot_id=identity.get("bot_id"), bot_user_id=identity.get("user_id"))

        app = App(client=WebClient(token=token, team_id=selected_team),
                  authorize=authorize, before_authorize=workspace_boundary)
    else:
        app = App(token=token, before_authorize=workspace_boundary)
    report_store = report_store or default_report_store()
    activity_store = activity_store or ActivityStore()
    if peers is None:
        try:
            peers = tag_handoff.parse_peers(os.getenv("OPENTAG_PEER_TAGS", ""))
        except ValueError as exc:
            raise RuntimeError(f"OPENTAG_PEER_TAGS is invalid: {exc}") from None
    # Other Tags whose bot messages this Tag accepts; everything else from bots is ignored.
    peer_ids = frozenset(peer.user_id for peer in peers)
    handoff_store = handoff_store or tag_handoff.HandoffStore(instance_home() / "state" / "handoffs")
    handled_peer_requests: set[tuple[str, str]] = set()
    handled_peer_lock = threading.Lock()
    fallback_reports: dict[str, ErrorReport] = {}

    def stored_report(reference: str) -> ErrorReport | None:
        report = report_store.get(reference)
        if report is not None:
            return report
        fallback = fallback_reports.get(reference)
        if fallback is not None and report_store.is_available(fallback):
            return fallback
        fallback_reports.pop(reference, None)
        return None

    def remember_fallback(report: ErrorReport) -> None:
        fallback_reports[report.reference] = report
        while len(fallback_reports) > report_store.max_records:
            fallback_reports.pop(next(iter(fallback_reports)))

    def authorized_report(
        body: dict[str, Any],
        logger: Any,
    ) -> tuple[ErrorReport | None, str]:
        user_id = body.get("user", {}).get("id", "")
        if not isinstance(user_id, str) or not slack_user_allowed(user_id, allowed_user_ids):
            return None, user_id
        try:
            value = body["actions"][0]["value"]
            metadata = json.loads(value)
            reference = metadata["reference"]
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            logger.warning("Ignoring malformed Tag report action")
            return None, user_id
        if not isinstance(reference, str):
            return None, user_id
        report = stored_report(reference)
        if report is None:
            return None, user_id
        origin = report.origin
        if origin.requester_id and origin.requester_id != user_id:
            logger.warning("Ignoring Tag report action from a different Slack caller")
            return None, user_id
        action_channel = body.get("channel", {}).get("id")
        if action_channel and action_channel != origin.channel_id:
            logger.warning("Ignoring Tag report action from a different Slack channel")
            return None, user_id
        action_team = body.get("team", {}).get("id")
        if action_team and origin.team_id and action_team != origin.team_id:
            logger.warning("Ignoring Tag report action from a different Slack workspace")
            return None, user_id
        if origin.channel_id and not slack_conversation_allowed(
            origin.channel_id,
            direct_message=is_direct_message_channel(origin.channel_id),
        ):
            return None, user_id
        return report, user_id

    def report_action_failure(
        client: Any,
        report: ErrorReport | None,
        user_id: str,
        body: dict[str, Any],
        logger: Any,
    ) -> None:
        if report is None:
            channel = body.get("channel", {}).get("id", "")
            if isinstance(channel, str) and slack_conversation_allowed(
                channel,
                direct_message=is_direct_message_channel(channel),
            ):
                try:
                    client.chat_postEphemeral(
                        channel=channel,
                        user=user_id,
                        text="That Tag report is no longer available. Run the request again to create a fresh reference.",
                    )
                except Exception:  # noqa: BLE001 - a stale-report notice must not stop the action handler
                    logger.debug("Could not post stale Tag report notice")

    def open_report_modal(
        body: dict[str, Any],
        client: Any,
        logger: Any,
    ) -> None:
        report, user_id = authorized_report(body, logger)
        if report is None:
            report_action_failure(client, report, user_id, body, logger)
            return
        trigger_id = body.get("trigger_id")
        if not isinstance(trigger_id, str) or not trigger_id:
            logger.warning("Tag report action did not include a Slack trigger id")
            return
        metadata = {
            "reference": report.reference,
            "channel": report.origin.channel_id,
            "thread_ts": report.origin.thread_ts,
            "user": user_id,
        }
        try:
            client.views_open(
                trigger_id=trigger_id,
                view=report_preview_modal(
                    report,
                    private_metadata=metadata,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - recovery UI must not stop the listener
            logger.warning("Could not open Tag report preview: %s", exc)

    def submit_report_preview(
        ack: Any,
        body: dict[str, Any],
        client: Any,
        logger: Any,
    ) -> None:
        user_id = body.get("user", {}).get("id", "")
        try:
            metadata = json.loads(body["view"]["private_metadata"])
            reference = metadata["reference"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            ack(response_action="errors", errors={"tag_report_text": "This report is unavailable."})
            return
        report = stored_report(reference) if isinstance(reference, str) else None
        if (
            not isinstance(user_id, str)
            or not slack_user_allowed(user_id, allowed_user_ids)
            or report is None
            or not report.origin.requester_id
            or report.origin.requester_id != user_id
        ):
            ack(response_action="errors", errors={"tag_report_text": "This report is unavailable."})
            return
        if report.origin.channel_id and not slack_conversation_allowed(
            report.origin.channel_id,
            direct_message=is_direct_message_channel(report.origin.channel_id),
        ):
            ack(response_action="errors", errors={"tag_report_text": "This channel is no longer allowed."})
            return
        supplied = _view_input_value(body["view"], "tag_report_text", "report_text")
        context = sanitize_user_context(_view_input_value(body["view"], "tag_user_context", "user_context"))
        original = report.report_text()
        if not supplied or supplied == _modal_text(original):
            supplied = report.report_text(context)
        elif context:
            context_block = f"User-provided context:\n{context}"
            if context_block not in supplied:
                supplied = f"{supplied.rstrip()}\n\n{context_block}"
        ack()
        text = (
            "Report preview — nothing has been sent. Join Hover Community and share this reviewed text with the developers.\n\n"
            + supplied
        )
        try:
            client.chat_postEphemeral(
                channel=report.origin.channel_id,
                user=user_id,
                thread_ts=report.origin.thread_ts,
                text=text,
            )
        except Exception as exc:  # noqa: BLE001 - preserve the submitted preview locally
            logger.warning("Could not post Tag report preview: %s", exc)

    @app.action(REPORT_ISSUE_ACTION_ID)
    def report_issue_action(ack: Any, body: dict[str, Any], client: Any, logger: Any) -> None:
        ack()
        open_report_modal(body, client, logger)

    @app.action(FIX_WITH_AGENT_ACTION_ID)
    def fix_with_agent_action(ack: Any, body: dict[str, Any], client: Any, logger: Any) -> None:
        ack()
        report, user_id = authorized_report(body, logger)
        if report is None:
            report_action_failure(client, report, user_id, body, logger)
            return
        try:
            message = {
                "channel": report.origin.channel_id,
                "thread_ts": report.origin.thread_ts,
                "text": build_troubleshooting_prompt(report),
            }
            if is_direct_message_channel(report.origin.channel_id):
                client.chat_postMessage(**message)
            else:
                client.chat_postEphemeral(user=user_id, **message)
        except Exception as exc:  # noqa: BLE001 - recovery UI must not stop the listener
            logger.warning("Could not show private Tag troubleshooting prompt: %s", exc)

    @app.action(JOIN_COMMUNITY_ACTION_ID)
    def join_community_action(ack: Any) -> None:
        ack()

    @app.view(REPORT_VIEW_ID)
    def submit_report_view(ack: Any, body: dict[str, Any], client: Any, logger: Any) -> None:
        submit_report_preview(ack, body, client, logger)

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
            pending_session_api, pending_legacy_status = clear_slack_session(
                client, channel, thread_ts, logger
            )
            if not pending_session_api and not pending_legacy_status:
                if session_journal is not None:
                    try:
                        session_journal.remove(str(team), channel, thread_ts)
                    except OSError as exc:
                        logger.warning("Could not update the Slack session journal: %s", exc)
            elif session_journal is not None:
                try:
                    session_journal.add(
                        str(team),
                        channel,
                        thread_ts,
                        pending_session_api=pending_session_api,
                        pending_legacy_status=pending_legacy_status,
                    )
                except OSError as exc:
                    logger.warning("Could not record the orphaned Slack session: %s", exc)
    models = discover_tag_models(backend)
    if os.getenv("TAG_INSTANCE_HOME"):
        remember_model_names(models, model_names_path(os.environ["TAG_INSTANCE_HOME"]))
    default_settings = default_agent_settings(models)

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

    @app.action(re.compile(r"^" + APPROVAL_CHOICE_ACTION_PREFIX + r"[0-9]{1,2}$"))
    @app.action(APPROVAL_APPROVE_ACTION_ID)
    @app.action(APPROVAL_DENY_ACTION_ID)
    def resolve_backend_approval(
        ack: Any,
        body: dict[str, Any],
        client: Any,
        logger: Any,
        respond: Any,
    ) -> None:
        ack()
        user_id = body.get("user", {}).get("id", "")
        channel = body.get("channel", {}).get("id", "")
        body_team = body.get("team", {}).get("id", "") or body.get("team_id", "")
        try:
            action = body["actions"][0]
            metadata = json.loads(action["value"])
            team = metadata["team"]
            expected_channel = metadata["channel"]
            thread_ts = metadata["thread_ts"]
            expected_user = metadata["user"]
            approval_id = metadata["approval_id"]
            action_id = action.get("action_id", "")
            if not isinstance(action_id, str):
                raise ValueError("invalid approval action")
            choice = metadata.get("choice")
            if action_id.startswith(APPROVAL_CHOICE_ACTION_PREFIX):
                if (not isinstance(choice, str) or not re.fullmatch(r"[0-9]{1,2}", choice)
                        or action_id != APPROVAL_CHOICE_ACTION_PREFIX + choice):
                    raise ValueError("invalid approval choice")
            elif choice is not None or action_id not in {APPROVAL_APPROVE_ACTION_ID, APPROVAL_DENY_ACTION_ID}:
                raise ValueError("invalid approval action")
            approved = action_id == APPROVAL_APPROVE_ACTION_ID
            if not all(
                isinstance(value, str) and value
                for value in (
                    team,
                    expected_channel,
                    thread_ts,
                    expected_user,
                    approval_id,
                )
            ):
                raise ValueError("invalid approval metadata")
            if (
                body_team != team
                or channel != expected_channel
                or user_id != expected_user
                or not slack_user_allowed(user_id, allowed_user_ids)
                or not slack_conversation_allowed(
                    channel,
                    direct_message=is_direct_message_channel(channel),
                )
            ):
                client.chat_postEphemeral(
                    channel=channel or expected_channel,
                    user=user_id,
                    thread_ts=thread_ts,
                    text="Only the authorized user who started this request can decide it.",
                )
                return
            if not resolve_active_approval(
                RunKey(team, channel, thread_ts),
                approval_id,
                approved=approved,
                choice=choice,
            ):
                respond(
                    text="This approval request has expired or was already decided.",
                    response_type="ephemeral",
                    replace_original=True,
                )
                return
            result = "Approved once" if approved else "Denied"
            text = ("Your choice was sent to Tag." if choice is not None
                    else f"{result}. Tag is continuing.")
            respond(
                text=text,
                blocks=[{
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": text},
                }],
                response_type="ephemeral",
                replace_original=True,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Could not resolve backend approval from Slack: %s", exc)

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
            client.views_open(trigger_id=body["trigger_id"], view=retired_settings_modal())
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Could not open Open Tag settings modal: %s", exc)

    @app.action(ACTIVITY_ACTION_ID)
    def open_activity(ack: Any, body: dict[str, Any], client: Any, logger: Any) -> None:
        ack()
        try:
            metadata = json.loads(body["actions"][0]["value"])
            user_id = body["user"]["id"]
            channel = metadata["channel"]
            thread_ts = metadata["thread_ts"]
            run_id = metadata["run_id"]
            if not all(isinstance(value, str) for value in (
                user_id, channel, thread_ts, run_id, metadata["team"],
            )):
                return
            if not slack_user_allowed(user_id, allowed_user_ids):
                return
            if not slack_conversation_allowed(
                channel, direct_message=is_direct_message_channel(channel),
            ):
                return
            action_channel = body.get("channel", {}).get("id") or body.get("container", {}).get("channel_id")
            action_team = body.get("team", {}).get("id") or body.get("team_id")
            if (action_channel and action_channel != channel) or (action_team and action_team != metadata["team"]):
                return
            record = activity_store.get(run_id)
            if record is None:
                client.chat_postEphemeral(
                    channel=channel, user=user_id, thread_ts=thread_ts,
                    text="Activity for this request is no longer available.",
                )
                return
            if (
                record["requester"] != user_id
                or record["team"] != metadata["team"]
                or record["channel"] != channel
                or record["thread_ts"] != thread_ts
            ):
                logger.warning("Ignoring Tag activity action with mismatched request identity")
                return
            client.views_open(trigger_id=body["trigger_id"], view=activity_modal(record))
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Could not open Tag activity modal: %s", exc)

    @app.action(ACTIVITY_DETAIL_ACTION_ID)
    def open_activity_detail(ack: Any, body: dict[str, Any], client: Any, logger: Any) -> None:
        ack()
        try:
            metadata = json.loads(body["view"]["private_metadata"])
            user_id = body["user"]["id"]
            channel = metadata["channel"]
            team = metadata["team"]
            thread_ts = metadata["thread_ts"]
            run_id = metadata["run_id"]
            index = int(body["actions"][0]["value"])
            if not all(isinstance(value, str) for value in (
                user_id, channel, team, thread_ts, run_id,
            )) or index < 0:
                return
            if not slack_user_allowed(user_id, allowed_user_ids) or not slack_conversation_allowed(
                channel, direct_message=is_direct_message_channel(channel),
            ):
                return
            action_team = body.get("team", {}).get("id") or body.get("team_id")
            if action_team and action_team != team:
                return
            record = activity_store.get(run_id)
            if record is None or not (
                record["requester"] == user_id
                and record["team"] == team
                and record["channel"] == channel
                and record["thread_ts"] == thread_ts
                and index < len(record["events"])
            ):
                return
            client.views_push(trigger_id=body["trigger_id"], view=activity_detail_modal(record, index))
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Could not open Tag activity detail: %s", exc)

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

    # Old messages and already-open modals must never restore retired overrides.
    @app.action(SETTINGS_MODEL_ACTION_ID)
    @app.action(SETTINGS_EFFORT_ACTION_ID)
    @app.action(SETTINGS_FAST_ACTION_ID)
    @app.action(SETTINGS_RESET_ACTION_ID)
    def acknowledge_retired_settings(ack: Any) -> None:
        ack()

    @app.view(SETTINGS_VIEW_ID)
    def dismiss_retired_settings(ack: Any) -> None:
        ack(response_action="update", view=retired_settings_modal())

    def update_handoff_status(client: Any, logger: Any, handoff_id: str) -> None:
        record = handoff_store.get(handoff_id)
        if record is None or not record.get("status_ts"):
            return
        try:
            client.chat_update(
                channel=record["origin_channel"], ts=record["status_ts"], text=tag_handoff.status_text(record)
            )
        except Exception as exc:  # noqa: BLE001 - progress text must not stop the handoff
            logger.warning("Could not update Tag handoff status %s: %s", handoff_id, exc)

    def start_handoff(
        client: Any, logger: Any, team: str, channel: str, thread_ts: str,
        requester: str, question: str, request: dict[str, Any],
    ) -> None:
        targets = [target for target in request["targets"] if target.get("user_id") in peer_ids]
        if not targets:
            return
        handoff_id = tag_handoff.new_handoff_id()
        wait_minutes = min(
            max(int(request.get("wait_minutes") or tag_handoff.DEFAULT_WAIT_MINUTES), 1),
            tag_handoff.MAX_WAIT_MINUTES,
        )
        # Save the wait before posting so an immediate reply always finds it.
        handoff_store.create(
            handoff_id=handoff_id, team=team, requester=requester, origin_channel=channel,
            origin_thread_ts=thread_ts, question=question, task=request["task"], targets=targets,
            request_ts="", wait_minutes=wait_minutes,
        )
        status = client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=tag_handoff.status_text(handoff_store.get(handoff_id) or {}),
        )
        handoff_store.set_field(handoff_id, "status_ts", status["ts"])
        try:
            posted = client.chat_postMessage(
                channel=channel, text=tag_handoff.request_text(handoff_id, targets, request["task"], requester),
            )
        except Exception as exc:  # noqa: BLE001 - report instead of waiting for nobody
            logger.warning("Could not post Tag handoff %s: %s", handoff_id, exc)
            handoff_store.finish(handoff_id, "failed")
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts, text="I couldn't post the request to the other Tags.",
            )
            return
        handoff_store.set_field(handoff_id, "request_ts", posted["ts"])

    def combine_handoff(record: dict[str, Any], client: Any, logger: Any) -> None:
        update_handoff_status(client, logger, record["id"])
        handle_invocation(
            {
                "channel": record["origin_channel"],
                "ts": record["origin_thread_ts"],
                "thread_ts": record["origin_thread_ts"],
                "user": record["requester"],
                "text": tag_handoff.combine_question(record),
            },
            {"team_id": record["team"]},
            client,
            logger,
            direct_message=False,
            combine=record,
        )

    def expire_handoffs(client: Any, logger: Any) -> None:
        for record in handoff_store.claim_expired():
            threading.Thread(
                target=combine_handoff, args=(record, client, logger),
                name=f"tag-handoff-{record['id']}", daemon=True,
            ).start()

    def handle_peer_message(event: dict[str, Any], body: dict[str, Any], client: Any, logger: Any) -> None:
        sender = event.get("user", "")
        if sender not in peer_ids:
            logger.info("Ignoring a bot mention from %s, which is not a peer Tag", sender or "(unknown)")
            return
        text = event.get("text", "")
        result = tag_handoff.RESULT_RE.search(text)
        if result:
            handoff_id, outcome = result.groups()
            record = handoff_store.get(handoff_id)
            if (
                record is None
                or record["origin_channel"] != event.get("channel")
                or event.get("thread_ts") != record["request_ts"]
            ):
                return
            record, ready = handoff_store.record_reply(handoff_id, sender, outcome, event.get("ts", ""))
            update_handoff_status(client, logger, handoff_id)
            if ready and record is not None:
                combine_handoff(record, client, logger)
            return
        request = tag_handoff.REQUEST_RE.search(text)
        if not request or event.get("thread_ts"):
            return
        handoff_id, requester = request.groups()
        with handled_peer_lock:
            key = (event.get("channel", ""), event.get("ts", ""))
            if key in handled_peer_requests:
                return
            handled_peer_requests.add(key)
        if not slack_user_allowed(requester, allowed_user_ids):
            client.chat_postMessage(
                channel=event["channel"],
                thread_ts=event["ts"],
                text=f"{tag_handoff.result_prefix(handoff_id, sender, 'failed')} "
                f"<@{requester}> isn't allowed to use this Tag.",
            )
            return
        peer_event = {key: value for key, value in event.items() if key not in {"bot_id", "bot_profile"}}
        peer_event.update(user=requester, text=tag_handoff.task_from_request(text))
        handle_invocation(peer_event, body, client, logger, direct_message=False, peer_request=(handoff_id, sender))

    def handle_invocation(
        event: dict[str, Any],
        body: dict[str, Any],
        client: Any,
        logger: Any,
        *,
        direct_message: bool,
        prior_error_reference: str | None = None,
        peer_request: tuple[str, str] | None = None,
        combine: dict[str, Any] | None = None,
    ) -> None:
        channel = event["channel"]
        thread_ts = event.get("thread_ts") or event["ts"]
        # Answering another Tag or combining replies never starts another handoff.
        handoff_depth = 1 if peer_request or combine else 0
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
        try:
            validate_attachment_metadata(
                message_files(event)
            )
        except AttachmentLimitError as exc:
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=str(exc),
            )
            return
        team = body.get("team_id") or event.get("team") or ""
        question = strip_mention(event.get("text", ""))
        configured_team = os.getenv("SLACK_TEAM_ID", "").strip()
        policy_team = team if not configured_team or configured_team == team else ""
        configured_channels = os.getenv("SLACK_CHANNEL_IDS", "").strip()
        if not configured_channels:
            configured_channels = os.getenv("SLACK_CHANNEL_ID", "").strip()
        scope_plan = plan_search_scopes(
            request_text=question,
            current_channel_id=channel,
            caller_id=user_id,
            team_id=policy_team,
            configured_channels=configured_channels,
            allowed_scopes=os.getenv("MFS_ALLOWED_SCOPES", ""),
            client=client,
            intent=SearchIntent("current"),
        )
        agent_settings = default_settings
        # Every requester uses this Tag's configured model and thinking level.
        request_backend = agent_settings.backend or backend
        request_started = time.monotonic()
        reported_runs: list[dict[str, str]] = []
        indicator = WorkingIndicator(
            client,
            channel,
            thread_ts,
            logger,
            journal=session_journal,
            team=team,
        )
        indicator.start()
        slack_search_grant = plan_search_scopes(
            request_text=question,
            current_channel_id=channel,
            caller_id=user_id,
            team_id=policy_team,
            configured_channels=configured_channels,
            allowed_scopes=os.getenv("MFS_ALLOWED_SCOPES", ""),
            client=client,
            intent=SearchIntent("all"),
        )
        answer_stream: SlackAnswerStream | None = None
        backend_error_code: str | None = None
        backend_error_events: list[str] = []
        failure_stage = "request preparation"
        activity_run_id: str | None = None

        def trace_activity(trace_event: dict[str, Any]) -> None:
            nonlocal activity_run_id
            if activity_run_id is not None:
                try:
                    activity_store.observe(activity_run_id, trace_event)
                except OSError as exc:
                    logger.warning("Could not save Tag activity: %s", exc)
                    activity_run_id = None
            if answer_stream is not None:
                answer_stream.activity(trace_event)

        def capture_run_info(info: dict[str, str]) -> None:
            reported_runs.append(info)
            if activity_run_id is not None:
                option = reported_model_option(info["model"], models, request_backend)
                try:
                    activity_store.save_model(activity_run_id, request_backend, info["model"],
                                              option.label if option else info["model"],
                                              reasoning_effort=info.get("reasoning_effort"))
                except OSError:
                    logger.warning("Could not save the activity model")

        def finish_activity(outcome: str) -> None:
            nonlocal activity_run_id
            if activity_run_id is None:
                return
            try:
                activity_store.finish(activity_run_id, outcome)
            except OSError as exc:
                logger.warning("Could not finish Tag activity: %s", exc)
                activity_run_id = None

        def capture_backend_error(code: str | None, _detail: str) -> None:
            nonlocal backend_error_code
            if code:
                backend_error_code = code
            classification = classify_failure("", code)
            if classification.backend_code:
                event = f"Observed backend error code: {classification.backend_code}."
            else:
                event = "Observed a backend failure event."
            if event not in backend_error_events:
                backend_error_events.append(event)

        def attach_activity_error(reference: str) -> None:
            if activity_run_id is not None:
                try:
                    activity_store.attach_error(activity_run_id, reference)
                except OSError:
                    logger.warning("Could not link the error report to Tag activity")
        output_manifest = (
            default_workdir() / f"{OUTPUT_ARTIFACT_MANIFEST_PREFIX}{uuid.uuid4().hex}.json"
        )
        memory_receipts = tag_temp_dir() / f"memory-receipts-{uuid.uuid4().hex}.jsonl"
        handoff_requests = (
            tag_temp_dir() / f"handoff-request-{uuid.uuid4().hex}.jsonl"
            if peer_ids and handoff_depth == 0 and not direct_message
            else None
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
                thread_text = build_thread_text(
                    client, channel, thread_ts, attachment_dir, request=None if combine else event,
                )
                if combine:
                    replies = build_thread_text(client, channel, combine["request_ts"], attachment_dir)
                    thread_text = f"{thread_text}\n\nHandoff thread with the other Tags' replies:\n{replies}"
                failure_stage = "backend execution"
                stream_available = (
                    env_enabled("OPENTAG_SLACK_STREAMING", default=True)
                    and indicator.native
                    and indicator.message_ts is None
                    # A reply to another Tag must be one new message that mentions it.
                    and peer_request is None
                )
                app_server_selected = rich_events_selected(request_backend)
                if app_server_selected:
                    try:
                        activity_run_id = activity_store.create(
                            team=team, channel=channel, thread_ts=thread_ts,
                            request_ts=event["ts"], requester=user_id,
                            reasoning_effort=agent_settings.reasoning_effort,
                        )
                    except OSError as exc:
                        logger.warning("Could not start Tag activity record: %s", exc)
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
                        request_backend,
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
                        on_approval=lambda approval: post_backend_approval(
                            client,
                            team=team,
                            channel=channel,
                            thread_ts=thread_ts,
                            user_id=user_id,
                            approval=approval,
                            backend=request_backend,
                        ),
                        fast_mode=agent_settings.fast_mode,
                        output_manifest=output_manifest,
                        max_timeout=max_timeout,
                        scope_plan=scope_plan,
                        slack_search_grant=slack_search_grant,
                        on_error=capture_backend_error,
                        on_trace_event=trace_activity if app_server_selected else None,
                        on_run_info=capture_run_info,
                        memory_receipts=memory_receipts,
                        handoff_requests=handoff_requests,
                        handoff_depth=handoff_depth,
                    )
                else:
                    answer, succeeded = run_backend(
                        request_backend,
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
                        scope_plan=scope_plan,
                        slack_search_grant=slack_search_grant,
                        on_error=capture_backend_error,
                        memory_receipts=memory_receipts,
                        handoff_requests=handoff_requests,
                        handoff_depth=handoff_depth,
                    )
                finish_activity(
                    "completed" if succeeded else
                    "interrupted" if answer.startswith(("Stopped.", "Stop requested")) else "failed"
                )
                artifact_button_blocks: list[dict[str, Any]] = []
                delivered_artifacts: list[dict[str, str]] = []
                if succeeded:
                    artifact_paths, _artifact_errors = load_output_artifacts(
                        output_manifest,
                        default_workdir(),
                    )
                    uploaded_paths: set[Path] = set()
                    delivery_messages = deliver_output_artifacts(
                        client,
                        channel,
                        thread_ts,
                        output_manifest,
                        default_workdir(),
                        logger,
                        uploaded_paths=uploaded_paths,
                        artifacts=delivered_artifacts,
                        on_upload_start=lambda: indicator.status(
                            "Uploading the result…"
                        ),
                    )
                    if delivery_messages:
                        answer = f"{answer.rstrip()}\n\n" + "\n".join(delivery_messages)
                    if activity_run_id is not None and delivered_artifacts:
                        try:
                            activity_store.save_artifacts(activity_run_id, delivered_artifacts)
                        except OSError:
                            logger.warning("Could not save output artifacts to Tag activity")
                    artifact_button_blocks = output_artifact_button_blocks(
                        artifact_paths,
                        default_workdir(),
                        uploaded_paths=uploaded_paths,
                        user_id=user_id,
                        channel=channel,
                        thread_ts=thread_ts,
                    )
                memory_changes = tag_memory.receipt_lines(memory_receipts)
                if succeeded and memory_changes:
                    answer = f"{answer.rstrip()}\n\n" + "\n".join(memory_changes)
                indicator.clear()
                stopped = answer.startswith(("Stopped.", "Stop requested"))
                if peer_request is not None and (succeeded or stopped):
                    handoff_id, sender = peer_request
                    outcome = "completed" if succeeded else "failed"
                    answer = f"{tag_handoff.result_prefix(handoff_id, sender, outcome)}\n\n{answer}"

                summary_blocks = run_summary_blocks(
                    agent_settings, models, request_backend, time.monotonic() - request_started,
                    outcome="completed" if succeeded else "stopped" if stopped else "failed",
                    reported_model=reported_runs[-1]["model"] if reported_runs else None,
                    reported_effort=reported_runs[-1].get("reasoning_effort") if reported_runs else None,
                )
                if succeeded:
                    footer_blocks = summary_blocks + (artifact_button_blocks or [])
                elif stopped:
                    footer_blocks = summary_blocks
                else:
                    error_reference = new_error_reference()
                    attach_activity_error(error_reference)
                    logger.error("Tag backend failure [%s]: %s", error_reference, answer)
                    failure_at = utc_timestamp()
                    report = make_error_report(
                        error_reference,
                        answer,
                        backend=request_backend,
                        backend_code=backend_error_code,
                        failed_stage=failure_stage,
                        related_reference=prior_error_reference,
                        origin=ReportOrigin(
                            team_id=team,
                            channel_id=channel,
                            thread_ts=thread_ts,
                            request_ts=event["ts"],
                            requester_id=user_id,
                        ),
                        error_events=tuple(backend_error_events),
                        health_checks=collect_health_checks(),
                        failure_at=failure_at,
                    )
                    try:
                        report_store.save(report)
                    except OSError as exc:
                        remember_fallback(report)
                        logger.warning("Could not persist Tag error report [%s]: %s", error_reference, exc)
                    answer = user_facing_failure(
                        answer,
                        timeout,
                        error_reference,
                        max_timeout,
                        backend_error_code,
                    )
                    footer_blocks = summary_blocks + failure_action_blocks(
                        team=team,
                        channel=channel,
                        thread_ts=thread_ts,
                        request_ts=event["ts"],
                        error_reference=error_reference,
                        direct_message=direct_message,
                    )
                streamed = (
                    answer_stream is not None
                    and succeeded
                    and answer_stream.finish(answer, footer_blocks)
                )
                if not streamed:
                    if answer_stream is not None:
                        outcome = "interrupted" if answer.startswith(("Stopped.", "Stop requested")) else "unknown" if succeeded else "failed"
                        answer_stream.abort(outcome)
                    if peer_request is not None and (succeeded or stopped):
                        # Editing a placeholder does not notify the other Tag; post anew.
                        post_final_reply(client, channel, thread_ts, answer, None, footer_blocks)
                        if indicator.message_ts is not None:
                            client.chat_delete(channel=channel, ts=indicator.message_ts)
                    elif succeeded or stopped:
                        post_final_reply(client, channel, thread_ts, answer, indicator.message_ts, footer_blocks)
                    else:
                        post_private_failure(
                            client, channel, thread_ts, user_id, answer, indicator.message_ts, footer_blocks
                        )
                        if peer_request is not None:
                            handoff_id, sender = peer_request
                            client.chat_postMessage(
                                channel=channel,
                                thread_ts=thread_ts,
                                text=f"{tag_handoff.result_prefix(handoff_id, sender, 'failed')} "
                                "Tag couldn't complete this request.",
                            )
                if succeeded and handoff_requests is not None:
                    request = tag_handoff.read_request(handoff_requests)
                    if request is not None:
                        start_handoff(client, logger, team, channel, thread_ts, user_id, question, request)
                if combine is not None:
                    handoff_store.finish(combine["id"], "completed" if succeeded else "failed")
                    update_handoff_status(client, logger, combine["id"])
                if memory_changes and not succeeded:
                    client.chat_postMessage(
                        channel=channel, thread_ts=thread_ts, text="\n".join(memory_changes)
                    )
                if succeeded:
                    upload_errors = upload_generated_images(
                        client,
                        channel,
                        thread_ts,
                        image_results_dir,
                        artifacts=delivered_artifacts,
                    )
                    if activity_run_id is not None:
                        try:
                            activity_store.save_artifacts(activity_run_id, delivered_artifacts)
                            activity_store.save_reply(activity_run_id, answer)
                            summary_answer = answer
                            if upload_errors:
                                summary_answer += "\n\nSome generated images could not be attached to Slack."
                            queue_reply_summary(activity_store, activity_run_id, summary_answer,
                                                request_backend,
                                                reported_runs[-1]["model"] if reported_runs else agent_settings.model)
                        except OSError:
                            logger.warning("Could not save the reply preview to Tag activity")
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
        except AttachmentLimitError as exc:
            finish_activity("failed")
            indicator.clear()
            if answer_stream is not None:
                answer_stream.abort("failed")
            post_private_failure(
                client,
                channel,
                thread_ts,
                user_id,
                str(exc),
                indicator.message_ts,
                None,
            )
        except Exception as exc:
            finish_activity("failed")
            error_reference = new_error_reference()
            attach_activity_error(error_reference)
            logger.exception("Open Tag failed [%s]", error_reference)
            indicator.clear()
            if answer_stream is not None:
                answer_stream.abort("failed")
            failure_at = utc_timestamp()
            report = make_error_report(
                error_reference,
                f"{type(exc).__name__}: {exc}",
                backend=request_backend,
                backend_code=backend_error_code,
                failed_stage=failure_stage,
                related_reference=prior_error_reference,
                origin=ReportOrigin(
                    team_id=team,
                    channel_id=channel,
                    thread_ts=thread_ts,
                    request_ts=event["ts"],
                    requester_id=user_id,
                ),
                error_events=tuple(backend_error_events),
                health_checks=collect_health_checks(),
                failure_at=failure_at,
            )
            try:
                report_store.save(report)
            except OSError as save_exc:
                remember_fallback(report)
                logger.warning("Could not persist Tag error report [%s]: %s", error_reference, save_exc)
            answer = user_facing_failure(
                f"{type(exc).__name__}: {exc}",
                timeout,
                error_reference,
                max_timeout,
                backend_error_code,
            )
            footer_blocks = failure_action_blocks(
                team=team,
                channel=channel,
                thread_ts=thread_ts,
                request_ts=event["ts"],
                error_reference=error_reference,
                direct_message=direct_message,
            )
            post_private_failure(
                client,
                channel,
                thread_ts,
                user_id,
                answer,
                indicator.message_ts,
                footer_blocks,
            )
        finally:
            output_manifest.unlink(missing_ok=True)
            memory_receipts.unlink(missing_ok=True)
            if handoff_requests is not None:
                handoff_requests.unlink(missing_ok=True)

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
                prior_error_reference=(
                    metadata.get("error_reference")
                    if isinstance(metadata.get("error_reference"), str)
                    else None
                ),
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
        if not (
            slack_conversation_allowed(channel)
            or newly_invited_channel_allowed(channel, client)
        ):
            logger.warning("Ignoring Open Tag mention from unapproved Slack channel %s", channel)
            return
        if event.get("bot_id") or event.get("user") in peer_ids:
            handle_peer_message(event, body, client, logger)
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

    app.tag_expire_handoffs = expire_handoffs
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
    # Cosmetic profile refresh must never block Socket Mode heartbeats or tasks.
    try:
        from .slack_profile_icon import watch as watch_avatar
        from .tag_paths import instance_home
    except ImportError:
        from slack_profile_icon import watch as watch_avatar
        from tag_paths import instance_home
    avatar_worker = None
    if os.getenv("SLACK_BOT_TOKEN"):
        avatar_worker = threading.Thread(target=watch_avatar, args=(instance_home(), shutdown_requested),
                                         name="slack-avatar", daemon=True)
        avatar_worker.start()
    invitation_memory = None
    if os.getenv("SLACK_CHANNEL_POLICY") == "invited":
        try:
            from .slack_invitation_memory import InvitationMemory
            from .tag_paths import instance_home
        except ImportError:
            from slack_invitation_memory import InvitationMemory
            from tag_paths import instance_home
        invitation_memory = InvitationMemory(instance_home())
        invitation_memory.start()
    next_handoff_check = 0.0
    try:
        handler.connect()
        while not shutdown_requested.is_set():
            if time.monotonic() >= next_handoff_check:
                next_handoff_check = time.monotonic() + 30
                try:
                    app.tag_expire_handoffs(app.client, app.logger)
                except Exception as exc:  # noqa: BLE001 - keep the bridge running
                    app.logger.warning("Could not check Tag handoff deadlines: %s", exc)
            if args.ready_file:
                invitation_ready = (
                    invitation_memory is None
                    or invitation_memory.ready_for_requests()
                )
                if handler.client.is_connected() and invitation_ready:
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
        shutdown_requested.set()
        if avatar_worker:
            avatar_worker.join(timeout=1)
        if invitation_memory:
            invitation_memory.stop()
        if args.ready_file:
            args.ready_file.unlink(missing_ok=True)
        handler.close()
        session_journal.reconcile(app.client, app.logger)


if __name__ == "__main__":
    main()
