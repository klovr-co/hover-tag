from __future__ import annotations

import json
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from slack_bolt.response import BoltResponse
from websockets.sync.client import connect
from websockets.sync.server import serve

from scripts.slack_relay import RelayHandler, validate_relay_url
from scripts.opentag_process_env import backend_environment
from scripts import tag_config


class RelayTests(unittest.TestCase):
    def test_relay_requires_secure_url_and_private_token(self):
        for url in ('ws://host/connect', 'wss://u:p@host/connect', 'wss://host/connect?token=x', 'wss://host/wrong'):
            self.assertFalse(validate_relay_url(url))
        self.assertTrue(validate_relay_url('wss://receiver.example/connect'))
        with self.assertRaises(ValueError):
            RelayHandler(MagicMock(), 'wss://host/connect', 'short', 'T1', 'A1')
        config = {'OPENTAG_RELAY_URL': 'wss://host/connect', 'OPENTAG_RELAY_TOKEN': 's' * 32}
        self.assertEqual('[set]', tag_config.public_config(config)['OPENTAG_RELAY_TOKEN'])
        env = backend_environment(config, transport='slack', conversation_id='C1', caller_id='U1')
        self.assertNotIn('OPENTAG_RELAY_TOKEN', env)
        self.assertNotIn('OPENTAG_RELAY_URL', env)

    def test_relay_config_requires_token_instead_of_socket_mode_token(self):
        errors = tag_config.config_errors({'OPENTAG_RELAY_URL': 'wss://host/connect', 'SLACK_APP_TOKEN': ''})
        self.assertNotIn('SLACK_APP_TOKEN', errors)
        self.assertIn('OPENTAG_RELAY_TOKEN', errors)

    def test_live_outbound_transport_dispatches_and_returns_interactive_response(self):
        completed = threading.Event()
        received = []
        app = MagicMock()
        app.dispatch.return_value = BoltResponse(status=200, body={'response_action': 'errors', 'errors': {'model': 'Choose a model'}})

        def remote(ws):
            self.assertEqual('Bearer ' + 's' * 32, ws.request.headers['Authorization'])
            ws.send(json.dumps({'type': 'hello', 'team': 'T1', 'app': 'A1'}))
            ws.send(json.dumps({'type': 'probe', 'id': 'probe-1'}))
            ws.send(json.dumps({'type': 'request', 'id': 'request-1', 'body': {'type': 'view_submission', 'team': {'id': 'T1'}, 'user': {'id': 'U1'}, 'view': {}}}))
            try:
                for raw in ws:
                    message = json.loads(raw)
                    if message['type'] == 'ping':
                        ws.send(json.dumps({'type': 'pong'}))
                    else:
                        received.append(message)
                        if len(received) == 2:
                            completed.set()
            except Exception:
                pass

        with serve(remote, '127.0.0.1', 0) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            port = server.socket.getsockname()[1]
            with patch('scripts.slack_relay.connect', side_effect=lambda _url, **kw: connect(f'ws://127.0.0.1:{port}/connect', proxy=None, **kw)):
                handler = RelayHandler(app, 'wss://host/connect', 's' * 32, 'T1', 'A1')
                handler.connect()
                try:
                    self.assertTrue(completed.wait(5))
                    self.assertTrue(handler.is_connected())
                    reply = next(item for item in received if item['id'] == 'request-1')
                    self.assertEqual('errors', json.loads(reply['body'])['response_action'])
                    app.dispatch.assert_called_once()
                finally:
                    handler.close()
            server.shutdown()
        self.assertFalse(handler.is_connected())

    def test_stale_connection_is_not_ready(self):
        handler = RelayHandler(MagicMock(), 'wss://host/connect', 's' * 32, 'T1', 'A1')
        handler._socket = MagicMock()
        handler._last_pong = time.monotonic() - 21
        self.assertFalse(handler.is_connected())
        handler.close()
