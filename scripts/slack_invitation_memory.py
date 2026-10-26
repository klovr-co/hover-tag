"""Follow the channels a Tag is invited to, without joining any on its own.

With ``SLACK_CHANNEL_POLICY=invited`` the Tag answers in every channel it is a
member of. This worker checks that membership every minute (at once when Slack
reports an invitation), keeps ``SLACK_CHANNEL_IDS`` current, and records each
channel's name for display. Nothing from those channels is copied or indexed:
the Tag reads Slack live when a request needs it.
"""
from __future__ import annotations

import os
from pathlib import Path
import threading
import time

try:
    from . import slack_identity, slack_channels, slack_channel_names, tag_config
except ImportError:
    import slack_identity
    import slack_channels
    import slack_channel_names
    import tag_config


def validate_slack_identity(token: str, *, team_id: str, app_id: str = "", enterprise_id: str = "", label: str) -> None:
    """Background validation must never enter onboarding's interactive recovery."""
    if tag_config.validation_error("SLACK_BOT_TOKEN", token):
        raise RuntimeError("Missing or invalid credential")
    slack_identity.validate(token, team_id=team_id, app_id=app_id, enterprise_id=enterprise_id,
                            label=label, api=slack_channels.slack_api)


class InvitationMemory:
    """Single worker that keeps the invited channels current."""

    def __init__(self, home: Path, environment=None):
        self.home = home
        self.environment = os.environ if environment is None else environment
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = None

    def status(self, state: str, count: int = 0, check: str = "") -> None:
        tag_config.save_config(self.home / "state/slack-memory.json", {
            "state": state, "channels": count, "checked_at": int(time.time()), "check": check,
        })

    def restrict(self, channel_ids: str = "") -> None:
        self.environment["SLACK_CHANNEL_IDS"] = channel_ids
        self.environment["SLACK_CHANNEL_ID"] = ""

    def tick(self) -> None:
        path = tag_config.config_path(self.home)
        values = tag_config.load_config(path)
        if values.get("SLACK_CHANNEL_POLICY") != "invited":
            self.environment["SLACK_CHANNEL_POLICY"] = values.get("SLACK_CHANNEL_POLICY", "selected")
            self.restrict(values.get("SLACK_CHANNEL_IDS", ""))
            self.ready_event.set()
            return
        self.environment["SLACK_CHANNEL_POLICY"] = "invited"
        team = values.get("SLACK_TEAM_ID", "")
        try:
            if not team or tag_config.validation_error("SLACK_TEAM_ID", team):
                raise RuntimeError("Invalid workspace")
            validate_slack_identity(values["SLACK_BOT_TOKEN"], team_id=team,
                                    app_id=values.get("SLACK_APP_ID", ""), enterprise_id=values.get("SLACK_ENTERPRISE_ID", ""), label="Bot token")
            channels = [c for c in slack_channels.list_channels(values["SLACK_BOT_TOKEN"],
                **({"team_id": team} if values.get("SLACK_ENTERPRISE_ID") else {})) if c.is_member]
            try:
                slack_channel_names.remember(self.home, team, {c.channel_id: c.name for c in channels})
            except OSError:
                pass  # Display metadata must not change membership.
            ids = ",".join(c.channel_id for c in channels)
            # Remove lost membership from live access first.
            self.restrict(ids)
            if self.stop_event.is_set() or tag_config.load_config(path) != values:
                self.ready_event.clear()
                self.status("settings_changed")
                return
            if values.get("SLACK_CHANNEL_IDS", "") != ids:
                tag_config.update_config(path, {"SLACK_CHANNEL_IDS": ids})
            # Ready with no channels: the empty allowlist admits none, DMs work, and
            # the first invitation is confirmed live.
            self.status("ready" if channels else "no_joined_channels", len(channels))
            self.ready_event.set()
        except Exception:
            # No raw API exception output: it may include credentials or content.
            self.restrict()
            self.ready_event.clear()
            self.status("needs_attention", check="membership")

    def start(self) -> None:
        # Fail closed until the first current membership check completes.
        self.restrict()
        self.ready_event.clear()

        def work():
            while not self.stop_event.is_set():
                try:
                    self.tick()
                except Exception:
                    self.ready_event.clear()
                    self.restrict()
                # Poll every minute, but check at once when Slack reports an invite.
                self.wake_event.wait(60)
                self.wake_event.clear()
        self.thread = threading.Thread(target=work, name="tag-invitation-channels", daemon=True)
        self.thread.start()

    def refresh(self) -> None:
        """Check membership now instead of waiting for the next minute."""
        self.wake_event.set()

    def ready_for_requests(self) -> bool:
        """Return whether current invitation policy permits serving Slack requests."""
        return self.ready_event.is_set()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()
