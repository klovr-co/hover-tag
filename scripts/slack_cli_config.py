"""Which Slack CLI sign-in Tag uses, kept apart from this computer's default.

The Slack CLI keeps its sign-ins in a system config folder (``~/.slack`` by
default). Tags that Chief of Staff creates are owned by a service account, so
Slack apps don't belong to one person. That account signs in to its own
folder, passed to every Slack CLI command with ``--config-dir``, so it never
replaces the operator's own sign-in.

- ``TAG_SLACK_CONFIG_DIR`` (process environment) selects the folder for the
  Slack CLI commands this process runs.
- ``OPENTAG_SLACK_CLI_CONFIG`` (a Tag's settings) keeps the folder with a Tag
  it created, so later renames, manifest updates and app removal use the same
  account. ``tag_cli`` copies it into ``TAG_SLACK_CONFIG_DIR`` for that Tag.
"""
from __future__ import annotations

import os
from pathlib import Path

CONFIG_DIR_ENV = "TAG_SLACK_CONFIG_DIR"
TAG_SETTING = "OPENTAG_SLACK_CLI_CONFIG"


def flags() -> list[str]:
    """``--config-dir`` for Slack CLI commands, or nothing for the computer's default sign-in."""
    folder = os.getenv(CONFIG_DIR_ENV, "").strip()
    return ["--config-dir", folder] if folder and Path(folder).is_absolute() else []


def service_account_dir(installation_root: Path, values: dict[str, str] | None = None) -> Path:
    """The service account's own Slack CLI folder; ``OPENTAG_CHIEF_SLACK_CONFIG`` overrides it."""
    override = (values or {}).get("OPENTAG_CHIEF_SLACK_CONFIG", "").strip()
    if override and Path(override).is_absolute():
        return Path(override)
    return installation_root.expanduser().absolute() / "shared/slack/service-account"


def apply_tag_setting(values: dict[str, str]) -> None:
    """Use the Tag's saved folder, if any, for this process's Slack CLI commands."""
    folder = values.get(TAG_SETTING, "").strip()
    if folder and Path(folder).is_absolute():
        os.environ[CONFIG_DIR_ENV] = folder
