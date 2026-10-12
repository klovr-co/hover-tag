import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

try:
    from mfs_server.connectors import registry
    from mfs_server.connectors.slack.plugin import SlackPlugin
    from slack_sdk.errors import SlackApiError
    from slack_sdk.web.async_slack_response import AsyncSlackResponse

    from scripts import tag_mfs_server
except ImportError:
    tag_mfs_server = None


@unittest.skipIf(tag_mfs_server is None, "Integration test requires the bundled MFS runtime")
class ConnectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        registry.load_builtin()
        original = registry.get_plugin_cls("slack")
        self.addCleanup(registry.register, original)
        self.plugin_type = tag_mfs_server.install_connector(Path(self.temp.name))
        self.plugin = self.plugin_type({}, "fixture", ctx=SimpleNamespace(state=None))
        self.api = patch.object(tag_mfs_server.AsyncWebClient, "api_call", new_callable=AsyncMock)
        self.call = self.api.start()
        self.addCleanup(self.api.stop)
        self.call.return_value = {"team_id": "TTEST"}

    async def test_registration_survives_builtin_reload_and_channel_listing_is_cached(self):
        registry.load_builtin()
        self.assertIs(registry.get_plugin_cls("slack"), self.plugin_type)
        await self.plugin.connect()
        with patch.object(SlackPlugin, "_channels", new_callable=AsyncMock, return_value=[{"id": "C1"}]) as channels:
            self.assertEqual(await self.plugin._channels(), [{"id": "C1"}])
            self.assertEqual(await self.plugin._channels(), [{"id": "C1"}])
            channels.assert_awaited_once()
            another = self.plugin_type({}, "fixture", ctx=SimpleNamespace(state=None))
            await another.connect()
            await another._channels()
            channels.assert_awaited_once()
            with patch.object(tag_mfs_server.time, "monotonic", return_value=10**12):
                await self.plugin._channels()
            self.assertEqual(channels.await_count, 2)

    async def test_channel_cache_respects_credential_and_allowlist_boundaries(self):
        await self.plugin.connect()
        with patch.object(SlackPlugin, "_channels", new_callable=AsyncMock, return_value=[]) as channels:
            await self.plugin._channels()
            for config, token in (({"channel_ids": ["COTHER"]}, "fixture"), ({}, "other-token")):
                other = self.plugin_type(config, token, ctx=SimpleNamespace(state=None))
                await other.connect()
                await other._channels()
            self.assertEqual(channels.await_count, 3)

    async def test_org_channel_cache_is_separate_for_each_workspace(self):
        auth = {"team_id": None, "enterprise_id": "EORG", "is_enterprise_install": True}
        async def response(method, **kwargs):
            if method == "auth.test":
                return auth
            if method == "auth.teams.list":
                return {"teams": [{"id": "TONE"}, {"id": "TTWO"}]}
            return {"channels": []}
        self.call.side_effect = response
        with patch.object(SlackPlugin, "_channels", new_callable=AsyncMock, return_value=[]) as channels:
            for team in ("TONE", "TTWO", "TONE"):
                plugin = self.plugin_type({"team_id": team, "channel_ids": ["CONE"]},
                                          "shared-org-token", ctx=SimpleNamespace(state=None))
                await plugin.connect()
                self.assertEqual(plugin._client.default_params["team_id"], team)
                await plugin._channels()
            self.assertEqual(channels.await_count, 2)

    async def test_org_connector_requires_workspace_and_actual_grant(self):
        self.call.return_value = {"team_id": None, "is_enterprise_install": True}
        with self.assertRaisesRegex(RuntimeError, "workspace"):
            await self.plugin.connect()
        self.call.side_effect = [
            {"team_id": None, "is_enterprise_install": True},
            {"teams": [{"id": "TOTHER"}]},
        ]
        plugin = self.plugin_type({"team_id": "TSELECTED"}, "new-token", ctx=SimpleNamespace(state=None))
        with self.assertRaisesRegex(RuntimeError, "no grant"):
            await plugin.connect()

    async def test_workspace_token_cannot_be_relabelled_as_another_workspace(self):
        plugin = self.plugin_type({"team_id": "TOTHER"}, "fixture", ctx=SimpleNamespace(state=None))
        with self.assertRaisesRegex(RuntimeError, "different workspace"):
            await plugin.connect()

    async def test_429_retries_current_cursor_without_reemitting_completed_page(self):
        await self.plugin.connect()
        response = AsyncSlackResponse(client=None, http_verb="GET", api_url="fixture",
            req_args={}, data={"ok": False, "error": "ratelimited"}, headers={"Retry-After": "1"}, status_code=429)
        self.call.side_effect = [
            {"messages": [{"ts": "1"}], "has_more": True, "response_metadata": {"next_cursor": "page2"}},
            SlackApiError("rate limit", response),
            {"messages": [{"ts": "2"}], "has_more": False},
        ]
        # Short real delays exercise the SDK -> limiter -> generator boundary.
        messages = [m async for m in self.plugin.read_records("/channels/team__C1/messages.jsonl")]
        self.assertEqual([m["ts"] for m in messages], ["1", "2"])
        cursors = [call.kwargs["params"].get("cursor") for call in self.call.await_args_list[1:]]
        self.assertEqual(cursors, [None, "page2", "page2"])


if __name__ == "__main__":
    unittest.main()


