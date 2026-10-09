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
    usage: dict[str, Any] | None = None


@dataclass
class UserMessage:
    content: list[dict[str, Any]]
    parent_tool_use_id: str | None = None


@dataclass
class SystemMessage:
    subtype: str
    data: dict[str, Any]


@dataclass
class ResultMessage:
    subtype: str = "success"
    is_error: bool = False
    result: str | None = None
    session_id: str | None = None
    num_turns: int = 1
    terminal_reason: str | None = None
    errors: list[str] | None = None
    api_error_status: int | None = None
    usage: dict[str, Any] | None = None


@dataclass
class TaskStartedMessage:
    task_id: str
    task_type: str = "local_agent"
    subtype: str = "task_started"


@dataclass
class TaskNotificationMessage:
    task_id: str
    status: str = "completed"
    subtype: str = "task_notification"


@dataclass
class TaskUpdatedMessage:
    task_id: str
    patch: dict[str, Any]
    subtype: str = "task_updated"


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

    def test_waits_for_background_agents_before_completing(self) -> None:
        events = mapped([
            AssistantMessage([{"id": "t1", "name": "Agent", "input": {"run_in_background": True}}]),
            TaskStartedMessage("a1"),
            TaskStartedMessage("a2"),
            TaskStartedMessage("shell", task_type="local_bash"),
            UserMessage([{"tool_use_id": "t1", "content": "started"}]),
            *stream("m1", "I'll report back.", stop_reason="end_turn"),
            ResultMessage(result="I'll report back."),
            TaskNotificationMessage("a1"),
            TaskUpdatedMessage("a2", {"status": "killed"}),
            *stream("m2", "Closed 3 issues.", stop_reason="end_turn"),
            ResultMessage(result="Closed 3 issues."),
        ])

        self.assertNotIn("I'll report back.", json.dumps(events))
        self.assertEqual(1, sum(event["type"] == "turn_complete" for event in events))
        self.assertEqual(
            {"type": "message_complete", "message_id": "m2", "phase": "final_answer", "text": "Closed 3 issues."},
            events[-2],
        )
        self.assertEqual({"type": "turn_complete", "status": "completed"}, events[-1])

    def test_reports_main_thread_conversation_size(self) -> None:
        usage = {"input_tokens": 10, "cache_read_input_tokens": 20000, "cache_creation_input_tokens": 300,
                 "output_tokens": 50}
        events = mapped([
            AssistantMessage([{"text": "hi"}], usage=usage),
            AssistantMessage([{"text": "sub"}], parent_tool_use_id="t1", usage={**usage, "input_tokens": 999999}),
        ])
        self.assertEqual([20360], [event["tokens"] for event in events if event["type"] == "context"])

    def test_failed_turn_completes_even_with_background_agents(self) -> None:
        events = mapped([
            TaskStartedMessage("a1"),
            ResultMessage(is_error=True, result="API Error: 500"),
        ])
        self.assertEqual("failed", events[-1]["status"])

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

    async def receive_messages(self):
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
    updated_permissions: list[Any] | None = None


@dataclass
class PermissionResultDeny:
    behavior: str = "deny"
    message: str = ""
    interrupt: bool = False


@dataclass
class PermissionRuleValue:
    tool_name: str
    rule_content: str | None = None


@dataclass
class PermissionUpdate:
    type: str
    rules: list[PermissionRuleValue] | None = None
    behavior: str | None = None
    destination: str | None = None
    directories: list[str] | None = None


CHROME_RULE = PermissionUpdate("addRules", [PermissionRuleValue("Bash", "claude --chrome:*")], "allow", "localSettings")


def fake_options(**kwargs: Any) -> types.SimpleNamespace:
    return types.SimpleNamespace(**kwargs)


FAKE_SDK = types.SimpleNamespace(
    ClaudeSDKClient=FakeClient,
    ClaudeAgentOptions=fake_options,
    PermissionResultAllow=PermissionResultAllow,
    PermissionResultDeny=PermissionResultDeny,
)
# Like the real SDK, rule classes are only exported from the types module.
FAKE_SDK_TYPES = types.SimpleNamespace(PermissionUpdate=PermissionUpdate, PermissionRuleValue=PermissionRuleValue)


class ClaudeAgentRunTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeClient.instances = []
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.approvals = self.root / "approvals"
        self.approvals.mkdir()
        self.control = self.root / "control"
        self.control.write_text("", encoding="utf-8")
        patcher = patch.dict("sys.modules", {"claude_agent_sdk": FAKE_SDK,
                                                "claude_agent_sdk.types": FAKE_SDK_TYPES})
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

    def test_tag_thinking_level_reaches_the_sdk_effort_option(self) -> None:
        from scripts import agent_models, slack_socket_agent

        async def script(_client):
            yield ResultMessage(result="ok")

        catalog = [agent_models.ModelOption("opus", "Opus", ("low", "medium", "high", "max"),
                                            default_reasoning_effort="high", is_default=True, backend="claude")]
        with patch.dict(os.environ, {"OPENTAG_DEFAULT_MODEL": "claude:opus", "OPENTAG_DEFAULT_EFFORT": "max"}), \
                patch.object(agent_models, "discover_models", return_value=catalog), \
                patch.object(agent_models, "backend_signed_in", side_effect=lambda name: name == "claude"):
            settings = slack_socket_agent.default_agent_settings(agent_models.discover_tag_models("claude"))
        self.run_agent(script, model=settings.model, reasoning_effort=settings.reasoning_effort)
        self.assertEqual(("opus", "max"), (FakeClient.instances[0].options.model, FakeClient.instances[0].options.effort))

    def test_claude_auto_memory_is_disabled_for_tag_runs(self) -> None:
        async def script(_client):
            yield ResultMessage(result="ok")

        self.run_agent(script)
        self.assertEqual({"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}, FakeClient.instances[0].options.env)

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

    def decide(self, choice_label: str, context: Any, tool_input: dict[str, Any] | None = None,
               ) -> tuple[Any, list[dict[str, Any]]]:
        """Run one Bash approval and click the Slack choice with this label."""
        tool_input = tool_input or {"command": "claude --chrome open"}
        decisions: list[Any] = []

        async def script(client):
            decisions.append(await client.options.can_use_tool("Bash", tool_input, context))
            yield ResultMessage(result="done")

        def click(events: list[dict[str, Any]]) -> None:
            while not any(event["type"] == "approval_request" for event in events):
                time.sleep(0.01)
            request = next(event for event in events if event["type"] == "approval_request")
            choice = next(c["id"] for c in request["choices"] if c["label"] == choice_label)
            (self.approvals / f"{request['approval_id']}.json").write_text(
                json.dumps({"choice": choice}), encoding="utf-8")

        FakeClient.script = script
        events: list[dict[str, Any]] = []
        agent = ClaudeAgentRun(cwd=self.root, timeout=30, control_file=self.control, run_id="run1",
                               approval_dir=self.approvals)
        clicker = threading.Thread(target=click, args=(events,))
        clicker.start()
        status, _ = agent.run("prompt", model=None, reasoning_effort=None, emit=events.append)
        clicker.join(timeout=5)
        self.assertEqual("completed", status)
        self.assertEqual([], list(self.approvals.iterdir()))
        return decisions[0], events

    def test_slack_approval_allows_one_tool_call(self) -> None:
        decision, events = self.decide("Allow once", None, {"command": "rm x"})
        request = next(event for event in events if event["type"] == "approval_request")
        self.assertEqual(APPROVAL_LABEL_COMMAND, request["label"])
        self.assertEqual(["Allow once", "Deny", "Deny and stop"], [c["label"] for c in request["choices"]])
        self.assertTrue(all("result" not in c for c in request["choices"]))
        self.assertEqual(PermissionResultAllow(updated_input={"command": "rm x"}), decision)
        self.assertIn({"type": "approval_expired", "approval_id": request["approval_id"]}, events)

    def test_suggested_rule_offers_task_and_saved_scopes(self) -> None:
        context = types.SimpleNamespace(suggestions=[
            CHROME_RULE, PermissionUpdate("setMode", behavior=None),
        ])
        _decision, events = self.decide("Deny", context)
        choices = next(event for event in events if event["type"] == "approval_request")["choices"]
        self.assertEqual(["Allow once", "Allow for this task", "Always allow", "Deny", "Deny and stop"],
                         [c["label"] for c in choices])
        saved = choices[2]
        self.assertTrue(saved["persistent"])
        self.assertIn("Bash(claude --chrome:*)", saved["detail"])
        self.assertFalse(choices[1]["persistent"])

    def test_task_scope_grants_the_rule_for_this_session_only(self) -> None:
        decision, _events = self.decide("Allow for this task", types.SimpleNamespace(suggestions=[CHROME_RULE]))
        self.assertEqual("allow", decision.behavior)
        self.assertEqual(["session"], [u.destination for u in decision.updated_permissions])
        self.assertEqual("localSettings", CHROME_RULE.destination)  # The CLI's suggestion is not mutated.

    def test_saved_rule_goes_to_the_tag_workspace_settings(self) -> None:
        context = types.SimpleNamespace(suggestions=[
            PermissionUpdate("addRules", [PermissionRuleValue("Bash", "claude --chrome:*")], "allow", "userSettings"),
        ])
        decision, _events = self.decide("Always allow", context)
        self.assertEqual("allow", decision.behavior)
        self.assertEqual([("localSettings", [PermissionRuleValue("Bash", "claude --chrome:*")])],
                         [(u.destination, u.rules) for u in decision.updated_permissions])

    def test_exact_command_suggestion_also_offers_a_saved_prefix_rule(self) -> None:
        exact = PermissionUpdate("addRules", [PermissionRuleValue("Bash", "claude --chrome --version")], "allow")
        decision, events = self.decide("Always allow claude --chrome commands", types.SimpleNamespace(suggestions=[exact]),
                                       {"command": "claude --chrome --version"})
        choices = next(event for event in events if event["type"] == "approval_request")["choices"]
        prefix = next(c for c in choices if c["label"] == "Always allow claude --chrome commands")
        self.assertTrue(prefix["persistent"])
        # The main row is allow once, the broadest saved rule, and deny.
        self.assertEqual(["Allow once", "Always allow claude --chrome commands", "Deny"],
                         [c["label"] for c in choices if c["primary"]])
        self.assertIn("Bash(claude --chrome:*)", prefix["detail"])
        self.assertEqual([PermissionUpdate("addRules", [PermissionRuleValue("Bash", "claude --chrome:*")],
                                           "allow", "localSettings")], decision.updated_permissions)

    def test_prefix_rule_needs_a_plain_command_with_a_subcommand(self) -> None:
        for command in ("ls foo", "claude --chrome x; rm y", "echo $(id) a b", "a 'b c' d"):
            self.assertIsNone(claude_agent_backend.command_prefix(command), command)
        labels = [c["label"] for c in claude_agent_backend.approval_choices(
            [], "Read", {"command": "git push origin"}, lambda *_: None)]
        self.assertNotIn("Always allow this prefix", labels)

    def test_folder_suggestions_offer_task_and_saved_access(self) -> None:
        folder = PermissionUpdate("addDirectories", destination="session", directories=["/Users/me/Downloads"])
        decision, events = self.decide("Always allow", types.SimpleNamespace(suggestions=[
            folder, PermissionUpdate("addDirectories", directories=["relative"]),
        ]), {"command": "ls"})
        choices = next(event for event in events if event["type"] == "approval_request")["choices"]
        self.assertIn("folder /Users/me/Downloads", choices[1]["detail"])
        self.assertNotIn("relative", choices[2]["detail"])
        self.assertEqual([("addDirectories", "localSettings", ["/Users/me/Downloads"])],
                         [(u.type, u.destination, u.directories) for u in decision.updated_permissions])

    def test_deny_and_stop_interrupts_the_task(self) -> None:
        decision, _events = self.decide("Deny and stop", None)
        self.assertEqual(("deny", True), (decision.behavior, decision.interrupt))

    def test_unsafe_or_non_allow_suggestions_are_not_offered(self) -> None:
        choices = claude_agent_backend.approval_choices([
            PermissionUpdate("addRules", [PermissionRuleValue("Bash", "x\nrm -rf /")], "allow"),
            PermissionUpdate("addRules", [PermissionRuleValue("Bash", "rm:*")], "deny"),
            PermissionUpdate("addRules", [], "allow"),
            {"type": "addRules", "behavior": "allow", "rules": [{"tool_name": "WebFetch", "rule_content": None}]},
        ])
        self.assertEqual(["Allow once", "Allow for this task", "Always allow", "Deny", "Deny and stop"],
                         [c["label"] for c in choices])
        self.assertTrue(choices[2]["detail"].endswith(": WebFetch"))
        self.assertEqual(["Allow once", "Always allow", "Deny"], [c["label"] for c in choices if c["primary"]])
        self.assertEqual([{"type": "addRules", "behavior": "allow", "destination": "session",
                           "rules": [{"tool_name": "WebFetch", "rule_content": None}]}],
                         choices[1]["result"]["updates"])

    def test_generic_approval_cannot_stand_in_for_a_choice(self) -> None:
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
        agent.run("prompt", model=None, reasoning_effort=None, emit=events.append)
        approver.join(timeout=5)
        self.assertEqual("deny", decisions[0].behavior)

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

    def test_run_continues_until_background_agents_report_back(self) -> None:
        async def script(_client):
            yield TaskStartedMessage("a1")
            for event in stream("m1", "Agents are working.", stop_reason="end_turn"):
                yield event
            yield ResultMessage(result="Agents are working.")
            yield TaskNotificationMessage("a1")
            for event in stream("m2", "All done.", stop_reason="end_turn"):
                yield event
            yield ResultMessage(result="All done.")

        result, events = self.run_agent(script)

        self.assertEqual(("completed", ""), result)
        answers = [event["text"] for event in events if event["type"] == "message_complete"]
        self.assertEqual(["All done."], answers)
        self.assertTrue(FakeClient.instances[0].disconnected)

    def test_resumes_the_slack_threads_session_and_reports_it(self) -> None:
        async def script(_client):
            yield SystemMessage("init", {"session_id": "s-1"})
            yield ResultMessage(result="Next answer", session_id="s-1")

        FakeClient.script = script
        events: list[dict[str, Any]] = []
        agent = ClaudeAgentRun(cwd=self.root, timeout=30, resume_session_id="s-1")
        result = agent.run("prompt", model=None, reasoning_effort=None, emit=events.append)

        self.assertEqual(("completed", ""), result)
        self.assertEqual("s-1", FakeClient.instances[0].options.resume)
        self.assertEqual([{"type": "session", "session_id": "s-1"}],
                         [event for event in events if event["type"] == "session"])

    def test_missing_resume_does_not_report_the_stale_session_from_init(self) -> None:
        async def script(client):
            if getattr(client.options, "resume", None):
                yield SystemMessage("init", {"session_id": "gone"})
                yield ResultMessage(subtype="error_during_execution", is_error=True, num_turns=0,
                                    session_id="gone",
                                    errors=["No conversation found with session ID: gone"])
                return
            yield SystemMessage("init", {"session_id": "new"})
            yield ResultMessage(result="ok", session_id="new")

        FakeClient.script = script
        events: list[dict[str, Any]] = []
        agent = ClaudeAgentRun(cwd=self.root, timeout=30, resume_session_id="gone")
        agent.run("prompt", model=None, reasoning_effort=None, emit=events.append)

        self.assertEqual([{"type": "session", "session_id": "new"}],
                         [event for event in events if event["type"] == "session"])

    def test_standing_instructions_are_the_system_prompt_and_resumes_get_new_messages(self) -> None:
        async def script(client):
            yield ResultMessage(result="ok", session_id="s-1")

        FakeClient.script = script
        agent = ClaudeAgentRun(cwd=self.root, timeout=30, resume_session_id="s-1", instructions="Standing rules")
        agent.run("Full prompt", model=None, reasoning_effort=None, emit=lambda _event: None,
                  continued_prompt="Only new messages")
        self.assertEqual("Standing rules", FakeClient.instances[0].options.system_prompt)
        self.assertEqual("Only new messages", FakeClient.instances[0].prompt)

    def test_failed_resume_sends_the_full_prompt_to_the_fresh_session(self) -> None:
        async def script(client):
            if getattr(client.options, "resume", None):
                raise RuntimeError("No conversation found with session ID: gone")
            yield ResultMessage(result="ok", session_id="new")

        FakeClient.script = script
        agent = ClaudeAgentRun(cwd=self.root, timeout=30, resume_session_id="gone")
        agent.run("Full prompt", model=None, reasoning_effort=None, emit=lambda _event: None,
                  continued_prompt="Only new messages")
        self.assertEqual("Full prompt", FakeClient.instances[-1].prompt)

    def test_missing_session_starts_a_fresh_one_without_reporting_failure(self) -> None:
        async def script(client):
            if getattr(client.options, "resume", None):
                yield ResultMessage(subtype="error_during_execution", is_error=True, num_turns=0,
                                    session_id="gone",
                                    errors=["No conversation found with session ID: gone"])
                return
            yield ResultMessage(result="Fresh answer", session_id="new")

        FakeClient.script = script
        events: list[dict[str, Any]] = []
        agent = ClaudeAgentRun(cwd=self.root, timeout=30, resume_session_id="gone")
        result = agent.run("prompt", model=None, reasoning_effort=None, emit=events.append)

        self.assertEqual(("completed", ""), result)
        self.assertEqual(2, len(FakeClient.instances))
        self.assertFalse(hasattr(FakeClient.instances[1].options, "resume"))
        self.assertNotIn("gone", json.dumps(events))
        self.assertEqual(1, sum(event["type"] == "turn_complete" for event in events))
        self.assertIn({"type": "session", "session_id": "new"}, events)

    def test_missing_session_raised_by_the_sdk_also_starts_fresh(self) -> None:
        async def script(client):
            if getattr(client.options, "resume", None):
                raise RuntimeError("Claude Code returned an error result: No conversation found with session ID: gone")
            yield ResultMessage(result="Fresh answer", session_id="new")

        FakeClient.script = script
        events: list[dict[str, Any]] = []
        agent = ClaudeAgentRun(cwd=self.root, timeout=30, resume_session_id="gone")
        self.assertEqual(("completed", ""), agent.run("prompt", model=None, reasoning_effort=None, emit=events.append))
        self.assertIn({"type": "session", "session_id": "new"}, events)

    def test_session_title_uses_the_sdk_rename(self) -> None:
        renamed = []
        with patch.dict("sys.modules", {"claude_agent_sdk": types.SimpleNamespace(
                rename_session=lambda *args, **kwargs: renamed.append((args, kwargs)))}):
            ClaudeAgentRun.set_session_title("s-1", "Closed 12 issues")
        self.assertEqual([(("s-1", "Closed 12 issues"), {"directory": None})], renamed)

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
