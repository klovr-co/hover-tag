"""Private per-instance credential files consumed by shared local services."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile


def slack_history_path(home: Path, name: str = "mfs-slack-token") -> Path:
    if not name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-." for char in name):
        raise ValueError("Invalid credential filename")
    return home / "config/credentials" / name


def write_slack_history(home: Path, token: str, *, name: str = "mfs-slack-token") -> Path:
    """Atomically rotate a Slack-history token without exposing it in argv."""
    if not token.startswith("xox") or any(char.isspace() for char in token):
        raise ValueError("Invalid Slack-history credential")
    path = slack_history_path(home, name)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".mfs-slack-", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path
