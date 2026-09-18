#!/usr/bin/env python3
"""Build the source release archive and checksum consumed by TAG installers."""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("out"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = (root / "VERSION").read_text().strip()
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / f"tag-{version}.zip"
    # Release builds use tracked files only, never .context or private local data.
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(filter(None, tracked)):
            path = root / name
            if path.is_file():
                bundle.write(path, name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (args.output / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(archive)


if __name__ == "__main__":
    main()
