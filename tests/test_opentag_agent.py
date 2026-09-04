from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import opentag_agent


class OpenTagAgentPromptTests(unittest.TestCase):
    def test_prompt_treats_gws_as_a_normal_local_tool(self) -> None:
        previous_transport = os.environ.get("OPENTAG_TRANSPORT")
        os.environ["OPENTAG_TRANSPORT"] = "slack"
        try:
            prompt = opentag_agent.build_prompt(
                skill_dir=Path("/tmp/open-tag"),
                workdir=Path("/tmp/workspace"),
                memory_root=Path("/tmp/memory"),
                channel_id="C123",
                question="Check my email",
                thread_text="",
                attachments_dir=None,
                allowed_scopes="file://local/tmp/workspace",
            )
        finally:
            if previous_transport is None:
                del os.environ["OPENTAG_TRANSPORT"]
            else:
                os.environ["OPENTAG_TRANSPORT"] = previous_transport

        self.assertIn("This includes `gws` when it is installed and authenticated.", prompt)
        self.assertIn("mfs_ls.py", prompt)
        self.assertIn("not add per-tool feature flags or caller allowlists.", prompt)
        self.assertNotIn("OPENTAG_GWS_ENABLED", prompt)
        self.assertNotIn("OPENTAG_GWS_ALLOWED_CALLERS", prompt)
        self.assertNotIn("read-only Gmail operations", prompt)

    def test_slack_prompt_includes_current_channel_posting_capability(self) -> None:
        previous_transport = os.environ.get("OPENTAG_TRANSPORT")
        os.environ["OPENTAG_TRANSPORT"] = "slack"
        try:
            prompt = opentag_agent.build_prompt(
                skill_dir=Path("/tmp/open-tag"),
                workdir=Path("/tmp/workspace"),
                memory_root=Path("/tmp/memory"),
                channel_id="C123",
                question="Summarise and send it to the channel",
                thread_text="",
                attachments_dir=None,
                allowed_scopes="file://local/tmp/workspace",
            )
        finally:
            if previous_transport is None:
                del os.environ["OPENTAG_TRANSPORT"]
            else:
                os.environ["OPENTAG_TRANSPORT"] = previous_transport

        self.assertIn("slack_post_message.py", prompt)
        self.assertIn("new top-level channel message", prompt)
        self.assertIn("only when the user", prompt)

    def test_codex_backend_uses_automatic_workspace_safety_review(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            with patch(
                "scripts.opentag_agent.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout="done"),
            ) as run:
                code, output = opentag_agent.run_codex_once(
                    "test prompt",
                    skill_dir=root / "skill",
                    workdir=root / "workspace",
                    memory_root=root / "memory",
                    attachments_dir=None,
                    timeout=30,
                )

        command = run.call_args.args[0]
        self.assertEqual(0, code)
        self.assertEqual("done", output)
        self.assertIn("--approve-for-me", command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
