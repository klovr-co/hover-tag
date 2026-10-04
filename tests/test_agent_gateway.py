from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from scripts.agent_gateway import GatewayProxy
from scripts import agent_connection, tag_config
from scripts.codex_agent_backend import CodexAppServer, CodexAppServerError
from scripts.claude_agent_backend import ClaudeAgentRun, ClaudeAgentError


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.status = 200
        self.body = b'data: {"type":"response.completed","response":{"usage":{"input_tokens":3}}}\n\n'
        self.first = threading.Event()
        self.release = threading.Event()
        self.stream = False
        case = self

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                case.requests.append((self.path, dict(self.headers), body))
                self.send_response(case.status)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Retry-After', '5')
                if case.status == 302: self.send_header('Location', 'http://example.invalid/')
                self.end_headers()
                try:
                    self.wfile.write(case.body)
                    self.wfile.flush()
                    case.first.set()
                    if case.stream:
                        case.release.wait(3)
                        self.wfile.write(b'data: [DONE]\n\n')
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.proxy = GatewayProxy(f'http://127.0.0.1:{self.server.server_port}/v1', 'upstream-secret', 'cursor_sdk', timeout=5)

    def tearDown(self):
        self.release.set()
        self.proxy.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)

    def request(self, payload=None, *, token=None, path='/responses', headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.proxy.server.server_port, timeout=3)
        self.addCleanup(conn.close)
        req_headers = {'Authorization': 'Bearer ' + (token if token is not None else self.proxy.token),
                       'Content-Type': 'application/json'}
        req_headers.update(headers or {})
        conn.request('POST', path, json.dumps(payload or {'model': 'grok-4.7'}), req_headers)
        return conn.getresponse()

    def test_route_injection_credentials_and_response_usage_preserved(self):
        body = {'model': 'grok-4.7', 'provider': {'order': ['other']}, 'input': 'hello',
                'tools': [{'type': 'custom', 'name': 'exec'}], 'stream': True}
        response = self.request(body)
        self.assertEqual(response.read(), self.body)
        path, headers, forwarded = self.requests[0]
        self.assertEqual(path, '/v1/responses')
        self.assertEqual(headers['Authorization'], 'Bearer upstream-secret')
        self.assertNotIn(self.proxy.token, str(headers))
        self.assertEqual(forwarded, dict(body, provider={'only': ['cursor_sdk']}))
        self.assertEqual(response.getheader('Retry-After'), '5')

    def test_chat_only_omits_tools_and_related_controls(self):
        self.proxy.disable_tools = True
        response = self.request({'model': 'grok-4.7', 'input': 'hello',
                                 'instructions': 'Be concise.', 'tools': [{'type': 'function'}],
                                 'tool_choice': 'required', 'parallel_tool_calls': True})
        response.read()
        forwarded = self.requests[0][2]
        for key in ('tools', 'tool_choice', 'parallel_tool_calls'):
            self.assertNotIn(key, forwarded)
        self.assertEqual(forwarded['input'], 'hello')
        self.assertTrue(forwarded['instructions'].startswith('Be concise.'))
        self.assertIn('Tool use is disabled', forwarded['instructions'])
        self.assertEqual(forwarded['provider'], {'only': ['cursor_sdk']})

    def test_sse_arrives_before_upstream_finishes(self):
        self.stream = True
        response = self.request()
        self.assertTrue(self.first.wait(1))
        self.assertEqual(response.readline(), self.body.splitlines(keepends=True)[0])
        self.assertFalse(self.release.is_set())
        self.release.set()
        self.assertIn(b'[DONE]', response.read())

    def test_close_interrupts_live_stream_and_is_idempotent(self):
        self.stream = True
        response = self.request()
        self.assertTrue(self.first.wait(1))
        started = time.monotonic()
        self.proxy.close()
        self.proxy.close()
        self.assertLess(time.monotonic() - started, 1)
        response.read()
        self.assertFalse(self.proxy.thread.is_alive())

    def test_errors_are_passed_without_retry_or_rerouting(self):
        self.status = 503
        self.body = b'{"error":{"message":"No eligible provider account"}}'
        response = self.request()
        self.assertEqual(response.status, 503)
        self.assertEqual(response.read(), self.body)
        self.assertEqual(len(self.requests), 1)

    def test_redirects_are_not_followed(self):
        self.status = 302
        response = self.request()
        self.assertEqual(response.status, 502)
        self.assertIn(b'redirect refused', response.read())
        self.assertEqual(len(self.requests), 1)

    def test_unauthenticated_wrong_path_and_compressed_requests_fail_closed(self):
        for kwargs, expected in [({'token': 'wrong'}, 401), ({'path': '/models'}, 404),
                                 ({'headers': {'Content-Encoding': 'zstd'}}, 415)]:
            response = self.request(**kwargs)
            self.assertEqual(response.status, expected)
            response.read()
        self.assertEqual(self.requests, [])


