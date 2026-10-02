"""Codex App Server stdio transport and Tag event normalization.

Transport safeguards build on OpenTag's Apache-2.0 App Server adapter. Tag's
event mapping and per-request lifecycle are local to this repository; see
NOTICE for attribution.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    from . import tag_chatgpt
    from .agent_activity import (
        APPROVAL_POLL_SECONDS,
        APPROVAL_TIMEOUT_SECONDS,
        INTERRUPT_GRACE_SECONDS,
        MCP_SERVICE_NAMES,
        activity_label,
    )
    from .tag_activity_details import item_activity_details
    from .tag_approval_choices import approval_choices, public_approval_choices, auto_review_details
except ImportError:  # Direct script execution does not create a package context.
    import tag_chatgpt
    from agent_activity import (
        APPROVAL_POLL_SECONDS,
        APPROVAL_TIMEOUT_SECONDS,
        INTERRUPT_GRACE_SECONDS,
        MCP_SERVICE_NAMES,
        activity_label,
    )
    from tag_activity_details import item_activity_details
    from tag_approval_choices import approval_choices, public_approval_choices, auto_review_details


# Prompts are controlled by Tag, while completed tool and image events may
# legitimately contain substantially larger output from Codex. The response
# bound accommodates Tag's 15 MiB image limit after base64 expansion.
MAX_REQUEST_LINE_BYTES = 1024 * 1024
MAX_RESPONSE_LINE_BYTES = 32 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 60.0
AUTO_REVIEW_RETRY_LABEL = "retry an action denied by automatic review"
MAX_AUTO_REVIEW_APPROVALS = 10

APPROVAL_REQUEST_LABELS = {
    "item/commandExecution/requestApproval": "run a command outside the workspace sandbox",
    "item/fileChange/requestApproval": "change files outside the workspace sandbox",
    "item/permissions/requestApproval": "use additional filesystem or network access",
    "applyPatchApproval": "apply a file change that requires approval",
    "execCommandApproval": "run a command that requires approval",
}

class CodexAppServerError(RuntimeError):
    """A bounded, user-safe App Server transport failure."""


class JsonLineDecoder:
    """Decode fragmented JSONL without allowing an unbounded partial line."""

    def __init__(self, max_line_bytes: int = MAX_RESPONSE_LINE_BYTES) -> None:
        self.max_line_bytes = max_line_bytes
        self.buffer = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        # The previous partial buffer contains no newline. Scan only new bytes
        # so a large image event delivered in small chunks remains linear-time.
        scan_from = len(self.buffer)
        self.buffer.extend(chunk)
        lines: list[bytes] = []
        while True:
            newline = self.buffer.find(b"\n", scan_from)
            if newline < 0:
                if len(self.buffer) > self.max_line_bytes:
                    raise CodexAppServerError("Codex App Server emitted an oversized JSONL line")
                break
            line = bytes(self.buffer[:newline]).strip()
            del self.buffer[: newline + 1]
            scan_from = 0
            if len(line) > self.max_line_bytes:
                raise CodexAppServerError("Codex App Server emitted an oversized JSONL line")
            if line:
                lines.append(line)
        return lines

    def finish(self) -> None:
        if self.buffer.strip():
            raise CodexAppServerError("Codex App Server exited with a truncated JSONL line")


class CodexEventMapper:
    """Map native lifecycle notifications to Tag's Slack-safe event contract."""

    def __init__(self) -> None:
        self.message_phases: dict[str, str | None] = {}
        self.pending_deltas: dict[str, list[str]] = {}
        self.completed_messages: list[tuple[str, str | None, str]] = []
        self.emitted_final_ids: set[str] = set()
        self.failure_detail = ""

    def map(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return []
        if method == "item/started":
            return self._item_started(params)
        if method == "item/agentMessage/delta":
            return self._message_delta(params)
        if method == "item/completed":
            return self._item_completed(params)
        if method == "turn/completed":
            return self._turn_completed(params)
        if method == "error":
            error = params.get("error")
            text = error.get("message") if isinstance(error, dict) else None
            if isinstance(text, str) and text and not params.get("willRetry"):
                code = error.get("code")
                prefix = f"{code}: " if isinstance(code, str) and code in tag_chatgpt.PLAN_ERRORS else ""
                self.failure_detail = prefix + text
                return [{"type": "error", "text": self.failure_detail}]
        return []

    def _item_started(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        item = params.get("item")
        if not isinstance(item, dict):
            return []
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            return []
        if item.get("type") == "agentMessage":
            phase = item.get("phase") if item.get("phase") in {"commentary", "final_answer"} else None
            self.message_phases[item_id] = phase
            events = [{"type": "message_start", "message_id": item_id, "phase": phase}]
            if phase == "final_answer":
                for delta in self.pending_deltas.pop(item_id, []):
                    events.append(self._delta_event(item_id, delta))
            elif phase == "commentary":
                self.pending_deltas.pop(item_id, None)
            return events
        label = activity_label(item)
        if label:
            server = item.get("server")
            service = (MCP_SERVICE_NAMES.get(server.lower())
                       if item.get("type") == "mcpToolCall" and isinstance(server, str) else None)
            return [{"type": "activity_start", "activity_id": item_id, "label": label,
                     "wait_label": f"Still waiting for {service}…" if service else "This operation is still running…",
                     "details": item_activity_details(item, completed=False)}]
        return []

    def _message_delta(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        item_id = params.get("itemId")
        delta = params.get("delta")
        if not isinstance(item_id, str) or not isinstance(delta, str) or not delta:
            return []
        phase = self.message_phases.get(item_id)
        if phase == "final_answer":
            return [self._delta_event(item_id, delta)]
        if phase is None:
            self.pending_deltas.setdefault(item_id, []).append(delta)
        return []

    def _delta_event(self, item_id: str, delta: str) -> dict[str, Any]:
        return {
            "type": "message_delta",
            "message_id": item_id,
            "phase": "final_answer",
            "text": delta,
        }

    def _item_completed(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        item = params.get("item")
        if not isinstance(item, dict):
            return []
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            return []
        if item.get("type") == "agentMessage":
            raw_phase = item.get("phase")
            phase = raw_phase if raw_phase in {"commentary", "final_answer"} else self.message_phases.get(item_id)
            text = item.get("text")
            if not isinstance(text, str):
                text = ""
            self.message_phases[item_id] = phase
            self.completed_messages.append((item_id, phase, text))
            pending = self.pending_deltas.pop(item_id, [])
            if phase == "final_answer" and text:
                self.emitted_final_ids.add(item_id)
                events = [self._delta_event(item_id, delta) for delta in pending]
                events.append({
                    "type": "message_complete",
                    "message_id": item_id,
                    "phase": phase,
                    "text": text,
                })
                return events
            return []
        label = activity_label(item)
        if label:
            status = item.get("status") if isinstance(item.get("status"), str) else "completed"
            return [{
                "type": "activity_complete",
                "activity_id": item_id,
                "label": label,
                "status": status,
                "details": item_activity_details(item, completed=True),
            }]
        return []

    def _turn_completed(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        turn = params.get("turn")
        if not isinstance(turn, dict):
            return []
        status = turn.get("status")
        events: list[dict[str, Any]] = []
        if not self.emitted_final_ids:
            fallback = next(
                ((item_id, text) for item_id, phase, text in reversed(self.completed_messages)
                 if phase is None and text),
                None,
            )
            if fallback:
                item_id, text = fallback
                events.append({
                    "type": "message_complete",
                    "message_id": item_id,
                    "phase": "final_answer",
                    "text": text,
                })
        terminal = {"type": "turn_complete", "status": status or "failed"}
        error = turn.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            code = error.get("code")
            prefix = f"{code}: " if isinstance(code, str) and code in tag_chatgpt.PLAN_ERRORS else ""
            terminal["text"] = prefix + error["message"]
        elif status == "failed" and self.failure_detail:
            terminal["text"] = self.failure_detail
        events.append(terminal)
        return events


class CodexAppServer:
    """One local App Server process for one request-scoped Tag run."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout: int,
        max_timeout: int | None = None,
        control_file: Path | None = None,
        run_id: str | None = None,
        approval_dir: Path | None = None,
    ) -> None:
        self.command = command
        self.chatgpt_token = ""
        self.auth_identity: tuple | None = None
        self.token_renewal_deadline = float("inf")
        self.renewing_token = False
        self.cwd = cwd
        self.timeout = timeout
        self.max_timeout = max_timeout if max_timeout is not None else timeout
        self.control_file = control_file
        self.run_id = run_id
        self.approval_dir = approval_dir
        self.process: subprocess.Popen[bytes] | None = None
        self.messages: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self.stderr = bytearray()
        self.write_lock = threading.Lock()
        self.next_id = 1
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.interrupt_sent = False
        self.reader_threads: list[threading.Thread] = []
        self.auto_review_denials: list[dict[str, Any]] = []
        self.seen_auto_reviews: set[str] = set()
        self.held_auto_review_messages: list[dict[str, Any]] = []

    def model_catalog(self) -> list[dict[str, Any]]:
        """Read models for the signed-in account without creating a thread or turn."""
        if tag_chatgpt.enabled():
            return tag_chatgpt.models()
        mapper = CodexEventMapper()
        deadline = time.monotonic() + self.timeout
        try:
            self._start()
            self._request("initialize", {"clientInfo": self._client_info()},
                          mapper, lambda event: None, deadline)
            self._notify("initialized", {})
            models: list[dict[str, Any]] = []
            cursor = None
            for _ in range(10):
                result = self._request("model/list", {"cursor": cursor} if cursor else {},
                                       mapper, lambda event: None, deadline)
                if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                    raise CodexAppServerError("Codex returned an invalid model/list response")
                models.extend(item for item in result["data"] if isinstance(item, dict))
                next_cursor = result.get("nextCursor")
                if not next_cursor:
                    return models
                if not isinstance(next_cursor, str) or next_cursor == cursor:
                    break
                cursor = next_cursor
            raise CodexAppServerError("Codex model/list pagination did not finish")
        finally:
            self.close()

    def run(
        self,
        prompt: str,
        *,
        model: str | None,
        reasoning_effort: str | None,
        emit: Callable[[dict[str, Any]], None],
    ) -> tuple[str, str]:
        self._start()
        mapper = CodexEventMapper()
        max_deadline = time.monotonic() + self.max_timeout
        try:
            self._request(
                "initialize",
                {"clientInfo": self._client_info(),
                 "capabilities": {"experimentalApi": True}},
                mapper,
                emit,
                max_deadline,
            )
            self._notify("initialized", {})
            thread_params: dict[str, Any] = {
                "cwd": str(self.cwd),
                "sandbox": "workspace-write",
                "approvalsReviewer": "auto_review",
                "ephemeral": not bool(self.chatgpt_token),
                "serviceName": "tag_slack_bridge",
            }
            if model:
                thread_params["model"] = model
            thread_result = self._request("thread/start", thread_params, mapper, emit, max_deadline)
            thread = thread_result.get("thread") if isinstance(thread_result, dict) else None
            if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
                raise CodexAppServerError("Codex returned an invalid thread/start response")
            self.thread_id = thread["id"]
            resolved_model = thread_result.get("model") or thread.get("model")
            if isinstance(resolved_model, str) and resolved_model:
                info: dict[str, Any] = {"type": "run_info", "model": resolved_model}
                resolved_effort = reasoning_effort or thread_result.get("reasoningEffort")
                if isinstance(resolved_effort, str) and resolved_effort:
                    info["reasoning_effort"] = resolved_effort
                emit(info)
            turn_params: dict[str, Any] = {
                "threadId": self.thread_id,
                "input": [{"type": "text", "text": prompt}],
            }
            if model:
                turn_params["model"] = model
            if reasoning_effort:
                turn_params["effort"] = reasoning_effort
            while True:
                turn_result = self._request("turn/start", turn_params, mapper, emit, max_deadline)
                turn = turn_result.get("turn") if isinstance(turn_result, dict) else None
                if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
                    raise CodexAppServerError("Codex returned an invalid turn/start response")
                self.turn_id = turn["id"]
                status, detail = self._consume_turn(
                    mapper, emit, idle_deadline=time.monotonic() + self.timeout,
                    max_deadline=max_deadline,
                )
                if status != "renew_token":
                    if self.chatgpt_token and "subscription_sharing_usage_limit_exceeded" in detail:
                        try:
                            tag_chatgpt.Store().pause_usage(expected_identity=self.auth_identity)
                        except tag_chatgpt.ChatGPTError as exc:
                            raise CodexAppServerError(str(exc)) from None
                    return status, detail
                if self._stop_requested():
                    return "interrupted", "Stopped by requester"
                # Resume the same saved history only after acknowledged interruption;
                # never rerun the original prompt or restart an unacknowledged turn.
                self.close()
                self.process = None
                self.messages = queue.Queue()
                self.stderr.clear()
                self.reader_threads = []
                self.interrupt_sent = False
                self.renewing_token = False
                self.turn_id = None
                self._start()
                self._request("initialize", {"clientInfo": self._client_info(),
                    "capabilities": {"experimentalApi": True}}, mapper, emit, max_deadline)
                self._notify("initialized", {})
                resumed = self._request("thread/resume", {"threadId": self.thread_id,
                    "cwd": str(self.cwd), "sandbox": "workspace-write", "approvalsReviewer": "auto_review"},
                    mapper, emit, max_deadline)
                resumed_thread = resumed.get("thread") if isinstance(resumed, dict) else None
                if not isinstance(resumed_thread, dict) or resumed_thread.get("id") != self.thread_id:
                    raise CodexAppServerError("Codex could not resume the task after ChatGPT token renewal")
                turn_params["input"] = [{"type": "text", "text":
                    "Continue the interrupted task from the saved history. Authentication was renewed. "
                    "Check the outcome of interrupted tools before proceeding; do not repeat completed actions."}]
                emit({"type": "status", "text": "ChatGPT connection renewed; continuing the task."})
        finally:
            self.close()

    @staticmethod
    def _client_info() -> dict[str, str]:
        return {"name": tag_chatgpt.APP_NAME, "title": "Tag",
                "version": (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip()}

    def _start(self) -> None:
        environment = None
        command = self.command
        store = tag_chatgpt.Store()
        try:
            identity = store.identity()
            if self.auth_identity is None:
                self.auth_identity = identity
            elif self.auth_identity != identity:
                raise tag_chatgpt.ChatGPTError("ChatGPT account changed during the task; restart the task with the intended account.")
        except tag_chatgpt.ChatGPTError as exc:
            raise CodexAppServerError(str(exc)) from None
        if self.auth_identity[0] == "chatgpt":
            try:
                self.chatgpt_token, expiry = store.lease(expected_identity=self.auth_identity)
                self.token_renewal_deadline = time.monotonic() + max(0, expiry - time.time() - 90)
            except tag_chatgpt.ChatGPTError as exc:
                raise CodexAppServerError(str(exc)) from None
            environment = dict(os.environ)
            environment[tag_chatgpt.TOKEN_ENV] = self.chatgpt_token
            command = [part for part in command if part != "--stdio"]
            command += ["--listen", "stdio://", *tag_chatgpt.provider_options()]
        try:
            self.process = subprocess.Popen(
                command,
                env=environment,
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as exc:
            raise CodexAppServerError(f"Could not start Codex App Server: {exc}") from exc
        self.reader_threads = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for thread in self.reader_threads:
            thread.start()

    def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        decoder = JsonLineDecoder()
        try:
            while chunk := self.process.stdout.read(4096):
                for raw_line in decoder.feed(chunk):
                    try:
                        payload = json.loads(raw_line.replace(self.chatgpt_token.encode(), b"<redacted>")
                                             if self.chatgpt_token else raw_line)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if isinstance(payload, dict):
                        self.messages.put(payload)
            decoder.finish()
        except BaseException as exc:  # noqa: BLE001 - wake the owning run on reader failure
            self.messages.put(exc)

    def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while chunk := self.process.stderr.read(4096):
            self.stderr.extend(chunk)
            if len(self.stderr) > MAX_STDERR_BYTES:
                del self.stderr[:-MAX_STDERR_BYTES]

    def _send(self, payload: dict[str, Any]) -> None:
        assert self.process is not None and self.process.stdin is not None
        raw = json.dumps(payload, separators=(",", ":")).encode() + b"\n"
        if len(raw) > MAX_REQUEST_LINE_BYTES:
            raise CodexAppServerError("Codex App Server request exceeds the JSONL line limit")
        with self.write_lock:
            try:
                self.process.stdin.write(raw)
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise CodexAppServerError("Codex App Server stdin is unavailable") from exc

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"method": method, "params": params})

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        mapper: CodexEventMapper,
        emit: Callable[[dict[str, Any]], None],
        deadline: float,
    ) -> Any:
        request_id = self.next_id
        self.next_id += 1
        self._send({"id": request_id, "method": method, "params": params})
        request_deadline = min(deadline, time.monotonic() + REQUEST_TIMEOUT_SECONDS)
        while True:
            message = self._next_message(request_deadline)
            if message.get("id") == request_id and ("result" in message or "error" in message):
                if "error" in message:
                    raise CodexAppServerError(self._response_error(method, message["error"]))
                return message.get("result")
            self._dispatch(message, mapper, emit, deadline)

    def _next_message(self, deadline: float) -> dict[str, Any]:
        while True:
            self._check_control()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexAppServerError("Codex App Server request timed out")
            try:
                message = self.messages.get(timeout=min(0.1, remaining))
            except queue.Empty:
                self._check_control()
                assert self.process is not None
                if self.process.poll() is not None:
                    raise CodexAppServerError(self._exit_message())
                continue
            if isinstance(message, BaseException):
                raise CodexAppServerError(str(message)) from message
            return message

    def _dispatch(
        self,
        message: dict[str, Any],
        mapper: CodexEventMapper,
        emit: Callable[[dict[str, Any]], None],
        deadline: float,
    ) -> bool:
        if "method" in message and "id" in message:
            return self._resolve_server_request(message, emit=emit, deadline=deadline)
        self._remember_auto_review(message)
        for event in mapper.map(message):
            if self.auto_review_denials and event.get("type", "").startswith("message_"):
                self.held_auto_review_messages.append(event)
            else:
                emit(event)
        return False

    def _consume_turn(
        self,
        mapper: CodexEventMapper,
        emit: Callable[[dict[str, Any]], None],
        idle_deadline: float,
        max_deadline: float,
    ) -> tuple[str, str]:
        held_messages = self.held_auto_review_messages
        timed_out = False
        timeout_detail = ""
        interrupt_deadline = float("inf")
        while True:
            try:
                deadline = min(idle_deadline, max_deadline, self.token_renewal_deadline) if not timed_out else interrupt_deadline
                message = self._next_message(deadline)
            except CodexAppServerError as exc:
                now = time.monotonic()
                if not timed_out and now >= deadline:
                    self.renewing_token = (self.chatgpt_token != "" and not self.interrupt_sent
                                           and self.token_renewal_deadline < min(idle_deadline, max_deadline))
                    timed_out = True
                    timeout_detail = (
                        f"maximum runtime of {self.max_timeout}s exceeded"
                        if now >= max_deadline
                        else f"no backend activity for {self.timeout}s"
                    )
                    interrupt_deadline = now + INTERRUPT_GRACE_SECONDS
                    self.interrupt()
                    continue
                if timed_out:
                    suffix = "Codex did not confirm interruption before cleanup"
                    return "timeout", f"{timeout_detail}; {suffix}"
                raise
            if "method" in message and "id" not in message:
                self._remember_auto_review(message)
                events = mapper.map(message)
                if self._is_progress_notification(message) and not timed_out:
                    idle_deadline = time.monotonic() + self.timeout
                for event in events:
                    if event.get("type") == "turn_complete":
                        if self.renewing_token and event.get("status") == "interrupted":
                            return "renew_token", ""
                        if (not timed_out and not self.interrupt_sent
                                and event.get("status") == "completed"
                                and self._approve_auto_review_denials(mapper, emit, max_deadline)):
                            held_messages.clear()
                            mapper = CodexEventMapper()
                            self.turn_id = None
                            result = self._request("turn/start", {
                                "threadId": self.thread_id,
                                "input": [{"type": "text", "text":
                                    "Retry only the exact denied action explicitly approved through "
                                    "the preceding approval marker, then continue the original task. "
                                    "Keep automatic review enabled and respect any further denial."}],
                            }, mapper, emit, max_deadline)
                            turn = result.get("turn") if isinstance(result, dict) else None
                            if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
                                raise CodexAppServerError("Codex returned an invalid retry turn")
                            self.turn_id = turn["id"]
                            idle_deadline = time.monotonic() + self.timeout
                            break
                        for held in held_messages:
                            emit(held)
                        held_messages.clear()
                        emit(event)
                        if self.renewing_token:
                            # The task can finish while renewal interruption is in flight.
                            return str(event.get("status", "failed")), str(event.get("text", ""))
                        if timed_out:
                            return "timeout", timeout_detail
                        if self.interrupt_sent:
                            return "interrupted", ""
                        return str(event.get("status", "failed")), str(event.get("text", ""))
                    if self.auto_review_denials and event.get("type", "").startswith("message_"):
                        held_messages.append(event)
                    else:
                        emit(event)
                continue
            if self._dispatch(message, mapper, emit, max_deadline) and not timed_out:
                idle_deadline = time.monotonic() + self.timeout

    def _remember_auto_review(self, message: dict[str, Any]) -> None:
        """Retain exact denials locally; Slack receives only a separate redacted preview."""
        if message.get("method") != "item/autoApprovalReview/completed" or self.approval_dir is None:
            return
        params = message.get("params")
        if not isinstance(params, dict) or params.get("threadId") != self.thread_id:
            return
        if not isinstance(params.get("turnId"), str) or not params["turnId"]:
            return
        if self.turn_id is not None and params.get("turnId") != self.turn_id:
            return
        review, action = params.get("review"), params.get("action")
        review_id = params.get("reviewId")
        if (not isinstance(review, dict) or review.get("status") != "denied"
                or not isinstance(action, dict) or not isinstance(review_id, str) or not review_id
                or review_id in self.seen_auto_reviews
                or len(self.seen_auto_reviews) >= MAX_AUTO_REVIEW_APPROVALS):
            return
        # The override takes the core protocol's snake_case action, not the
        # app-server's camelCase action. Unknown variants fail closed.
        variants = {
            "command": ("command", ("command", "cwd", "source")),
            "execve": ("execve", ("program", "argv", "cwd", "source")),
            "writeStdin": ("write_stdin", ("approvalId", "processId", "stdin", "cwd")),
            "applyPatch": ("apply_patch", ("cwd", "files")),
            "networkAccess": ("network_access", ("target", "host", "protocol", "port")),
            "mcpToolCall": ("mcp_tool_call", ("server", "toolName")),
        }
        action_type = action.get("type")
        if not isinstance(action_type, str):
            return
        variant = variants.get(action_type)
        if variant is None or any(key not in action for key in variant[1]):
            return
        def snake(key: str) -> str:
            return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
        core_action = {snake(key): value for key, value in action.items()}
        core_action["type"] = variant[0]
        if core_action.get("source") == "unifiedExec":
            core_action["source"] = "unified_exec"
        event = {
            "id": review_id, "turn_id": params.get("turnId"),
            "target_item_id": params.get("targetItemId"),
            "started_at_ms": params.get("startedAtMs", 0),
            "completed_at_ms": params.get("completedAtMs"),
            "status": "denied", "risk_level": review.get("riskLevel"),
            "user_authorization": review.get("userAuthorization"),
            "rationale": review.get("rationale"), "action": core_action,
        }
        self.seen_auto_reviews.add(review_id)
        self.auto_review_denials.append(event)

    def _approve_auto_review_denials(
        self, mapper: CodexEventMapper, emit: Callable[[dict[str, Any]], None], deadline: float,
    ) -> bool:
        denials, self.auto_review_denials = self.auto_review_denials, []
        approved_any = False
        for event in denials:
            self._check_control()
            if self.interrupt_sent or time.monotonic() >= deadline:
                break
            approval_id = uuid.uuid4().hex
            emit({"type": "approval_request", "approval_id": approval_id,
                  "label": AUTO_REVIEW_RETRY_LABEL, "review_details": auto_review_details(event)})
            approved = self._wait_for_approval(
                approval_id, min(deadline, time.monotonic() + APPROVAL_TIMEOUT_SECONDS),
            )
            emit({"type": "approval_expired", "approval_id": approval_id})
            if approved and not self.interrupt_sent and time.monotonic() < deadline:
                try:
                    self._request("thread/approveGuardianDeniedAction", {
                        "threadId": self.thread_id, "event": event,
                    }, mapper, emit, deadline)
                except CodexAppServerError:
                    # A rejected override grants no retry; preserve the held answer.
                    continue
                approved_any = True
        return approved_any and not self.interrupt_sent and time.monotonic() < deadline

    @staticmethod
    def _is_progress_notification(message: dict[str, Any]) -> bool:
        """Count private item/turn lifecycle too, without accepting generic pings."""
        method = message.get("method")
        return isinstance(method, str) and (
            method.startswith("item/")
            or method in {"turn/started", "turn/diff/updated", "turn/plan/updated"}
        )

    def _resolve_server_request(
        self,
        message: dict[str, Any],
        *,
        emit: Callable[[dict[str, Any]], None] | None = None,
        deadline: float | None = None,
    ) -> bool:
        request_id = message.get("id")
        method = message.get("method")
        params = message.get("params")
        if (
            isinstance(method, str)
            and method in APPROVAL_REQUEST_LABELS
            and self.approval_dir is not None
            and emit is not None
            and deadline is not None
        ):
            approval_id = uuid.uuid4().hex
            request_params = params if isinstance(params, dict) else {}
            choices = approval_choices(method, request_params)
            result = self._approval_result(method, request_params, False)
            if choices:
                emit({
                    "type": "approval_request", "approval_id": approval_id,
                    "label": APPROVAL_REQUEST_LABELS[method],
                    "choices": public_approval_choices(choices),
                })
                try:
                    selected = self._wait_for_approval_decision(
                        approval_id, min(deadline, time.monotonic() + APPROVAL_TIMEOUT_SECONDS),
                    )
                finally:
                    emit({"type": "approval_expired", "approval_id": approval_id})
                choice = next((c for c in choices if c["id"] == selected.get("choice")), None)
                self._check_control()
                if choice is not None and not self.interrupt_sent and time.monotonic() < deadline:
                    result = choice["result"]
            self._send({"id": request_id, "result": result})
            return True
        responses: dict[str, dict[str, Any]] = {
            "item/commandExecution/requestApproval": {"decision": "decline"},
            "item/fileChange/requestApproval": {"decision": "decline"},
            "item/tool/requestUserInput": {"answers": {}},
            "mcpServer/elicitation/request": {"action": "decline", "content": None},
            "item/permissions/requestApproval": {"permissions": {}},
            "applyPatchApproval": {"decision": "denied"},
            "execCommandApproval": {"decision": "denied"},
        }
        if isinstance(method, str) and method in responses:
            self._send({"id": request_id, "result": responses[method]})
        else:
            self._send({
                "id": request_id,
                "error": {"code": -32601, "message": "Tag does not support this interactive request"},
            })
        return False

    def _wait_for_approval(self, approval_id: str, deadline: float) -> bool:
        return self._wait_for_approval_decision(approval_id, deadline).get("decision") == "approve"

    def _wait_for_approval_decision(self, approval_id: str, deadline: float) -> dict[str, Any]:
        assert self.approval_dir is not None
        decision_file = self.approval_dir / f"{approval_id}.json"
        while time.monotonic() < deadline:
            self._check_control()
            if self.interrupt_sent:
                return {}
            try:
                payload = json.loads(decision_file.read_text(encoding="utf-8"))
            except FileNotFoundError:
                time.sleep(APPROVAL_POLL_SECONDS)
                continue
            except (OSError, json.JSONDecodeError):
                decision_file.unlink(missing_ok=True)
                return {}
            decision_file.unlink(missing_ok=True)
            if time.monotonic() >= deadline:
                return {}
            return payload if isinstance(payload, dict) else {}
        return {}

    @staticmethod
    def _approval_result(method: str, params: dict[str, Any], approved: bool) -> dict[str, Any]:
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            return {"decision": "accept" if approved else "decline"}
        if method == "item/permissions/requestApproval":
            permissions = params.get("permissions")
            return {
                "permissions": permissions if approved and isinstance(permissions, dict) else {},
                "scope": "turn",
            }
        if method in {"applyPatchApproval", "execCommandApproval"}:
            decision: Any = "approved" if approved else {
                "denied": {"rejection": "Denied in Slack"}
            }
            return {"decision": decision}
        return {}

    def _stop_requested(self) -> bool:
        if not self.control_file or not self.run_id:
            return False
        try:
            return self.control_file.read_text(encoding="utf-8").strip() == self.run_id
        except OSError:
            return False

    def _check_control(self) -> None:
        if self.interrupt_sent or not self.control_file or not self.run_id:
            return
        try:
            requested = self.control_file.read_text(encoding="utf-8").strip()
        except OSError:
            return
        if requested == self.run_id:
            self.interrupt()

    def interrupt(self) -> None:
        if self.interrupt_sent or not self.thread_id or not self.turn_id:
            return
        self.interrupt_sent = True
        request_id = self.next_id
        self.next_id += 1
        self._send({
            "id": request_id,
            "method": "turn/interrupt",
            "params": {"threadId": self.thread_id, "turnId": self.turn_id},
        })

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        for thread in self.reader_threads:
            thread.join(timeout=0.5)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _exit_message(self) -> str:
        assert self.process is not None
        detail = bytes(self.stderr).decode("utf-8", errors="replace").strip()
        if self.chatgpt_token:
            detail = detail.replace(self.chatgpt_token, "<redacted>")
        suffix = f": {detail[-4000:]}" if detail else ""
        return f"Codex App Server exited with code {self.process.returncode}{suffix}"

    @staticmethod
    def _response_error(method: str, error: Any) -> str:
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return f"Codex App Server rejected {method}: {error['message']}"
        return f"Codex App Server rejected {method}"
