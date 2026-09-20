from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from scripts.codex_app_server import (
    CodexAppServer,
    CodexAppServerError,
    CodexEventMapper,
    JsonLineDecoder,
    activity_label,
)


class JsonLineDecoderTests(unittest.TestCase):
    def test_decodes_fragmented_and_coalesced_jsonl(self) -> None:
        decoder = JsonLineDecoder()

        self.assertEqual([], decoder.feed(b'{"id":'))
        self.assertEqual(
            [b'{"id":1}', b'{"method":"turn/started"}'],
            decoder.feed(b'1}\n{"method":"turn/started"}\n'),
        )
        decoder.finish()

    def test_rejects_truncated_and_oversized_lines(self) -> None:
        decoder = JsonLineDecoder(max_line_bytes=4)
        with self.assertRaisesRegex(CodexAppServerError, "oversized"):
            decoder.feed(b"12345")

        decoder = JsonLineDecoder()
        decoder.feed(b'{"id":1}')
        with self.assertRaisesRegex(CodexAppServerError, "truncated"):
            decoder.finish()


class CodexEventMapperTests(unittest.TestCase):
    def test_streams_only_explicit_final_answer_messages(self) -> None:
        mapper = CodexEventMapper()
        mapper.map({
            "method": "item/started",
            "params": {"item": {"id": "comment", "type": "agentMessage", "phase": "commentary"}},
        })
        self.assertEqual([], mapper.map({
            "method": "item/agentMessage/delta",
            "params": {"itemId": "comment", "delta": "private preamble"},
        }))

        mapper.map({
            "method": "item/started",
            "params": {"item": {"id": "answer", "type": "agentMessage", "phase": "final_answer"}},
        })
        self.assertEqual(
            [{
                "type": "message_delta",
                "message_id": "answer",
                "phase": "final_answer",
                "text": "Hello",
            }],
            mapper.map({
                "method": "item/agentMessage/delta",
                "params": {"itemId": "answer", "delta": "Hello"},
            }),
        )
        completed = mapper.map({
            "method": "item/completed",
            "params": {"item": {
                "id": "answer", "type": "agentMessage", "phase": "final_answer", "text": "Hello!"
            }},
        })
        self.assertEqual("message_complete", completed[0]["type"])
        self.assertEqual("Hello!", completed[0]["text"])

    def test_unknown_phase_is_buffered_until_terminal_fallback(self) -> None:
        mapper = CodexEventMapper()
        self.assertEqual([], mapper.map({
            "method": "item/agentMessage/delta",
            "params": {"itemId": "unknown", "delta": "Do not stream yet"},
        }))
        self.assertEqual([], mapper.map({
            "method": "item/completed",
            "params": {"item": {"id": "unknown", "type": "agentMessage", "text": "Final fallback"}},
        }))
        events = mapper.map({
            "method": "turn/completed",
            "params": {"turn": {"id": "turn", "status": "completed", "items": []}},
        })

        self.assertEqual("message_complete", events[0]["type"])
        self.assertEqual("Final fallback", events[0]["text"])
        self.assertEqual({"type": "turn_complete", "status": "completed"}, events[1])

    def test_unknown_started_phase_flushes_when_completion_classifies_final(self) -> None:
        mapper = CodexEventMapper()
        mapper.map({
            "method": "item/started",
            "params": {"item": {"id": "late-phase", "type": "agentMessage"}},
        })
        self.assertEqual([], mapper.map({
            "method": "item/agentMessage/delta",
            "params": {"itemId": "late-phase", "delta": "Buffered"},
        }))

        events = mapper.map({
            "method": "item/completed",
            "params": {"item": {
                "id": "late-phase",
                "type": "agentMessage",
                "phase": "final_answer",
                "text": "Buffered answer",
            }},
        })

        self.assertEqual(["message_delta", "message_complete"], [event["type"] for event in events])
        self.assertEqual("Buffered", events[0]["text"])

    def test_message_completion_is_not_turn_completion(self) -> None:
        mapper = CodexEventMapper()
        mapper.map({
            "method": "item/started",
            "params": {"item": {"id": "m1", "type": "agentMessage", "phase": "final_answer"}},
        })
        events = mapper.map({
            "method": "item/completed",
            "params": {"item": {
                "id": "m1", "type": "agentMessage", "phase": "final_answer", "text": "Ready"
            }},
        })
        self.assertNotIn("turn_complete", {event["type"] for event in events})

    def test_maps_identifiable_activity_without_raw_payloads(self) -> None:
        mapper = CodexEventMapper()
        started = mapper.map({
            "method": "item/started",
            "params": {"item": {
                "id": "tool-1", "type": "commandExecution", "command": "python mfs_search.py secret"
            }},
        })

        self.assertEqual("Searching connected knowledge…", started[0]["label"])
        self.assertNotIn("command", started[0])
        self.assertEqual("Running a command…", activity_label({
            "type": "commandExecution", "command": "cat private.txt"
        }))

    def test_mcp_labels_expose_only_approved_metadata(self) -> None:
        for server, tool, expected in [
            ("github", "search_issues", "Searching GitHub issues…"),
            ("Slack", "private_tool", "Using Slack…"),
            ("private-company", "search", "Searching with a connected tool…"),
            ("<!channel>", "<https://private|secret>", "Using a connected tool…"),
            ("github\nsecret", "search_issues\u202esecret", "Using a connected tool…"),
            (None, ["search"], "Using a connected tool…"),
        ]:
            with self.subTest(server=server, tool=tool):
                mapper = CodexEventMapper()
                event = mapper.map({
                    "method": "item/started", "params": {"item": {
                        "id": "tool-1", "type": "mcpToolCall", "server": server,
                        "tool": tool, "arguments": {"token": "secret"},
                        "result": "private output",
                    }},
                })[0]
                self.assertEqual(expected, event["label"])
                self.assertEqual({"type", "activity_id", "label", "wait_label"}, set(event))
                self.assertNotIn("secret", event["wait_label"])

    def test_helper_mentions_do_not_claim_execution(self) -> None:
        for command in [
            "echo mfs_search.py", "cat mfs_search.py", "python -c 'mfs_search.py'",
            "python unrelated.py mfs_search.py", "python mfs_search.py.bak query",
            "false && python mfs_search.py query", "python mfs_search.py query; echo done",
            "python 'unterminated", "bash -lc 'echo mfs_search.py'",
            "python $(echo mfs_search.py) query",
        ]:
            with self.subTest(command=command):
                self.assertEqual("Running a command…", activity_label({
                    "type": "commandExecution", "command": command,
                }))

    def test_recognizes_simple_helpers_through_shell_wrapper(self) -> None:
        for command in [
            "python3 /workspace/scripts/mfs_search.py query",
            "'/workspace with spaces/mfs_search.py' query",
            "zsh -lc 'python3.12 scripts/mfs_search.py query'",
        ]:
            with self.subTest(command=command):
                self.assertEqual("Searching connected knowledge…", activity_label({
                    "type": "commandExecution", "command": command,
                }))


