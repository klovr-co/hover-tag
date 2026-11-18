# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""``tag [TAG] run --prompt-file FILE|- --json``: one prompt through the Tag's agent, as JSON lines.

The run uses what a routine or a Slack thread uses: the Tag's AI connection,
default model, thinking level, Fast Mode, permission rules and working folder.
It has no Slack conversation, so it gets no Slack access and posts nothing, and
nobody can answer an approval, so every approval request is denied. It runs the
same backend process as the bridge (``opentag_agent.py --event-stream``) and
turns its normalized events into the lines in docs/reference/app-protocol.md.
Each run is kept in Activity with ``trigger: {"type": "run"}``.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    from .agent_activity import THINKING_LABEL, token_usage
    from .agent_models import default_agent_settings, discover_tag_models, rich_events_selected
    from .opentag_process_env import backend_environment, isolated_environment
    from .tag_activity import ITEM_STATUSES, ActivityStore, activity_step
    from .tag_error_reporting import redact_sensitive_text
    from .tag_paths import tag_temp_dir
except ImportError:  # Direct script execution does not create a package context.
    from agent_activity import THINKING_LABEL, token_usage
    from agent_models import default_agent_settings, discover_tag_models, rich_events_selected
    from opentag_process_env import backend_environment, isolated_environment
    from tag_activity import ITEM_STATUSES, ActivityStore, activity_step
    from tag_error_reporting import redact_sensitive_text
    from tag_paths import tag_temp_dir

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "scripts/opentag_agent.py"
SCHEMA_VERSION = 1
# How long the agent gets to stop by itself after a stop request before it is killed.
CANCEL_GRACE_SECONDS = 6.0
MAX_TIMEOUT_MINUTES = 24 * 60
MAX_TURNS = 1000
# Activity's own outcome for each result status.
OUTCOMES = {"complete": "completed", "cancelled": "interrupted", "failed": "failed", "timeout": "failed"}
STOP_SIGNALS = ("SIGINT", "SIGTERM", "SIGBREAK")


