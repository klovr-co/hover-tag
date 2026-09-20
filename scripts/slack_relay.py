"""Outbound connection from the local Slack bridge to Tag's hosted receiver."""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from slack_bolt.request import BoltRequest
from websockets.sync.client import connect


def validate_relay_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return bool(parsed.scheme == "wss" and parsed.hostname and (parsed.path == "/connect" or re.fullmatch(r"/v1/apps/A[A-Z0-9]+/connect", parsed.path))
                    and not (parsed.username or parsed.password or parsed.query or parsed.fragment))
    except ValueError:
        return False


class RelayHandler:
    """Match the lifecycle surface used by SocketModeHandler, without opening Slack sockets."""

    def __init__(self, app, url: str, token: str, team: str, app_id: str):
        if not validate_relay_url(url):
            raise ValueError("OPENTAG_RELAY_URL must be wss://host/connect without credentials or query")
        if len(token) < 32 or any(char.isspace() for char in token):
            raise ValueError("OPENTAG_RELAY_TOKEN must contain at least 32 non-whitespace characters")
        self.app, self.url, self.token = app, url, token
        self.team, self.app_id = team, app_id
        self.client = self
        self._stop = threading.Event()
        self._socket = None
        self._last_pong = 0.0
        self._thread = None
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tag-relay-request")
        self._slots = threading.BoundedSemaphore(8)

    def is_connected(self) -> bool:
        return self._socket is not None and time.monotonic() - self._last_pong < 20

    def connect(self) -> None:
        self._thread = threading.Thread(target=self._run, name="tag-relay", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._socket:
            self._socket.close()
        if self._thread:
            self._thread.join(timeout=12)
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _dispatch(self, socket, message) -> None:
        try:
            response = self.app.dispatch(BoltRequest(mode="socket_mode", body=message["body"]))
            socket.send(json.dumps({"type": "response", "id": message["id"],
                                    "status": response.status, "body": response.body,
                                    "content_type": response.headers.get("content-type", ["application/json"])[0]}))
        except Exception:  # A disconnected socket must not replay a possibly started task.
            self.app.logger.warning("Hosted receiver request acknowledgement failed")
        finally:
            self._slots.release()

    def _run(self) -> None:
        delay = 1
        while not self._stop.is_set():
            try:
                with connect(self.url, additional_headers={"Authorization": f"Bearer {self.token}"},
                             open_timeout=10, close_timeout=2, max_size=256 * 1024,
                             ping_interval=10, ping_timeout=10) as socket:
                    hello = json.loads(socket.recv(timeout=10))
                    if hello.get("type") != "hello" or hello.get("team") != self.team or hello.get("app") != self.app_id:
                        raise ValueError("Hosted receiver installation mismatch")
                    self._socket = socket
                    self._last_pong = time.monotonic()
                    delay = 1
                    next_ping = 0.0
                    while not self._stop.is_set():
                        now = time.monotonic()
                        if now - self._last_pong > 20:
                            raise TimeoutError("Hosted receiver heartbeat expired")
                        if now >= next_ping:
                            socket.send(json.dumps({"type": "ping"}))
                            next_ping = now + 5
                        try:
                            message = json.loads(socket.recv(timeout=1))
                        except TimeoutError:
                            continue
                        kind = message.get("type")
                        if kind == "pong":
                            if message.get("connected", True):
                                self._last_pong = time.monotonic()
                            else:
                                self._last_pong = 0.0
                        elif kind == "probe":
                            socket.send(json.dumps({"type": "response", "id": message["id"]}))
                        elif kind == "request":
                            if self._slots.acquire(blocking=False):
                                self._pool.submit(self._dispatch, socket, message)
                            else:
                                socket.send(json.dumps({"type": "response", "id": message["id"],
                                                        "status": 503, "body": "Tag is busy. Please retry."}))
            except Exception:
                # Exception strings can contain handshake headers; never log credentials.
                if not self._stop.is_set():
                    self.app.logger.warning("Hosted receiver disconnected; reconnecting")
            finally:
                self._socket = None
                self._last_pong = 0.0
            self._stop.wait(delay)
            delay = min(delay * 2, 30)
