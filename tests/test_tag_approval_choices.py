from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from scripts.codex_agent_backend import CodexAppServer
from scripts.tag_approval_choices import approval_choices, public_approval_choices

COMMAND = "item/commandExecution/requestApproval"


class NativeApprovalChoiceTests(unittest.TestCase):
    def test_explicit_choices_are_authoritative_and_keep_order(self):
        choices = approval_choices(COMMAND, {
            "availableDecisions": ["cancel", "accept"],
            "proposedExecpolicyAmendment": ["git", "status"],
        })
        self.assertEqual(["cancel", "accept"], [c["result"]["decision"] for c in choices])
        self.assertEqual([], approval_choices(COMMAND, {"availableDecisions": []}))
        self.assertEqual([], approval_choices(COMMAND, {"availableDecisions": "accept"}))
        self.assertEqual([], approval_choices(COMMAND, {"availableDecisions": ["futureDecision"]}))

    def test_native_proposals_preserve_exact_payload_and_display_target(self):
        prefix = {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["git", "show", "a b"]}}
        network = {"applyNetworkPolicyAmendment": {
            "network_policy_amendment": {"host": "forms.google.com", "action": "allow"}}}
        choices = approval_choices(COMMAND, {"availableDecisions": [prefix, network]})
        self.assertEqual([prefix, network], [c["result"]["decision"] for c in choices])
        self.assertIn("git show 'a b'", choices[0]["detail"])
        self.assertIn("forms.google.com", choices[1]["detail"])
        self.assertTrue(all(c["persistent"] for c in choices))
        self.assertNotIn("result", json.dumps(public_approval_choices(choices)))

    def test_older_server_proposals_and_deny_host_are_supported(self):
        choices = approval_choices(COMMAND, {
            "proposedExecpolicyAmendment": ["git", "status"],
            "proposedNetworkPolicyAmendments": [{"host": "blocked.example", "action": "deny"}],
        })
        self.assertEqual(6, len(choices))
        self.assertEqual("Always deny this host", choices[-1]["label"])
        self.assertEqual("acceptForSession", choices[1]["result"]["decision"])

    def test_malformed_or_incomplete_persistent_targets_are_not_offered(self):
        for prefix in ([], [None], [""], ["a\nb"], ["a" * 2000]):
            with self.subTest(prefix=prefix):
                choices = approval_choices(COMMAND, {"availableDecisions": [
                    {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": prefix}}]})
                self.assertEqual([], choices)
        choices = approval_choices(COMMAND, {"availableDecisions": [
            {"applyNetworkPolicyAmendment": {"network_policy_amendment": {
                "host": "example.com", "action": "future"}}}]})
        self.assertEqual([], choices)

    def test_file_permissions_and_legacy_scopes(self):
        file_choices = approval_choices("item/fileChange/requestApproval", {})
        self.assertEqual(["accept", "acceptForSession", "decline", "cancel"],
                         [c["result"]["decision"] for c in file_choices])
        permissions = {"network": {"enabled": True}, "fileSystem": {"read": ["/tmp/report"]}}
        choices = approval_choices("item/permissions/requestApproval", {"permissions": permissions})
        self.assertEqual({"permissions": permissions, "scope": "session"}, choices[1]["result"])
        self.assertEqual({}, choices[-1]["result"]["permissions"])
        legacy = approval_choices("execCommandApproval", {"proposedExecpolicyAmendment": ["git"]})
        self.assertEqual("approved_for_session", legacy[1]["result"]["decision"])
        self.assertEqual({"approved_execpolicy_amendment": {"proposed_execpolicy_amendment": ["git"]}},
                         legacy[2]["result"]["decision"])

    def test_wire_response_uses_only_the_selected_offered_payload(self):
        decisions = ["accept", "acceptForSession", "decline", "cancel", {
            "acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["git", "status"]}}, {
            "applyNetworkPolicyAmendment": {"network_policy_amendment": {
                "host": "forms.google.com", "action": "allow"}}}]
        for index, expected in enumerate(decisions):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                server = CodexAppServer(["codex"], cwd=root, timeout=5, approval_dir=root)
                server._send = MagicMock()
                events = []
                def emit(event):
                    events.append(event)
                    if event["type"] == "approval_request":
                        (root / (event["approval_id"] + ".json")).write_text(json.dumps({"choice": str(index)}))
                server._resolve_server_request({"id": 8, "method": COMMAND, "params": {
                    "availableDecisions": decisions}}, emit=emit, deadline=time.monotonic() + 5)
                server._send.assert_called_once_with({"id": 8, "result": {"decision": expected}})
                self.assertEqual("approval_expired", events[-1]["type"])

    def test_forged_and_missing_choices_cannot_grant_access(self):
        for payload in ({"choice": "99"}, {"decision": "approve"}, {}, {"choice": "acceptForSession"}):
            with self.subTest(payload=payload):
                server = CodexAppServer(["codex"], cwd=Path("/tmp"), timeout=5, approval_dir=Path("/tmp"))
                server._wait_for_approval_decision = MagicMock(return_value=payload)
                server._send = MagicMock()
                server._resolve_server_request({"id": 1, "method": COMMAND, "params": {
                    "availableDecisions": ["accept"]}}, emit=lambda e: None, deadline=time.monotonic() + 5)
                server._send.assert_called_once_with({"id": 1, "result": {"decision": "decline"}})


class AutoReviewDetailTests(unittest.TestCase):
    def details(self, action, reason="Connection can access other signed-in Chrome tabs."):
        from scripts.tag_approval_choices import auto_review_details
        return auto_review_details({"action": action, "rationale": reason})

    def test_browser_action_and_actual_rationale(self):
        details = self.details({"type": "mcp_tool_call", "server": "chrome", "tool_name": "connect",
                                "tool_title": "Connect browser", "connector_id": "hidden-id"})
        self.assertEqual("Connect browser\nTool: chrome/connect", details["action"])
        self.assertEqual("Connection can access other signed-in Chrome tabs.", details["reason"])
        self.assertNotIn("hidden-id", json.dumps(details))

    def test_all_supported_actions_have_concrete_context(self):
        for action, expected in [
            ({"type": "command", "command": "python create_form.py"}, "python create_form.py"),
            ({"type": "execve", "program": "curl", "argv": ["curl", "https://example.com/form"]}, "example.com/form"),
            ({"type": "write_stdin", "process_id": "42", "stdin": "sensitive-input"}, "process 42"),
            ({"type": "apply_patch", "files": ["/tmp/survey.md"]}, "/tmp/survey.md"),
            ({"type": "network_access", "host": "forms.google.com", "port": 443, "protocol": "https"}, "forms.google.com:443"),
        ]:
            with self.subTest(action=action):
                details = self.details(action)
                self.assertIn(expected, details["action"])
                self.assertNotIn("sensitive-input", json.dumps(details))

    def test_secrets_are_redacted_in_action_and_reason(self):
        text = ('curl https://user:pass@example.com/form?code=private-query#fragment '
                '--password hiddenpass -H "Authorization: Bearer opaque-value" '
                '-H "Cookie: session=private-cookie" token=private-token xoxb-secret-slack')
        details = self.details({"type": "command", "command": text}, text)
        rendered = json.dumps(details)
        for secret in ("user:pass", "private-query", "hiddenpass", "opaque-value", "private-cookie",
                       "private-token", "xoxb-secret-slack"):
            self.assertNotIn(secret, rendered)
        self.assertIn("example.com/form", rendered)

    def test_private_keys_basic_auth_and_cookie_headers_are_redacted(self):
        text = ('curl -H "Cookie: session=cookie-one; other=cookie-two" '
                '-H "Authorization: Basic dXNlcjpwYXNz" '
                '-----BEGIN ' + 'PRIVATE KEY-----\nkey-material\n-----END ' + 'PRIVATE KEY-----')
        details = self.details({"type": "command", "command": text}, text)
        for secret in ("cookie-one", "cookie-two", "dXNlcjpwYXNz", "key-material"):
            self.assertNotIn(secret, json.dumps(details))

    def test_missing_malformed_and_long_details_are_honest_and_bounded(self):
        details = self.details(None, None)
        self.assertIn("did not provide action", details["action"])
        self.assertIn("did not provide a reason", details["reason"])
        details = self.details({"type": "command", "command": "x" * 2000}, "r" * 2000)
        self.assertLessEqual(len(details["action"]), 900)
        self.assertIn("truncated", details["reason"])
        details = self.details({"type": "command", "command": "x" * 9000})
        self.assertIn("oversized", details["action"])
