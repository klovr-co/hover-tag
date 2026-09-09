#!/usr/bin/env python3
"""Reject token-shaped secrets and generated runtime state in tracked files."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "Slack token": re.compile(rb"\bx(?:ox[baprs]|app)-[A-Za-z0-9-]{20,}\b"),
    "GitHub token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "API key": re.compile(rb"\bsk-[A-Za-z0-9]{20,}\b"),
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
FORBIDDEN_TRACKED = {".env", "opentag-slack-bridge.log", "opentag-zulip-bridge.log"}


def tracked_files(root: Path) -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
    )
    return [root / raw.decode() for raw in completed.stdout.split(b"\0") if raw]


def validate_secrets(root: Path) -> list[str]:
    errors: list[str] = []
    for path in tracked_files(root):
        relative = path.relative_to(root)
        if str(relative) in FORBIDDEN_TRACKED or ".runtime" in relative.parts:
            errors.append(f"generated or private state is tracked: {relative}")
            continue
        content = path.read_bytes()
        for label, pattern in PATTERNS.items():
            if pattern.search(content):
                errors.append(f"possible {label} in tracked file: {relative}")
    return errors


def main() -> int:
    errors = validate_secrets(ROOT)
    if errors:
        print("Secret/state check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Secret/state check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
