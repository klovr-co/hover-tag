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
