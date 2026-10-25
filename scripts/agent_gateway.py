"""Task-scoped Responses forwarding with explicit provider routing.

No request/response logging, redirect following, retries, or provider fallback.
The loopback token authenticates only this adapter, never the upstream service.
"""
from __future__ import annotations

import hmac
import http.client
import json
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

MAX_BODY_BYTES = 32 * 1024 * 1024


class GatewayProxy:
    def __init__(self, base_url: str, api_key: str, provider: str, *, timeout: float = 420, disable_tools: bool = False):
        try:
            from .agent_connection import validate_url
        except ImportError:
            from agent_connection import validate_url
        validate_url(base_url)
        self.upstream = urlsplit(base_url)
        self.api_key = api_key
        self.provider = provider
        self.timeout = timeout
        self.disable_tools = disable_tools
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.connections: set[http.client.HTTPConnection] = set()
        self.clients: set[socket.socket] = set()
        self.upstream_sockets: set[socket.socket] = set()
        self.closed = False
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(proxy.timeout)
                with proxy.lock:
                    proxy.clients.add(self.connection)

            def finish(self):
                try:
                    super().finish()
                except OSError:
                    pass
                finally:
                    with proxy.lock:
                        proxy.clients.discard(self.connection)

            def reject(self, status: int, message: str):
                data = json.dumps({"error": {"message": message, "type": "gateway_adapter_error"}}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.reject(405, "Gateway routing accepts Responses POST requests only")

            def do_POST(self):
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + proxy.token):
                    self.reject(401, "Invalid gateway adapter authentication")
                    return
                if self.path not in {"/responses", "/responses/compact"}:
                    self.reject(404, "Unsupported gateway adapter endpoint")
                    return
                if self.headers.get("Content-Encoding", "identity") != "identity" or self.headers.get("Transfer-Encoding"):
                    self.reject(415, "Gateway routing requires uncompressed JSON")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= MAX_BODY_BYTES:
                        self.reject(413, "Gateway request body is empty or too large")
                        return
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict):
                        raise ValueError()
                except (ValueError, OSError):
                    self.reject(400, "Invalid gateway JSON request")
                    return
                # Replace any competing route rather than allowing silent fallback.
                payload["provider"] = {"only": [proxy.provider]}
                if proxy.disable_tools:
                    for key in ("tools", "tool_choice", "parallel_tool_calls"):
                        payload.pop(key, None)
                    payload["instructions"] = (payload.get("instructions") or "") + (
                        "\nTool use is disabled for this connection. Respond with text only. "
                        "You cannot browse, search, read files, run commands, or take actions. "
                        "Do not claim to have performed those actions."
                    )
                body = json.dumps(payload).encode()
                connection_type = http.client.HTTPSConnection if proxy.upstream.scheme == "https" else http.client.HTTPConnection
                conn = connection_type(proxy.upstream.hostname, proxy.upstream.port, timeout=proxy.timeout)
                with proxy.lock:
                    if proxy.closed:
                        conn.close()
                        return
                    proxy.connections.add(conn)
                started = False
                upstream_socket = None
                try:
                    headers = {"Authorization": "Bearer " + proxy.api_key,
                               "Content-Type": "application/json", "Accept": self.headers.get("Accept", "text/event-stream"),
                               "Accept-Encoding": "identity"}
                    conn.request("POST", proxy.upstream.path.rstrip("/") + self.path, body=body, headers=headers)
                    upstream_socket = conn.sock
                    with proxy.lock:
                        if proxy.closed:
                            return
                        if upstream_socket is not None:
                            proxy.upstream_sockets.add(upstream_socket)
                    upstream = conn.getresponse()
                    if 300 <= upstream.status < 400:
                        self.reject(502, "Gateway redirect refused; configure the final API base URL")
                        return
                    self.send_response(upstream.status)
                    for name in ("Content-Type", "Content-Encoding", "Retry-After", "X-Request-Id"):
                        value = upstream.getheader(name)
                        if value:
                            self.send_header(name, value)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    started = True
                    while not proxy.closed:
                        chunk = upstream.read1(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (OSError, http.client.HTTPException):
                    if not started and not proxy.closed:
                        try:
                            self.reject(502, "Gateway connection failed before response headers")
                        except OSError:
                            pass
                finally:
                    conn.close()
                    with proxy.lock:
                        proxy.connections.discard(conn)
                        if upstream_socket is not None:
                            proxy.upstream_sockets.discard(upstream_socket)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            sockets = list(self.clients | self.upstream_sockets) + [conn.sock for conn in self.connections if conn.sock is not None]
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
