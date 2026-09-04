#!/usr/bin/env python3
"""Run Open Tag as a mention-driven bot in Zulip streams and direct messages."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from opentag_process_env import backend_environment


MAX_THREAD_MESSAGES = 30
MAX_STREAM_CONTEXT_MESSAGES = 80
MAX_RESPONSE_CHARS = 9_000


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def skill_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def default_workdir() -> Path:
    return Path(os.getenv("OPENTAG_WORKDIR", str(Path.cwd())))


def strip_mention(text: str, bot_name: str) -> str:
    """Remove Zulip's Markdown mention for this bot without touching other mentions."""
    escaped_name = re.escape(bot_name)
    text = re.sub(rf"@\*\*{escaped_name}\*\*", "", text, flags=re.IGNORECASE)
    text = re.sub(rf"@{escaped_name}\b", "", text, flags=re.IGNORECASE)
    stripped = text.strip()
    return stripped or "Find the most relevant context for this conversation and summarize it."


def format_messages(
    messages: list[dict[str, Any]], *, include_topic: bool = False, limit: int = MAX_THREAD_MESSAGES
) -> str:
    return "\n".join(
        f"{'[' + str(message.get('subject', '')) + '] ' if include_topic else ''}"
        f"{message.get('sender_full_name') or message.get('sender_email') or 'unknown'}: "
        f"{message.get('content', '')}"
        for message in messages[-limit:]
    )


def get_topic_context(client: Any, message: dict[str, Any]) -> str:
    """Fetch a bounded transcript for the stream/topic containing the mention."""
    if message["type"] != "stream":
        return format_messages([message])

    narrow = [["stream", message["display_recipient"]], ["topic", message["subject"]]]
    response = client.get_messages(
        {
            "anchor": message["id"],
            "num_before": MAX_THREAD_MESSAGES - 1,
            "num_after": 0,
            "narrow": json.dumps(narrow),
        }
    )
    return format_messages(response.get("messages", []))


def get_stream_context(client: Any, message: dict[str, Any]) -> str:
    """Fetch recent messages across prior topics in the mentioned stream."""
    if message["type"] != "stream":
        return format_messages([message])

    response = client.get_messages(
        {
            "anchor": message["id"],
            "num_before": MAX_STREAM_CONTEXT_MESSAGES - 1,
            "num_after": 0,
            "narrow": json.dumps([["stream", message["display_recipient"]]]),
        }
    )
    return format_messages(
        response.get("messages", []), include_topic=True, limit=MAX_STREAM_CONTEXT_MESSAGES
    )


def enable_private_stream_history(admin_client: Any, message: dict[str, Any], bot_user_id: int) -> None:
    """Grant Hover Bot scoped history access after an explicit mention.

    This intentionally runs only with an administrator's separate credentials.
    It never enumerates streams: only the stream that generated the mention is
    changed and subscribed.
    """
    if message["type"] != "stream":
        return

    stream_id = message["stream_id"]
    stream_response = admin_client.call_endpoint(url=f"/streams/{stream_id}", method="GET")
    if stream_response.get("result") != "success":
        raise RuntimeError(stream_response.get("msg", "Could not inspect the mentioned stream"))
    stream = stream_response.get("stream", stream_response)
    if not stream.get("invite_only"):
        return

    if not stream.get("history_public_to_subscribers"):
        result = admin_client.call_endpoint(
            url=f"/streams/{stream_id}",
            method="PATCH",
            request={"history_public_to_subscribers": True},
        )
        if result.get("result") != "success":
            raise RuntimeError(result.get("msg", "Could not enable shared stream history"))

    result = admin_client.add_subscriptions(
        streams=[{"name": message["display_recipient"]}],
        principals=[bot_user_id],
        send_new_subscription_messages=False,
    )
    if result.get("result") != "success":
        raise RuntimeError(result.get("msg", "Could not subscribe Hover Bot to the mentioned stream"))


