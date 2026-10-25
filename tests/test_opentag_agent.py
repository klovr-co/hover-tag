from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from scripts import opentag_agent

ROOT = Path(__file__).resolve().parents[1]


def skill_text(name: str, *, raw: bool = False) -> str:
    """A runtime skill's text, with line wrapping folded to single spaces unless ``raw``."""
    text = (ROOT / "runtime-skills/skills" / name / "SKILL.md").read_text(encoding="utf-8")
    return text if raw else " ".join(text.split())


class OpenTagAgentPromptTests(unittest.TestCase):
    def test_standing_instructions_hold_nothing_that_changes_between_requests(self) -> None:
        root = Path("/tag")
        instructions = opentag_agent.build_instructions(
            skill_dir=root / "skill", workdir=root / "workspace", channel_id="C123", output_files=True)
        request = opentag_agent.build_request(
            channel_id="C123", question="Summarize the plan", thread_text="U1: first\nU2: second",
            attachments_dir=root / "inv-7/attachments", allowed_scopes="file://local/tag/workspace",
            output_manifest=root / "inv-7/manifest.json")
        self.assertNotIn("inv-7", instructions)
        self.assertNotIn("Summarize the plan", instructions)
        self.assertIn("File delivery default: `local+slack`", instructions)
        self.assertIn(str(root / "workspace/artifacts/C123"), instructions)
        attachments = root / "inv-7/attachments"
        for value in (str(attachments), str(attachments / "results/images"),
                      str(attachments / "results/artifacts"), str(root / "inv-7/manifest.json"),
                      "file://local/tag/workspace", "Summarize the plan", "Slack thread context:"):
            self.assertIn(value, request)

    def test_continued_request_carries_only_new_thread_messages(self) -> None:
        request = opentag_agent.build_request(
            channel_id="C123", question="And now?", thread_text="U2: new message", attachments_dir=None,
            allowed_scopes="x", continued=True)
        self.assertIn("New Slack thread messages since your last reply", request)
        self.assertIn("U2: new message", request)
        empty = opentag_agent.build_request(channel_id="C123", question="And now?", thread_text="", attachments_dir=None,
                                            allowed_scopes="x", continued=True)
        self.assertIn("(none)", empty)

    def test_slack_prompt_requires_clarification_for_ambiguous_scope(self) -> None:
        prompt = opentag_agent.build_prompt(
            skill_dir=Path("/tmp/open-tag"),
            workdir=Path("/tmp/workspace"),
            channel_id="C123",
            question="search general workspace all",
            thread_text="",
            attachments_dir=None,
            allowed_scopes="slack://tag-t1/channels/general__C123",
        )
        # Search scope is a procedure: the prompt routes unclear scope to the skill that settles it.
        self.assertNotIn("runtime-agent.md", prompt)
        self.assertIn("search general workspace all", prompt)
        # The guard must fire before any search, so it stays in the base, not only in the skill.
        self.assertIn("ask a short scope question and do not search", prompt)
        self.assertIn("this channel's history, use tag-slack-search", prompt)
        search = skill_text("tag-slack-search")
        self.assertIn("`search general workspace all` is ambiguous: ask a short scope question and do not call a search helper", search)
        self.assertIn("Never silently fix a typo", search)
        self.assertIn("`__<conversation id>`", search)

    def test_prompt_loads_channel_memory_and_explains_the_memory_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "memory"
            from scripts.tag_memory import MemoryStore

            MemoryStore(root, "C123", "UANA").save("deadline", "Reports are due Friday.", all_channels=True)
            MemoryStore(root, "C123", "UBEN").save("pricing", "Launch pricing is 49 per seat.", kind="note")
            with patch.dict(os.environ, {"OPENTAG_MEMORY_ROOT": str(root)}):
                prompt = opentag_agent.build_prompt(
                    skill_dir=Path("/tmp/open-tag"),
                    workdir=Path("/tmp/workspace"),
                    channel_id="C123",
                    question="what is due?",
                    thread_text="",
                    attachments_dir=None,
                    allowed_scopes="slack://tag-t1/channels/general__C123",
                )
        self.assertIn("- deadline: Reports are due Friday.", prompt)
        self.assertIn("- pricing: Launch pricing is 49 per seat. (this channel)", prompt)
        self.assertIn(opentag_agent.helper_command(Path("/tmp/open-tag/scripts/NAME.py")), prompt)
        self.assertIn("or forget memory (tag-memory) only when the user explicitly asks", prompt)
        memory = skill_text("tag-memory")
        self.assertIn("`tag_memory.py` helper", memory)
        self.assertIn("Do not save memory on your own initiative", memory)
        self.assertIn("only when the user explicitly says it is for all channels", memory)
        self.assertLess(prompt.index("<tag-memory"), prompt.index("User question:"))

    def test_native_memory_is_disabled_for_both_backends(self) -> None:
        with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0"}):
            opentag_agent.disable_native_memory()
            self.assertEqual("1", os.environ["CLAUDE_CODE_DISABLE_AUTO_MEMORY"])
        with patch.object(opentag_agent, "codex_workspace_args", return_value=[]), \
                patch.object(opentag_agent, "backend_command", side_effect=lambda name: [name]):
            commands = [
                opentag_agent.codex_app_server_command(Path("/work")),
                opentag_agent.codex_stream_command(
                    "prompt", skill_dir=Path("/skill"), workdir=Path("/work"),
                    attachments_dir=None, output_path=Path("/tmp/final.txt"),
                ),
            ]
        for command in commands:
            index = command.index("features.memories=false")
            self.assertEqual("-c", command[index - 1])

    def test_windows_npm_backend_bypasses_command_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "node_modules/@openai/codex/bin/codex.js"
            script.parent.mkdir(parents=True)
            script.write_text("// fixture", encoding="utf-8")
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
            # CI interpreter paths are random and can contain "gws" by chance.
            with patch.object(opentag_agent.sys, "executable", "/usr/bin/python3"):
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

        self.assertIn("Installed commands and skills may be used under their own permissions", prompt)
        self.assertIn("mfs_search.py", prompt)
        self.assertIn("Never expose tokens or credentials", prompt)
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

        self.assertIn("Post to the channel, create or change a Canvas (tag-slack-actions)", prompt)
        self.assertIn("only when the user explicitly asks", prompt)
        self.assertIn("Never comment on a file you have not opened", prompt)
        actions = skill_text("tag-slack-actions")
        self.assertIn("`slack_post_message.py` helper", actions)
        self.assertIn("new top-level channel message", actions)
        self.assertIn("Do not post merely because you produced a summary", actions)
        self.assertIn("`slack_thread_file.py`", skill_text("tag-attachments"))

    def test_slack_prompt_registers_only_explicitly_requested_output_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            prompt = opentag_agent.build_prompt(
                skill_dir=root / "tag",
                workdir=root / "workspace",
                channel_id="C123",
                question="Create an archive",
                thread_text="",
                attachments_dir=root / "attachments",
                allowed_scopes=f"file://local{root / 'workspace'}",
                output_manifest=root / "workspace/.opentag-output.json",
            )

        self.assertIn("Before saving any file for the user, use tag-files", prompt)
        self.assertIn("Never claim that something was saved,\n  posted, remembered, attached, or downloadable", prompt)
        self.assertIn("empty stdout/stderr", prompt)
        files = skill_text("tag-files")
        self.assertIn("`record_output_artifact.py` helper", files)
        self.assertIn("file type is supported", files)
        self.assertIn("supporting files", files)
        self.assertIn("Individual Open file buttons appear only", files)
        self.assertIn("Add `--attach` when", files)
        self.assertIn("Add `--local-only`", files)
        self.assertIn("every file in a multi-file request", files)
        self.assertIn("Do not claim a file is attached", files)
        self.assertIn("Do not mention the manifest helper", files)

    def test_slack_prompt_exposes_generated_image_result_directory(self) -> None:
        prompt = opentag_agent.build_prompt(
            skill_dir=Path("/tmp/open-tag"),
            workdir=Path("/tmp/workspace"),
            channel_id="C123",
            question="Create a launch graphic",
            thread_text="",
            attachments_dir=Path("/tmp/invocation"),
            allowed_scopes="file://local/tmp/workspace",
        )

        self.assertIn(str(Path("/tmp/invocation/results/images")), prompt)
        self.assertIn(str(Path("/tmp/invocation/results/artifacts")), prompt)
        self.assertIn("including\n  generated HTML, goes in the request's temporary artifacts directory", prompt)
        files = skill_text("tag-files")
        self.assertIn("Slack bridge uploads supported files", files)
        self.assertIn("Do not call Slack's API to upload them", files)

    def test_standing_instructions_stay_lean(self) -> None:
        # Procedures belong in runtime skills; the base was ~10,000 characters before they moved.
        with patch.dict(os.environ, {"OPENTAG_PEER_TAGS": "Writer Tag=U0123ABCD", "OPENTAG_HANDOFF_REQUESTS": "/r"}):
            instructions = opentag_agent.build_instructions(
                skill_dir=Path("/tag/release"), workdir=Path("/tag/workspace"), channel_id="C123", output_files=True)
        self.assertLess(len(instructions), 3300)
        self.assertIn("tag-handoff", instructions)

    def test_runtime_skills_ship_and_name_real_helpers(self) -> None:
        import json
        import re

        plugin = ROOT / "runtime-skills"
        self.assertEqual("tag", json.loads((plugin / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["name"])
        shipped = set(json.loads((ROOT / "scripts/runtime-files.json").read_text(encoding="utf-8"))["files"])
        self.assertIn("runtime-skills/.claude-plugin/plugin.json", shipped)
        self.assertEqual(set(opentag_agent.RUNTIME_SKILLS),
                         {path.parent.name for path in (plugin / "skills").glob("*/SKILL.md")})
        for name in opentag_agent.RUNTIME_SKILLS:
            text = skill_text(name, raw=True)
            self.assertIn(f"runtime-skills/skills/{name}/SKILL.md", shipped)
            self.assertRegex(text, rf"\A---\nname: {name}\ndescription: Use .+\n---\n")
            for helper in re.findall(r"`([a-z_]+\.py)`", text):
                self.assertTrue((ROOT / "scripts" / helper).is_file(), helper)

    def test_handoff_skill_states_the_real_default_deadline(self) -> None:
        from scripts import tag_handoff

        self.assertIn(f"(default {tag_handoff.DEFAULT_WAIT_MINUTES})", skill_text("tag-handoff"))

    def test_both_backends_register_the_runtime_skills(self) -> None:
        plugin = str(Path("/tag/release/runtime-skills"))
        with patch.object(opentag_agent, "claude_rule_args", return_value=[]):
            command = opentag_agent.claude_stream_command(
                skill_dir=Path("/tag/release"), workdir=Path("/work"), attachments_dir=None)
        self.assertEqual(plugin, command[command.index("--plugin-dir") + 1])
        with patch.object(opentag_agent, "ClaudeAgentRun") as claude, \
                patch.object(opentag_agent, "run_rich_events", side_effect=lambda start, **_: start(lambda _e: None)):
            opentag_agent.run_claude_sdk_events("p", skill_dir=Path("/tag/release"), workdir=Path("/work"),
                                                attachments_dir=None, timeout=5)
        self.assertEqual([Path(plugin)], claude.call_args.kwargs["plugin_dirs"])
        with patch.object(opentag_agent, "CodexAppServer") as codex, \
                patch.object(opentag_agent, "codex_app_server_command", return_value=["codex"]), \
                patch.object(opentag_agent, "run_rich_events", side_effect=lambda start, **_: start(lambda _e: None)):
            opentag_agent.run_codex_app_server_events("p", workdir=Path("/work"), skill_dir=Path("/tag/release"),
                                                      timeout=5)
        self.assertEqual([Path(plugin) / "skills"], codex.call_args.kwargs["skill_roots"])

    @patch("scripts.opentag_agent.backend_command", return_value=["codex"])
    def test_codex_backend_uses_automatic_workspace_safety_review(self, _backend) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            with patch.object(opentag_agent, "tag_temp_dir", return_value=root), patch(
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
        self.assertIn("features.memories=false", command)
        self.assertIn('service_tier="default"', command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)


class BackendStreamEventTests(unittest.TestCase):
    def setUp(self) -> None:
        # On Windows, Tag resolves codex's npm shim on PATH; these tests don't install Codex.
        resolve = patch.object(opentag_agent, "backend_command", side_effect=lambda name: [name])
        resolve.start()
        self.addCleanup(resolve.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        usage_home = patch("scripts.agent_usage.instance_home", return_value=Path(temporary.name))
        usage_home.start()
        self.addCleanup(usage_home.stop)

    def test_backend_progress_requires_a_recognized_lifecycle_event(self) -> None:
        self.assertTrue(opentag_agent.backend_made_progress({"type": "item.started"}))
        self.assertTrue(opentag_agent.backend_made_progress({"type": "stream_event"}))
        self.assertFalse(opentag_agent.backend_made_progress({"type": "keepalive"}))
        self.assertFalse(opentag_agent.backend_made_progress({"message": "noise"}))

    def test_watchdog_resets_idle_deadline_but_not_maximum_runtime(self) -> None:
        stopped = threading.Event()
        # Proportions matter, not absolute times; generous margins keep slow CI runners honest.
        watchdog = opentag_agent.BackendWatchdog(
            idle_timeout=0.8,
            max_timeout=1.8,
            stop=stopped.set,
        )
        watchdog.start()
        try:
            self.assertFalse(stopped.wait(0.5))
            watchdog.touch()
            self.assertFalse(stopped.wait(0.5))
            watchdog.touch()
            self.assertTrue(stopped.wait(1.2))
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

    def test_claude_event_transport_defaults_to_sdk_and_keeps_print_rollback(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual("sdk", opentag_agent.claude_event_transport())
        with patch.dict(os.environ, {"OPENTAG_CLAUDE_TRANSPORT": "print"}, clear=True):
            self.assertEqual("print", opentag_agent.claude_event_transport())
        with patch.dict(os.environ, {"OPENTAG_CLAUDE_TRANSPORT": "socket"}, clear=True):
            with self.assertRaisesRegex(ValueError, "print or sdk"):
                opentag_agent.claude_event_transport()

    def test_claude_sdk_run_uses_shared_retry_and_exit_codes(self) -> None:
        run = MagicMock()
        run.run.side_effect = [("failed", "API Error: 529 overloaded"), ("interrupted", "")]
        with patch.dict(os.environ, {"OPENTAG_BACKEND_ATTEMPTS": "3"}, clear=False), patch.object(
            opentag_agent, "ClaudeAgentRun", return_value=run
        ) as run_class, patch.object(opentag_agent, "emit_event") as emit, patch.object(
            opentag_agent.time, "sleep"
        ):
            result = opentag_agent.run_claude_sdk_events(
                "prompt", skill_dir=Path("/skill"), workdir=Path("/work"),
                attachments_dir=Path("/attachments"), timeout=30, model="opus",
                reasoning_effort="high", fast_mode=True,
            )

        self.assertEqual(130, result)
        self.assertEqual(2, run_class.call_count)
        self.assertEqual([Path("/skill"), Path("/attachments")], run_class.call_args.kwargs["add_dirs"])
        self.assertEqual(
            {"model": "opus", "reasoning_effort": "high", "fast_mode": True},
            {key: run.run.call_args.kwargs[key] for key in ("model", "reasoning_effort", "fast_mode")},
        )
        self.assertIn(call("status", "Backend busy — retrying (2/3)…"), emit.call_args_list)

    def test_claude_sdk_timeout_and_failure_are_reported(self) -> None:
        run = MagicMock()
        run.run.side_effect = [("timeout", "no backend activity for 30s"),
                               opentag_agent.ClaudeAgentError("Claude Agent SDK failed: auth")]
        with patch.object(opentag_agent, "ClaudeAgentRun", return_value=run), patch.object(
            opentag_agent, "emit_event"
        ) as emit:
            kwargs = {"skill_dir": Path("/s"), "workdir": Path("/w"), "attachments_dir": None, "timeout": 30}
            self.assertEqual(124, opentag_agent.run_claude_sdk_events("p", **kwargs))
            self.assertEqual(1, opentag_agent.run_claude_sdk_events("p", **kwargs))
        self.assertEqual(
            [call("error", "Tag backend timed out: no backend activity for 30s"),
             call("error", "Claude Agent SDK failed: auth")],
            emit.call_args_list,
        )

    def test_codex_event_transport_defaults_to_app_server_and_keeps_exec_rollback(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual("app-server", opentag_agent.codex_event_transport())
        with patch.dict(os.environ, {"OPENTAG_CODEX_TRANSPORT": "exec"}, clear=True):
            self.assertEqual("exec", opentag_agent.codex_event_transport())
        with patch.dict(os.environ, {"OPENTAG_CODEX_TRANSPORT": "socket"}, clear=True):
            with self.assertRaisesRegex(ValueError, "exec or app-server"):
                opentag_agent.codex_event_transport()

    def test_app_server_command_applies_selected_fast_mode(self) -> None:
        with patch.object(opentag_agent, "codex_workspace_args", return_value=[]), \
                patch.object(opentag_agent, "backend_command", side_effect=lambda name: [name]):
            command = opentag_agent.codex_app_server_command(
                Path("/work"),
                fast_mode=True,
            )

        self.assertIn("features.fast_mode=true", command)
        self.assertIn('service_tier="fast"', command)

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


class ChannelArtifactRoutingTests(unittest.TestCase):
    def test_backend_receives_channel_output_location_with_original_workspace(self):
        import sys
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            legacy = root / 'existing.md'
            legacy.write_text('existing', encoding="utf-8")
            def backend(prompt, **kwargs):
                self.assertEqual(kwargs['workdir'], root)
                self.assertTrue((root / 'artifacts/C123').is_dir())
                self.assertIn(str(root / 'artifacts/C123'), prompt)
                self.assertIn('Conversation id: C123', prompt)
                self.assertIn('Before saving any file for the user, use tag-files', prompt)
                files = skill_text('tag-files')
                self.assertIn('`--channel-id` set to the conversation id', files)
                self.assertIn('edit existing', files)
                self.assertIn('workspace root for older files', files)
                self.assertEqual(legacy.read_text(encoding="utf-8"), 'existing')
                return 0
            # Without --event-stream this is claude -p, which runs only as Bypass.
            with patch.dict(os.environ, {"OPENTAG_CLAUDE_PERMISSION_MODE": "bypassPermissions"}, clear=True), patch.object(sys, 'argv', [
                'opentag_agent', '--backend', 'claude', '--channel-id', 'C123',
                '--question', 'Create a report.md', '--workdir', str(root),
                '--thread-file', str(legacy),
                '--output-manifest', str(root / 'manifest.json'),
            ]), patch.object(opentag_agent, 'run_claude', side_effect=backend):
                self.assertEqual(opentag_agent.main(), 0)

class TagRulesAtStartTests(unittest.TestCase):
    """Before Codex runs, the Tag's folder gets the rules for all Tags, or the run is refused."""

    def run_main(self, root: Path, workdir: Path) -> tuple[int, list]:
        import sys
        from scripts import agent_permissions
        seen = []
        (root / "thread.md").write_text("", encoding="utf-8")
        env = {"TAG_HOME": str(root / "install"), "TAG_INSTANCE_HOME": str(root / "home" / ".tag")}
        with patch.dict(os.environ, env, clear=True), patch.object(sys, "argv", [
            "opentag_agent", "--backend", "codex", "--channel-id", "C123", "--question", "hi", "--workdir", str(workdir),
            "--thread-file", str(root / "thread.md"),
        ]), patch.object(opentag_agent, "run_codex", side_effect=lambda prompt, **kw: seen.append(kw) or 0), \
                patch("sys.stderr"):
            agent_permissions.add_rule("codex", None, "block", "git push")
            return opentag_agent.main(), seen

    def test_a_tags_own_folder_gets_a_copy_of_the_rules_for_all_tags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            own = root / "home"
            own.mkdir()
            code, seen = self.run_main(root, own)
            self.assertIn('"git", "push"', (own / ".codex/rules/all-tags.rules").read_text(encoding="utf-8"))
            # The exec rollback can't confirm Codex read them, so it refuses a block.
            self.assertEqual((1, []), (code, seen))

    def test_a_folder_of_yours_never_gets_tag_rules_and_refuses_blocks_for_all_tags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            yours = root / "my-repo"
            yours.mkdir()
            code, seen = self.run_main(root, yours)
            self.assertEqual((1, []), (code, seen))
            self.assertFalse((yours / ".codex").exists())


class ClaudePrintModelTests(unittest.TestCase):
    def test_both_print_paths_forward_only_explicit_models(self):
        for model in (None, "default", "opus"):
            with self.subTest(model=model), patch.object(
                opentag_agent, "stream_command", return_value=(0, "ok", True, False)
            ) as stream, patch.object(opentag_agent.subprocess, "run") as run, \
                    patch.object(opentag_agent, "backend_command", side_effect=lambda name: [name]):
                run.return_value = SimpleNamespace(stdout="", returncode=0)
                kwargs = dict(skill_dir=Path('/skill'), workdir=Path('/work'),
                              attachments_dir=None, timeout=30, model=model)
                opentag_agent.run_claude_events("prompt", **kwargs)
                opentag_agent.run_claude("prompt", **kwargs)
                for command in (stream.call_args.args[0], run.call_args.args[0]):
                    self.assertEqual(model == "opus", "--model" in command)
                    if model == "opus":
                        self.assertEqual("opus", command[command.index("--model") + 1])
