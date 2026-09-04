#!/usr/bin/env python3
"""Post a top-level message to the Slack channel that invoked Open Tag."""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request


API_URL = "https://slack.com/api/chat.postMessage"
MAX_MESSAGE_CHARS = 4_000


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def post_to_current_channel(*, text: str) -> dict:
    """Post a new channel message; the caller cannot choose another channel."""
    payload = {
        "channel": require_env("OPENTAG_CURRENT_CHANNEL_ID"),
        "text": text,
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {require_env('SLACK_BOT_TOKEN')}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Slack message request failed ({exc.code}): {detail}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"Slack message request failed: {result.get('error', 'unknown error')}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Post a top-level message to Open Tag's current Slack channel."
    )
    parser.add_argument("--text", required=True, help="Message text")
    args = parser.parse_args()
    text = args.text.strip()
    if not text:
        parser.error("--text must not be empty")
    if len(text) > MAX_MESSAGE_CHARS:
        parser.error(f"--text exceeds the {MAX_MESSAGE_CHARS:,} character safety limit")

    result = post_to_current_channel(text=text)
    message = result.get("message") or {}
    print(json.dumps({"ok": True, "channel": result.get("channel"), "ts": message.get("ts")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
