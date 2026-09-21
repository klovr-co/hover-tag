"""Codex App Server stdio transport and Tag event normalization.

Transport safeguards build on OpenTag's Apache-2.0 App Server adapter. Tag's
event mapping and per-request lifecycle are local to this repository; see
NOTICE for attribution.
"""

from __future__ import annotations

import json
import queue
import re
import shlex
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


# Prompts are controlled by Tag, while completed tool and image events may
# legitimately contain substantially larger output from Codex. The response
# bound accommodates Tag's 15 MiB image limit after base64 expansion.
MAX_REQUEST_LINE_BYTES = 1024 * 1024
MAX_RESPONSE_LINE_BYTES = 32 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 60.0
INTERRUPT_GRACE_SECONDS = 5.0

# Public display vocabulary, never populated from tool arguments or results.
MCP_SERVICE_NAMES = {
    "github": "GitHub", "slack": "Slack", "linear": "Linear",
    "notion": "Notion", "datadog": "Datadog", "mfs": "connected knowledge",
    "playwright": "Playwright", "context7": "Context7",
}
MCP_TOOL_NAMES = frozenset({
    "search", "fetch", "read_resource", "list_resources", "search_issues",
    "get_issue", "list_issues", "create_issue", "update_issue",
    "search_code", "get_file_contents", "list_pull_requests", "pull_request_read",
    "create_pull_request", "query_metrics", "search_logs", "list_dashboards",
    "get_document", "search_pages", "fetch_documentation", "resolve_library_id",
    "resolve-library-id", "query-docs", "browser_navigate", "browser_snapshot",
    "browser_click", "browser_take_screenshot",
})
COMMAND_LABELS = {
    "mfs_search.py": "Searching connected knowledge…",
    "mfs_cat.py": "Reading connected knowledge…",
    "mfs_ls.py": "Browsing connected knowledge…",
    "slack_canvas.py": "Creating a Slack canvas…",
    "slack_post_message.py": "Posting to Slack…",
}


def command_activity_label(command: Any, depth: int = 0) -> str:
    """Recognize a simple invocation, not a helper name mentioned in arguments.

    This is conservative classification, not a shell interpreter or a claim
    that the operation succeeded. Compound commands retain a generic label.
    """
    fallback = "Running a command…"
    if not isinstance(command, str) or len(command) > 8192 or depth > 1:
        return fallback
    try:
        argv = shlex.split(command)
    except ValueError:
        return fallback
    if not argv:
        return fallback
    executable = argv[0].replace("\\", "/").rsplit("/", 1)[-1]
    if executable in {"sh", "bash", "zsh"} and len(argv) == 3 and argv[1] in {"-c", "-lc"}:
        return command_activity_label(argv[2], depth + 1)
    if any(char in command for char in "\n\r;&|<>`$"):
        return fallback
    if re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", executable):
        if len(argv) < 2 or argv[1].startswith("-"):
            return fallback
        executable = argv[1].replace("\\", "/").rsplit("/", 1)[-1]
    return COMMAND_LABELS.get(executable, fallback)


def mcp_activity_label(item: dict[str, Any]) -> str:
    """Expose only explicitly approved service/tool names, with safe fallbacks."""
    server, tool = item.get("server"), item.get("tool")
    service = MCP_SERVICE_NAMES.get(server.lower()) if isinstance(server, str) else None
    name = tool if isinstance(tool, str) and tool in MCP_TOOL_NAMES else None
    if name:
        words = name.replace("-", "_").split("_")
        verb = {"search": "Searching", "fetch": "Fetching", "get": "Reading",
                "read": "Reading", "list": "Listing", "create": "Creating",
                "update": "Updating", "query": "Querying", "resolve": "Looking up"}.get(words[0])
        if verb:
            subject = " ".join(words[1:])
            target = " ".join(part for part in (service, subject) if part)
            return f"{verb} {target or 'with a connected tool'}…"
    if service:
        return f"Using {service}…"
    return "Using a connected tool…"


class CodexAppServerError(RuntimeError):
    """A bounded, user-safe App Server transport failure."""


class JsonLineDecoder:
    """Decode fragmented JSONL without allowing an unbounded partial line."""

    def __init__(self, max_line_bytes: int = MAX_RESPONSE_LINE_BYTES) -> None:
        self.max_line_bytes = max_line_bytes
        self.buffer = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        self.buffer.extend(chunk)
        if len(self.buffer) > self.max_line_bytes and b"\n" not in self.buffer:
            raise CodexAppServerError("Codex App Server emitted an oversized JSONL line")
        lines: list[bytes] = []
        while True:
            newline = self.buffer.find(b"\n")
            if newline < 0:
                break
            line = bytes(self.buffer[:newline]).strip()
            del self.buffer[: newline + 1]
            if len(line) > self.max_line_bytes:
                raise CodexAppServerError("Codex App Server emitted an oversized JSONL line")
            if line:
                lines.append(line)
        return lines

    def finish(self) -> None:
        if self.buffer.strip():
            raise CodexAppServerError("Codex App Server exited with a truncated JSONL line")


