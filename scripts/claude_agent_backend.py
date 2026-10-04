"""Claude Agent SDK transport and Tag event normalization.

This adapter gives the Claude backend the same request-scoped contract as the
Codex App Server adapter: one SDK client and one session per Slack request,
final-answer-only streaming, sanitized activity events, Slack approvals,
control-file interruption, and separate idle and maximum deadlines.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

try:
    from .opentag_process_env import text_only_environment
    from . import agent_connection, agent_usage
    from .agent_activity import (
        APPROVAL_POLL_SECONDS,
        APPROVAL_TIMEOUT_SECONDS,
        INTERRUPT_GRACE_SECONDS,
        MCP_SERVICE_NAMES,
        activity_label,
        token_usage,
    )
    from .tag_activity_details import item_activity_details, preview
except ImportError:  # Direct script execution does not create a package context.
    from opentag_process_env import text_only_environment
    import agent_connection, agent_usage
    from agent_activity import (
        APPROVAL_POLL_SECONDS,
        APPROVAL_TIMEOUT_SECONDS,
        INTERRUPT_GRACE_SECONDS,
        MCP_SERVICE_NAMES,
        activity_label,
        token_usage,
    )
    from tag_activity_details import item_activity_details, preview


CONTROL_POLL_SECONDS = 0.1
CATALOG_TIMEOUT_SECONDS = 30.0
PERMISSION_MODES = frozenset({"default", "acceptEdits", "auto", "bypassPermissions", "dontAsk"})
FINAL_STOP_REASONS = frozenset({"end_turn", "stop_sequence", "max_tokens", "refusal", "pause_turn"})
READ_TOOLS = frozenset({"Read", "Glob", "Grep", "LS", "NotebookRead"})
FILE_CHANGE_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})
AGENT_TOOLS = frozenset({"Task", "Agent", "Skill", "ToolSearch"})
# Planning and bookkeeping tools are private model state, not workspace activity.
SILENT_TOOLS = frozenset({"TodoWrite", "TodoRead", "EnterPlanMode", "ExitPlanMode", "AskUserQuestion"})
INTERACTIVE_TOOLS = frozenset({"AskUserQuestion", "ExitPlanMode"})

APPROVAL_LABEL_COMMAND = "run a command that requires approval"
APPROVAL_LABEL_FILE = "apply a file change that requires approval"
APPROVAL_LABEL_ACCESS = "use additional filesystem or network access"
APPROVAL_LABEL_TOOL = "use a tool that requires approval"


class ClaudeAgentError(RuntimeError):
    """A bounded, user-safe Claude Agent SDK transport failure."""


def claude_permission_mode() -> str:
    """Return the validated permission mode; auto mirrors Codex auto review."""
    mode = os.getenv("OPENTAG_CLAUDE_PERMISSION_MODE", "auto").strip()
    if mode not in PERMISSION_MODES:
        raise ValueError("OPENTAG_CLAUDE_PERMISSION_MODE must be one of " + ", ".join(sorted(PERMISSION_MODES)))
    return mode


def claude_cli_path() -> str | None:
    """Prefer the operator's signed-in CLI; otherwise use the SDK's bundled CLI."""
    configured = os.getenv("OPENTAG_CLAUDE_CLI", "").strip()
    return configured or shutil.which("claude")


def message_payload(message: Any) -> dict[str, Any]:
    """Convert one SDK message into a plain dict tagged with its SDK class."""
    payload = asdict(message) if is_dataclass(message) else dict(message) if isinstance(message, dict) else {}
    payload.setdefault("kind", type(message).__name__)
    return payload


def tool_item(tool_use_id: str, name: str, tool_input: dict[str, Any]) -> dict[str, Any] | None:
    """Translate a Claude tool call into the App Server item vocabulary."""
    if name == "Bash":
        return {"id": tool_use_id, "type": "commandExecution", "command": tool_input.get("command")}
    if name in FILE_CHANGE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        kind = "add" if name == "Write" else "update"
        changes = [{"path": path, "kind": kind}] if isinstance(path, str) else []
        return {"id": tool_use_id, "type": "fileChange", "changes": changes}
    if name in {"WebSearch", "WebFetch", "web_search", "web_fetch"}:
        query = tool_input.get("query") if "earch" in name else tool_input.get("url")
        return {"id": tool_use_id, "type": "webSearch", "query": query}
    if name.startswith("mcp__"):
        _, _, rest = name.partition("mcp__")
        server, _, tool = rest.partition("__")
        return {"id": tool_use_id, "type": "mcpToolCall", "server": server, "tool": tool,
                "arguments": tool_input}
    if name in AGENT_TOOLS:
        return {"id": tool_use_id, "type": "dynamicToolCall", "tool": name, "arguments": tool_input}
    return None


def tool_activity(
    tool_use_id: str,
    name: str,
    tool_input: dict[str, Any],
) -> tuple[str, dict[str, Any] | None] | None:
    """Return a public label and its App Server-shaped item, if any."""
    if name in SILENT_TOOLS:
        return None
    if name in READ_TOOLS:
        return "Reading files…", None
    item = tool_item(tool_use_id, name, tool_input)
    if item is None:
        return "Using agent tools…", None
    label = activity_label(item)
    return (label, item) if label else None


def tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block["text"] for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return ""


class ClaudeEventMapper:
    """Map SDK messages to Tag's Slack-safe event contract.

    Claude does not label an answer before it is written. A message is the
    final answer only when the API reports a terminal stop reason; messages
    that stop for tool use are private commentary. Text deltas are therefore
    buffered per message and released once the stop reason is known.
    """

    def __init__(self) -> None:
        self.current_message: str | None = None
        self.pending_deltas: dict[str, list[str]] = {}
        self.final_ids: list[str] = []
        self.final_text: dict[str, str] = {}
        self.tools: dict[str, tuple[str, dict[str, Any] | None]] = {}
        self.completed = False
        self.reported_model: str | None = None

    @property
    def tools_running(self) -> bool:
        return bool(self.tools)

    def map(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        kind = payload.get("kind")
        if kind == "StreamEvent":
            return self._stream_event(payload)
        if kind == "AssistantMessage":
            return self._assistant_message(payload)
        if kind == "UserMessage":
            return self._user_message(payload)
        if kind == "ResultMessage":
            return self._result(payload)
        return []

    def _stream_event(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if payload.get("parent_tool_use_id") is not None:
            return []
        event = payload.get("event")
        if not isinstance(event, dict):
            return []
        event_type = event.get("type")
        if event_type == "message_start":
            message = event.get("message")
            message_id = message.get("id") if isinstance(message, dict) else None
            self.current_message = message_id if isinstance(message_id, str) and message_id else uuid.uuid4().hex
            self.pending_deltas[self.current_message] = []
            return []
        if self.current_message is None:
            return []
        if event_type == "content_block_delta":
            delta = event.get("delta")
            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                text = delta.get("text")
                if isinstance(text, str) and text:
                    self.pending_deltas.setdefault(self.current_message, []).append(text)
            return []
        if event_type == "message_delta":
            delta = event.get("delta")
            stop_reason = delta.get("stop_reason") if isinstance(delta, dict) else None
            if not isinstance(stop_reason, str):
                return []
            message_id = self.current_message
            pending = self.pending_deltas.pop(message_id, [])
            if stop_reason not in FINAL_STOP_REASONS:
                return [{"type": "message_start", "message_id": message_id, "phase": "commentary"}]
            if not pending:
                return []
            self.final_ids.append(message_id)
            self.final_text[message_id] = "".join(pending)
            events = [{"type": "message_start", "message_id": message_id, "phase": "final_answer"}]
            events.extend(
                {"type": "message_delta", "message_id": message_id, "phase": "final_answer", "text": text}
                for text in pending
            )
            return events
        return []

    def _assistant_message(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        model = payload.get("model")
        if (
            payload.get("parent_tool_use_id") is None
            and isinstance(model, str) and model and not model.startswith("<")
            and model != self.reported_model
        ):
            # Synthetic error messages use placeholder names such as <synthetic>.
            self.reported_model = model
            events.append({"type": "run_info", "model": model})
        error = payload.get("error")
        if isinstance(error, str) and error and payload.get("parent_tool_use_id") is None:
            events.append({"type": "error", "text": f"Claude reported {error.replace('_', ' ')}", "code": error})
        for block in payload.get("content") or []:
            if not isinstance(block, dict):
                continue
            if isinstance(block.get("tool_use_id"), str):
                events.extend(self._complete_tool(block))
                continue
            tool_use_id, name = block.get("id"), block.get("name")
            if not isinstance(tool_use_id, str) or not isinstance(name, str):
                continue
            tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
            mapped = tool_activity(tool_use_id, name, tool_input)
            if mapped is None or tool_use_id in self.tools:
                continue
            label, item = mapped
            self.tools[tool_use_id] = (label, item)
            if item is not None:
                details = item_activity_details(item, completed=False)
            else:
                details = {"tool": name}
                target = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("pattern")
                if isinstance(target, str):
                    details["input"] = preview(target)
            service = None
            if item is not None and item.get("type") == "mcpToolCall":
                server = item.get("server")
                service = MCP_SERVICE_NAMES.get(server.lower()) if isinstance(server, str) else None
            events.append({
                "type": "activity_start",
                "activity_id": tool_use_id,
                "label": label,
                "wait_label": f"Still waiting for {service}…" if service else "This operation is still running…",
                "details": details,
            })
        return events

    def _user_message(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        content = payload.get("content")
        if not isinstance(content, list):
            return []
        events: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("tool_use_id"), str):
                events.extend(self._complete_tool(block))
        return events

    def _complete_tool(self, block: dict[str, Any]) -> list[dict[str, Any]]:
        started = self.tools.pop(block["tool_use_id"], None)
        if started is None:
            return []
        label, item = started
        status = "failed" if block.get("is_error") else "completed"
        output = tool_result_text(block.get("content"))
        if item is not None and item.get("type") == "commandExecution":
            details = item_activity_details({**item, "aggregatedOutput": output}, completed=True)
        elif item is not None:
            details = item_activity_details(item, completed=True)
        else:
            details = {}
        if output and "output" not in details:
            details["output"] = preview(output)
        return [{
            "type": "activity_complete",
            "activity_id": block["tool_use_id"],
            "label": label,
            "status": status,
            "details": details,
        }]

    def _result(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if self.completed:
            return []
        self.completed = True
        events: list[dict[str, Any]] = agent_usage.claude_event(payload)
        for tool_use_id, (label, _item) in list(self.tools.items()):
            events.append({"type": "activity_complete", "activity_id": tool_use_id, "label": label,
                           "status": "interrupted", "details": {}})
        self.tools.clear()
        terminal_reason = payload.get("terminal_reason")
        interrupted = isinstance(terminal_reason, str) and terminal_reason.startswith("aborted")
        if payload.get("is_error") and not interrupted:
            errors = payload.get("errors")
            detail = payload.get("result") if isinstance(payload.get("result"), str) else ""
            if not detail and isinstance(errors, list):
                detail = "; ".join(str(item) for item in errors if item)
            terminal: dict[str, Any] = {"type": "turn_complete", "status": "failed",
                                        "text": detail or str(payload.get("subtype") or "Claude turn failed")}
            status_code = payload.get("api_error_status")
            if isinstance(status_code, int):
                terminal["code"] = f"http_{status_code}"
            events.append(terminal)
            return events
        if not interrupted:
            result = payload.get("result")
            message_id = self.final_ids[-1] if self.final_ids else None
            text = result if isinstance(result, str) and result else (
                self.final_text.get(message_id, "") if message_id else ""
            )
            if text:
                if message_id is None:
                    message_id = uuid.uuid4().hex
                    events.append({"type": "message_start", "message_id": message_id, "phase": "final_answer"})
                events.append({"type": "message_complete", "message_id": message_id,
                               "phase": "final_answer", "text": text})
        events.append({"type": "turn_complete", "status": "interrupted" if interrupted else "completed"})
        return events


def approval_label(tool_name: str) -> str:
    if tool_name == "Bash":
        return APPROVAL_LABEL_COMMAND
    if tool_name in FILE_CHANGE_TOOLS:
        return APPROVAL_LABEL_FILE
    if tool_name in READ_TOOLS or tool_name in {"WebFetch", "WebSearch"}:
        return APPROVAL_LABEL_ACCESS
    return APPROVAL_LABEL_TOOL


def workspace_mcp_config(workdir: Path) -> Path | None:
    """Layer only the workspace's Tag-owned MCP servers, like Codex's config."""
    config = workdir / ".mcp.json"
    return config if config.is_file() else None


def normalize_catalog(models: Any) -> list[dict[str, Any]]:
    """Convert the CLI's initialize model list into Tag's catalog fields."""
    catalog: list[dict[str, Any]] = []
    for item in models if isinstance(models, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("value"), str) or not item["value"]:
            continue
        efforts = item.get("supportedEffortLevels")
        catalog.append({
            "model": item["value"],
            "displayName": item.get("displayName") if isinstance(item.get("displayName"), str) else item["value"],
            "isDefault": item["value"] == "default",
            "supportedEfforts": [effort for effort in efforts if isinstance(effort, str)]
            if isinstance(efforts, list) else [],
            "supportsFastMode": item.get("supportsFastMode") is True,
            "resolvedModel": item.get("resolvedModel") if isinstance(item.get("resolvedModel"), str) else None,
        })
    return catalog


class ClaudeAgentRun:
    """One Claude Agent SDK client for one request-scoped Tag run."""

    def __init__(
        self,
        *,
        cwd: Path,
        add_dirs: list[Path] | None = None,
        timeout: int,
        max_timeout: int | None = None,
        control_file: Path | None = None,
        run_id: str | None = None,
        approval_dir: Path | None = None,
        text_only_instructions: str | None = None,
    ) -> None:
        self.cwd = cwd
        self.add_dirs = add_dirs or []
        self.timeout = timeout
        self.max_timeout = max_timeout if max_timeout is not None else timeout
        self.control_file = control_file
        self.run_id = run_id
        self.approval_dir = approval_dir
        self.text_only_instructions = text_only_instructions
        self.client: Any = None
        self.interrupt_sent = False
        self.pending_approvals = 0
        self.stderr: list[str] = []
        self.last_activity = time.monotonic()

    @staticmethod
    def _sdk() -> tuple[Any, Any, Any, Any]:
        try:
            from claude_agent_sdk import (
                ClaudeAgentOptions,
                ClaudeSDKClient,
                PermissionResultAllow,
                PermissionResultDeny,
            )
        except ImportError as exc:
            raise ClaudeAgentError(
                "The Claude Agent SDK is not installed; run tag upgrade or reinstall Tag's runtime"
            ) from exc
        return ClaudeSDKClient, ClaudeAgentOptions, PermissionResultAllow, PermissionResultDeny

    def options(
        self,
        *,
        model: str | None,
        reasoning_effort: str | None,
        fast_mode: bool,
        emit: Callable[[dict[str, Any]], None] | None,
        deadline: float,
    ) -> Any:
        _client, options_factory, allow, deny = self._sdk()

        def capture_stderr(line: str) -> None:
            self.stderr.append(line)
            del self.stderr[:-200]

        # This adapter supports one-time SDK decisions only; it exposes no
        # persistent/session grants or automatic-review denial retry protocol.
        async def can_use_tool(tool_name: str, tool_input: dict[str, Any], _context: Any) -> Any:
            if self.text_only_instructions is not None:
                return deny(message="Tools are disabled for reply summaries.")
            if tool_name in INTERACTIVE_TOOLS:
                return deny(message="Interactive questions are unavailable in Slack; ask in your final answer instead.")
            if self.approval_dir is None or emit is None:
                return deny(message="Tag cannot request approval for this run.")
            approval_id = uuid.uuid4().hex
            emit({"type": "approval_request", "approval_id": approval_id, "label": approval_label(tool_name)})
            self.pending_approvals += 1
            try:
                approved = await self._wait_for_approval(
                    approval_id, min(deadline, time.monotonic() + APPROVAL_TIMEOUT_SECONDS),
                )
            finally:
                self.pending_approvals -= 1
                self.last_activity = time.monotonic()
            if approved:
                return allow(updated_input=tool_input)
            return deny(message="Denied in Slack")

        kwargs: dict[str, Any] = {
            "cwd": str(self.cwd),
            "add_dirs": [str(path) for path in self.add_dirs],
            "permission_mode": claude_permission_mode(),
            "include_partial_messages": True,
            "setting_sources": ["user", "project", "local"],
            "can_use_tool": can_use_tool,
            "stderr": capture_stderr,
        }
        try:
            agent_connection.routing("claude")
        except ValueError as exc:
            raise ClaudeAgentError(str(exc)) from None
        if agent_connection.active("claude"):
            try:
                kwargs["env"] = agent_connection.claude_environment()
            except ValueError as exc:
                raise ClaudeAgentError(str(exc)) from None
            kwargs["model"] = model or agent_connection.models("claude")[0]
        cli_path = claude_cli_path()
        if cli_path:
            kwargs["cli_path"] = cli_path
        mcp_config = workspace_mcp_config(self.cwd)
        if mcp_config is not None:
            kwargs["mcp_servers"] = mcp_config
        if model and model != "default":
            kwargs["model"] = model
        if reasoning_effort:
            kwargs["effort"] = reasoning_effort
        if fast_mode:
            kwargs["settings"] = json.dumps({"fastMode": True})
        if self.text_only_instructions is not None:
            kwargs.update(tools=[], mcp_servers={}, setting_sources=[], add_dirs=[],
                          permission_mode="dontAsk", max_turns=1,
                          system_prompt=self.text_only_instructions,
                          env=text_only_environment(os.environ),
                          extra_args={"strict-mcp-config": None, "disable-slash-commands": None},
                          settings=json.dumps({"disableAllHooks": True}))
        return options_factory(**kwargs)

    async def _wait_for_approval(self, approval_id: str, deadline: float) -> bool:
        assert self.approval_dir is not None
        decision_file = self.approval_dir / f"{approval_id}.json"
        while time.monotonic() < deadline:
            if self.interrupt_sent or self._control_requested():
                return False
            try:
                payload = json.loads(decision_file.read_text(encoding="utf-8"))
            except FileNotFoundError:
                await asyncio.sleep(APPROVAL_POLL_SECONDS)
                continue
            except (OSError, json.JSONDecodeError):
                decision_file.unlink(missing_ok=True)
                return False
            decision_file.unlink(missing_ok=True)
            return isinstance(payload, dict) and payload.get("decision") == "approve"
        return False

    def _control_requested(self) -> bool:
        if not self.control_file or not self.run_id:
            return False
        try:
            return self.control_file.read_text(encoding="utf-8").strip() == self.run_id
        except OSError:
            return False

    def model_catalog(self) -> list[dict[str, Any]]:
        """Read the signed-in account's models without starting a turn."""
        return asyncio.run(asyncio.wait_for(self._model_catalog(), CATALOG_TIMEOUT_SECONDS))

    async def _model_catalog(self) -> list[dict[str, Any]]:
        client_factory, options_factory, _allow, _deny = self._sdk()
        kwargs: dict[str, Any] = {"cwd": str(self.cwd), "setting_sources": ["user", "project", "local"]}
        try:
            agent_connection.routing("claude")
        except ValueError as exc:
            raise ClaudeAgentError(str(exc)) from None
        if agent_connection.active("claude"):
            try:
                kwargs["env"] = agent_connection.claude_environment()
            except ValueError as exc:
                raise ClaudeAgentError(str(exc)) from None
            kwargs["model"] = agent_connection.models("claude")[0]
        cli_path = claude_cli_path()
        if cli_path:
            kwargs["cli_path"] = cli_path
        client = client_factory(options_factory(**kwargs))
        try:
            await client.connect()
            info = await client.get_server_info()
        finally:
            await client.disconnect()
        if not isinstance(info, dict):
            raise ClaudeAgentError("Claude returned no initialization metadata")
        return normalize_catalog(info.get("models"))

    def run(
        self,
        prompt: str,
        *,
        model: str | None,
        reasoning_effort: str | None,
        fast_mode: bool = False,
        emit: Callable[[dict[str, Any]], None],
    ) -> tuple[str, str]:
        return asyncio.run(self._run(prompt, model=model, reasoning_effort=reasoning_effort,
                                     fast_mode=fast_mode, emit=emit))

    async def _run(
        self,
        prompt: str,
        *,
        model: str | None,
        reasoning_effort: str | None,
        fast_mode: bool,
        emit: Callable[[dict[str, Any]], None],
    ) -> tuple[str, str]:
        started = time.monotonic()
        max_deadline = started + self.max_timeout
        self.last_activity = started
        client_factory = self._sdk()[0]
        options = self.options(model=model, reasoning_effort=reasoning_effort, fast_mode=fast_mode,
                               emit=emit, deadline=max_deadline)
        self.client = client_factory(options)
        mapper = ClaudeEventMapper()
        terminal: dict[str, Any] | None = None
        next_message: asyncio.Task[Any] | None = None
        try:
            try:
                await asyncio.wait_for(self.client.connect(), timeout=max(1.0, max_deadline - time.monotonic()))
                await self.client.query(prompt)
            except asyncio.TimeoutError as exc:
                raise ClaudeAgentError("Claude Agent SDK did not start before the deadline") from exc
            except Exception as exc:  # noqa: BLE001 - SDK raises several connection error types
                raise ClaudeAgentError(self._failure_message(exc)) from exc
            stream = self.client.receive_response().__aiter__()
            timed_out = False
            timeout_detail = ""
            interrupt_deadline = float("inf")
            while True:
                if next_message is None:
                    next_message = asyncio.ensure_future(stream.__anext__())
                done, _pending = await asyncio.wait({next_message}, timeout=CONTROL_POLL_SECONDS)
                now = time.monotonic()
                if not done:
                    if timed_out:
                        if now >= interrupt_deadline:
                            return "timeout", f"{timeout_detail}; Claude did not confirm interruption before cleanup"
                        continue
                    if self._control_requested():
                        await self.interrupt()
                        interrupt_deadline = min(interrupt_deadline, now + INTERRUPT_GRACE_SECONDS)
                    if self.pending_approvals or mapper.tools_running:
                        self.last_activity = now
                    if now >= max_deadline or now >= self.last_activity + self.timeout:
                        timed_out = True
                        timeout_detail = (
                            f"maximum runtime of {self.max_timeout}s exceeded" if now >= max_deadline
                            else f"no backend activity for {self.timeout}s"
                        )
                        interrupt_deadline = now + INTERRUPT_GRACE_SECONDS
                        await self.interrupt()
                    continue
                task, next_message = next_message, None
                try:
                    message = task.result()
                except StopAsyncIteration:
                    break
                except Exception as exc:  # noqa: BLE001 - surface bounded SDK failures
                    raise ClaudeAgentError(self._failure_message(exc)) from exc
                if not timed_out:
                    self.last_activity = now
                for event in mapper.map(message_payload(message)):
                    emit(event)
                    if event.get("type") == "turn_complete":
                        terminal = event
                if terminal is not None:
                    break
            if timed_out:
                return "timeout", timeout_detail
            if terminal is None:
                if self.interrupt_sent:
                    return "interrupted", ""
                raise ClaudeAgentError(self._failure_message(None))
            return str(terminal.get("status", "failed")), str(terminal.get("text", ""))
        finally:
            if next_message is not None and not next_message.done():
                next_message.cancel()
                await asyncio.gather(next_message, return_exceptions=True)
            await self.close()

    async def interrupt(self) -> None:
        if self.interrupt_sent or self.client is None:
            return
        self.interrupt_sent = True
        try:
            await asyncio.wait_for(self.client.interrupt(), timeout=INTERRUPT_GRACE_SECONDS)
        except Exception:  # noqa: BLE001 - cleanup still bounds an unresponsive CLI
            pass

    async def close(self) -> None:
        if self.client is None:
            return
        client, self.client = self.client, None
        try:
            await asyncio.wait_for(client.disconnect(), timeout=5)
        except Exception:  # noqa: BLE001 - the CLI process is terminated by disconnect or exit
            pass

    def _failure_message(self, exc: BaseException | None) -> str:
        detail = "\n".join(self.stderr).strip()[-4000:]
        base = f"Claude Agent SDK failed: {exc}" if exc is not None else "Claude Agent SDK ended without a result"
        return f"{base}: {detail}" if detail else base
