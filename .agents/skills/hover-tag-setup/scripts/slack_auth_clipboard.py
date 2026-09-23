#!/usr/bin/env python3
"""Complete Slack CLI's one-time login exchange through the local clipboard.

The script deliberately emits only state metadata and status. Ticket and
challenge values never appear in stdout or stderr.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


TICKET_LINE = re.compile(r"(?m)^\s*/slackauthticket\s+(\S+)\s*$")


def emit(**payload: object) -> None:
    print(json.dumps(payload, separators=(",", ":")))


def clipboard_command(*, write: bool) -> list[str] | None:
    if sys.platform == "darwin":
        return ["pbcopy" if write else "pbpaste"]
    if os.name == "nt":
        return ["clip.exe"] if write else [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "Get-Clipboard -Raw",
        ]
    candidates = (
        (["wl-copy"], ["wl-paste", "--no-newline"]),
        (["xclip", "-selection", "clipboard"], ["xclip", "-selection", "clipboard", "-o"]),
        (["xsel", "--clipboard", "--input"], ["xsel", "--clipboard", "--output"]),
    )
    for writer, reader in candidates:
        if shutil.which(writer[0]) and shutil.which(reader[0]):
            return writer if write else reader
    return None


def write_clipboard(value: str) -> None:
    command = clipboard_command(write=True)
    if not command:
        raise RuntimeError("No supported local clipboard command is available")
    subprocess.run(command, input=value, text=True, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def read_clipboard() -> str:
    command = clipboard_command(write=False)
    if not command:
        raise RuntimeError("No supported local clipboard command is available")
    result = subprocess.run(command, text=True, check=True, capture_output=True)
    return result.stdout.strip()


def slack_command() -> str:
    executable = shutil.which("slack")
    if not executable:
        raise RuntimeError("Slack CLI is not installed")
    return executable


def validated_state_path(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    expected_parent = Path(tempfile.gettempdir()).resolve()
    if resolved.parent != expected_parent or not re.fullmatch(
        r"hover-tag-slack-auth-[A-Za-z0-9._-]+\.json", resolved.name
    ):
        raise RuntimeError("Slack authorization state path is invalid")
    return resolved


def begin() -> int:
    try:
        slack = slack_command()
        result = subprocess.run(
            [slack, "auth", "login", "--no-prompt", "--skip-update", "--no-color"],
            text=True, capture_output=True, check=False,
        )
        match = TICKET_LINE.search("\n".join((result.stdout, result.stderr)))
        if result.returncode or not match:
            raise RuntimeError("Slack CLI could not start the one-time connection")
        ticket = match.group(1)
        write_clipboard(f"/slackauthticket {ticket}")
        handle, raw_path = tempfile.mkstemp(prefix="hover-tag-slack-auth-", suffix=".json")
        path = Path(raw_path)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as state:
                json.dump({"slack": slack, "ticket": ticket}, state)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        emit(ok=True, state="waiting_for_slack", state_file=str(path))
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        emit(ok=False, state="not_started", error=str(exc))
        return 1


def complete(path: Path) -> int:
    try:
        path = validated_state_path(path)
    except (OSError, RuntimeError) as exc:
        emit(ok=False, state="not_authorized", error=str(exc))
        return 1
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        slack, ticket = saved["slack"], saved["ticket"]
        challenge = read_clipboard()
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,128}", challenge):
            raise RuntimeError("The clipboard does not contain a Slack challenge code")
        result = subprocess.run(
            [slack, "auth", "login", "--ticket", ticket, "--challenge", challenge,
             "--skip-update", "--no-color"],
            text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("Slack rejected or expired the one-time connection")
        emit(ok=True, state="authorized")
        return 0
    except (KeyError, json.JSONDecodeError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        emit(ok=False, state="not_authorized", error=str(exc))
        return 1
    finally:
        path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("begin")
    finish = subparsers.add_parser("complete")
    finish.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    return begin() if args.command == "begin" else complete(args.state)


if __name__ == "__main__":
    raise SystemExit(main())
