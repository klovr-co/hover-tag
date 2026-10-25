#!/usr/bin/env python3
# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""Write the Tag.app update manifests for a newly published release.

Tag.app checks `tag-app-CHANNEL.json` on the `channels` release for the line
its own version belongs to. A release updates every line at or below its own
stability: a stable release reaches stable, beta, and alpha users; a beta
reaches beta and alpha; an alpha reaches alpha only. A manifest only ever
moves forward, so publishing an older fix never downgrades anyone.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

try:
    from tag_install import release_version_key
except ImportError:
    from scripts.tag_install import release_version_key

REPOSITORY = "klovr-co/hover-tag"
# Updater artifact suffix for each Tauri platform key. The macOS build is universal.
PLATFORMS = {
    "darwin-aarch64": "macos.app.tar.gz",
    "darwin-x86_64": "macos.app.tar.gz",
    "windows-x86_64": "windows-setup.exe",
    "linux-x86_64": "linux-x86_64.AppImage",
}


def channels_for(version: str) -> list[str]:
    if "-alpha" in version:
        return ["alpha"]
    if "-beta" in version:
        return ["beta", "alpha"]
    return ["stable", "beta", "alpha"]


def manifest(version: str, directory: Path, *, notes: str = "", published: str | None = None) -> dict:
    """The manifest for one release, from its signed updater artifacts in `directory`."""
    platforms = {}
    for platform, suffix in PLATFORMS.items():
        name = f"Tag-{version}-{suffix}"
        signature = directory / f"{name}.sig"
        if not signature.is_file():
            continue  # A platform that failed to build gets no update from this release.
        platforms[platform] = {
            "signature": signature.read_text(encoding="utf-8").strip(),
            "url": f"https://github.com/{REPOSITORY}/releases/download/v{version}/{name}",
        }
    if not platforms:
        raise ValueError(f"No signed Tag.app updates found for {version} in {directory}")
    return {
        "version": version,
        "notes": notes or f"Tag {version}",
        "pub_date": published or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": platforms,
    }


def merge(existing: dict | None, new: dict) -> dict | None:
    """The manifest to publish for one line, or None to leave it unchanged."""
    if not existing:
        return new
    old_version = str(existing.get("version", "0.0.0"))
    if release_version_key(new["version"]) < release_version_key(old_version):
        return None
    if new["version"] == old_version:
        # A rebuilt platform joins the platforms already published for this version.
        return {**new, "platforms": {**existing.get("platforms", {}), **new["platforms"]}}
    # Never mix versions: an older build listed under a newer version would be
    # offered again forever. A platform that failed to build waits for the next one.
    return new


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--signatures", type=Path, required=True, help="folder with Tag-VERSION-*.sig files")
    parser.add_argument("--current", type=Path, required=True, help="folder with the published tag-app-*.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--notes", default="")
    args = parser.parse_args()
    version = args.version.removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+(-(alpha|beta)(\.\d+)?)?", version):
        raise ValueError(f"Not a release version: {version}")
    new = manifest(version, args.signatures, notes=args.notes)
    args.output.mkdir(parents=True, exist_ok=True)
    for channel in channels_for(version):
        name = f"tag-app-{channel}.json"
        current = args.current / name
        existing = json.loads(current.read_text(encoding="utf-8")) if current.is_file() else None
        result = merge(existing, new)
        if result is None:
            print(f"{name}: kept {existing['version']}, newer than {version}")
            continue
        (args.output / name).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"{name}: {version} for {', '.join(sorted(result['platforms']))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