class ServerRequestTests(unittest.TestCase):
    def test_only_item_and_turn_lifecycle_refresh_idle_time(self) -> None:
        self.assertTrue(CodexAppServer._is_progress_notification({"method": "item/started"}))
        self.assertTrue(CodexAppServer._is_progress_notification({"method": "turn/plan/updated"}))
        self.assertFalse(CodexAppServer._is_progress_notification({"method": "ping"}))
        self.assertFalse(CodexAppServer._is_progress_notification({"method": "account/updated"}))

    def test_unattended_requests_are_resolved_conservatively(self) -> None:
        server = CodexAppServer(["codex"], cwd=Path("/tmp"), timeout=1)
        server._send = MagicMock()  # type: ignore[method-assign]

        server._resolve_server_request({
            "id": 7, "method": "item/commandExecution/requestApproval", "params": {}
        })
        server._send.assert_called_once_with({"id": 7, "result": {"decision": "decline"}})

    def test_unknown_server_request_gets_json_rpc_error(self) -> None:
        server = CodexAppServer(["codex"], cwd=Path("/tmp"), timeout=1)
        server._send = MagicMock()  # type: ignore[method-assign]

        server._resolve_server_request({"id": "x", "method": "future/request", "params": {}})

        payload = server._send.call_args.args[0]
        self.assertEqual(-32601, payload["error"]["code"])


