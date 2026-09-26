#!/usr/bin/env python3
"""Record a requested output file for host-local access and optional delivery."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import time
from pathlib import Path


LOCK_ATTEMPTS = 100
LOCK_RETRY_SECONDS = 0.05


def channel_artifact_directory(workdir: Path, channel_id: str, *, create: bool = False) -> Path:
    """Stable per-channel output location without changing the agent workspace."""
    if not re.fullmatch(r"[CDG][A-Z0-9]+", channel_id):
        raise ValueError("artifact channel must be a Slack channel or conversation ID")
    root = workdir.expanduser().resolve()
    directory = root / "artifacts" / channel_id
    for path in (directory.parent, directory):
        if path.is_symlink():
            raise ValueError(f"artifact directory must not be a symlink: {path}")
        if path.exists() and not path.is_dir():
            raise ValueError(f"artifact directory conflicts with an existing file: {path}")
        if create:
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


def validated_output_path(raw_path: Path, workdir: Path) -> Path:
    path = raw_path.expanduser()
    if not path.is_absolute():
        path = workdir / path
    path = path.resolve(strict=True)
    root = workdir.expanduser().resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("output must be inside the configured workspace") from exc
    if not path.is_file():
        raise ValueError("output must be a regular file")
    return path


def _update_manifest(manifest: Path, path: Path, *, attach: bool) -> None:
    """Read, update, and atomically replace an artifact manifest."""
    try:
        existing = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        existing = []
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("the output artifact manifest is unreadable") from exc
    if not isinstance(existing, list) or not all(
        isinstance(item, str)
        or (
            isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and isinstance(item.get("attach"), bool)
        )
        for item in existing
    ):
        raise ValueError("the output artifact manifest is invalid")
    value = str(path)
    normalized = [
        {"path": item, "attach": True}
        if isinstance(item, str)
        else {"path": item["path"], "attach": item["attach"]}
        for item in existing
    ]
    recorded = next((item for item in normalized if item["path"] == value), None)
    if recorded is None:
        normalized.append({"path": value, "attach": attach})
    elif attach:
        # A later explicit attachment request upgrades a local-only record.
        recorded["attach"] = True

    manifest.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{manifest.name}.", dir=manifest.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(normalized, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, manifest)
    finally:
        Path(temporary).unlink(missing_ok=True)


def record_artifact(manifest: Path, path: Path, *, attach: bool = False) -> None:
    """Record one artifact while serializing concurrent manifest writers."""
    manifest.parent.mkdir(parents=True, exist_ok=True)
    lock = manifest.with_suffix(manifest.suffix + ".lock")
    for _attempt in range(LOCK_ATTEMPTS):
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            time.sleep(LOCK_RETRY_SECONDS)
            continue
        os.close(descriptor)
        break
    else:
        raise ValueError("the output artifact manifest is busy")

    try:
        _update_manifest(manifest, path, attach=attach)
    finally:
        lock.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mark a requested output for local access and optional Slack attachment."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--channel-id", help="resolve relative output filenames in this channel's artifact folder")
    parser.add_argument(
        "--attach",
        action="store_true",
        help="also attach the output to the originating Slack thread",
    )
    args = parser.parse_args()
    try:
        output = args.file
        if args.channel_id:
            directory = channel_artifact_directory(args.workdir, args.channel_id)
            if not output.is_absolute():
                output = validated_output_path(output, directory)
        path = validated_output_path(output, args.workdir)
        record_artifact(args.manifest, path, attach=args.attach)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(path.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