class RoutingConfigTests(unittest.TestCase):
    def values(self):
        return {'OPENTAG_CODEX_AUTH': 'api', 'OPENTAG_CODEX_API_KEY': 'secret',
                'OPENTAG_CODEX_MODELS': 'grok-4.7', 'OPENTAG_CODEX_BASE_URL': 'https://gateway.example/v1',
                'OPENTAG_CODEX_GATEWAY_FORMAT': 'provider.only', 'OPENTAG_CODEX_GATEWAY_PROVIDER': 'cursor_sdk'}

    def test_optional_and_invalid_routes(self):
        self.assertIsNone(agent_connection.routing('codex', {}))
        self.assertEqual(agent_connection.routing('codex', self.values()), 'cursor_sdk')
        for key, value in [('OPENTAG_CODEX_AUTH','inherit'), ('OPENTAG_CODEX_AUTH','azure'),
                           ('OPENTAG_CODEX_GATEWAY_FORMAT',''), ('OPENTAG_CODEX_GATEWAY_FORMAT','unknown'),
                           ('OPENTAG_CODEX_GATEWAY_PROVIDER',''), ('OPENTAG_CODEX_GATEWAY_PROVIDER','a/b'),
                           ('OPENTAG_CODEX_BASE_URL','')]:
            with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                agent_connection.validate('codex', dict(self.values(), **{key:value}))

    def test_chat_only_requires_routing_and_valid_boolean(self):
        key = 'OPENTAG_CODEX_GATEWAY_DISABLE_TOOLS'
        agent_connection.validate('codex', dict(self.values(), **{key: '1'}))
        for value in ('yes', 'false'):
            with self.assertRaises(ValueError):
                agent_connection.validate('codex', dict(self.values(), **{key: value}))
        with self.assertRaisesRegex(ValueError, 'requires explicit'):
            agent_connection.routing('codex', {key: '1'})
        self.assertIsNone(agent_connection.routing('codex', {key: '0'}))
        self.assertIn(key, tag_config.EDITABLE)
        self.assertIsNotNone(tag_config.validation_error(key, 'yes'))

    def test_claude_rejects_chat_only_explicitly(self):
        with patch.dict(os.environ, {'OPENTAG_CLAUDE_GATEWAY_DISABLE_TOOLS': '1'}, clear=True), \
                tempfile.TemporaryDirectory() as tmp, \
                patch.object(ClaudeAgentRun, '_sdk', return_value=(None, lambda **kw: kw, None, None)):
            with self.assertRaisesRegex(ClaudeAgentError, 'not Claude'):
                ClaudeAgentRun(cwd=Path(tmp), timeout=1).options(
                    model=None, reasoning_effort=None, fast_mode=False, emit=None, deadline=10)

    def test_claude_rejects_routing_explicitly(self):
        values = {k.replace('CODEX','CLAUDE'):v for k,v in self.values().items()}
        with patch.dict(os.environ, values, clear=True), tempfile.TemporaryDirectory() as tmp, \
                patch.object(ClaudeAgentRun, '_sdk', return_value=(None, lambda **kw: kw, None, None)):
            with self.assertRaisesRegex(ClaudeAgentError, 'not Claude'):
                ClaudeAgentRun(cwd=Path(tmp), timeout=1).options(
                    model=None, reasoning_effort=None, fast_mode=False, emit=None, deadline=10)

    def test_start_failure_closes_adapter_and_secrets_stay_out_of_argv(self):
        with patch.dict(os.environ, dict(self.values(), OPENTAG_CODEX_GATEWAY_DISABLE_TOOLS='1'), clear=True), tempfile.TemporaryDirectory() as tmp, \
                patch('scripts.codex_agent_backend.agent_gateway.GatewayProxy', wraps=GatewayProxy) as factory, \
                patch('scripts.codex_agent_backend.subprocess.Popen', side_effect=OSError('test failure')) as popen:
            runner = CodexAppServer(['codex','app-server','--stdio'], cwd=Path(tmp), timeout=1)
            with self.assertRaises(CodexAppServerError): runner._start()
            self.assertIsNone(runner.gateway_proxy)
            self.assertEqual(factory.call_count, 1)
            self.assertTrue(factory.call_args.kwargs["disable_tools"])
            args = popen.call_args.args[0]
            env = popen.call_args.kwargs['env']
            self.assertNotIn('secret', str(args))
            self.assertNotIn(env['TAG_GATEWAY_PROXY_TOKEN'], str(args))
            self.assertIn('features.enable_request_compression=false', args)
            self.assertIn('model_providers.tag_api.env_key="TAG_GATEWAY_PROXY_TOKEN"', args)
            runner.close()

    def test_settings_are_editable_and_validate_format(self):
        for key in ('OPENTAG_CODEX_GATEWAY_FORMAT','OPENTAG_CODEX_GATEWAY_PROVIDER'):
            self.assertIn(key, tag_config.EDITABLE)