class AppServerWireTests(unittest.TestCase):
    FAKE_SERVER = r'''
import json
import sys

def send(payload):
    print(json.dumps(payload), flush=True)

pending_thread_start = None
for raw in sys.stdin:
    message = json.loads(raw)
    method = message.get("method")
    if message.get("id") == 900 and method is None:
        assert message["result"]["decision"] == "decline"
        send({"id": pending_thread_start["id"], "result": {"thread": {"id": "thread-1"}}})
    elif method == "initialize":
        send({"id": message["id"], "result": {"userAgent": "fake"}})
    elif method == "initialized":
        pass
    elif method == "thread/start":
        pending_thread_start = message
        send({"id": 900, "method": "item/commandExecution/requestApproval", "params": {}})
    elif method == "turn/start":
        prompt = message["params"]["input"][0]["text"]
        if "wait-for-interrupt" not in prompt:
            # Lifecycle notifications may arrive before the matching turn/start response.
            send({"method": "item/started", "params": {
                "threadId": "thread-1", "turnId": "turn-1", "startedAtMs": 1,
                "item": {"id": "message-1", "type": "agentMessage", "text": "", "phase": "final_answer"}
            }})
            send({"method": "item/agentMessage/delta", "params": {
                "threadId": "thread-1", "turnId": "turn-1", "itemId": "message-1", "delta": "Hello"
            }})
        send({"id": message["id"], "result": {
            "turn": {"id": "turn-1", "status": "inProgress", "items": []}
        }})
        if "exit-after-start" in prompt:
            raise SystemExit(7)
        if "wait-for-interrupt" not in prompt:
            print("malformed diagnostic", flush=True)
            send({"method": "item/completed", "params": {
                "threadId": "thread-1", "turnId": "turn-1", "completedAtMs": 2,
                "item": {"id": "message-1", "type": "agentMessage", "text": "Hello", "phase": "final_answer"}
            }})
            send({"method": "turn/completed", "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-1", "status": "completed", "items": []}
            }})
    elif method == "turn/interrupt":
        send({"id": message["id"], "result": {}})
        send({"method": "turn/completed", "params": {
            "threadId": "thread-1",
            "turn": {"id": "turn-1", "status": "interrupted", "items": []}
        }})
'''

    def fake_server_path(self, root: Path) -> Path:
        path = root / "fake_app_server.py"
        path.write_text(self.FAKE_SERVER, encoding="utf-8")
        return path

    def test_handshake_stream_and_terminal_status(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            events: list[dict[str, object]] = []
            server = CodexAppServer(
                [sys.executable, "-u", str(self.fake_server_path(root))],
                cwd=root,
                timeout=5,
            )

            status, detail = server.run(
                "answer normally", model="gpt-test", reasoning_effort="high", emit=events.append
            )

        self.assertEqual(("completed", ""), (status, detail))
        self.assertEqual("Hello", next(
            event["text"] for event in events if event["type"] == "message_delta"
        ))
        self.assertEqual("turn_complete", events[-1]["type"])

    def test_control_file_interrupt_waits_for_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            control = root / "control"
            control.write_text("run-1", encoding="utf-8")
            events: list[dict[str, object]] = []
            server = CodexAppServer(
                [sys.executable, "-u", str(self.fake_server_path(root))],
                cwd=root,
                timeout=5,
                control_file=control,
                run_id="run-1",
            )

            status, _detail = server.run(
                "wait-for-interrupt", model=None, reasoning_effort=None, emit=events.append
            )

        self.assertEqual("interrupted", status)
        self.assertEqual("interrupted", events[-1]["status"])

    def test_timeout_interrupts_and_waits_for_terminal_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            events: list[dict[str, object]] = []
            server = CodexAppServer(
                [sys.executable, "-u", str(self.fake_server_path(root))],
                cwd=root,
                timeout=0.5,
                max_timeout=5,
            )

            status, detail = server.run(
                "wait-for-interrupt", model=None, reasoning_effort=None, emit=events.append
            )

        self.assertEqual(("timeout", "no backend activity for 0.5s"), (status, detail))
        self.assertEqual("interrupted", events[-1]["status"])

    def test_unexpected_exit_reports_the_bounded_process_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            server = CodexAppServer(
                [sys.executable, "-u", str(self.fake_server_path(root))],
                cwd=root,
                timeout=5,
            )

            with self.assertRaisesRegex(CodexAppServerError, "exited with code 7"):
                server.run(
                    "exit-after-start", model=None, reasoning_effort=None, emit=lambda _event: None
                )


if __name__ == "__main__":
    unittest.main()
