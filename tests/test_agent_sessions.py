from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import agent_sessions
from scripts.agent_sessions import ThreadSessions


class ThreadSessionsTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.sessions = ThreadSessions(Path(directory.name) / "state/agent-sessions.json")
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in ("OPENTAG_THREAD_MAX_CONTEXT_TOKENS", "OPENTAG_THREAD_IDLE_HOURS"):
            os.environ.pop(name, None)

    def save(self, session_id: str = "thread-1", *, thread_ts: str = "1.1", backend: str = "codex",
             requester: str = "U1", request_ts: str = "1.5", context_tokens: int | None = None) -> None:
        self.sessions.save("T", "C", thread_ts, backend, workdir="/w", requester=requester,
                           session_id=session_id, request_ts=request_ts, context_tokens=context_tokens)

    def get(self, *, thread_ts: str = "1.1", backend: str = "codex", workdir: str = "/w",
            requester: str = "U1") -> tuple[str, str] | None:
        return self.sessions.get("T", "C", thread_ts, backend, workdir=workdir, requester=requester)

    def test_same_slack_thread_and_requester_continue_one_conversation(self) -> None:
        self.save()
        self.assertEqual(("thread-1", "1.5"), self.get())
        self.save("thread-2", request_ts="2.5")
        self.assertEqual(("thread-2", "2.5"), self.get())

    def test_other_threads_backends_workspaces_and_requesters_start_fresh(self) -> None:
        self.save()
        self.assertIsNone(self.get(thread_ts="2.2"))
        self.assertIsNone(self.get(backend="claude"))
        self.assertIsNone(self.get(workdir="/other"))
        # Another person's request must not read tool results gathered under the first grant.
        self.assertIsNone(self.get(requester="U2"))

    def test_large_conversations_start_fresh(self) -> None:
        self.save(context_tokens=149_999)
        self.assertIsNotNone(self.get())
        self.save(context_tokens=150_000)
        self.assertIsNone(self.get())
        with patch.dict(os.environ, {"OPENTAG_THREAD_MAX_CONTEXT_TOKENS": "200000"}):
            self.assertIsNotNone(self.get())
        with patch.dict(os.environ, {"OPENTAG_THREAD_MAX_CONTEXT_TOKENS": "0"}):
            self.save(context_tokens=0)
            self.assertIsNone(self.get())

    def test_size_survives_a_later_session_report_for_the_same_conversation(self) -> None:
        self.save(context_tokens=160_000)
        self.save()
        self.assertIsNone(self.get())
        self.save("thread-2")
        self.assertEqual(("thread-2", "1.5"), self.get())

    def test_idle_conversations_start_fresh(self) -> None:
        self.save()
        later = time.time() + 4 * 3600 + 1
        with patch.object(agent_sessions.time, "time", return_value=later):
            self.assertIsNone(self.get())
            with patch.dict(os.environ, {"OPENTAG_THREAD_IDLE_HOURS": "24"}):
                self.assertIsNotNone(self.get())
        with patch.dict(os.environ, {"OPENTAG_THREAD_IDLE_HOURS": "0"}), \
                patch.object(agent_sessions.time, "time", return_value=time.time() + 1):
            self.assertIsNone(self.get())

    def test_invalid_and_unreadable_records_are_ignored(self) -> None:
        self.save("../escape")
        self.save(request_ts="not-a-ts")
        self.assertFalse(self.sessions.path.exists())
        self.save()
        self.sessions.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(self.get())
        self.save("thread-3")
        self.assertEqual(("thread-3", "1.5"), self.get())

    def test_store_keeps_only_the_most_recent_threads(self) -> None:
        with patch.object(agent_sessions, "MAX_SESSIONS", 2):
            for index in range(3):
                self.save(f"thread-{index}", thread_ts=f"{index}.0")
        self.assertIsNone(self.get(thread_ts="0.0"))
        self.assertEqual(("thread-2", "1.5"), self.get(thread_ts="2.0"))


if __name__ == "__main__":
    unittest.main()
