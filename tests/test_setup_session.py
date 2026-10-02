from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from scripts import setup_session

# Stands in for `tag setup --json`: asks two questions, then reports a result.
FAKE_SETUP = textwrap.dedent("""
    import json, sys
    def ask(qid, **extra):
        print(json.dumps({"type": "question", "id": qid, "kind": "choose", "prompt": qid, **extra}), flush=True)
        line = sys.stdin.readline()
        if not line:
            return None
        reply = json.loads(line)
        return None if reply.get("pause") else reply["answer"]
    print("not json", flush=True)
    print(json.dumps({"type": "message", "text": "Checking Slack"}), flush=True)
    first = ask("workspace", sign_in_line="/slackauthticket abc")
    if first is None:
        print(json.dumps({"type": "result", "status": "paused"}), flush=True); sys.exit(0)
    second = ask("approve_setup")
    status = "paused" if second is None else "complete"
    print(json.dumps({"type": "result", "status": status, "answers": [first, second]}), flush=True)
""")


class SetupSessionTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        script = self.home / "fake_setup.py"
        script.write_text(FAKE_SETUP, encoding="utf-8")
        self.command = [sys.executable, str(script)]

    def tearDown(self):
        try:
            setup_session.stop(self.home)
        except setup_session.SessionError:
            pass

    def test_step_answer_and_result_across_separate_calls(self):
        first = setup_session.step(self.home, self.command)
        self.assertEqual(first["state"], "waiting")
        self.assertEqual(first["question"]["id"], "workspace")
        self.assertEqual([event["type"] for event in first["events"]], ["message", "question"])
        # Asking again returns the same pending question without new events.
        again = setup_session.step(self.home, self.command)
        self.assertEqual(again["question"]["id"], "workspace")
        self.assertEqual(again["events"], [])

        second = setup_session.answer(self.home, 1, "workspace")
        self.assertEqual(second["question"]["id"], "approve_setup")
        last = setup_session.answer(self.home, 0)
        self.assertEqual(last["state"], "ended")
        self.assertEqual(last["result"], {"type": "result", "status": "complete", "answers": [1, 0]})
        # The supervisor exits right after delivering the ending.
        session = self.home / ".setup-session/session.json"
        deadline = time.monotonic() + 5
        while session.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(session.exists())

    def test_answer_for_a_different_question_is_refused(self):
        setup_session.step(self.home, self.command)
        with self.assertRaisesRegex(setup_session.SessionError, "asking 'workspace'"):
            setup_session.answer(self.home, 0, "approve_setup")
        self.assertEqual(setup_session.step(self.home, self.command)["question"]["id"], "workspace")

    def test_stop_pauses_setup(self):
        setup_session.step(self.home, self.command)
        reply = setup_session.stop(self.home)
        self.assertEqual(reply["state"], "ended")
        self.assertEqual(reply["result"]["status"], "paused")

    def test_answer_without_session_and_wrong_token_are_errors(self):
        with self.assertRaisesRegex(setup_session.SessionError, "No setup is running"):
            setup_session.answer(self.home, 0)
        setup_session.step(self.home, self.command)
        path = self.home / ".setup-session/session.json"
        session = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**session, "token": "wrong"}), encoding="utf-8")
        try:
            with self.assertRaisesRegex(setup_session.SessionError, "another client"):
                setup_session.answer(self.home, 0)
        finally:
            path.write_text(json.dumps(session), encoding="utf-8")

    def test_session_file_is_private_and_answers_are_not_written(self):
        setup_session.step(self.home, self.command)
        directory = self.home / ".setup-session"
        if os.name != "nt":  # Windows protects it with an ACL; POSIX mode bits don't apply.
            self.assertEqual((directory / "session.json").stat().st_mode & 0o777, 0o600)
        if os.name != "nt":
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        setup_session.answer(self.home, "secret-code-123")
        for path in directory.iterdir():
            self.assertNotIn("secret-code-123", path.read_text(encoding="utf-8"))

    def test_unfetched_ending_is_kept_for_the_next_step(self):
        directory = self.home / ".setup-session"
        directory.mkdir()
        setup_session._write_private(directory / "final.json", {"state": "ended", "events": [], "question": None,
                                                                "result": {"type": "result", "status": "paused"}})
        self.assertEqual(setup_session.step(self.home, self.command)["result"]["status"], "paused")
        self.assertFalse((directory / "final.json").exists())

    def test_stale_session_file_starts_a_new_session(self):
        directory = self.home / ".setup-session"
        directory.mkdir()
        (directory / "session.json").write_text(json.dumps({"pid": 1, "port": 1, "token": "x"}), encoding="utf-8")
        started = time.monotonic()
        self.assertEqual(setup_session.step(self.home, self.command)["question"]["id"], "workspace")
        self.assertLess(time.monotonic() - started, setup_session.START_SECONDS)


if __name__ == "__main__":
    unittest.main()


class ConcurrentStartTests(unittest.TestCase):
    def test_two_concurrent_steps_start_one_setup(self) -> None:
        import threading
        from unittest.mock import patch
        from scripts import setup_session
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            directory = setup_session.session_dir(home)
            launches = []

            class FakeSupervisor:
                def __init__(self, *_args, **_kwargs):
                    launches.append(self)
                    time.sleep(0.3)  # starting takes a moment
                    (directory / "session.json").write_text("{}", encoding="utf-8")

            def request(_directory, _message):
                return {"state": "waiting"} if launches else None

            replies = []
            with patch.object(setup_session.subprocess, "Popen", FakeSupervisor), \
                    patch.object(setup_session, "_request", side_effect=request):
                threads = [threading.Thread(target=lambda: replies.append(setup_session.step(home, ["tag", "setup"])))
                           for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
            self.assertEqual(len(launches), 1)
            self.assertEqual(replies, [{"state": "waiting"}] * 2)
