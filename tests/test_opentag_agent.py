from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

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
    def test_backend_progress_requires_a_recognized_lifecycle_event(self) -> None:
        self.assertTrue(opentag_agent.backend_made_progress({"type": "item.started"}))
        self.assertTrue(opentag_agent.backend_made_progress({"type": "stream_event"}))
        self.assertFalse(opentag_agent.backend_made_progress({"type": "keepalive"}))
        self.assertFalse(opentag_agent.backend_made_progress({"message": "noise"}))

    def test_watchdog_resets_idle_deadline_but_not_maximum_runtime(self) -> None:
        stopped = threading.Event()
        watchdog = opentag_agent.BackendWatchdog(
            idle_timeout=0.08,
            max_timeout=0.18,
            stop=stopped.set,
        )
        watchdog.start()
        try:
            self.assertFalse(stopped.wait(0.05))
            watchdog.touch()
            self.assertFalse(stopped.wait(0.05))
            watchdog.touch()
            self.assertTrue(stopped.wait(0.12))
            self.assertEqual("maximum", watchdog.reason)
        finally:
            watchdog.close()

    def test_watchdog_reports_idle_timeout_without_progress(self) -> None:
        stopped = threading.Event()
        watchdog = opentag_agent.BackendWatchdog(
            idle_timeout=0.04,
            max_timeout=0.5,
            stop=stopped.set,
        )
        watchdog.start()
        try:
            self.assertTrue(stopped.wait(0.2))
            self.assertEqual("idle", watchdog.reason)
        finally:
            watchdog.close()

    def test_retryable_failure_recognizes_structured_rate_limit_errors(self) -> None:
        self.assertTrue(opentag_agent.retryable_backend_failure("rate_limit_exceeded"))
        self.assertTrue(opentag_agent.retryable_backend_failure("HTTP 429: too many requests"))
        self.assertFalse(opentag_agent.retryable_backend_failure("invalid authentication"))

    def test_app_server_retries_capacity_before_work_begins(self) -> None:
        server = MagicMock()
        server.run.side_effect = [
            ("failed", "selected model is at capacity"),
            ("completed", ""),
        ]
        with patch.dict(os.environ, {"OPENTAG_BACKEND_ATTEMPTS": "3"}, clear=False), patch.object(
            opentag_agent, "CodexAppServer", return_value=server
        ) as server_class, patch.object(opentag_agent, "emit_event") as emit, patch.object(
            opentag_agent.time, "sleep"
        ):
            result = opentag_agent.run_codex_app_server_events(
                "prompt", workdir=Path("/work"), timeout=30
            )

        self.assertEqual(0, result)
        self.assertEqual(2, server_class.call_count)
        self.assertIn(
            call("status", "Backend busy — retrying (2/3)…"),
            emit.call_args_list,
        )

    def test_app_server_does_not_retry_after_observable_work(self) -> None:
        server = MagicMock()

        def fail_after_activity(*_args, **kwargs):
            kwargs["emit"]({
                "type": "activity_start",
                "activity_id": "one",
                "label": "Running a command…",
            })
            return "failed", "rate limit exceeded"

        server.run.side_effect = fail_after_activity
        with patch.dict(os.environ, {"OPENTAG_BACKEND_ATTEMPTS": "3"}, clear=False), patch.object(
            opentag_agent, "CodexAppServer", return_value=server
        ) as server_class, patch.object(opentag_agent, "emit_event") as emit:
            result = opentag_agent.run_codex_app_server_events(
                "prompt", workdir=Path("/work"), timeout=30
            )

        self.assertEqual(1, result)
        server_class.assert_called_once()
        self.assertNotIn("status", [item.args[0] for item in emit.call_args_list])

    def test_codex_event_transport_defaults_to_app_server_and_keeps_exec_rollback(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual("app-server", opentag_agent.codex_event_transport())
        with patch.dict(os.environ, {"OPENTAG_CODEX_TRANSPORT": "exec"}, clear=True):
            self.assertEqual("exec", opentag_agent.codex_event_transport())
        with patch.dict(os.environ, {"OPENTAG_CODEX_TRANSPORT": "socket"}, clear=True):
            with self.assertRaisesRegex(ValueError, "exec or app-server"):
                opentag_agent.codex_event_transport()

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
