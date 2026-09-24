"""Versioned local migrations for Tag error-report storage."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


MIGRATION_VERSION = 1
MIGRATION_NAME = "tag-error-reporting"


def _marker_path(home: Path) -> Path:
    return home / "state/migrations" / f"{MIGRATION_NAME}.json"


def _already_complete(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("version", 0) >= MIGRATION_VERSION


def migrate(home: Path) -> bool:
    """Create and verify the private report directory, then record completion."""
    marker = _marker_path(home)
    if _already_complete(marker):
        return False

    configured = os.getenv("OPENTAG_ERROR_REPORTS_DIR", "").strip()
    directory = Path(configured).expanduser() if configured else home / "state/error-reports"
    if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
        raise RuntimeError(f"Tag error report storage is not a private directory: {directory}")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        directory.chmod(0o700)
    except OSError as exc:
        raise RuntimeError(f"Could not secure Tag error report storage: {directory}") from exc
    if not directory.is_dir() or directory.is_symlink():
        raise RuntimeError(f"Tag error report storage could not be verified: {directory}")

    marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        "schema_version": 1,
        "migration": MIGRATION_NAME,
        "version": MIGRATION_VERSION,
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    descriptor, temporary = tempfile.mkstemp(prefix=f".{MIGRATION_NAME}-", dir=marker.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, marker)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True
