from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import opentag_process_env
from scripts import zulip_runtime


class ZulipRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.base_env = {
            "OPENTAG_ZULIP_ENGINE": "zulipmcp",
            "OPENTAG_BACKEND": "codex",
            "OPENTAG_WORKDIR": "/tmp/opentag-workspace",
            "ZULIP_CONFIG_FILE": "/tmp/opentag-bot.zuliprc",
            "MFS_URL": "http://127.0.0.1:13619",
            "MFS_ALLOWED_SCOPES": "file://local/tmp/opentag-workspace",
        }

    def test_native_command_keeps_existing_adapter_available(self) -> None:
        command = zulip_runtime.build_runtime_command(
            {**self.base_env, "OPENTAG_ZULIP_ENGINE": "native"},
            root=self.root,
        )

        self.assertEqual(
            command[:4], ["uv", "run", "--with", zulip_runtime.ZULIP_PACKAGE]
        )
        self.assertIn(str(self.root / "scripts" / "zulip_agent.py"), command)
        self.assertEqual(command[-2:], ["--backend", "codex"])

    def test_zulipmcp_command_uses_pinned_package_and_policy_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw_runtime_dir:
            runtime_dir = Path(raw_runtime_dir)
            command = zulip_runtime.build_runtime_command(
                self.base_env,
                root=self.root,
                runtime_dir=runtime_dir,
            )

            self.assertEqual(command[:3], ["uv", "run", "--with"])
            self.assertEqual(command[3], zulip_runtime.ZULIPMCP_PACKAGE)
            self.assertIn(str(self.root / "scripts" / "zulipmcp_entrypoint.py"), command)
            self.assertIn("--codex-permission-mode", command)
            self.assertIn("workspace-write", command)
            self.assertIn("--max-sessions", command)
            self.assertIn("1", command)

            mcp_path = runtime_dir / "zulipmcp.mcp.json"
            prompt_path = runtime_dir / "zulipmcp-system-prompt.md"
            self.assertTrue(mcp_path.is_file())
            self.assertTrue(prompt_path.is_file())

            mcp_config = json.loads(mcp_path.read_text(encoding="utf-8"))
            server = mcp_config["mcpServers"]["zulip"]
            self.assertIn(zulip_runtime.ZULIPMCP_PACKAGE, server["args"])
            self.assertEqual(server["env"]["ZULIP_RC_PATH"], "${ZULIP_RC_PATH}")

            prompt = prompt_path.read_text(encoding="utf-8")
            self.assertIn(str(self.root / "scripts" / "mfs_search.py"), prompt)
            self.assertIn("listen(timeout_hours=0.5)", prompt)
            self.assertNotIn("{{", prompt)

    def test_zulip_environment_does_not_expose_slack_credentials(self) -> None:
        source = {
            **self.base_env,
            "SLACK_APP_TOKEN": "xapp-secret",
            "SLACK_BOT_TOKEN": "xoxb-secret",
            "SLACK_CHANNEL_ID": "C123",
            "ZULIP_ADMIN_CONFIG_FILE": "/tmp/admin.zuliprc",
            "ZULIP_AUTO_GRANT_PRIVATE_HISTORY": "true",
        }

        sanitized = zulip_runtime.sanitized_environment(source, engine="zulipmcp")

        self.assertNotIn("SLACK_APP_TOKEN", sanitized)
        self.assertNotIn("SLACK_BOT_TOKEN", sanitized)
        self.assertNotIn("SLACK_CHANNEL_ID", sanitized)
        self.assertNotIn("ZULIP_ADMIN_CONFIG_FILE", sanitized)
        self.assertEqual(sanitized["ZULIP_AUTO_GRANT_PRIVATE_HISTORY"], "false")
        self.assertEqual(sanitized["MFS_ALLOWED_SCOPES"], source["MFS_ALLOWED_SCOPES"])

    def test_native_zulip_environment_retains_native_admin_option(self) -> None:
        source = {
            **self.base_env,
            "SLACK_BOT_TOKEN": "xoxb-secret",
            "ZULIP_ADMIN_CONFIG_FILE": "/tmp/admin.zuliprc",
            "ZULIP_AUTO_GRANT_PRIVATE_HISTORY": "true",
        }

        sanitized = zulip_runtime.sanitized_environment(source, engine="native")

        self.assertNotIn("SLACK_BOT_TOKEN", sanitized)
        self.assertEqual(sanitized["ZULIP_ADMIN_CONFIG_FILE"], "/tmp/admin.zuliprc")
        self.assertEqual(sanitized["ZULIP_AUTO_GRANT_PRIVATE_HISTORY"], "true")

    def test_unknown_engine_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "native or zulipmcp"):
            zulip_runtime.build_runtime_command(
                {**self.base_env, "OPENTAG_ZULIP_ENGINE": "mystery"},
                root=self.root,
            )

    def test_backend_environment_isolates_other_transport_credentials(self) -> None:
        source = {
            "SLACK_APP_TOKEN": "xapp-secret",
            "SLACK_BOT_TOKEN": "xoxb-secret",
            "ZULIP_CONFIG_FILE": "/tmp/bot.zuliprc",
            "MFS_TOKEN": "mfs-secret",
        }

        slack = opentag_process_env.backend_environment(
            source,
            transport="slack",
            conversation_id="C123",
            caller_id="U123",
        )
        zulip = opentag_process_env.backend_environment(
            source,
            transport="zulip",
            conversation_id="stream:1:topic:test",
            caller_id="person@example.com",
        )

        self.assertNotIn("ZULIP_CONFIG_FILE", slack)
        self.assertNotIn("SLACK_APP_TOKEN", slack)
        self.assertEqual(slack["SLACK_BOT_TOKEN"], "xoxb-secret")
        self.assertNotIn("SLACK_BOT_TOKEN", zulip)
        self.assertNotIn("ZULIP_CONFIG_FILE", zulip)
        self.assertEqual(slack["MFS_TOKEN"], "mfs-secret")
        self.assertEqual(zulip["MFS_TOKEN"], "mfs-secret")


if __name__ == "__main__":
    unittest.main()