class FakeClient:
    def __init__(self, collections):
        self.collections = collections
        self.flushed = []

    def has_collection(self, name):
        return name in self.collections

    def list_collections(self):
        return list(self.collections)

    def list_indexes(self, name):
        return []

    def flush(self, collection_name):
        self.flushed.append(collection_name)


class FakeStore:
    lite = True
    collections = ("chunks",)

    def connect(self):
        self.client = FakeClient(self.collections)

    def is_lite(self):
        return self.lite

    def resolve_collection(self, namespace_id):
        return "chunks"

    def _build_index_params(self):
        from pymilvus import MilvusClient

        params = MilvusClient.prepare_index_params()
        params.add_index("dense_vec", index_type="AUTOINDEX", metric_type="COSINE")
        params.add_index("sparse_vec", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
        return params

    def upsert(self, namespace_id, rows):
        pass

    def delete_by_object(self, namespace_id, connector_uri, object_uri):
        pass

    def delete_by_connector(self, namespace_id, connector_uri):
        pass


@unittest.skipIf(tag_mfs_server is None, "Integration test requires the bundled MFS runtime")
class StoreMaintenanceTests(unittest.TestCase):
    def store(self, **attributes):
        store_type = type("Store", (FakeStore,), attributes)
        tag_mfs_server.install_store_maintenance(store_type, flush_after=3)
        store = store_type()
        store.connect()
        return store

    def test_startup_compacts_logs_left_by_earlier_releases(self):
        store = self.store(collections=("chunks", "other"))
        self.assertEqual(store.client.flushed, ["chunks", "other"])

    def test_repeated_rewrites_flush_once_threshold_is_reached(self):
        store = self.store()
        store.client.flushed.clear()
        store.upsert("ns", [{}, {}])
        self.assertEqual(store.client.flushed, [])
        store.upsert("ns", [{}])
        store.upsert("ns", [{}, {}])
        store.delete_by_object("ns", "slack://a", "slack://a/x")
        self.assertEqual(store.client.flushed, ["chunks", "chunks"])

    def test_removing_a_connector_flushes_its_tombstones(self):
        store = self.store()
        store.client.flushed.clear()
        store.delete_by_connector("ns", "slack://a")
        self.assertEqual(store.client.flushed, ["chunks"])

    def test_remote_milvus_is_left_to_its_own_compaction(self):
        store = self.store(lite=False)
        for _ in range(5):
            store.upsert("ns", [{}, {}])
        self.assertEqual(store.client.flushed, [])

    def test_new_lite_collections_use_exact_dense_search(self):
        indexes = {i.field_name: i.to_dict() for i in self.store()._build_index_params()}
        self.assertEqual(indexes["dense_vec"]["index_type"], "FLAT")
        self.assertEqual(indexes["dense_vec"]["metric_type"], "COSINE")
        self.assertEqual(indexes["sparse_vec"]["index_type"], "SPARSE_INVERTED_INDEX")
        remote = {i.field_name: i.to_dict() for i in self.store(lite=False)._build_index_params()}
        self.assertEqual(remote["dense_vec"]["index_type"], "AUTOINDEX")

    def test_missing_collection_is_not_flushed(self):
        store = self.store(collections=())
        store.upsert("ns", [{}, {}, {}])
        self.assertEqual(store.client.flushed, [])


@unittest.skipIf(tag_mfs_server is None, "Integration test requires the bundled MFS runtime")
class MilvusLiteCompactionTests(unittest.TestCase):
    def setUp(self):
        from pymilvus import DataType, MilvusClient

        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "lite.db"
        self.client = MilvusClient(uri=str(self.path))
        self.addCleanup(self.client.close)
        schema = MilvusClient.create_schema(auto_id=False)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=16)
        schema.add_field("dense_vec", DataType.FLOAT_VECTOR, dim=8)
        index = self.client.prepare_index_params()
        index.add_index("dense_vec", index_type="AUTOINDEX", metric_type="COSINE")
        self.client.create_collection("chunks", schema=schema, index_params=index)
        self.rows = [{"id": str(i), "dense_vec": [float((i * k) % 11 + 1) for k in range(1, 9)]} for i in range(100)]

    def connect(self, flush_after=500):
        client = self.client
        store_type = type("Store", (FakeStore,), {"connect": lambda self: setattr(self, "client", client)})
        tag_mfs_server.install_store_maintenance(store_type, flush_after=flush_after)
        store = store_type()
        store.connect()
        return store

    def size(self):
        return sum(f.stat().st_size for f in self.path.rglob("*") if f.is_file())

    def test_older_install_is_switched_to_exact_search_and_compacted(self):
        for _ in range(4):
            self.client.upsert("chunks", self.rows)
        unflushed = self.size()
        self.connect()
        self.connect()  # repeated startup is a no-op
        self.assertEqual(self.client.describe_index("chunks", "dense_vec")["index_type"], "FLAT")
        self.assertLess(self.size(), unflushed)
        for row in self.rows:
            found = self.client.search("chunks", [row["dense_vec"]], limit=100, anns_field="dense_vec")[0]
            self.assertIn(row["id"], {hit["id"] for hit in found})

    def test_rewritten_rows_are_compacted_and_preserved(self):
        store = self.connect()
        for _ in range(4):
            self.client.upsert("chunks", self.rows)
        unflushed = self.size()
        for _ in range(5):
            store.upsert("ns", self.rows)
        self.assertLess(self.size(), unflushed)
        count = self.client.query("chunks", filter="", output_fields=["count(*)"])
        self.assertEqual(count[0]["count(*)"], 100)
