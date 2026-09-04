#!/usr/bin/env python3
from __future__ import annotations

import argparse
import mimetypes
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from opentag_process_env import backend_environment


MENTION_RE = re.compile(r"<@[^>]+>")
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_ATTACHMENT_TEXT_CHARS = 12_000


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
                request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
                with urllib.request.urlopen(request, timeout=30) as response, target.open("wb") as output:
                    content_type = response.headers.get_content_type()
                    if content_type and not content_type.startswith("image/"):
                        raise ValueError(f"Slack returned {content_type}, not an image")
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > MAX_ATTACHMENT_BYTES:
                            raise ValueError("image exceeds the 15 MB safety limit")
                        output.write(chunk)
                detected_type = mimetypes.guess_type(target.name)[0] or mime_type
                lines.append(f"[Slack image attachment: {name} ({detected_type}) at {target}]")
            except (OSError, urllib.error.URLError, ValueError) as exc:
                target.unlink(missing_ok=True)
                lines.append(f"[Could not retrieve Slack image {name}: {exc}]")
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
    lines.extend(download_thread_images(messages, attachment_dir))
    return "\n".join(lines)


def run_backend(
    backend: str,
    channel: str,
    caller_id: str,
    question: str,
    thread_text: str,
    attachment_dir: Path,
    timeout: int,
) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as f:
        f.write(thread_text)
        thread_file = Path(f.name)

    cmd = [
        "python3",
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
        if len(output) > 3_800:
            output = output[:3_750] + "\n\n[Response truncated; ask OpenMax to continue.]"
        return output or "Open Tag finished without output."
    finally:
        try:
            thread_file.unlink()
        except OSError:
            pass


def suggested_bot_name(backend: str) -> str:
    if os.getenv("OPENTAG_BOT_NAME"):
        return os.environ["OPENTAG_BOT_NAME"]
    return {"claude": "OpenClaude", "codex": "OpenCodex"}.get(backend, "OpenTag")


def slack_channel_allowed(channel: str) -> bool:
    """Restrict Slack execution to the configured channel when one is set."""
    allowed_channel = os.getenv("SLACK_CHANNEL_ID", "").strip()
    return not allowed_channel or channel == allowed_channel


def print_live_summary(backend: str) -> None:
    bot = suggested_bot_name(backend)
    scopes = [s.strip() for s in os.getenv("MFS_ALLOWED_SCOPES", "").split(",") if s.strip()]
    channel = os.getenv("SLACK_CHANNEL_ID", "").strip() or "(any joined channel)"
    invoke = {
        "claude": "claude -p --dangerously-skip-permissions",
        "codex": "codex exec --approve-for-me",
    }[backend]

    print("=" * 64)
    print(f"Open Tag is live as @{bot}")
    print(f"  Brain   : {backend}  ->  {invoke}")
    print(f"  Memory  : {len(scopes)} permitted MFS scope(s):")
    for scope in scopes or ["(none — set MFS_ALLOWED_SCOPES)"]:
        print(f"            - {scope}")
    print(f"  Slack   : listening for @mentions in channel {channel}")
    print("")
    print("  Anyone who can @mention the bot in that channel can drive the")
    print("  backend, which runs with your shell and inherited environment.")
    print("  Slack text flows into the prompt, so treat every mention as")
    print("  untrusted input: use an isolated channel on a non-production host.")
    print(f"  Invite the bot only where it should respond: /invite @{bot}")
    print("=" * 64)


def create_app(backend: str, timeout: int) -> App:
    app = App(token=require_env("SLACK_BOT_TOKEN"))

    @app.event("app_mention")
    def handle_mention(event: dict[str, Any], client: Any, logger: Any) -> None:
        channel = event["channel"]
        if not slack_channel_allowed(channel):
            logger.warning("Ignoring Open Tag mention from unapproved Slack channel %s", channel)
            return
        thread_ts = event.get("thread_ts") or event["ts"]
        question = strip_mention(event.get("text", ""))

        status = client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"Open Tag is working on this with `{backend}`.",
        )

        try:
            with tempfile.TemporaryDirectory(prefix="opentag-slack-") as raw_attachment_dir:
                attachment_dir = Path(raw_attachment_dir)
                thread_text = build_thread_text(client, channel, thread_ts, attachment_dir)
                answer = run_backend(
                    backend,
                    channel,
                    event.get("user", ""),
                    question,
                    thread_text,
                    attachment_dir,
                    timeout,
                )
            client.chat_update(
                channel=channel,
                ts=status["ts"],
                text=answer,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Open Tag failed")
            answer = f"Open Tag failed: `{type(exc).__name__}: {exc}`"
            client.chat_update(channel=channel, ts=status["ts"], text=answer)

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
    args = parser.parse_args()
    if not args.backend:
        parser.error("--backend or OPENTAG_BACKEND is required")

    app = create_app(args.backend, args.timeout)
    print_live_summary(args.backend)
    SocketModeHandler(app, require_env("SLACK_APP_TOKEN")).start()


if __name__ == "__main__":
    main()
