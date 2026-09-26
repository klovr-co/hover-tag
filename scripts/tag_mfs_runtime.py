"""Identity-verified migration to the rate-aware managed memory runtime."""
from __future__ import annotations

import json
from pathlib import Path

VERSION = 1


def active(shared: Path, process) -> bool:
    if process is None:
        return False
    try:
        record = json.loads((shared / "mfs.json").read_text())
        ready = json.loads((shared / "slack-runtime-ready-v1.json").read_text())
        return (
            record.get("slack_runtime_version") == VERSION
            and ready.get("version") == VERSION
            and ready.get("pid") == process.pid
            and ready.get("instance_id") == record.get("instance_id")
            and bool(ready.get("instance_id"))
            and any(Path(arg).name == "tag_mfs_server.py" for arg in process.cmdline())
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return False
