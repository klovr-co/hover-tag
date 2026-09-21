"""Reconcile explicitly enabled invitation-following memory without joining channels."""
from __future__ import annotations

import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

try:
    from . import slack_channels, tag_config, tag_cli
    from .opentag_setup import connector_scope, write_slack_connector
except ImportError:
    import slack_channels
    import tag_config
    import tag_cli
    from opentag_setup import connector_scope, write_slack_connector


def validate_slack_identity(token: str, *, team_id: str, app_id: str = "", label: str) -> None:
    """Background validation must never enter onboarding's interactive recovery."""
    if tag_config.validation_error("MFS_SLACK_TOKEN", token):
        raise RuntimeError("Missing or invalid credential")
    payload = slack_channels.slack_api(token, "auth.test", {})
    if payload.get("team_id") != team_id:
        raise RuntimeError("Credential workspace mismatch")
    if app_id and payload.get("app_id") and payload["app_id"] != app_id:
        raise RuntimeError("Credential app mismatch")


class InvitationMemory:
    """Single worker: discover every minute, incrementally sync every five minutes."""

    def __init__(self, home: Path, environment=None):
        self.home = home
        self.environment = os.environ if environment is None else environment
        self.stop_event = threading.Event()
        self.thread = None
        self.last_sync = 0.0
        self.last_signature = None

    def status(self, state: str, count: int = 0, check: str = "") -> None:
        tag_config.save_config(self.home / "state/slack-memory.json", {
            "state": state, "channels": count, "checked_at": int(time.time()), "check": check,
        })

    def restrict(self, scopes: list[str], channel_ids: str = "") -> None:
        self.environment["SLACK_CHANNEL_IDS"] = channel_ids
        self.environment["SLACK_CHANNEL_ID"] = ""
        self.environment["MFS_ALLOWED_SCOPES"] = ",".join(scopes)

    def tick(self) -> None:
        path = tag_config.config_path(self.home)
        values = tag_config.load_config(path)
        if values.get("SLACK_CHANNEL_POLICY") != "invited":
            self.environment["SLACK_CHANNEL_POLICY"] = values.get("SLACK_CHANNEL_POLICY", "selected")
            self.restrict(values.get("MFS_ALLOWED_SCOPES", "").split(","), values.get("SLACK_CHANNEL_IDS", ""))
            return
        self.environment["SLACK_CHANNEL_POLICY"] = "invited"
        team = values.get("SLACK_TEAM_ID", "")
        uri = f"slack://tag-{team.lower()}"
        # Keep unrelated configured sources; Slack reply scopes remain narrowed
        # by backend_environment. Never restore old Slack scopes on a failed check.
        unrelated = [s.strip() for s in values.get("MFS_ALLOWED_SCOPES", "").split(",")
                     if s.strip() and urlsplit(s.strip()).scheme != "slack"]
        check = "membership"
        try:
            if not team or tag_config.validation_error("SLACK_TEAM_ID", team):
                raise RuntimeError("Invalid workspace")
            validate_slack_identity(values["SLACK_BOT_TOKEN"], team_id=team,
                                    app_id=values.get("SLACK_APP_ID", ""), label="Bot token")
            channels = [c for c in slack_channels.list_channels(values["SLACK_BOT_TOKEN"]) if c.is_member]
            ids = ",".join(c.channel_id for c in channels)
            scopes = [connector_scope(team, c) for c in channels]
            # Remove lost membership from live access before any index operation.
            self.restrict(unrelated + scopes, ids)
            if not channels:
                self.last_signature = None
                self.status("no_joined_channels")
                return  # Never send an empty connector allowlist to MFS.
            signature = (tuple(sorted(scopes)), values.get("MFS_SLACK_HISTORY_DAYS", "30"),
                         values.get("MFS_SLACK_TOKEN"), values.get("MFS_URL"), values.get("MFS_TOKEN"))
            if signature == self.last_signature and time.monotonic() - self.last_sync < 300:
                return
            self.status("syncing", len(channels))
            check = "history_access"
            history = values.get("MFS_SLACK_TOKEN", "")
            validate_slack_identity(history, team_id=team, label="Slack-history credential")
            for channel in channels:
                slack_channels.slack_api(history, "conversations.history", {"channel": channel.channel_id, "limit": "1"})
            # Do not continue if settings changed while Slack checks were running.
            if self.stop_event.is_set() or tag_config.load_config(path) != values:
                self.restrict(unrelated)
                self.status("settings_changed")
                return
            connector = write_slack_connector(team, channels, signature[1], home=self.home)
            check = "index_submission"
            env = dict(self.environment)
            env.update(values)
            env.update({"MFS_SLACK_CONNECTOR_URI": uri, "MFS_SLACK_CONNECTOR_CONFIG": str(connector)})
            tag_cli.sync_configured_slack_memory(env)
            if self.stop_event.is_set() or tag_config.load_config(path) != values:
                self.restrict(unrelated)
                self.status("settings_changed")
                return
            # Preserve unrelated scopes/config on disk. Only replace this
            # workspace's managed Slack scopes, not other source registrations.
            preserved = [s.strip() for s in values.get("MFS_ALLOWED_SCOPES", "").split(",")
                         if s.strip() and not (s.strip() == uri or s.strip().startswith(uri + "/"))]
            tag_config.update_config(path, {
                "SLACK_CHANNEL_IDS": ids,
                "MFS_ALLOWED_SCOPES": ",".join(dict.fromkeys(preserved + scopes)),
                "MFS_SLACK_CONNECTOR_URI": uri, "MFS_SLACK_CONNECTOR_CONFIG": str(connector),
            })
            self.last_signature = signature
            self.last_sync = time.monotonic()
            self.status("sync_requested", len(channels))
        except tag_cli.MfsHistoryCredentialUnavailable:
            # The server's environment is distinct from Tag's. Record a safe,
            # actionable status without retaining the credential or CLI output.
            self.restrict(unrelated)
            self.last_signature = None
            self.status("needs_attention", check="mfs_history_credential")
        except tag_cli.MfsSlackConnectorUnavailable:
            self.restrict(unrelated)
            self.last_signature = None
            self.status("needs_attention", check="mfs_slack_connector")
        except Exception:
            # No raw API/CLI exception output: it may include credentials/content.
            self.restrict(unrelated)
            self.last_signature = None
            self.status("needs_attention", check=check)

    def start(self) -> None:
        # Fail closed until the first current membership check completes.
        self.restrict([])
        def work():
            while not self.stop_event.is_set():
                try:
                    self.tick()
                except Exception:
                    self.restrict([])
                self.stop_event.wait(60)
        self.thread = threading.Thread(target=work, name="tag-invitation-memory", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
