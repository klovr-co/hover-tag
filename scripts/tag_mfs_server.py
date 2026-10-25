"""Launch managed MFS with Tag's rate-aware Slack connector (runtime schema v1)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from mfs_server.connectors import registry
from mfs_server.connectors.slack.plugin import SlackPlugin
from mfs_server.storage.milvus import MilvusStore
from pymilvus import MilvusClient
from pymilvus.milvus_client.index import IndexParam
from slack_sdk.web.async_client import AsyncWebClient

try:
    from .tag_slack_backoff import Coordinator
    from .tag_config import save_config
except ImportError:
    from tag_slack_backoff import Coordinator
    from tag_config import save_config

RUNTIME_VERSION = 3
MIRROR_EVENT_TYPES = frozenset({"hover_whatsapp_mirror"})
# Milvus Lite appends every write to a log and only tidies that log into
# indexed storage once 10,000 *different* chunks have changed. Slack syncs keep
# rewriting the same few thousand chunks, so it never tidies up: the log fills
# with old copies and every search reads all of them. Tidy up ourselves.
FLUSH_AFTER_ROWS = 2_000
# Flushed Lite segments get a FAISS HNSW graph that can strand rows from vector
# search (milvus-io/milvus-lite#369). Exact search is fast at Tag's scale.
DENSE_FIELD = "dense_vec"
LITE_DENSE_INDEX = "FLAT"


def install_store_maintenance(store_type=MilvusStore, flush_after=FLUSH_AFTER_ROWS):
    lock = threading.Lock()
    pending = {}
    connect, upsert = store_type.connect, store_type.upsert
    build_index_params = store_type._build_index_params
    delete_by_object = store_type.delete_by_object
    delete_by_connector = store_type.delete_by_connector

    def flush(store, name):
        if store.client.has_collection(name):
            store.client.flush(collection_name=name)

    def written(store, namespace_id, rows):
        if not store.is_lite():
            return
        name = store.resolve_collection(namespace_id)
        with lock:
            pending[name] = pending.get(name, 0) + rows
            if pending[name] < flush_after:
                return
            pending[name] = 0
        flush(store, name)

    def index_params(store):
        params = build_index_params(store)
        if store.is_lite():
            for position, index in enumerate(params):
                if index.field_name == DENSE_FIELD:
                    metric = index.to_dict().get("metric_type", "COSINE")
                    params[position] = IndexParam(
                        DENSE_FIELD, LITE_DENSE_INDEX, index.index_name, metric_type=metric
                    )
        return params

    def use_exact_search(store, name):
        metric = "COSINE"
        if DENSE_FIELD in store.client.list_indexes(name):
            current = store.client.describe_index(name, DENSE_FIELD)
            if current.get("index_type") == LITE_DENSE_INDEX:
                return
            metric = current.get("metric_type", metric)
            store.client.release_collection(name)
            store.client.drop_index(name, DENSE_FIELD)
        elif DENSE_FIELD not in {f["name"] for f in store.client.describe_collection(name)["fields"]}:
            # A switch that stopped after dropping the index finishes here.
            return
        exact = MilvusClient.prepare_index_params()
        exact.add_index(
            field_name=DENSE_FIELD,
            index_type=LITE_DENSE_INDEX,
            metric_type=metric,
        )
        store.client.create_index(name, exact)
        store.client.load_collection(name)

    def connected(store):
        connect(store)
        if store.is_lite():
            # Repair collections from earlier releases before serving searches:
            # switch to exact search before the first flush builds HNSW.
            for name in store.client.list_collections():
                use_exact_search(store, name)
                flush(store, name)

    def upserted(store, namespace_id, rows):
        upsert(store, namespace_id, rows)
        written(store, namespace_id, len(rows))

    def deleted_object(store, namespace_id, *args):
        delete_by_object(store, namespace_id, *args)
        written(store, namespace_id, 1)

    def deleted_connector(store, namespace_id, *args):
        delete_by_connector(store, namespace_id, *args)
        written(store, namespace_id, flush_after)

    store_type._build_index_params = index_params
    store_type.connect = connected
    store_type.upsert = upserted
    store_type.delete_by_object = deleted_object
    store_type.delete_by_connector = deleted_connector


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
        async def read_records(self, path, range=None):
            # Skip what an approved app (Hover) posts: its mirror of WhatsApp chats is indexed
            # elsewhere, and its requests and logs are not knowledge. Human messages stay.
            skipped = {part.strip() for part in os.environ.get("SLACK_ALLOWED_BOT_IDS", "").split(",") if part.strip()}
            async for message in super().read_records(path, range):
                metadata = message.get("metadata")
                if isinstance(metadata, dict) and metadata.get("event_type") in MIRROR_EVENT_TYPES:
                    continue
                if skipped and skipped & {message.get("bot_id"), message.get("user"), message.get("app_id")}:
                    continue
                yield message

        async def connect(self):
            self._client = PacedClient(self._cfg("token") or self.credential)
            token_key = self._client.identity
            lock = identity_locks.setdefault(token_key, asyncio.Lock())
            async with lock:
                cached = identities.get(token_key)
                if cached is None or time.monotonic() - cached[0] >= 60:
                    identity = await self._client.auth_test()
                    identities[token_key] = (time.monotonic(), identity)
                identity = identities[token_key][1]
            selected_team = self._cfg("team_id") or identity.get("team_id")
            if not isinstance(selected_team, str) or not re.fullmatch(r"T[A-Z0-9]+", selected_team):
                raise RuntimeError("Slack memory requires a selected workspace Team ID")
            if identity.get("is_enterprise_install"):
                if not self._cfg("team_id"):
                    raise RuntimeError("Organization Slack memory requires an explicit workspace")
                cursor, seen, granted = "", set(), False
                while True:
                    page = await self._client.auth_teams_list(limit=200, cursor=cursor or None)
                    if any(team.get("id") == selected_team for team in page.get("teams", [])):
                        granted = True
                        break
                    cursor = (page.get("response_metadata") or {}).get("next_cursor", "")
                    if not cursor or cursor in seen:
                        break
                    seen.add(cursor)
                if not granted:
                    raise RuntimeError("Slack history token has no grant for the selected workspace")
            elif identity.get("team_id") != selected_team:
                raise RuntimeError("Slack history token belongs to a different workspace")
            self._client.default_params["team_id"] = selected_team
            # Share a workspace budget across credentials, never cache its channels across teams.
            self._client.identity = selected_team
            filters = [
                selected_team,
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
    install_store_maintenance()
    save_config(state / "slack-runtime-ready-v1.json", {
        "version": RUNTIME_VERSION, "pid": os.getpid(),
        "instance_id": os.environ["TAG_MFS_PROCESS_ID"],
    })
    from mfs_server.server.__main__ import main as serve
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
