from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.agent_activity import APPROVAL_TIMEOUT_SECONDS, activity_label
from scripts.codex_agent_backend import (
    CodexAppServer,
    CodexAppServerError,
    CodexEventMapper,
    JsonLineDecoder,
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


class ModelCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        # These fixtures exercise inherited Codex sign-in, independent of local accounts.
        auth = patch("scripts.tag_chatgpt.enabled", return_value=False)
        auth.start()
        self.addCleanup(auth.stop)

    def test_catalog_reads_all_pages_without_starting_an_agent_task(self) -> None:
        server = CodexAppServer(["codex", "app-server"], cwd=Path.cwd(), timeout=10)
        with patch.object(server, "_start"), patch.object(server, "_notify"), patch.object(server, "close") as close, patch.object(
            server, "_request", side_effect=[{}, {"data": [{"model": "first"}], "nextCursor": "page-2"},
                                            {"data": [{"model": "second", "isDefault": True}], "nextCursor": None}],
        ) as request:
            self.assertEqual([model["model"] for model in server.model_catalog()], ["first", "second"])
            self.assertEqual([call.args[0] for call in request.call_args_list], ["initialize", "model/list", "model/list"])
            self.assertEqual(request.call_args_list[-1].args[1], {"cursor": "page-2"})
        close.assert_called_once()

    def test_failed_catalog_lookup_cleans_up_and_can_be_retried(self) -> None:
        server = CodexAppServer(["codex", "app-server"], cwd=Path.cwd(), timeout=10)
        with patch.object(server, "_start"), patch.object(server, "_notify"), patch.object(server, "close") as close, patch.object(
            server, "_request", side_effect=[{}, CodexAppServerError("unavailable")],
        ):
            with self.assertRaises(CodexAppServerError):
                server.model_catalog()
        close.assert_called_once()


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
        self.assertEqual("Reading files…", activity_label({
            "type": "commandExecution", "command": "cat private.txt"
        }))
        self.assertEqual("Using agent tools…", activity_label({
            "type": "dynamicToolCall", "tool": "private nested connector",
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
                self.assertEqual({"type", "activity_id", "label", "wait_label", "details"}, set(event))
                self.assertNotIn("secret", event["wait_label"])

    def test_tool_details_are_redacted_before_the_event_leaves_app_server(self) -> None:
        mapper = CodexEventMapper()
        start = mapper.map({"method": "item/started", "params": {"item": {
            "id": "tool-1", "type": "mcpToolCall", "server": "gmail",
            "tool": "send_email", "arguments": {
                "to": "person@example.com", "subject": "Refund",
                "password": "hidden-password", "body": "Please issue a refund.",
            },
        }}})[0]
        complete = mapper.map({"method": "item/completed", "params": {"item": {
            "id": "tool-1", "type": "mcpToolCall", "server": "gmail",
            "tool": "send_email", "status": "completed",
            "result": {"message_id": "sent-123", "access_token": "hidden-token"},
        }}})[0]
        self.assertIn("person@example.com", start["details"]["input"])
        self.assertIn("Please issue a refund", start["details"]["input"])
        self.assertIn("sent-123", complete["details"]["output"])
        self.assertNotIn("hidden-password", str(start))
        self.assertNotIn("hidden-token", str(complete))
        self.assertEqual([], mapper.map({"method": "item/completed", "params": {"item": {
            "id": "reason-1", "type": "reasoning", "content": "private reasoning",
            "summary": "private summary",
        }}}))

    def test_helper_mentions_do_not_claim_execution(self) -> None:
        for command in [
            "echo mfs_search.py", "python -c 'mfs_search.py'",
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

    def test_recognizes_safe_file_document_and_test_commands(self) -> None:
        for command, expected in [
            ("cat notes.txt", "Reading files…"),
            ("cp draft.md final.md", "Writing files…"),
            ("python create_launch_document_docx.py", "Creating a document…"),
            ("python -m pytest tests", "Running tests…"),
            ("pnpm test", "Running tests…"),
            ("cargo build", "Running a command…"),
        ]:
            with self.subTest(command=command):
                self.assertEqual(expected, activity_label({
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

    def test_slack_approval_accepts_one_command_and_resumes_same_request(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            approval_dir = Path(raw_dir)
            approval_id = "a" * 32
            (approval_dir / f"{approval_id}.json").write_text(
                json.dumps({"choice": "0"}), encoding="utf-8"
            )
            server = CodexAppServer(
                ["codex"], cwd=Path(raw_dir), timeout=1, approval_dir=approval_dir
            )
            server._send = MagicMock()  # type: ignore[method-assign]
            emitted: list[dict[str, object]] = []

            with patch("scripts.codex_agent_backend.uuid.uuid4") as make_id:
                make_id.return_value.hex = approval_id
                handled = server._resolve_server_request(
                    {
                        "id": 8,
                        "method": "item/commandExecution/requestApproval",
                        "params": {"command": "private command is never emitted"},
                    },
                    emit=emitted.append,
                    deadline=time.monotonic() + 1,
                )

        self.assertTrue(handled)
        self.assertEqual("approval_request", emitted[0]["type"])
        self.assertEqual(approval_id, emitted[0]["approval_id"])
        self.assertEqual(["Allow once", "Allow for this task", "Deny", "Deny and stop"],
                         [c["label"] for c in emitted[0]["choices"]])
        self.assertEqual("approval_expired", emitted[-1]["type"])
        self.assertNotIn("private command", json.dumps(emitted))
        server._send.assert_called_once_with({"id": 8, "result": {"decision": "accept"}})

    def test_permission_approval_grants_only_requested_permissions_for_turn(self) -> None:
        requested = {"network": {"enabled": True}}

        self.assertEqual(
            {"permissions": requested, "scope": "turn"},
            CodexAppServer._approval_result(
                "item/permissions/requestApproval",
                {"permissions": requested},
                True,
            ),
        )
        self.assertEqual(
            {"permissions": {}, "scope": "turn"},
            CodexAppServer._approval_result(
                "item/permissions/requestApproval",
                {"permissions": requested},
                False,
            ),
        )

    def test_slack_approval_wait_uses_earliest_dedicated_or_outer_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            server = CodexAppServer(
                ["codex"], cwd=Path(raw_dir), timeout=1, approval_dir=Path(raw_dir)
            )
            server._send = MagicMock()  # type: ignore[method-assign]
            server._wait_for_approval_decision = MagicMock(return_value={})  # type: ignore[method-assign]

            for outer_deadline, expected_deadline in [
                (10_000.0, 100.0 + APPROVAL_TIMEOUT_SECONDS),
                (500.0, 500.0),
            ]:
                with self.subTest(outer_deadline=outer_deadline), patch(
                    "scripts.codex_agent_backend.time.monotonic", return_value=100.0
                ):
                    server._resolve_server_request(
                        {
                            "id": 9,
                            "method": "item/commandExecution/requestApproval",
                            "params": {},
                        },
                        emit=lambda _event: None,
                        deadline=outer_deadline,
                    )

                self.assertEqual(
                    expected_deadline,
                    server._wait_for_approval_decision.call_args.args[1],
                )

    def test_unknown_server_request_gets_json_rpc_error(self) -> None:
        server = CodexAppServer(["codex"], cwd=Path("/tmp"), timeout=1)
        server._send = MagicMock()  # type: ignore[method-assign]

        server._resolve_server_request({"id": "x", "method": "future/request", "params": {}})

        payload = server._send.call_args.args[0]
        self.assertEqual(-32601, payload["error"]["code"])


class AutoReviewTests(unittest.TestCase):
    def denial(self, **overrides):
        params = {
            "threadId": "thread-1", "turnId": "turn-1", "reviewId": "review-1",
            "startedAtMs": 1, "completedAtMs": 2,
            "review": {"status": "denied", "rationale": "private rationale"},
            "action": {"type": "mcpToolCall", "server": "chrome",
                       "toolName": "connect", "connectorId": "private-id"},
        }
        params.update(overrides)
        return {"method": "item/autoApprovalReview/completed", "params": params}

    def server(self, root):
        server = CodexAppServer(["codex"], cwd=root, timeout=5, approval_dir=root)
        server.thread_id, server.turn_id = "thread-1", "turn-1"
        return server

    def test_only_matching_supported_denials_are_retained_once(self):
        server = self.server(Path("/tmp"))
        for override in [{"threadId": "other"}, {"turnId": "old"},
                         {"review": {"status": "approved"}},
                         {"action": {"type": "futureAction"}},
                         {"action": {"type": "command"}}]:
            server._remember_auto_review(self.denial(**override))
        self.assertEqual([], server.auto_review_denials)
        server._remember_auto_review(self.denial())
        server._remember_auto_review(self.denial())
        self.assertEqual(1, len(server.auto_review_denials))
        event = server.auto_review_denials[0]
        self.assertEqual("mcp_tool_call", event["action"]["type"])
        self.assertEqual("connect", event["action"]["tool_name"])
        self.assertEqual("private-id", event["action"]["connector_id"])

    def test_early_denial_buffers_answer_and_caps_offers(self):
        server = self.server(Path("/tmp"))
        mapper = CodexEventMapper()
        emitted = []
        server._dispatch(self.denial(), mapper, emitted.append, time.monotonic() + 5)
        server._dispatch({"method": "item/completed", "params": {"item": {
            "id": "answer", "type": "agentMessage", "phase": "final_answer",
            "text": "blocked answer",
        }}}, mapper, emitted.append, time.monotonic() + 5)
        self.assertEqual([], emitted)
        self.assertEqual("blocked answer", server.held_auto_review_messages[0]["text"])
        for index in range(20):
            server._remember_auto_review(self.denial(reviewId=f"extra-{index}"))
        self.assertEqual(10, len(server.auto_review_denials))

    def test_command_enum_conversion_does_not_rewrite_command_text(self):
        server = self.server(Path("/tmp"))
        server._remember_auto_review(self.denial(action={
            "type": "command", "source": "unifiedExec", "cwd": "/tmp",
            "command": "echo toolName unifiedExec",
        }))
        self.assertEqual({"type": "command", "source": "unified_exec", "cwd": "/tmp",
                          "command": "echo toolName unifiedExec"},
                         server.auto_review_denials[0]["action"])

    def test_approval_is_exact_private_and_bounded(self):
        server = self.server(Path("/tmp"))
        server._remember_auto_review(self.denial())
        exact = server.auto_review_denials[0]
        server._wait_for_approval = MagicMock(return_value=True)
        server._request = MagicMock(return_value={})
        events = []
        deadline = time.monotonic() + 5
        self.assertTrue(server._approve_auto_review_denials(CodexEventMapper(), events.append, deadline))
        args = server._request.call_args.args
        self.assertEqual("thread/approveGuardianDeniedAction", args[0])
        self.assertEqual({"threadId": "thread-1", "event": exact}, args[1])
        self.assertLessEqual(server._wait_for_approval.call_args.args[1], deadline)
        self.assertNotIn("private-id", json.dumps(events))
        self.assertEqual({"action": "Use connected tool: chrome/connect", "reason": "private rationale"},
                         events[0]["review_details"])
        self.assertEqual("approval_expired", events[-1]["type"])
        self.assertFalse(server._approve_auto_review_denials(CodexEventMapper(), events.append, deadline))
        server._request.assert_called_once()

    def test_dismiss_expiry_stop_and_unavailable_api_never_retry(self):
        for mode in ("dismiss", "expired", "stopped", "unsupported"):
            with self.subTest(mode=mode):
                server = self.server(Path("/tmp"))
                server._remember_auto_review(self.denial())
                server._wait_for_approval = MagicMock(return_value=mode == "unsupported")
                server._request = MagicMock(side_effect=CodexAppServerError("unsupported"))
                server.interrupt_sent = mode == "stopped"
                deadline = time.monotonic() + (-1 if mode == "expired" else 5)
                self.assertFalse(server._approve_auto_review_denials(CodexEventMapper(), lambda e: None, deadline))
                if mode == "unsupported":
                    server._request.assert_called_once()
                    self.assertEqual("thread/approveGuardianDeniedAction", server._request.call_args.args[0])
                else:
                    server._request.assert_not_called()

    def test_wire_retry_preserves_thread_and_emits_only_retry_answer(self):
        fake = r'''
import json, sys
turn = 0
approved = False
for line in sys.stdin:
    m = json.loads(line)
    method = m.get("method")
    def send(p): print(json.dumps(p), flush=True)
    if method == "initialize":
        assert m["params"]["capabilities"]["experimentalApi"]
        send({"id":m["id"], "result":{}})
    elif method == "thread/start":
        assert m["params"]["approvalsReviewer"] == "auto_review"
        send({"id":m["id"], "result":{"thread":{"id":"thread-1"}}})
    elif method == "thread/approveGuardianDeniedAction":
        assert m["params"]["threadId"] == "thread-1"
        event = m["params"]["event"]
        assert event["id"] == "review-1"
        assert event["action"]["tool_name"] == "connect"
        if sys.argv[1] == "reject":
            send({"id":m["id"], "error":{"code":-32601, "message":"unsupported"}})
        else:
            approved = True
            send({"id":m["id"], "result":{}})
    elif method == "turn/start":
        turn += 1
        assert m["params"]["threadId"] == "thread-1"
        if turn > 1: assert approved
        tid = "turn-" + str(turn)
        send({"id":m["id"], "result":{"turn":{"id":tid}}})
        if turn == 1:
            send({"method":"item/autoApprovalReview/completed", "params":{
                "threadId":"thread-1", "turnId":tid, "reviewId":"review-1",
                "review":{"status":"denied"}, "action":{
                    "type":"mcpToolCall", "server":"chrome", "toolName":"connect"}}})
        send({"method":"item/completed", "params":{"item":{
            "id":"answer-"+tid, "type":"agentMessage", "phase":"final_answer",
            "text":"Created form" if approved else "Browser denied"}}})
        send({"method":"turn/completed", "params":{"turn":{"id":tid,"status":"completed"}}})
'''
        for approve, reject in ((True, False), (False, False), (True, True)):
            with self.subTest(approve=approve, reject=reject), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                script = root / "server.py"
                script.write_text(fake, encoding="utf-8")
                server = CodexAppServer([sys.executable, "-u", str(script),
                                         "reject" if reject else "accept"], cwd=root,
                                        timeout=5, max_timeout=10, approval_dir=root)
                events = []
                def emit(event):
                    events.append(event)
                    if event["type"] == "approval_request":
                        (root / (event["approval_id"] + ".json")).write_text(json.dumps(
                            {"decision": "approve" if approve else "deny"}), encoding="utf-8")
                self.assertEqual(("completed", ""), server.run("Create form", model=None,
                                 reasoning_effort=None, emit=emit))
                answers = [e["text"] for e in events if e["type"] == "message_complete"]
                self.assertEqual(["Created form" if approve and not reject else "Browser denied"], answers)
                self.assertEqual("turn-2" if approve and not reject else "turn-1", server.turn_id)
                self.assertEqual(1, sum(e["type"] == "turn_complete" for e in events))
                self.assertIsNotNone(server.process.poll())


class ThreadSessionTests(unittest.TestCase):
    """One Slack thread continues one saved Codex thread."""

    FAKE = r"""
import json, sys
mode = sys.argv[1]
log = open(sys.argv[2], "a")
def send(p): print(json.dumps(p), flush=True)
for line in sys.stdin:
    m = json.loads(line)
    method = m.get("method")
    if method:
        log.write(json.dumps(m) + "\n"); log.flush()
    if method == "initialize":
        send({"id": m["id"], "result": {}})
    elif method == "thread/resume":
        if mode == "missing":
            send({"id": m["id"], "error": {"code": -32600, "message": "no rollout found"}})
        else:
            send({"id": m["id"], "result": {"thread": {"id": m["params"]["threadId"]}}})
    elif method == "thread/start":
        send({"id": m["id"], "result": {"thread": {"id": "fresh"}}})
    elif method == "thread/name/set":
        send({"id": m["id"], "result": {}})
    elif method == "config/read":
        send({"id": m["id"], "result": {"config": {}}})
    elif method == "turn/start":
        tid = m["params"]["threadId"]
        send({"id": m["id"], "result": {"turn": {"id": "turn-1"}}})
        answer = {"id": "a", "type": "agentMessage", "phase": "final_answer", "text": "Answer in " + tid}
        send({"method": "item/completed", "params": {"threadId": tid, "item": answer}})
        send({"method": "turn/completed", "params": {"threadId": tid, "turn": {"id": "turn-1", "status": "completed"}}})
"""

    def run_server(self, mode: str, **kwargs) -> tuple[tuple[str, str], list[dict], list[dict]]:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            script, log = root / "server.py", root / "requests.jsonl"
            script.write_text(self.FAKE, encoding="utf-8")
            server = CodexAppServer([sys.executable, "-u", str(script), mode, str(log)], cwd=root,
                                    timeout=5, max_timeout=10, **kwargs)
            events: list[dict] = []
            result = server.run("Next question", model=None, reasoning_effort=None, emit=events.append)
            requests = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return result, events, requests

    def test_resumes_the_slack_threads_saved_conversation(self) -> None:
        result, events, requests = self.run_server("ok", resume_thread_id="saved")
        methods = [request["method"] for request in requests]
        self.assertEqual(("completed", ""), result)
        self.assertIn("thread/resume", methods)
        self.assertNotIn("thread/start", methods)
        self.assertIn({"type": "session", "session_id": "saved"}, events)
        self.assertEqual("Answer in saved", events[-2]["text"])

    def test_resumed_thread_gets_the_continued_prompt_and_standing_instructions(self) -> None:
        _result, _events, requests = self.run_server_with("ok", resume_thread_id="saved")
        resume = next(request for request in requests if request["method"] == "thread/resume")
        turn = next(request for request in requests if request["method"] == "turn/start")
        self.assertEqual("Standing rules", resume["params"]["developerInstructions"])
        self.assertEqual("Only new messages", turn["params"]["input"][0]["text"])

    def test_fresh_thread_gets_the_full_prompt_after_a_failed_resume(self) -> None:
        _result, _events, requests = self.run_server_with("missing", resume_thread_id="gone")
        start = next(request for request in requests if request["method"] == "thread/start")
        turn = next(request for request in requests if request["method"] == "turn/start")
        self.assertEqual("Standing rules", start["params"]["developerInstructions"])
        self.assertEqual("Next question", turn["params"]["input"][0]["text"])

    def run_server_with(self, mode: str, **kwargs):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            script, log = root / "server.py", root / "requests.jsonl"
            script.write_text(self.FAKE, encoding="utf-8")
            server = CodexAppServer([sys.executable, "-u", str(script), mode, str(log)], cwd=root,
                                    timeout=5, max_timeout=10, **kwargs)
            events: list[dict] = []
            result = server.run("Next question", model=None, reasoning_effort=None, emit=events.append,
                                instructions="Standing rules", continued_prompt="Only new messages")
            requests = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return result, events, requests

    def test_missing_conversation_is_replaced_by_a_saved_fresh_one(self) -> None:
        result, events, requests = self.run_server("missing", resume_thread_id="gone")
        start = next(request for request in requests if request["method"] == "thread/start")
        self.assertEqual(("completed", ""), result)
        self.assertFalse(start["params"]["ephemeral"])
        self.assertIn({"type": "session", "session_id": "fresh"}, events)

    def test_new_conversations_are_saved_so_sub_agents_can_load_them(self) -> None:
        _result, events, requests = self.run_server("ok")
        start = next(request for request in requests if request["method"] == "thread/start")
        self.assertFalse(start["params"]["ephemeral"])
        self.assertIn({"type": "session", "session_id": "fresh"}, events)

    def test_reply_summaries_stay_out_of_history_and_never_resume(self) -> None:
        _result, events, requests = self.run_server("ok", resume_thread_id="saved",
                                                    text_only_instructions="Summarize.")
        start = next(request for request in requests if request["method"] == "thread/start")
        self.assertTrue(start["params"]["ephemeral"])
        self.assertNotIn("thread/resume", [request["method"] for request in requests])
        self.assertNotIn("session", [event["type"] for event in events])

    def test_names_a_saved_thread_without_starting_a_turn(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            script, log = root / "server.py", root / "requests.jsonl"
            script.write_text(self.FAKE, encoding="utf-8")
            server = CodexAppServer([sys.executable, "-u", str(script), "ok", str(log)], cwd=root, timeout=5)
            server.set_thread_name("saved", "Closed 12 of 67 issues")
            requests = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertIn({"threadId": "saved", "name": "Closed 12 of 67 issues"},
                      [request["params"] for request in requests if request["method"] == "thread/name/set"])
        self.assertNotIn("turn/start", [request["method"] for request in requests])
        self.assertIsNotNone(server.process is None or server.process.poll())


class SubAgentTests(unittest.TestCase):
    """Codex never wakes the main thread when a background sub-agent finishes."""

    @staticmethod
    def activity(kind: str, agent: str = "child-1", thread: str = "main") -> dict:
        return {"method": "item/completed", "params": {"threadId": thread, "item": {
            "id": f"{kind}-{agent}", "type": "subAgentActivity", "kind": kind, "agentThreadId": agent}}}

    @staticmethod
    def message(item_id: str, text: str, thread: str = "main") -> list[dict]:
        item = {"id": item_id, "type": "agentMessage", "phase": "final_answer"}
        return [
            {"method": "item/started", "params": {"threadId": thread, "item": item}},
            {"method": "item/completed", "params": {"threadId": thread, "item": {**item, "text": text}}},
        ]

    @staticmethod
    def turn_completed(thread: str = "main") -> dict:
        return {"method": "turn/completed", "params": {"threadId": thread,
                                                       "turn": {"id": "t", "status": "completed"}}}

    def mapped(self, mapper: CodexEventMapper, messages: list[dict]) -> list[dict]:
        return [event for message in messages for event in mapper.map(message)]

    def test_reports_the_conversation_size_from_the_latest_call(self) -> None:
        mapper = CodexEventMapper("main")
        events = mapper.map({"method": "thread/tokenUsage/updated", "params": {"threadId": "main", "tokenUsage": {
            "total": {"inputTokens": 90000, "outputTokens": 900, "cachedInputTokens": 0},
            "last": {"inputTokens": 30000, "outputTokens": 500}}}})
        self.assertIn({"type": "context", "tokens": 30500}, events)
        self.assertEqual([], mapper.map({"method": "thread/tokenUsage/updated", "params": {
            "threadId": "child", "tokenUsage": {"total": {}, "last": {"inputTokens": 1, "outputTokens": 1}}}}))

    def test_sub_agent_threads_never_reach_slack(self) -> None:
        mapper = CodexEventMapper("main")
        events = self.mapped(mapper, [*self.message("child-answer", "child text", thread="child-1"),
                                      self.turn_completed(thread="child-1")])
        self.assertEqual([], events)

    def test_turn_ending_with_running_sub_agents_waits_and_demotes_its_note(self) -> None:
        mapper = CodexEventMapper("main")
        events = self.mapped(mapper, [self.activity("started"),
                                      *self.message("note", "I'll report back."),
                                      self.turn_completed()])
        self.assertNotIn("I'll report back.", json.dumps(events))
        self.assertTrue(mapper.awaiting_subagents)
        self.mapped(mapper, [self.activity("completed")])
        self.assertEqual(set(), mapper.subagents)

    def test_interrupted_sub_agent_is_no_longer_running(self) -> None:
        mapper = CodexEventMapper("main")
        self.mapped(mapper, [self.activity("started"), self.activity("interrupted")])
        self.assertEqual(set(), mapper.subagents)

    def test_note_stands_as_answer_when_sub_agents_finish_within_the_turn(self) -> None:
        mapper = CodexEventMapper("main")
        events = self.mapped(mapper, [self.activity("started"), *self.message("note", "All done."),
                                      self.activity("completed"), self.turn_completed()])
        answers = [e["text"] for e in events if e["type"] == "message_complete"]
        self.assertEqual(["All done."], answers)
        self.assertEqual("turn_complete", events[-1]["type"])

    FAKE = r"""
import json, sys
turn = 0
def send(p): print(json.dumps(p), flush=True)
for line in sys.stdin:
    m = json.loads(line)
    method = m.get("method")
    if method == "initialize":
        send({"id": m["id"], "result": {}})
    elif method == "thread/start":
        send({"id": m["id"], "result": {"thread": {"id": "main"}}})
    elif method == "turn/interrupt":
        send({"id": m["id"], "error": {"code": -32600, "message": "no active turn"}})
    elif method == "turn/start":
        turn += 1
        tid = "turn-" + str(turn)
        send({"id": m["id"], "result": {"turn": {"id": tid}}})
        def item(kind, item, thread="main"):
            send({"method": "item/" + kind, "params": {"threadId": thread, "turnId": tid, "item": item}})
        if turn == 1:
            spawn = {"id": "call-1", "type": "subAgentActivity", "kind": "started", "agentThreadId": "child-1"}
            item("started", spawn); item("completed", spawn)
            note = {"id": "note", "type": "agentMessage", "phase": "final_answer", "text": "I'll report back."}
            item("started", note); item("completed", note)
            send({"method": "turn/completed", "params": {"threadId": "main", "turn": {"id": tid, "status": "completed"}}})
            if sys.argv[1] == "hang":
                continue
            child = {"id": "child-answer", "type": "agentMessage", "phase": "final_answer", "text": "child text"}
            item("started", child, "child-1"); item("completed", child, "child-1")
            send({"method": "turn/completed", "params": {"threadId": "child-1", "turn": {"id": "c", "status": "completed"}}})
            done = {"id": "done-1", "type": "subAgentActivity", "kind": "completed", "agentThreadId": "child-1"}
            item("started", done); item("completed", done)
        else:
            assert "sub-agents have finished" in m["params"]["input"][0]["text"]
            answer = {"id": "answer", "type": "agentMessage", "phase": "final_answer", "text": "Closed 3 issues."}
            item("started", answer); item("completed", answer)
            send({"method": "turn/completed", "params": {"threadId": "main", "turn": {"id": tid, "status": "completed"}}})
"""

    def server(self, root: Path, mode: str, **kwargs) -> CodexAppServer:
        script = root / "server.py"
        script.write_text(self.FAKE, encoding="utf-8")
        return CodexAppServer([sys.executable, "-u", str(script), mode], cwd=root,
                              timeout=5, max_timeout=10, **kwargs)

    def test_wire_starts_follow_up_turn_once_sub_agents_finish(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            events: list[dict] = []
            server = self.server(Path(raw), "finish")
            result = server.run("Review issues", model=None, reasoning_effort=None, emit=events.append)
        self.assertEqual(("completed", ""), result)
        answers = [e["text"] for e in events if e["type"] == "message_complete"]
        self.assertEqual(["Closed 3 issues."], answers)
        self.assertNotIn("child text", json.dumps(events))
        self.assertEqual(1, sum(e["type"] == "turn_complete" for e in events))
        self.assertEqual("turn-2", server.turn_id)

    def test_wire_stop_while_waiting_for_sub_agents_ends_promptly(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            control = root / "control"
            server = self.server(root, "hang", control_file=control, run_id="run-1")

            def emit(event: dict) -> None:
                if event.get("type") == "usage" or event.get("type") == "message_start":
                    control.write_text("run-1", encoding="utf-8")

            started = time.monotonic()
            result = server.run("Review issues", model=None, reasoning_effort=None, emit=emit)
        self.assertEqual(("interrupted", ""), result)
        self.assertLess(time.monotonic() - started, 5)


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
        send({"id": pending_thread_start["id"], "result": {"thread": {"id": "thread-1"}, "model": "gpt-resolved"}})
    elif method == "initialize":
        send({"id": message["id"], "result": {"userAgent": "fake"}})
    elif method == "initialized":
        pass
    elif method == "thread/start":
        assert message["params"]["sandbox"] == "workspace-write"
        assert message["params"]["approvalsReviewer"] == "auto_review"
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
            if "large-image-event" in prompt:
                encoded_image_bytes = ((15 * 1024 * 1024 + 2) // 3) * 4
                send({"method": "item/completed", "params": {
                    "threadId": "thread-1", "turnId": "turn-1", "completedAtMs": 2,
                    "item": {
                        "id": "image-large", "type": "imageGeneration", "status": "completed",
                        "result": "data:image/png;base64," + "x" * encoded_image_bytes,
                    }
                }})
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
        self.assertEqual({"type": "run_info", "model": "gpt-resolved", "reasoning_effort": "high"}, events[0])
        self.assertEqual("Hello", next(
            event["text"] for event in events if event["type"] == "message_delta"
        ))
        self.assertEqual("turn_complete", events[-1]["type"])

    def test_accepts_valid_large_image_completion_event(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            events: list[dict[str, object]] = []
            server = CodexAppServer(
                [sys.executable, "-u", str(self.fake_server_path(root))],
                cwd=root,
                # Payload-size coverage must not depend on host throughput.
                timeout=30,
            )

            status, detail = server.run(
                "large-image-event", model=None, reasoning_effort=None, emit=events.append
            )

        self.assertEqual(("completed", ""), (status, detail))
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


class TagThinkingLevelTests(unittest.TestCase):
    FAKE = r'''
import json, sys
record = open(sys.argv[1], "w")
def send(payload):
    print(json.dumps(payload), flush=True)
for raw in sys.stdin:
    m = json.loads(raw)
    method = m.get("method")
    if method == "initialize":
        send({"id": m["id"], "result": {}})
    elif method == "thread/start":
        send({"id": m["id"], "result": {"thread": {"id": "thread-1"}, "model": "gpt-5.5"}})
    elif method == "turn/start":
        record.write(json.dumps(m["params"])); record.close()
        send({"id": m["id"], "result": {"turn": {"id": "turn-1"}}})
        send({"method": "item/completed", "params": {"item": {
            "id": "answer", "type": "agentMessage", "phase": "final_answer", "text": "Done"}}})
        send({"method": "turn/completed", "params": {"turn": {"id": "turn-1", "status": "completed"}}})
'''

    def test_tag_thinking_level_reaches_the_turn_effort(self) -> None:
        from scripts import agent_models, slack_socket_agent

        catalog = [agent_models.ModelOption("gpt-5.5", "GPT-5.5", ("low", "medium", "high", "xhigh"),
                                            default_reasoning_effort="medium", is_default=True)]
        with patch.dict("os.environ", {"OPENTAG_DEFAULT_MODEL": "codex:gpt-5.5", "OPENTAG_DEFAULT_EFFORT": "xhigh"}), \
                patch.object(agent_models, "discover_models", return_value=catalog), \
                patch.object(agent_models, "backend_signed_in", side_effect=lambda name: name == "codex"):
            settings = slack_socket_agent.default_agent_settings(agent_models.discover_tag_models("codex"))
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "server.py").write_text(self.FAKE, encoding="utf-8")
            server = CodexAppServer([sys.executable, "-u", str(root / "server.py"), str(root / "turn.json")],
                                    cwd=root, timeout=5)
            events: list[dict] = []
            self.assertEqual(("completed", ""), server.run(
                "task", model=settings.model, reasoning_effort=settings.reasoning_effort, emit=events.append))
            params = json.loads((root / "turn.json").read_text(encoding="utf-8"))
        self.assertEqual(("gpt-5.5", "xhigh"), (params["model"], params["effort"]))
        self.assertEqual("xhigh", events[0]["reasoning_effort"])


if __name__ == "__main__":
    unittest.main()
