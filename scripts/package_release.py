#!/usr/bin/env python3
"""Build a reproducible source archive consumed by Tag installers."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path


SYMLINK_MODE = 0o120000
GITLINK_MODE = 0o160000


def _source_epoch(root: Path) -> int:
    configured = os.environ.get("SOURCE_DATE_EPOCH")
    if configured is not None:
        return int(configured)
    return int(subprocess.check_output(
        ["git", "show", "-s", "--format=%ct", "HEAD"], cwd=root, text=True
    ).strip())


def _tracked_files(root: Path) -> list[tuple[str, int]]:
    output = subprocess.check_output(["git", "ls-files", "-s", "-z"], cwd=root)
    tracked: list[tuple[str, int]] = []
    for entry in output.decode().split("\0"):
        if not entry:
            continue
        metadata, name = entry.split("\t", 1)
        mode = int(metadata.split()[0], 8)
        tracked.append((name, mode))
    return sorted(tracked)


def build_archive(root: Path, archive: Path, epoch: int | None = None) -> str:
    """Write tracked working-tree files with stable metadata and return SHA-256."""
    timestamp = time.gmtime(epoch if epoch is not None else _source_epoch(root))[:6]
    # ZIP cannot represent timestamps before 1980.
    timestamp = max(timestamp, (1980, 1, 1, 0, 0, 0))
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, git_mode in _tracked_files(root):
            # Installers reject symlink entries, and gitlinks contain no file data.
            if git_mode in {SYMLINK_MODE, GITLINK_MODE}:
                continue
            path = root / name
            if not path.is_file():
                raise FileNotFoundError(
                    f"tracked file missing from the working tree: {name}"
                )
            info = zipfile.ZipInfo(name, timestamp)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = ((0o755 if git_mode & 0o111 else 0o644) & 0xFFFF) << 16
            bundle.writestr(info, path.read_bytes())
    return hashlib.sha256(archive.read_bytes()).hexdigest()


def _git_value(root: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=root, text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("out"))
    parser.add_argument("--channel", choices=("release", "edge"), default="release")
    parser.add_argument("--commit-sha")
    parser.add_argument("--source-ref")
    parser.add_argument("--built-at")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = (root / "VERSION").read_text().strip()
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / ("tag-edge.zip" if args.channel == "edge" else f"tag-{version}.zip")
    digest = build_archive(root, archive)
    (args.output / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    if args.channel == "edge":
        if not args.commit_sha or not args.source_ref:
            parser.error("edge packages require --commit-sha and --source-ref")
    commit_sha = args.commit_sha or _git_value(root, "rev-parse", "HEAD")
    source_ref = args.source_ref or "refs/heads/main"
    built_at = args.built_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    provenance = {
        "schema_version": 1,
        "channel": args.channel,
        "commit_sha": commit_sha,
        "built_at": built_at,
        "source_ref": source_ref,
        "version": version,
        "archive": {"name": archive.name, "sha256": digest},
    }
    (args.output / "BUILD-PROVENANCE.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(archive)


if __name__ == "__main__":
    main()
