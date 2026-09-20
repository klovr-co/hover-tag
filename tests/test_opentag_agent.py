from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import opentag_agent


class OpenTagAgentPromptTests(unittest.TestCase):
    def test_windows_npm_backend_bypasses_command_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "node_modules/@openai/codex/bin/codex.js"
            script.parent.mkdir(parents=True)
            script.write_text("// fixture")
            shim = root / "codex.cmd"
            with patch.object(opentag_agent, "os", SimpleNamespace(name="nt")), patch(
                "scripts.opentag_agent.shutil.which", side_effect=[str(shim), "node.exe"]
            ):
                command = opentag_agent.executable_command(["codex", "exec", "text & special %characters%"])
            self.assertEqual(command, ["node.exe", str(script), "exec", "text & special %characters%"])

    def test_helper_command_quotes_application_support_paths(self) -> None:
        path = Path("/Users/person/Library/Application Support/Tag/slack_canvas.py")
        command = opentag_agent.helper_command(path)
        self.assertIn("Application Support", command)
        self.assertIn("'", command)

    def test_prompt_treats_installed_tools_as_normal_local_tools(self) -> None:
        previous_transport = os.environ.get("OPENTAG_TRANSPORT")
        os.environ["OPENTAG_TRANSPORT"] = "slack"
        try:
            prompt = opentag_agent.build_prompt(
                skill_dir=Path("/tmp/open-tag"),
                workdir=Path("/tmp/workspace"),
                channel_id="C123",
                question="Use an installed local tool",
                thread_text="",
                attachments_dir=None,
                allowed_scopes="file://local/tmp/workspace",
            )
        finally:
            if previous_transport is None:
                del os.environ["OPENTAG_TRANSPORT"]
            else:
                os.environ["OPENTAG_TRANSPORT"] = previous_transport

        self.assertIn("commands and skills installed in its environment", prompt)
        self.assertIn("mfs_ls.py", prompt)
        self.assertIn("not add per-tool feature flags or caller allowlists.", prompt)
        self.assertNotIn("gws", prompt.lower())
        self.assertNotIn("gmail", prompt.lower())

    def test_slack_prompt_includes_current_channel_posting_capability(self) -> None:
        previous_transport = os.environ.get("OPENTAG_TRANSPORT")
        os.environ["OPENTAG_TRANSPORT"] = "slack"
        try:
            prompt = opentag_agent.build_prompt(
                skill_dir=Path("/tmp/open-tag"),
                workdir=Path("/tmp/workspace"),
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

    @patch("scripts.opentag_agent.backend_command", return_value=["codex"])
    def test_codex_backend_uses_automatic_workspace_safety_review(self, _backend) -> None:
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
                    attachments_dir=None,
                    timeout=30,
                )

        command = run.call_args.args[0]
        self.assertEqual(0, code)
        self.assertEqual("done", output)
        self.assertIn("--approve-for-me", command)
        self.assertIn("features.fast_mode=true", command)
        self.assertIn('service_tier="default"', command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)


class BackendStreamEventTests(unittest.TestCase):
    def test_codex_exposes_only_completed_agent_messages(self) -> None:
        self.assertEqual(
            ("final", "Ready"),
            opentag_agent.parse_codex_stream_event(
                {"type": "item.completed", "item": {"type": "agent_message", "text": "Ready"}}
            ),
        )
        self.assertIsNone(
            opentag_agent.parse_codex_stream_event(
                {"type": "item.completed", "item": {"type": "command_execution", "command": "secret"}}
            )
        )
        self.assertIsNone(
            opentag_agent.parse_codex_stream_event(
                {"type": "item.completed", "item": {"type": "reasoning", "text": "private"}}
            )
        )

    def test_claude_exposes_text_deltas_but_not_thinking_or_subagent_text(self) -> None:
        self.assertEqual(
            ("delta", "Hello"),
            opentag_agent.parse_claude_stream_event(
                {
                    "type": "stream_event",
                    "parent_tool_use_id": None,
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "Hello"},
                    },
                }
            ),
        )
        self.assertIsNone(
            opentag_agent.parse_claude_stream_event(
                {
                    "type": "stream_event",
                    "parent_tool_use_id": None,
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "thinking_delta", "thinking": "private"},
                    },
                }
            )
        )
        self.assertIsNone(
            opentag_agent.parse_claude_stream_event(
                {
                    "type": "stream_event",
                    "parent_tool_use_id": "tool-123",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "subagent output"},
                    },
                }
            )
        )

    def test_claude_success_result_is_authoritative_final_text(self) -> None:
        self.assertEqual(
            ("final", "Complete answer"),
            opentag_agent.parse_claude_stream_event(
                {"type": "result", "is_error": False, "result": "Complete answer"}
            ),
        )

    def test_stream_commands_enable_each_backends_json_mode(self) -> None:
        codex = opentag_agent.codex_stream_command(
            "prompt",
            skill_dir=Path("/skill"),
            workdir=Path("/work"),
            attachments_dir=None,
            output_path=Path("/tmp/final.txt"),
        )
        claude = opentag_agent.claude_stream_command(
            skill_dir=Path("/skill"),
            workdir=Path("/work"),
            attachments_dir=None,
        )

        self.assertIn("--json", codex)
        self.assertIn("--output-last-message", codex)
        self.assertIn("stream-json", claude)
        self.assertIn("--include-partial-messages", claude)
        self.assertNotIn("prompt", claude)

    def test_codex_stream_command_applies_model_reasoning_and_fast_overrides(self) -> None:
        command = opentag_agent.codex_stream_command(
            "prompt",
            skill_dir=Path("/skill"),
            workdir=Path("/work"),
            attachments_dir=None,
            output_path=Path("/tmp/final.txt"),
            model="gpt-example",
            reasoning_effort="high",
            fast_mode=True,
        )

        self.assertIn("gpt-example", command)
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertIn("features.fast_mode=true", command)
        self.assertIn('service_tier="fast"', command)

    def test_codex_stream_command_explicitly_turns_fast_mode_off(self) -> None:
        command = opentag_agent.codex_stream_command(
            "prompt",
            skill_dir=Path("/skill"),
            workdir=Path("/work"),
            attachments_dir=None,
            output_path=Path("/tmp/final.txt"),
            fast_mode=False,
        )

        self.assertIn("features.fast_mode=true", command)
        self.assertIn('service_tier="default"', command)