def activity_label(item: dict[str, Any]) -> str | None:
    """Return a truthful, sanitized label derived from an identifiable action."""
    item_type = item.get("type")
    if item_type == "webSearch":
        return "Searching the web…"
    if item_type == "fileChange":
        return "Updating files…"
    if item_type == "imageView":
        return "Inspecting an image…"
    if item_type == "imageGeneration":
        return "Creating an image…"
    if item_type == "mcpToolCall":
        return mcp_activity_label(item)
    if item_type == "commandExecution":
        return command_activity_label(item.get("command"))
    return None


class CodexEventMapper:
    """Map native lifecycle notifications to Tag's Slack-safe event contract."""

    def __init__(self) -> None:
        self.message_phases: dict[str, str | None] = {}
        self.pending_deltas: dict[str, list[str]] = {}
        self.completed_messages: list[tuple[str, str | None, str]] = []
        self.emitted_final_ids: set[str] = set()

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
                return [{"type": "error", "text": text}]
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
                     "wait_label": f"Still waiting for {service}…" if service else "This operation is still running…"}]
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
            terminal["text"] = error["message"]
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
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.timeout = timeout
        self.max_timeout = max_timeout if max_timeout is not None else timeout
        self.control_file = control_file
        self.run_id = run_id
        self.process: subprocess.Popen[bytes] | None = None
        self.messages: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self.stderr = bytearray()
        self.write_lock = threading.Lock()
        self.next_id = 1
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.interrupt_sent = False
        self.reader_threads: list[threading.Thread] = []

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
                {"clientInfo": {"name": "tag", "title": "Tag", "version": "0.1"}},
                mapper,
                emit,
                max_deadline,
            )
            self._notify("initialized", {})
            thread_params: dict[str, Any] = {
                "cwd": str(self.cwd),
                "sandbox": "workspace-write",
                "approvalsReviewer": "auto_review",
                "ephemeral": True,
                "serviceName": "tag_slack_bridge",
            }
            if model:
                thread_params["model"] = model
            thread_result = self._request("thread/start", thread_params, mapper, emit, max_deadline)
            thread = thread_result.get("thread") if isinstance(thread_result, dict) else None
            if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
                raise CodexAppServerError("Codex returned an invalid thread/start response")
            self.thread_id = thread["id"]
            turn_params: dict[str, Any] = {
                "threadId": self.thread_id,
                "input": [{"type": "text", "text": prompt}],
            }
            if model:
                turn_params["model"] = model
            if reasoning_effort:
                turn_params["effort"] = reasoning_effort
            turn_result = self._request("turn/start", turn_params, mapper, emit, max_deadline)
            turn = turn_result.get("turn") if isinstance(turn_result, dict) else None
            if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
                raise CodexAppServerError("Codex returned an invalid turn/start response")
            self.turn_id = turn["id"]
            return self._consume_turn(
                mapper,
                emit,
                idle_deadline=time.monotonic() + self.timeout,
                max_deadline=max_deadline,
            )
        finally:
            self.close()

    def _start(self) -> None:
        try:
            self.process = subprocess.Popen(
                self.command,
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
                        payload = json.loads(raw_line)
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
            self._dispatch(message, mapper, emit)

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
    ) -> None:
        if "method" in message and "id" in message:
            self._resolve_server_request(message)
            return
        for event in mapper.map(message):
            emit(event)

    def _consume_turn(
        self,
        mapper: CodexEventMapper,
        emit: Callable[[dict[str, Any]], None],
        idle_deadline: float,
        max_deadline: float,
    ) -> tuple[str, str]:
        timed_out = False
        timeout_detail = ""
        interrupt_deadline = float("inf")
        while True:
            try:
                deadline = min(idle_deadline, max_deadline) if not timed_out else interrupt_deadline
                message = self._next_message(deadline)
            except CodexAppServerError as exc:
                now = time.monotonic()
                if not timed_out and now >= deadline:
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
                events = mapper.map(message)
                if self._is_progress_notification(message) and not timed_out:
                    idle_deadline = time.monotonic() + self.timeout
                for event in events:
                    emit(event)
                    if event.get("type") == "turn_complete":
                        if timed_out:
                            return "timeout", timeout_detail
                        return str(event.get("status", "failed")), str(event.get("text", ""))
                continue
            self._dispatch(message, mapper, emit)

    @staticmethod
    def _is_progress_notification(message: dict[str, Any]) -> bool:
        """Count private item/turn lifecycle too, without accepting generic pings."""
        method = message.get("method")
        return isinstance(method, str) and (
            method.startswith("item/")
            or method in {"turn/started", "turn/diff/updated", "turn/plan/updated"}
        )

    def _resolve_server_request(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        method = message.get("method")
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
        suffix = f": {detail[-4000:]}" if detail else ""
        return f"Codex App Server exited with code {self.process.returncode}{suffix}"

    @staticmethod
    def _response_error(method: str, error: Any) -> str:
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return f"Codex App Server rejected {method}: {error['message']}"
        return f"Codex App Server rejected {method}"
