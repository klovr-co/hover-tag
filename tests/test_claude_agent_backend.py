from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
import types
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts import claude_agent_backend
from scripts.claude_agent_backend import (
    APPROVAL_LABEL_COMMAND,
    APPROVAL_LABEL_TOOL,
    ClaudeAgentError,
    ClaudeAgentRun,
    ClaudeEventMapper,
    normalize_catalog,
)


# Stand-ins named like the SDK message classes; the adapter keys on class names.
@dataclass
class StreamEvent:
    event: dict[str, Any]
    uuid: str = "u"
    session_id: str = "s"
    parent_tool_use_id: str | None = None


@dataclass
class AssistantMessage:
    content: list[dict[str, Any]]
    model: str = "claude"
    parent_tool_use_id: str | None = None
    error: str | None = None


@dataclass
class UserMessage:
    content: list[dict[str, Any]]
    parent_tool_use_id: str | None = None


@dataclass
class ResultMessage:
    subtype: str = "success"
    is_error: bool = False
    result: str | None = None
    terminal_reason: str | None = None
    errors: list[str] | None = None
    api_error_status: int | None = None


def stream(message_id: str, *texts: str, stop_reason: str, tool: bool = False) -> list[StreamEvent]:
    events = [StreamEvent({"type": "message_start", "message": {"id": message_id}})]
    events.extend(
        StreamEvent({"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}})
        for text in texts
    )
    if tool:
        events.append(StreamEvent({"type": "content_block_start", "content_block": {"type": "tool_use"}}))
    events.append(StreamEvent({"type": "message_delta", "delta": {"stop_reason": stop_reason}}))
    return events


def mapped(messages: list[Any]) -> list[dict[str, Any]]:
    mapper = ClaudeEventMapper()
    return [event for message in messages for event in mapper.map(claude_agent_backend.message_payload(message))]


class ClaudeEventMapperTests(unittest.TestCase):
    def test_streams_only_messages_that_end_the_turn(self) -> None:
        events = mapped([
            *stream("m1", "Let me check ", "the files.", stop_reason="tool_use", tool=True),
            AssistantMessage([{"id": "t1", "name": "Bash", "input": {"command": "pytest"}}]),
            UserMessage([{"tool_use_id": "t1", "content": "2 passed", "is_error": False}]),
            *stream("m2", "All ", "tests pass.", stop_reason="end_turn"),
            AssistantMessage([{"text": "All tests pass."}]),
            ResultMessage(result="All tests pass."),
        ])

        answer = [event["text"] for event in events if event["type"] == "message_delta"]
        self.assertEqual(["All ", "tests pass."], answer)
        self.assertIn({"type": "message_start", "message_id": "m1", "phase": "commentary"}, events)
        self.assertNotIn("Let me check ", json.dumps(events))
        self.assertEqual(
            {"type": "message_complete", "message_id": "m2", "phase": "final_answer", "text": "All tests pass."},
            events[-2],
        )
        self.assertEqual({"type": "turn_complete", "status": "completed"}, events[-1])

    def test_maps_tools_to_sanitized_activity_labels(self) -> None:
        events = mapped([
            AssistantMessage([
                {"id": "t1", "name": "Bash", "input": {"command": "python mfs_search.py launch"}},
                {"id": "t2", "name": "Edit", "input": {"file_path": "/work/notes.md", "old_string": "a"}},
                {"id": "t3", "name": "Read", "input": {"file_path": "/work/plan.md"}},
                {"id": "t4", "name": "mcp__github__search_issues", "input": {"q": "bug"}},
                {"id": "t5", "name": "WebSearch", "input": {"query": "release"}},
                {"id": "t6", "name": "TodoWrite", "input": {"todos": []}},
            ]),
            UserMessage([{"tool_use_id": "t1", "content": "hits", "is_error": False},
                         {"tool_use_id": "t2", "content": "denied", "is_error": True}]),
        ])

        starts = {event["activity_id"]: event["label"] for event in events if event["type"] == "activity_start"}
        self.assertEqual({
            "t1": "Searching connected knowledge…",
            "t2": "Updating files…",
            "t3": "Reading files…",
            "t4": "Searching GitHub issues…",
            "t5": "Searching the web…",
        }, starts)
        self.assertEqual("Still waiting for GitHub…",
                         next(event["wait_label"] for event in events if event.get("activity_id") == "t4"))
        completions = {event["activity_id"]: event["status"] for event in events
                       if event["type"] == "activity_complete"}
        self.assertEqual({"t1": "completed", "t2": "failed"}, completions)

    def test_reports_the_main_model_once_and_ignores_subagents(self) -> None:
        events = mapped([
            AssistantMessage([{"text": "a"}], model="claude-opus-5-5"),
            AssistantMessage([{"text": "b"}], model="claude-opus-5-5"),
            AssistantMessage([{"text": "c"}], model="claude-haiku-4-5", parent_tool_use_id="task"),
            AssistantMessage([{"text": "d"}], model="<synthetic>"),
        ])
        self.assertEqual([{"type": "run_info", "model": "claude-opus-5-5"}],
                         [event for event in events if event["type"] == "run_info"])

    def test_tool_secrets_are_redacted_from_activity_details(self) -> None:
        events = mapped([
            AssistantMessage([{"id": "t1", "name": "Bash",
                               "input": {"command": "curl -H 'Authorization: Bearer abc123' https://x"}}]),
        ])
        self.assertNotIn("abc123", json.dumps(events))

    def test_subagent_text_is_never_streamed(self) -> None:
        events = mapped([
            *[replace_parent(event) for event in stream("m1", "secret", stop_reason="end_turn")],
            ResultMessage(result="Done."),
        ])
        self.assertNotIn("secret", json.dumps(events))
        self.assertEqual("Done.", events[-2]["text"])

    def test_result_without_streamed_text_still_returns_final_answer(self) -> None:
        events = mapped([ResultMessage(result="Short answer.")])
        self.assertEqual(["message_start", "message_complete", "turn_complete"], [e["type"] for e in events])
        self.assertEqual("final_answer", events[0]["phase"])

    def test_interrupted_result_closes_running_tools(self) -> None:
        events = mapped([
            AssistantMessage([{"id": "t1", "name": "Bash", "input": {"command": "sleep 60"}}]),
            ResultMessage(subtype="error_during_execution", is_error=True, terminal_reason="aborted_tools"),
        ])
        self.assertEqual("interrupted", events[-2]["status"])
        self.assertEqual({"type": "turn_complete", "status": "interrupted"}, events[-1])

    def test_failed_result_reports_error_and_http_code(self) -> None:
        events = mapped([
            AssistantMessage([{"text": "x"}], error="rate_limit"),
            ResultMessage(is_error=True, result="API Error: 429 rate limit", api_error_status=429),
        ])
        self.assertIn({"type": "error", "text": "Claude reported rate limit", "code": "rate_limit"}, events)
        self.assertEqual({"type": "turn_complete", "status": "failed",
                          "text": "API Error: 429 rate limit", "code": "http_429"}, events[-1])


def replace_parent(event: StreamEvent) -> StreamEvent:
    event.parent_tool_use_id = "task"
    return event


class FakeClient:
    """Minimal ClaudeSDKClient replacement driven by a scripted coroutine."""

    instances: list["FakeClient"] = []
    script: Any = None
    server_info: dict[str, Any] | None = None

    def __init__(self, options: Any) -> None:
        self.options = options
        self.prompt: str | None = None
        self.interrupted = asyncio.Event()
        self.disconnected = False
        FakeClient.instances.append(self)

    async def connect(self) -> None:
        return None

    async def query(self, prompt: str) -> None:
        self.prompt = prompt

    async def receive_response(self):
        async for message in FakeClient.script(self):
            yield message

    async def interrupt(self) -> None:
        self.interrupted.set()

    async def disconnect(self) -> None:
        self.disconnected = True

    async def get_server_info(self) -> dict[str, Any] | None:
        return FakeClient.server_info


@dataclass
class PermissionResultAllow:
    behavior: str = "allow"
    updated_input: dict[str, Any] | None = None


@dataclass
class PermissionResultDeny:
    behavior: str = "deny"
    message: str = ""


def fake_options(**kwargs: Any) -> types.SimpleNamespace:
    return types.SimpleNamespace(**kwargs)


FAKE_SDK = types.SimpleNamespace(
    ClaudeSDKClient=FakeClient,
    ClaudeAgentOptions=fake_options,
    PermissionResultAllow=PermissionResultAllow,
    PermissionResultDeny=PermissionResultDeny,
)


class ClaudeAgentRunTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeClient.instances = []
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.approvals = self.root / "approvals"
        self.approvals.mkdir()
        self.control = self.root / "control"
        self.control.write_text("", encoding="utf-8")
        patcher = patch.dict("sys.modules", {"claude_agent_sdk": FAKE_SDK})
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {"OPENTAG_CLAUDE_CLI": "/bin/claude"}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.directory.cleanup)

    def run_agent(self, script: Any, *, timeout: int = 30, max_timeout: int | None = None,
                  **kwargs: Any) -> tuple[tuple[str, str], list[dict[str, Any]]]:
        FakeClient.script = script
        events: list[dict[str, Any]] = []
        agent = ClaudeAgentRun(cwd=self.root, add_dirs=[self.root / "skill"], timeout=timeout,
                               max_timeout=max_timeout, control_file=self.control, run_id="run1",
                               approval_dir=self.approvals)
        result = agent.run("prompt", emit=events.append, **{
            "model": None, "reasoning_effort": None, **kwargs,
        })
        return result, events

    def test_passes_model_effort_fast_mode_and_permissions_to_sdk(self) -> None:
        async def script(_client):
            yield ResultMessage(result="ok")

        (status, _), _events = self.run_agent(script, model="opus", reasoning_effort="xhigh", fast_mode=True)
        options = FakeClient.instances[0].options
        self.assertEqual("completed", status)
        self.assertEqual(("opus", "xhigh", "auto"), (options.model, options.effort, options.permission_mode))
        self.assertEqual({"fastMode": True}, json.loads(options.settings))
        self.assertEqual([str(self.root / "skill")], options.add_dirs)
        self.assertEqual("/bin/claude", options.cli_path)
        self.assertTrue(options.include_partial_messages)
        self.assertTrue(FakeClient.instances[0].disconnected)

    def test_default_model_alias_lets_claude_choose(self) -> None:
        async def script(_client):
            yield ResultMessage(result="ok")

        self.run_agent(script, model="default")
        self.assertFalse(hasattr(FakeClient.instances[0].options, "model"))
        self.assertFalse(hasattr(FakeClient.instances[0].options, "settings"))

    def test_workspace_mcp_config_is_layered(self) -> None:
        (self.root / ".mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")

        async def script(_client):
            yield ResultMessage(result="ok")

        self.run_agent(script)
        self.assertEqual(self.root / ".mcp.json", FakeClient.instances[0].options.mcp_servers)

    def test_slack_approval_allows_one_tool_call(self) -> None:
        decisions: list[Any] = []

        async def script(client):
            decisions.append(await client.options.can_use_tool("Bash", {"command": "rm x"}, None))
            yield ResultMessage(result="done")

        def approve(events: list[dict[str, Any]]) -> None:
            while not any(event["type"] == "approval_request" for event in events):
                time.sleep(0.01)
            approval_id = next(event["approval_id"] for event in events if event["type"] == "approval_request")
            (self.approvals / f"{approval_id}.json").write_text('{"decision": "approve"}', encoding="utf-8")

        FakeClient.script = script
        events: list[dict[str, Any]] = []
        agent = ClaudeAgentRun(cwd=self.root, timeout=30, control_file=self.control, run_id="run1",
                               approval_dir=self.approvals)
        approver = threading.Thread(target=approve, args=(events,))
        approver.start()
        status, _ = agent.run("prompt", model=None, reasoning_effort=None, emit=events.append)
        approver.join(timeout=5)

        self.assertEqual("completed", status)
        self.assertEqual(APPROVAL_LABEL_COMMAND,
                         next(event["label"] for event in events if event["type"] == "approval_request"))
        self.assertEqual(PermissionResultAllow(updated_input={"command": "rm x"}), decisions[0])
        self.assertEqual([], list(self.approvals.iterdir()))

    def test_denies_interactive_questions_and_unapproved_tools(self) -> None:
        decisions: list[Any] = []

        async def script(client):
            decisions.append(await client.options.can_use_tool("AskUserQuestion", {}, None))
            self.control.write_text("run1", encoding="utf-8")
            decisions.append(await client.options.can_use_tool("mcp__x__write", {}, None))
            yield ResultMessage(result="done")

        _result, events = self.run_agent(script)
        self.assertEqual(["deny", "deny"], [decision.behavior for decision in decisions])
        self.assertEqual(APPROVAL_LABEL_TOOL,
                         next(event["label"] for event in events if event["type"] == "approval_request"))

    def test_control_file_interrupts_and_waits_for_confirmation(self) -> None:
        async def script(client):
            yield AssistantMessage([{"id": "t1", "name": "Bash", "input": {"command": "sleep 60"}}])
            self.control.write_text("run1", encoding="utf-8")
            await client.interrupted.wait()
            yield ResultMessage(is_error=True, terminal_reason="aborted_tools")

        (status, _detail), events = self.run_agent(script)
        self.assertEqual("interrupted", status)
        self.assertEqual("interrupted", events[-2]["status"])

    def test_idle_timeout_interrupts_and_reports_timeout(self) -> None:
        async def script(client):
            await client.interrupted.wait()
            yield ResultMessage(is_error=True, terminal_reason="aborted_streaming")

        with patch.object(claude_agent_backend, "CONTROL_POLL_SECONDS", 0.01):
            (status, detail), _events = self.run_agent(script, timeout=0, max_timeout=30)
        self.assertEqual(("timeout", "no backend activity for 0s"), (status, detail))

    def test_unconfirmed_timeout_is_bounded(self) -> None:
        async def script(_client):
            await asyncio.sleep(30)
            yield ResultMessage(result="late")

        with patch.object(claude_agent_backend, "INTERRUPT_GRACE_SECONDS", 0.05), patch.object(
            claude_agent_backend, "CONTROL_POLL_SECONDS", 0.01
        ):
            started = time.monotonic()
            (status, detail), _events = self.run_agent(script, timeout=0, max_timeout=30)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual("timeout", status)
        self.assertIn("did not confirm interruption", detail)

    def test_stream_ending_without_result_is_a_bounded_failure(self) -> None:
        async def script(_client):
            if False:
                yield None

        with self.assertRaisesRegex(ClaudeAgentError, "ended without a result"):
            self.run_agent(script)

    def test_missing_sdk_reports_actionable_error(self) -> None:
        with patch.dict("sys.modules", {"claude_agent_sdk": None}):
            with self.assertRaisesRegex(ClaudeAgentError, "tag upgrade"):
                ClaudeAgentRun(cwd=self.root, timeout=1).run(
                    "prompt", model=None, reasoning_effort=None, emit=lambda _event: None,
                )

    def test_model_catalog_uses_initialize_metadata_without_a_turn(self) -> None:
        FakeClient.server_info = {"models": [
            {"value": "default", "displayName": "Default (recommended)",
             "supportedEffortLevels": ["low", "high"]},
            {"value": "opus", "displayName": "Opus", "supportedEffortLevels": ["low", "max"],
             "supportsFastMode": True, "resolvedModel": "claude-opus-5-5"},
            {"value": "haiku", "displayName": "Haiku"},
            {"displayName": "missing value"},
        ]}
        catalog = ClaudeAgentRun(cwd=self.root, timeout=5).model_catalog()
        self.assertEqual(["default", "opus", "haiku"], [item["model"] for item in catalog])
        self.assertTrue(catalog[0]["isDefault"])
        self.assertTrue(catalog[1]["supportsFastMode"])
        self.assertEqual("claude-opus-5-5", catalog[1]["resolvedModel"])
        self.assertEqual([], catalog[2]["supportedEfforts"])
        self.assertIsNone(FakeClient.instances[0].prompt)

    def test_pending_receive_is_cancelled_before_disconnect_on_timeout_or_error(self):
        for fail_interrupt in (False, True):
            cancelled = []
            disconnect_states = []

            async def script(_client):
                try:
                    await asyncio.sleep(30)
                    yield ResultMessage(result="late")
                finally:
                    cancelled.append(True)

            async def disconnect(_client):
                disconnect_states.append(list(cancelled))

            async def interrupt(_agent):
                raise RuntimeError("interrupt failed")

            with self.subTest(fail_interrupt=fail_interrupt), patch.object(
                FakeClient, "disconnect", disconnect
            ), patch.object(claude_agent_backend, "INTERRUPT_GRACE_SECONDS", 0.01), patch.object(
                claude_agent_backend, "CONTROL_POLL_SECONDS", 0.01
            ):
                if fail_interrupt:
                    with patch.object(ClaudeAgentRun, "interrupt", interrupt), self.assertRaisesRegex(
                        RuntimeError, "interrupt failed"
                    ):
                        self.run_agent(script, timeout=0, max_timeout=30)
                else:
                    (status, _), _events = self.run_agent(script, timeout=0, max_timeout=30)
                    self.assertEqual("timeout", status)
                self.assertEqual([True], cancelled)
                self.assertEqual([[True]], disconnect_states)

    def test_permission_mode_is_validated(self) -> None:
        with patch.dict(os.environ, {"OPENTAG_CLAUDE_PERMISSION_MODE": "plan"}):
            with self.assertRaisesRegex(ValueError, "OPENTAG_CLAUDE_PERMISSION_MODE"):
                claude_agent_backend.claude_permission_mode()


class CatalogTests(unittest.TestCase):
    def test_normalize_catalog_ignores_malformed_entries(self) -> None:
        self.assertEqual([], normalize_catalog(None))
        self.assertEqual([], normalize_catalog([{"value": ""}, "opus"]))


if __name__ == "__main__":
    unittest.main()
