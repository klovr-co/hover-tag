"""Launch managed MFS with Tag's rate-aware Slack connector (runtime schema v1)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

from mfs_server.connectors import registry
from mfs_server.connectors.slack.plugin import SlackPlugin
from slack_sdk.web.async_client import AsyncWebClient

try:
    from .tag_slack_backoff import Coordinator
    from .tag_config import save_config
except ImportError:
    from tag_slack_backoff import Coordinator
    from tag_config import save_config

RUNTIME_VERSION = 1


def install_connector(state: Path):
    coordinator = Coordinator(state / "slack-cooldowns-v1")
    identities = {}
    identity_locks = {}
    channels = {}
    channel_locks = {}

    class PacedClient(AsyncWebClient):
        def __init__(self, token):
            super().__init__(token=token)
            # Bootstrap auth.test without retaining a plaintext token as a key.
            self.identity = hashlib.sha256(token.encode()).hexdigest()

        async def api_call(self, api_method, **kwargs):
            return await coordinator.call(
                self.identity, api_method,
                lambda: super(PacedClient, self).api_call(api_method, **kwargs),
            )

    class RateAwareSlackPlugin(SlackPlugin):
        async def connect(self):
            self._client = PacedClient(self._cfg("token") or self.credential)
            token_key = self._client.identity
            lock = identity_locks.setdefault(token_key, asyncio.Lock())
            async with lock:
                cached = identities.get(token_key)
                if cached is None or time.monotonic() - cached[0] >= 60:
                    identity = await self._client.auth_test()
                    identities[token_key] = (time.monotonic(), identity["team_id"])
                # Conservatively share a workspace budget across credentials.
                self._client.identity = identities[token_key][1]
            filters = [
                self._cfg("channel_types", "public_channel"),
                bool(self._cfg("include_unjoined", False)),
                sorted(self._cfg_set("channel_ids")),
                sorted(self._cfg_set("channel_names")),
            ]
            self._cache_key = (token_key, json.dumps(filters, sort_keys=True))

        async def _channels(self):
            # MFS rebuilds plugins for read-path /ls requests. Share this cache
            # across instances, but never across credentials or channel filters.
            lock = channel_locks.setdefault(self._cache_key, asyncio.Lock())
            async with lock:
                cached = channels.get(self._cache_key)
                if cached is None or time.monotonic() - cached[0] >= 60:
                    found = await super()._channels()
                    channels[self._cache_key] = (time.monotonic(), found)
                return list(channels[self._cache_key][1])

    registry.load_builtin()
    registry.register(RateAwareSlackPlugin)
    return RateAwareSlackPlugin


def main():
    state = Path(os.environ["TAG_MFS_STATE_DIR"])
    install_connector(state)
    save_config(state / "slack-runtime-ready-v1.json", {
        "version": RUNTIME_VERSION, "pid": os.getpid(),
        "instance_id": os.environ["TAG_MFS_PROCESS_ID"],
    })
    from mfs_server.server.__main__ import main as serve
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
