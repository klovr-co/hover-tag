#!/usr/bin/env python3
"""Record an explicitly requested output file for bridge-managed delivery."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path


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


def record_artifact(manifest: Path, path: Path) -> None:
    try:
        existing = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        existing = []
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("the output artifact manifest is unreadable") from exc
    if not isinstance(existing, list) or not all(isinstance(item, str) for item in existing):
        raise ValueError("the output artifact manifest is invalid")
    value = str(path)
    if value not in existing:
        existing.append(value)

    manifest.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{manifest.name}.", dir=manifest.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(existing, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, manifest)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mark an explicitly requested file for delivery to Slack."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    try:
        path = validated_output_path(args.file, args.workdir)
        record_artifact(args.manifest, path)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(path.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