def run_backend(
    backend: str,
    conversation_id: str,
    caller_id: str,
    question: str,
    thread_text: str,
    timeout: int,
) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as output:
        output.write(thread_text)
        thread_file = Path(output.name)

    cmd = [
        "python3",
        str(skill_dir() / "scripts" / "opentag_agent.py"),
        "--backend",
        backend,
        "--channel-id",
        conversation_id,
        "--question",
        question,
        "--thread-file",
        str(thread_file),
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
            transport="zulip",
            conversation_id=conversation_id,
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
        if len(output) > MAX_RESPONSE_CHARS:
            output = output[:MAX_RESPONSE_CHARS] + "\n\n[Response truncated; ask OpenTag to continue.]"
        return output or "Open Tag finished without output."
    finally:
        thread_file.unlink(missing_ok=True)


def conversation_id(message: dict[str, Any]) -> str:
    if message["type"] == "stream":
        return f"stream:{message['stream_id']}:topic:{message['subject']}"
    return f"dm:{message['recipient_id']}"


def reply(client: Any, message: dict[str, Any], content: str) -> int:
    if message["type"] == "stream":
        request = {
            "type": "stream",
            # A message event's display_recipient is presentation data. The
            # numeric stream ID is stable and unambiguous for API replies.
            "to": message["stream_id"],
            "topic": message["subject"],
            "content": content,
        }
    else:
        recipients = message.get("display_recipient") or []
        request = {
            "type": "private",
            "to": [recipient["email"] for recipient in recipients],
            "content": content,
        }
    result = client.send_message(request)
    if result.get("result") != "success":
        raise RuntimeError(result.get("msg", "Zulip rejected the response"))
    message_id = result.get("id")
    if not isinstance(message_id, int):
        raise RuntimeError("Zulip did not return an ID for the response message")
    return message_id


def update_reply(client: Any, message_id: int, content: str) -> None:
    result = client.update_message({"message_id": message_id, "content": content})
    if result.get("result") != "success":
        raise RuntimeError(result.get("msg", "Zulip rejected the response update"))


def print_live_summary(backend: str, bot_name: str) -> None:
    scopes = [scope.strip() for scope in os.getenv("MFS_ALLOWED_SCOPES", "").split(",") if scope.strip()]
    print("=" * 64)
    print(f"Open Tag is live in Zulip as @{bot_name}")
    print(f"  Brain   : {backend}")
    print(f"  Memory  : {len(scopes)} permitted MFS scope(s)")
    print("  Zulip   : listening for direct messages and @mentions")
    print("  Security: anyone who can mention the bot can invoke the backend;")
    print("            use an isolated stream and a non-production host.")
    print("=" * 64)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger("opentag.zulip")
    parser = argparse.ArgumentParser(description="Run Open Tag as a Zulip bot.")
    parser.add_argument("--backend", choices=["claude", "codex"], default=os.getenv("OPENTAG_BACKEND"))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("OPENTAG_TIMEOUT_SECONDS", "420")))
    parser.add_argument("--config-file", default=os.getenv("ZULIP_CONFIG_FILE"))
    args = parser.parse_args()
    if not args.backend:
        parser.error("--backend or OPENTAG_BACKEND is required")
    if not args.config_file:
        parser.error("--config-file or ZULIP_CONFIG_FILE is required")

    import zulip

    client = zulip.Client(config_file=args.config_file, client="OpenTag")
    profile = client.get_profile()
    if profile.get("result") != "success":
        raise RuntimeError(profile.get("msg", "Could not load the Zulip bot profile"))
    bot_email = profile["email"]
    bot_user_id = profile["user_id"]
    bot_name = os.getenv("OPENTAG_BOT_NAME", profile["full_name"])
    auto_grant_history = os.getenv("ZULIP_AUTO_GRANT_PRIVATE_HISTORY", "").lower() in {"1", "true", "yes"}
    admin_client = None
    if auto_grant_history:
        admin_config_file = require_env("ZULIP_ADMIN_CONFIG_FILE")
        admin_client = zulip.Client(config_file=admin_config_file, client="OpenTag private-history access")
        admin_profile = admin_client.get_profile()
        if admin_profile.get("result") != "success":
            raise RuntimeError(admin_profile.get("msg", "Could not authenticate the Zulip administrator"))
        logger.info("Automatic private-stream history access is enabled")

    def handle_event(event: dict[str, Any]) -> None:
        if event.get("type") != "message":
            return
        message = event["message"]
        if message.get("sender_email") == bot_email:
            return
        is_direct_message = message.get("type") == "private"
        if not is_direct_message and "mentioned" not in event.get("flags", []):
            return

        status_message_id: int | None = None
        try:
            logger.info("Handling Zulip message id=%s type=%s", message.get("id"), message.get("type"))
            status_message_id = reply(
                client,
                message,
                f"Hover Bot is working on this with `{args.backend}`.",
            )
            if admin_client is not None:
                enable_private_stream_history(admin_client, message, bot_user_id)
            question = strip_mention(message.get("content", ""), bot_name)
            context = get_stream_context(client, message) if admin_client is not None else get_topic_context(client, message)
            answer = run_backend(
                args.backend,
                conversation_id(message),
                message.get("sender_email", ""),
                question,
                context,
                args.timeout,
            )
            try:
                update_reply(client, status_message_id, answer)
            except Exception:  # noqa: BLE001
                logger.exception("Could not update OpenTag status; posting the final answer instead")
                reply(client, message, answer)
            logger.info("Posted OpenTag reply for message id=%s", message.get("id"))
        except Exception as exc:  # noqa: BLE001
            logger.exception("OpenTag failed for message id=%s", message.get("id"))
            try:
                error_message = f"Open Tag failed: `{type(exc).__name__}: {exc}`"
                if status_message_id is not None:
                    update_reply(client, status_message_id, error_message)
                else:
                    reply(client, message, error_message)
            except Exception:  # noqa: BLE001
                logger.exception("Could not post OpenTag failure message")

    print_live_summary(args.backend, bot_name)
    client.call_on_each_event(handle_event, event_types=["message"])


if __name__ == "__main__":
    main()
