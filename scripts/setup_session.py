"""Run JSON-lines setup in the background so a client can drive it one step at a time.

``tag setup --json`` needs one process that stays alive while the operator
answers, because a Slack sign-in ticket only lives inside that process. Agents
and other short-lived clients cannot easily hold a pipe open across turns, so
``tag setup --step`` starts that process under a small supervisor and returns
the next question. ``--answer`` sends one answer and returns the next question;
``--stop`` pauses setup, which saves progress.

The supervisor listens on 127.0.0.1 with a random port and a per-session token
stored in an owner-only file under the Tag home. Answers never reach disk.
If nobody talks to it for ``IDLE_SECONDS``, it pauses setup and exits.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

SESSION_DIR = ".setup-session"
IDLE_SECONDS = 30 * 60
WAIT_SECONDS = 25
START_SECONDS = 15


class SessionError(RuntimeError):
    """The step session is missing, unreachable, or was used incorrectly."""


def session_dir(home: Path) -> Path:
    return home / SESSION_DIR


def _write_private(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(payload, handle)
    os.replace(temporary, path)


def _read(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


# Supervisor -----------------------------------------------------------------

class _Supervisor:
    def __init__(self, directory: Path, command: list[str]):
        self.directory = directory
        self.token = secrets.token_urlsafe(32)
        self.events: list[dict] = []
        self.question: dict | None = None
        self.result: dict | None = None
        self.condition = threading.Condition()
        self.last_contact = time.monotonic()
        error_log = os.fdopen(os.open(directory / "err.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "ab")
        environment = dict(os.environ, TAG_SETUP_PROTOCOL="jsonl")
        self.child = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=error_log,
            env=environment, text=True, bufsize=1,
        )
        threading.Thread(target=self._read_child, daemon=True).start()

    def _read_child(self) -> None:
        assert self.child.stdout is not None
        for line in self.child.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            with self.condition:
                self.events.append(event)
                if event.get("type") == "question":
                    self.question = event
                elif event.get("type") == "result":
                    self.result = event
                self.condition.notify_all()
        self.child.wait()
        with self.condition:
            if self.result is None:
                self.result = {"type": "result", "status": "failed", "exit_code": self.child.returncode}
                self.events.append(self.result)
            self.question = None
            self.condition.notify_all()

    def _send(self, payload: dict) -> None:
        assert self.child.stdin is not None
        try:
            self.child.stdin.write(json.dumps(payload) + "\n")
            self.child.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def _close_stdin(self) -> None:
        try:
            if self.child.stdin:
                self.child.stdin.close()
        except OSError:
            pass

    def _drain(self, wait: float) -> dict:
        """Wait until setup asks something or ends, then hand over new events."""
        deadline = time.monotonic() + wait
        with self.condition:
            while self.question is None and self.result is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(remaining)
            events, self.events = self.events, []
            state = "ended" if self.result else "waiting" if self.question else "working"
            return {"state": state, "events": events, "question": self.question, "result": self.result}

    def handle(self, request: dict) -> dict:
        self.last_contact = time.monotonic()
        operation = request.get("op")
        if operation == "next":
            return self._drain(WAIT_SECONDS)
        if operation == "answer":
            with self.condition:
                question = self.question
                if question is None:
                    raise SessionError("Setup is not waiting for an answer")
                expected = request.get("id")
                if expected and expected != question.get("id"):
                    raise SessionError(f"Setup is asking '{question.get('id')}', not '{expected}'")
                self.question = None
                # Keep the asked question out of the next batch; the client has it.
                self.events = [event for event in self.events if event is not question]
            self._send({"answer": request.get("answer")})
            return self._drain(WAIT_SECONDS)
        if operation == "back":
            with self.condition:
                question = self.question
                if question is None or not question.get("can_go_back"):
                    raise SessionError("Setup can't go back from here")
                self.question = None
                self.events = [event for event in self.events if event is not question]
            self._send({"back": True})
            return self._drain(WAIT_SECONDS)
        if operation == "stop":
            with self.condition:
                waiting = self.question is not None
                self.question = None
            if waiting:
                self._send({"answer": None, "pause": True})
            self._close_stdin()
            return self._drain(WAIT_SECONDS)
        raise SessionError("Unknown session operation")

    def serve(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(1)
        _write_private(self.directory / "session.json", {
            "pid": os.getpid(), "port": listener.getsockname()[1], "token": self.token,
        })
        delivered_end = False
        try:
            while not delivered_end:
                if time.monotonic() - self.last_contact > IDLE_SECONDS:
                    # Nobody is answering: pause setup, which saves progress.
                    with self.condition:
                        waiting = self.question is not None
                    if waiting:
                        self._send({"answer": None, "pause": True})
                    self._close_stdin()
                    self.last_contact = time.monotonic()
                if self.result is not None and time.monotonic() - self.last_contact > IDLE_SECONDS:
                    break
                try:
                    connection, _address = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    continue
                with connection:
                    delivered_end = self._serve_connection(connection)
        finally:
            listener.close()
            leftover = self._drain(0)
            if not delivered_end and (leftover["events"] or leftover["result"]):
                # Keep the ending for the next --step, which reads it once.
                _write_private(self.directory / "final.json", leftover)
            (self.directory / "session.json").unlink(missing_ok=True)
            if self.child.poll() is None:
                self._close_stdin()

    def _serve_connection(self, connection: socket.socket) -> bool:
        connection.settimeout(WAIT_SECONDS + 10)
        try:
            raw = connection.makefile("r").readline()
            request = json.loads(raw)
            if not isinstance(request, dict) or not hmac.compare_digest(str(request.get("token", "")), self.token):
                raise SessionError("This setup session belongs to another client")
            reply = {"ok": True, **self.handle(request)}
        except (SessionError, ValueError) as error:
            reply = {"ok": False, "error": str(error)}
        except OSError:
            return False
        try:
            connection.sendall((json.dumps(reply, ensure_ascii=False) + "\n").encode())
        except OSError:
            return False
        return bool(reply.get("ok") and reply.get("state") == "ended")


def serve(directory: Path, command: list[str]) -> int:
    _Supervisor(directory, command).serve()
    return 0


# Client ---------------------------------------------------------------------

def _request(directory: Path, payload: dict) -> dict | None:
    """Talk to a live supervisor; ``None`` means no session is running."""
    session = _read(directory / "session.json")
    if not session:
        return None
    try:
        with socket.create_connection(("127.0.0.1", int(session["port"])), timeout=5) as connection:
            connection.settimeout(WAIT_SECONDS + 15)
            connection.sendall((json.dumps({**payload, "token": session["token"]}) + "\n").encode())
            raw = connection.makefile("r").readline()
    except (OSError, KeyError, ValueError, TypeError):
        # A supervisor that died without cleaning up leaves a stale file.
        (directory / "session.json").unlink(missing_ok=True)
        return None
    try:
        reply = json.loads(raw)
    except ValueError:
        raise SessionError("The setup session sent an unreadable reply") from None
    if not reply.get("ok"):
        raise SessionError(reply.get("error") or "The setup session refused the request")
    reply.pop("ok", None)
    return reply


def _finished(directory: Path) -> dict | None:
    final = directory / "final.json"
    reply = _read(final)
    final.unlink(missing_ok=True)
    return reply


# The supervisor outlives this command; holding the handle avoids a spurious
# "still running" warning when it is garbage-collected.
_launched: list[subprocess.Popen] = []


def step(home: Path, command: list[str]) -> dict:
    """Return the next setup question, starting a session when none is running."""
    directory = session_dir(home)
    if (reply := _request(directory, {"op": "next"})) is not None:
        return reply
    if (reply := _finished(directory)) is not None:
        return reply
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    _launched.append(subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "serve", str(directory), "--", *command],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True,
    ))
    deadline = time.monotonic() + START_SECONDS
    while time.monotonic() < deadline:
        if (directory / "session.json").exists():
            if (reply := _request(directory, {"op": "next"})) is not None:
                return reply
        time.sleep(0.1)
    raise SessionError("Setup did not start. See the setup log for details.")


def answer(home: Path, value: object, question_id: str | None = None) -> dict:
    reply = _request(session_dir(home), {"op": "answer", "answer": value, "id": question_id})
    if reply is None:
        raise SessionError("No setup is running. Start one with --step.")
    return reply


def back(home: Path) -> dict:
    """Return to the previous question; its earlier answer comes back as the default."""
    reply = _request(session_dir(home), {"op": "back"})
    if reply is None:
        raise SessionError("No setup is running. Start one with --step.")
    return reply


def stop(home: Path) -> dict:
    directory = session_dir(home)
    reply = _request(directory, {"op": "stop"})
    if reply is None:
        reply = _finished(directory) or {"state": "ended", "events": [], "question": None, "result": None}
    return reply


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[1] == "serve" and sys.argv[3] == "--":
        sys.exit(serve(Path(sys.argv[2]), sys.argv[4:]))
    sys.exit("usage: setup_session.py serve DIR -- COMMAND...")
