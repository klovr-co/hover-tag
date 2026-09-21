#!/usr/bin/env python3
"""Validate release metadata that must be correct before dependencies install."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
NUMBERED_ALPHA_RE = re.compile(r"^(\d+\.\d+\.\d+-alpha)\.\d+$")
MODIFICATION_NOTICE = "Modified by klovr.co in 2026 for Tag."
MODIFIED_UPSTREAM_FILES = (
    ".env.example",
    "README.md",
    "references/runtime-agent.md",
    "references/slack-adapter.md",
    "scripts/opentag_agent.py",
    "scripts/opentag_doctor.py",
    "scripts/slack_canvas.py",
    "scripts/slack_socket_agent.py",
)


def _read(root: Path, relative_path: str, errors: list[str]) -> str:
    path = root / relative_path
    if not path.is_file():
        errors.append(f"missing required file: {relative_path}")
        return ""
    return path.read_text(encoding="utf-8")


def _release_contract_version(version: str) -> str:
    """Return the source-selected version that RELEASE.md must document."""
    numbered_alpha = NUMBERED_ALPHA_RE.fullmatch(version)
    return numbered_alpha.group(1) if numbered_alpha else version


def validate_release(root: Path) -> list[str]:
    errors: list[str] = []

    version = _read(root, "VERSION", errors).strip()
    if version and not VERSION_RE.fullmatch(version):
        errors.append(f"VERSION is not a supported prerelease version: {version!r}")

    channels_text = _read(root, "release-channels.json", errors)
    if channels_text:
        try:
            channels = json.loads(channels_text)
        except json.JSONDecodeError:
            errors.append("release-channels.json is malformed")
        else:
            if (
                not isinstance(channels, dict)
                or channels.get("schema_version") != 1
                or channels.get("default_channel") not in {"stable", "beta", "alpha", "edge"}
            ):
                errors.append("release-channels.json contains an unsupported release policy")

    license_text = _read(root, "LICENSE", errors)
    for token in ("Apache License", "Version 2.0, January 2004", "END OF TERMS AND CONDITIONS"):
        if license_text and token not in license_text:
            errors.append(f"LICENSE is missing required Apache-2.0 text: {token!r}")

    notice = _read(root, "NOTICE", errors)
    for token in ("Tag", "Copyright 2026 klovr.co", "Zilliz", "Open Tag Example"):
        if notice and token not in notice:
            errors.append(f"NOTICE is missing attribution: {token!r}")

    release = _read(root, "RELEASE.md", errors)
    contract_version = _release_contract_version(version)
    for token in (
        "Slack + Codex CLI + a local MFS server",
        f"v{contract_version}" if contract_version else "## v",
        "Stable releases remain explicit owner actions",
    ):
        if release and token not in release:
            errors.append(f"RELEASE.md is missing contract text: {token!r}")

    security = _read(root, "SECURITY.md", errors)
    if security and "security/advisories/new" not in security:
        errors.append("SECURITY.md must link to private vulnerability reporting")

    readme = _read(root, "README.md", errors)
    for token in ("# Tag", "https://github.com/klovr-co/hover-tag.git", "Apache License 2.0"):
        if readme and token not in readme:
            errors.append(f"README.md is missing release identity: {token!r}")

    for relative_path in MODIFIED_UPSTREAM_FILES:
        content = _read(root, relative_path, errors)
        if content and MODIFICATION_NOTICE not in content:
            errors.append(f"modified upstream file lacks modification notice: {relative_path}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    errors = validate_release(args.root.resolve())
    if errors:
        print("Release metadata check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    version = (args.root / "VERSION").read_text(encoding="utf-8").strip()
    print(f"Release metadata check passed for v{version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