def parse(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tag [TAG] run", description="Run one prompt through the Tag's agent and stream what it does as JSON lines.")
    parser.add_argument("--prompt-file", required=True, metavar="FILE",
                        help="the prompt, from a UTF-8 file, or - for standard input")
    parser.add_argument("--resume", metavar="SESSION_ID", help="continue the agent session an earlier run reported")
    parser.add_argument("--max-turns", type=int, metavar="N", help=f"stop after N agent turns, 1-{MAX_TURNS} (Claude only)")
    parser.add_argument("--timeout-minutes", type=int, metavar="N",
                        help=f"stop after N minutes, 1-{MAX_TIMEOUT_MINUTES} (default: the Tag's maximum runtime)")
    parser.add_argument("--json", action="store_true", required=True, help="stream JSON lines (required)")
    args = parser.parse_args(arguments)
    if args.max_turns is not None and not 1 <= args.max_turns <= MAX_TURNS:
        parser.error(f"--max-turns must be between 1 and {MAX_TURNS}")
    if args.timeout_minutes is not None and not 1 <= args.timeout_minutes <= MAX_TIMEOUT_MINUTES:
        parser.error(f"--timeout-minutes must be between 1 and {MAX_TIMEOUT_MINUTES}")
    if args.resume is not None and not args.resume.strip():
        parser.error("--resume needs the session_id an earlier run reported")
    try:
        source = sys.stdin if args.prompt_file == "-" else open(args.prompt_file, encoding="utf-8")
        with source:
            args.prompt = source.read()
    except (OSError, UnicodeError) as error:
        parser.error(f"Could not read the prompt: {error}")
    if not args.prompt.strip():
        parser.error("The prompt is empty")
    return args


@dataclass
class Run:
    """What one run has reported so far, and the lines it has printed."""

    resume: str | None
    seq: int = 0
    started: bool = False
    session_id: str | None = None
    model: str | None = None
    usage: dict[str, int] | None = None
    answer: list[str] = field(default_factory=list)
    streamed: bool = False
    error_code: str | None = None
    error_text: str = ""
    diagnostics: list[str] = field(default_factory=list)
    cancel_requested: bool = False
    timed_out: bool = False

    def line(self, payload: dict[str, Any]) -> None:
        print(json.dumps({"schema_version": SCHEMA_VERSION, **payload}), flush=True)

    def start(self, session_id: str | None) -> None:
        if not self.started:
            self.started = True
            self.line({"type": "started", "session_id": session_id})

    def event(self, kind: str, **fields: Any) -> None:
        # A backend that reports its session late (Claude, when resuming) still starts first.
        self.start(self.session_id or self.resume)
        self.seq += 1
        self.line({"type": "event", "seq": self.seq, "kind": kind, **fields})


def observe(run: Run, event: dict[str, Any], *, deny_approval) -> None:
    """Turn one normalized backend event into protocol lines and run state."""
    kind, text = event.get("type"), event.get("text")
    if kind == "session" and isinstance(session_id := event.get("session_id"), str) and session_id:
        run.session_id = session_id
        run.start(session_id)
    elif kind == "message_start":
        run.streamed = False
    elif kind in {"message_delta", "message_complete"} and event.get("phase", "final_answer") == "final_answer" \
            and isinstance(text, str) and text and not (kind == "message_complete" and run.streamed):
        # Deltas stream the answer; a message that arrived whole, without deltas, is sent at once.
        run.streamed = kind == "message_delta"
        run.event("text", phase="final_answer", delta=text)
        run.answer.append(text)
    elif kind in {"activity_start", "activity_complete"} and (step := activity_step(event)) is not None:
        step_id, label, details = step
        status = "running" if kind == "activity_start" else (
            event.get("status") if event.get("status") in ITEM_STATUSES - {"running"} else "unknown")
        if label == THINKING_LABEL:
            run.event("thinking", id=step_id, status=status,
                      **({"text": details["output"]} if details.get("output") else {}))
        else:
            run.event("tool_use" if kind == "activity_start" else "tool_result",
                      id=step_id, label=label, status=status, details=details)
    elif kind == "status" and isinstance(text, str) and text:
        run.event("status", text=text)
    elif kind == "approval_request" and isinstance(approval_id := event.get("approval_id"), str):
        deny_approval(approval_id)
        label = event.get("label") if isinstance(event.get("label"), str) else "an action"
        run.event("status", text=f"Denied, because nobody can approve during a run: {label}")
    elif kind == "usage":
        run.usage = token_usage(event.get("usage")) or run.usage
    elif kind == "run_info" and isinstance(model := event.get("model"), str) and model:
        run.model = model[:120]
    elif kind in {"error", "turn_complete"}:
        if isinstance(code := event.get("code"), str):
            run.error_code = code
        if isinstance(text, str) and text:
            run.error_text = text


def result_status(run: Run, return_code: int) -> str:
    if run.cancel_requested:
        return "cancelled"
    if run.timed_out or return_code == 124:
        return "timeout"
    return "complete" if return_code == 0 else "failed"


def error_message(run: Run, status: str) -> str:
    if status == "cancelled":
        return "Stopped. Actions completed before the stop were not rolled back."
    detail = run.error_text or "\n".join(run.diagnostics)[-1000:].strip()
    return redact_sensitive_text(detail, limit=1000) or f"The agent stopped with status {status}."


def kill_tree(process: subprocess.Popen[str]) -> None:
    """Stop the agent and everything it started, such as a tool that ignored the interruption."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except OSError:
            pass
    if process.poll() is None:
        process.kill()


def run(arguments: list[str], *, load: Callable[[], tuple[Path, dict[str, str] | None]]) -> int:
    """Print the protocol lines for one run; ``load`` finds the Tag and its environment (None: not set up).

    The stop signals are handled for the whole call, so a stop at any point still ends with the result line.
    """
    args = parse(arguments)
    state = Run(resume=args.resume)
    agent: dict[str, Any] = {}

    def stop(*_: Any) -> None:
        process = agent.get("process")
        if process is not None and process.poll() is not None:
            return
        if state.cancel_requested:
            if process is not None:
                kill_tree(process)  # A second stop request doesn't wait for the agent.
            return
        state.cancel_requested = True
        if process is not None:
            signal_agent(process, agent)

    previous = {name: signal.signal(getattr(signal, name), stop) for name in STOP_SIGNALS if hasattr(signal, name)}
    try:
        try:
            status = launch(args, state, agent, load)
        except Exception as error:  # noqa: BLE001 - whatever happens, the last line is the result
            state.error_text = state.error_text or f"Tag couldn't finish the run: {error}"
            status = "cancelled" if state.cancel_requested else "failed"
        if agent.get("activity") is not None:
            agent["record"]("finish", OUTCOMES[status])
            if status == "complete" and state.answer:
                agent["record"]("save_reply", "".join(state.answer))
        if "process" in agent:
            state.start(state.session_id or state.resume)
        result: dict[str, Any] = {
            "type": "result", "status": status, "session_id": state.session_id or state.resume,
            "usage": state.usage, "model": state.model, "run_id": agent.get("activity"), "text": "".join(state.answer)}
        if status != "complete":
            result["error"] = {"code": state.error_code, "message": error_message(state, status)}
        state.line(result)
        return 0 if status == "complete" else 1
    finally:
        for name, handler in previous.items():
            signal.signal(getattr(signal, name), handler)


def signal_agent(process: subprocess.Popen[str], agent: dict[str, Any]) -> None:
    """Ask the agent to stop as Slack's Stop does, and kill it if it hasn't within the grace period."""
    try:
        agent["control_file"].write_text(agent["run_id"], encoding="utf-8")
    except OSError:
        kill_tree(process)
        return
    grace = threading.Timer(CANCEL_GRACE_SECONDS, kill_tree, args=(process,))
    grace.daemon = True
    grace.start()


def launch(args: argparse.Namespace, state: Run, agent: dict[str, Any],
           load: Callable[[], tuple[Path, dict[str, str] | None]]) -> str:
    """Start the Tag's agent, stream its events, and return the result status."""
    home, environment = load()
    if environment is None:
        state.error_text = "This Tag isn't set up yet. Run tag setup first."
        return "failed"
    # Model discovery and every helper below read the Tag's settings from the environment.
    # Without Slack's: discovery starts the backends too, and a run has no Slack access.
    os.environ.clear()
    os.environ.update(isolated_environment(environment, transport="local"))
    default_backend = os.getenv("OPENTAG_BACKEND") or "codex"
    settings = default_agent_settings(discover_tag_models(default_backend))
    backend = settings.backend or default_backend
    if not rich_events_selected(backend):
        state.error_text = (f"tag run needs the {backend} event transport "
                            f"({'app-server' if backend == 'codex' else 'sdk'}); this Tag uses an older one.")
        return "failed"
    if state.cancel_requested:
        return "cancelled"
    default_limit = int(os.getenv("OPENTAG_MAX_TIMEOUT_SECONDS", "3600"))
    max_timeout = args.timeout_minutes * 60 if args.timeout_minutes else default_limit
    timeout = min(int(os.getenv("OPENTAG_TIMEOUT_SECONDS", "420")), max_timeout)

    activity = ActivityStore(home / "state/activity", publish_live=False)

    def record(method: str, *values: Any, **options: Any) -> None:
        try:
            getattr(activity, method)(agent["activity"], *values, **options)
        except OSError:
            pass

    try:
        agent["activity"] = activity.create(team="", channel="", thread_ts="", request_ts="", requester="",
                                            reasoning_effort=settings.reasoning_effort, trigger={"type": "run"})
        agent["record"] = record
    except OSError:
        pass

    with tempfile.TemporaryDirectory(prefix="tag-run-", dir=tag_temp_dir()) as raw:
        work = Path(raw)
        (work / "question.txt").write_text(args.prompt, encoding="utf-8")
        (work / "thread.txt").write_text("", encoding="utf-8")
        (work / "approvals").mkdir()
        agent.update(control_file=work / "control", run_id=uuid.uuid4().hex)
        agent["control_file"].write_text("", encoding="utf-8")
        command = [
            sys.executable, str(AGENT), "--backend", backend, "--channel-id", "",
            "--question-file", str(work / "question.txt"), "--thread-file", str(work / "thread.txt"),
            "--skill-dir", str(ROOT), "--workdir", os.getenv("OPENTAG_WORKDIR", str(Path.cwd())),
            "--timeout", str(timeout), "--max-timeout", str(max_timeout), "--event-stream",
            "--fast-mode", "on" if settings.fast_mode else "off",
            "--control-file", str(agent["control_file"]), "--run-id", agent["run_id"],
            "--approval-dir", str(work / "approvals"),
        ]
        if settings.model:
            command += ["--model", settings.model]
        if settings.reasoning_effort:
            command += ["--reasoning-effort", settings.reasoning_effort]
        if args.resume:
            # A continued session already holds the earlier conversation; the prompt is all that is new.
            command += ["--resume-session", args.resume, "--thread-new-file", str(work / "thread.txt")]
        child_environment = backend_environment(
            os.environ, transport="local", conversation_id="", caller_id="",
            run_limits={"max_turns": args.max_turns} if args.max_turns else None)
        try:
            process = subprocess.Popen(
                command, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, bufsize=1, env=child_environment,
                # The agent must not see this terminal's Ctrl+C itself: it stops through the control file.
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        except OSError as error:
            state.error_text = f"Could not start the agent: {error}"
            return "failed"
        agent["process"] = process
        if state.cancel_requested:
            signal_agent(process, agent)  # Stopped while it was starting.

        def time_out() -> None:
            state.timed_out = True
            kill_tree(process)

        def deny_approval(approval_id: str) -> None:
            if len(approval_id) == 32 and all(c in "0123456789abcdef" for c in approval_id):
                target = work / "approvals" / f"{approval_id}.json"
                temporary = target.with_name(f".{approval_id}.tmp")
                try:
                    temporary.write_text(json.dumps({"decision": "deny"}), encoding="utf-8")
                    os.replace(temporary, target)
                except OSError:
                    kill_tree(process)  # An approval it can't refuse must not wait for an answer.

        timer = threading.Timer(max_timeout + 10, time_out)
        timer.daemon = True
        timer.start()
        try:
            assert process.stdout is not None
            for raw_line in process.stdout:
                line = raw_line.strip()
                try:
                    event = json.loads(line) if line else None
                except json.JSONDecodeError:
                    event = None
                if not isinstance(event, dict):
                    if line:
                        state.diagnostics = [*state.diagnostics[-50:], line]
                    continue
                observe(state, event, deny_approval=deny_approval)
                if "record" not in agent:
                    continue
                if event.get("type") in {"usage", "activity_start", "activity_complete"}:
                    record("observe", event)
                elif event.get("type") == "session" and state.session_id:
                    record("save_session", state.session_id)
                elif event.get("type") == "run_info" and state.model:
                    effort = event.get("reasoning_effort")
                    record("save_model", backend, state.model,
                           reasoning_effort=effort if isinstance(effort, str) else None)
            return result_status(state, process.wait())
        finally:
            timer.cancel()
            kill_tree(process)
            if process.stdout is not None:
                process.stdout.close()
