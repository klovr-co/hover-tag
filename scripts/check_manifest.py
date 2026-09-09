#!/usr/bin/env python3
"""Validate the Slack app manifest against features used by Tag."""

from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SCOPES = {
    "app_mentions:read",
    "canvases:write",
    "channels:history",
    "channels:read",
    "chat:write",
    "files:read",
    "groups:history",
    "groups:read",
}


def validate_manifest(root: Path) -> list[str]:
    manifest = yaml.safe_load((root / "slack-app-manifest.yaml").read_text(encoding="utf-8"))
    errors: list[str] = []
    scopes = set(manifest.get("oauth_config", {}).get("scopes", {}).get("bot", []))
    missing_scopes = sorted(REQUIRED_SCOPES - scopes)
    if missing_scopes:
        errors.append(f"missing bot scopes: {', '.join(missing_scopes)}")

    settings = manifest.get("settings", {})
    if settings.get("socket_mode_enabled") is not True:
        errors.append("Socket Mode must be enabled")
    events = settings.get("event_subscriptions", {}).get("bot_events", [])
    if "app_mention" not in events:
        errors.append("app_mention must be subscribed")
    return errors


def main() -> int:
    errors = validate_manifest(ROOT)
    if errors:
        print("Slack manifest check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Slack manifest check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
