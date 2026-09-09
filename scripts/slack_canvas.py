#!/usr/bin/env python3
# Modified by klovr.co in 2026 for Tag. See NOTICE and repository history.
"""Create a Slack Canvas in the channel that invoked Open Tag."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


API_URL = "https://slack.com/api/conversations.canvases.create"


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def create_channel_canvas(*, title: str, markdown: str) -> dict:
    payload = {
        "channel_id": require_env("OPENTAG_CURRENT_CHANNEL_ID"),
        "title": title,
        "document_content": {"type": "markdown", "markdown": markdown},
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
        raise RuntimeError(f"Slack Canvas request failed ({exc.code}): {detail}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"Slack Canvas request failed: {result.get('error', 'unknown error')}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Canvas in Open Tag's current Slack channel.")
    parser.add_argument("--title", required=True, help="Canvas title")
    parser.add_argument("--markdown-file", type=Path, required=True, help="UTF-8 Markdown content")
    args = parser.parse_args()
    markdown = args.markdown_file.read_text(encoding="utf-8")
    if not markdown.strip():
        parser.error("--markdown-file must not be empty")
    if len(markdown) > 500_000:
        parser.error("Canvas content exceeds the 500 KB safety limit")

    result = create_channel_canvas(title=args.title, markdown=markdown)
    canvas = result.get("canvas") or {}
    print(json.dumps({
        "ok": True,
        "canvas_id": canvas.get("id") or result.get("canvas_id"),
        "url": canvas.get("url") or canvas.get("permalink"),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
