"""Which backend conversation each Slack thread continues.

Every Tag request in one Slack thread resumes the same Codex thread or Claude
session, so the backend keeps the earlier turns' tool results and reasoning and
the conversation appears once in the operator's local history. Records hold
only opaque backend ids, the last answered request, and the conversation's
size; prompts and answers stay in the backend's own store.

A conversation is continued only while it is small enough and recently used:
``OPENTAG_THREAD_MAX_CONTEXT_TOKENS`` bounds what each resumed request re-reads,
and ``OPENTAG_THREAD_IDLE_HOURS`` starts fresh once the provider's prompt cache
has expired and re-reading the history would cost full price. Only the
requester who started a conversation continues it: its history holds tool
results gathered under that person's Slack search grant.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from .tag_paths import instance_home, restrict_windows_acl
except ImportError:  # Direct script execution does not create a package context.
    from tag_paths import instance_home, restrict_windows_acl


SCHEMA_VERSION = 1
RETENTION_SECONDS = 30 * 24 * 60 * 60
MAX_SESSIONS = 500
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SLACK_TS_RE = re.compile(r"^\d+\.\d+$")
DEFAULT_MAX_CONTEXT_TOKENS = 150_000
DEFAULT_IDLE_HOURS = 4


def setting(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value.isascii() and value.isdigit() else default


def thread_key(team: str, channel: str, thread_ts: str, backend: str) -> str:
    return f"{team}:{channel}:{thread_ts}:{backend}"


class ThreadSessions:
    """Bounded map from a Slack thread and backend to its conversation id."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or instance_home() / "state/agent-sessions.json"
        self.lock = threading.RLock()

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            return {}
        sessions = payload.get("sessions")
        return sessions if isinstance(sessions, dict) else {}

    def _write(self, sessions: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        restrict_windows_acl(self.path.parent)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"schema_version": SCHEMA_VERSION, "sessions": sessions}, stream,
                          separators=(",", ":"))
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, team: str, channel: str, thread_ts: str, backend: str, *,
            workdir: str, requester: str) -> tuple[str, str] | None:
        """Return the conversation to continue and the last request it answered, if any."""
        with self.lock:
            record = self._read().get(thread_key(team, channel, thread_ts, backend))
        if (not isinstance(record, dict) or record.get("workdir") != workdir
                or record.get("requester") != requester):
            return None
        session_id, request_ts = record.get("session_id"), record.get("request_ts")
        updated, context = record.get("updated_at"), record.get("context_tokens", 0)
        max_context = setting("OPENTAG_THREAD_MAX_CONTEXT_TOKENS", DEFAULT_MAX_CONTEXT_TOKENS)
        idle_seconds = setting("OPENTAG_THREAD_IDLE_HOURS", DEFAULT_IDLE_HOURS) * 3600
        if (not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id)
                or not isinstance(request_ts, str) or not SLACK_TS_RE.fullmatch(request_ts)
                or not isinstance(updated, (int, float)) or not isinstance(context, int)
                or time.time() - updated > min(idle_seconds, RETENTION_SECONDS)
                or context >= max_context):
            return None
        return session_id, request_ts

    def save(self, team: str, channel: str, thread_ts: str, backend: str, *,
             workdir: str, requester: str, session_id: str, request_ts: str,
             context_tokens: int | None = None) -> None:
        """Record the conversation a request continued; later sizes replace earlier ones."""
        if not SESSION_ID_RE.fullmatch(session_id) or not SLACK_TS_RE.fullmatch(request_ts):
            return
        key = thread_key(team, channel, thread_ts, backend)
        with self.lock:
            sessions = self._read()
            previous = sessions.get(key) if isinstance(sessions.get(key), dict) else {}
            if context_tokens is None and previous.get("session_id") == session_id:
                context_tokens = previous.get("context_tokens")
            sessions[key] = {
                "session_id": session_id, "workdir": workdir, "requester": requester, "request_ts": request_ts,
                "context_tokens": context_tokens if isinstance(context_tokens, int) and context_tokens >= 0 else 0,
                "updated_at": time.time()}
            cutoff = time.time() - RETENTION_SECONDS
            kept = sorted(
                ((key, value) for key, value in sessions.items()
                 if isinstance(value, dict) and isinstance(value.get("updated_at"), (int, float))
                 and value["updated_at"] >= cutoff),
                key=lambda item: item[1]["updated_at"], reverse=True,
            )[:MAX_SESSIONS]
            self._write(dict(kept))
