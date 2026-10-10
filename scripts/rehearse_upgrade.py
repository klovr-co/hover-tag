#!/usr/bin/env python3
"""Check release assets with the installer of an earlier release.

Installed Tag versions verify a release's provenance with their own code and
cannot be updated to relax it. Run this before publishing, against a checkout
of the previous stable release, so a release those installs would reject never
reaches a channel.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import sys
from pathlib import Path


def rehearse(previous_checkout: Path, directory: Path, version: str, channel: str = "release") -> str:
    """Return the commit SHA the earlier installer accepts, or raise ValueError."""
    sys.path.insert(0, str(previous_checkout))
    sys.path.insert(0, str(previous_checkout / "scripts"))
    try:
        install = importlib.import_module("scripts.tag_install")
    finally:
        del sys.path[:2]
    archive = directory / f"tag-{version}.zip"
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    return install._verify_provenance(
        (directory / "BUILD-PROVENANCE.json").read_bytes(),
        expected_channel=channel, archive_name=archive.name, digest=digest, version=version,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-checkout", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        commit = rehearse(args.previous_checkout, args.directory, args.version)
    except ValueError as error:
        print(f"The previous release's installer rejects {args.version}: {error}", file=sys.stderr)
        return 1
    print(f"The previous release's installer accepts {args.version} ({commit}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
