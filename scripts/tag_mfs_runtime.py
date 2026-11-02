"""Identity-verified migration to the rate-aware managed memory runtime."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from tag_process_identity import reported_by, runs_script
except ImportError:
    from scripts.tag_process_identity import reported_by, runs_script

VERSION = 3
SERVER = "tag_mfs_server.py"


def active(shared: Path, process) -> bool:
    if process is None:
        return False
    try:
        record = json.loads((shared / "mfs.json").read_text(encoding="utf-8"))
        ready = json.loads((shared / "slack-runtime-ready-v1.json").read_text(encoding="utf-8"))
        return (
            record.get("slack_runtime_version") == VERSION
            and ready.get("version") == VERSION
            and ready.get("instance_id") == record.get("instance_id")
            and bool(ready.get("instance_id"))
            and runs_script(process, SERVER)
            and reported_by(process, ready.get("pid"), SERVER)
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return False
