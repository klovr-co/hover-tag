#!/usr/bin/env python3
"""Apply OpenTag canary policy before entering the pinned ZulipMCP listener."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable


def _topic_key(message: dict[str, Any]) -> tuple[str, str]:
    return (
        str(message.get("display_recipient", "")).lower(),
        str(message.get("subject", "")).lower(),
    )


def active_session_count(listener: Any) -> int:
    return sum(1 for process in listener._sessions.values() if process.poll() is None)


def notify_capacity(listener: Any, config: Any, message: dict[str, Any], limit: int) -> None:
    payload = {
        "type": "stream",
        "to": message.get("stream_id") or message.get("display_recipient"),
        "topic": message.get("subject", ""),
        "content": (
            f"Open Tag is already using its Zulip canary session limit ({limit}). "
            "End the active session with :stop_sign: or try again shortly."
        ),
    }
    try:
        client = listener.zulip.Client(config_file=str(config.zuliprc))
        client.send_message(payload)
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps({"type": "opentag.capacity_notice_error", "error": str(exc)}),
            file=sys.stderr,
        )


def install_session_limit(
    listener: Any,
    *,
    max_sessions: int,
    on_capacity: Callable[[Any, dict[str, Any], int], None] | None = None,
) -> Callable[[Any, dict[str, Any]], None]:
    if max_sessions < 1:
        raise ValueError("max_sessions must be a positive integer")
    original = listener._spawn
    listener._opentag_original_spawn = original
    capacity_handler = on_capacity or (
        lambda config, message, limit: notify_capacity(listener, config, message, limit)
    )

    def limited_spawn(config: Any, message: dict[str, Any]) -> None:
        key = _topic_key(message)
        process = listener._sessions.get(key)
        if process is not None and process.poll() is None:
            original(config, message)
            return
        if active_session_count(listener) >= max_sessions:
            print(
                json.dumps(
                    {
                        "type": "opentag.session_capacity",
                        "limit": max_sessions,
                        "stream": message.get("display_recipient"),
                        "topic": message.get("subject"),
                    }
                ),
                file=sys.stderr,
            )
            capacity_handler(config, message, max_sessions)
            return
        original(config, message)

    listener._spawn = limited_spawn
    return original


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ZulipMCP with OpenTag canary policy.")
    parser.add_argument("--zuliprc", required=True, type=Path)
    parser.add_argument("--backend", choices=["claude", "codex"], required=True)
    parser.add_argument("--mcp-config", required=True, type=Path)
    parser.add_argument("--system-prompt", required=True, type=Path)
    parser.add_argument("--working-dir", required=True, type=Path)
    parser.add_argument("--log-dir", required=True, type=Path)
    parser.add_argument(
        "--codex-permission-mode",
        choices=["parity", "workspace-write", "read-only", "none"],
        default="workspace-write",
    )
    parser.add_argument("--max-sessions", type=int, default=1)
    parser.add_argument("backend_flags", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    from zulipmcp import listener

    flags = args.backend_flags or []
    if flags and flags[0] == "--":
        flags = flags[1:]
    install_session_limit(listener, max_sessions=args.max_sessions)
    config = listener.Config(
        zuliprc=args.zuliprc,
        backend=args.backend,
        mcp_config=args.mcp_config,
        system_prompt=args.system_prompt,
        working_dir=args.working_dir,
        log_dir=args.log_dir,
        codex_permission_mode=args.codex_permission_mode,
        backend_flags=flags,
    )
    listener.run(config)


if __name__ == "__main__":
    main()
