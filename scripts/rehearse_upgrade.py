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
import subprocess
import sys
from pathlib import Path

# Runs in a new process with the earlier checkout as the working directory, so
# no module this process already imported can stand in for that release's code.
VERIFY = """
import sys
from pathlib import Path
sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
import scripts.tag_install as install
if not Path(install.__file__).resolve().is_relative_to(Path.cwd().resolve()):
    sys.exit("loaded " + install.__file__ + " instead of the previous release's installer")
directory, version, digest = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
try:
    print(install._verify_provenance(
        (directory / "BUILD-PROVENANCE.json").read_bytes(), expected_channel="release",
        archive_name=f"tag-{version}.zip", digest=digest, version=version,
    ))
except ValueError as error:
    sys.exit(str(error))
"""


def rehearse(previous_checkout: Path, directory: Path, version: str) -> str:
    """Return the commit SHA the earlier installer accepts, or raise ValueError."""
    digest = hashlib.sha256((directory / f"tag-{version}.zip").read_bytes()).hexdigest()
    result = subprocess.run(
        [sys.executable, "-c", VERIFY, str(directory.resolve()), version, digest],
        cwd=previous_checkout, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "the previous installer failed to run")
    return result.stdout.strip()


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
