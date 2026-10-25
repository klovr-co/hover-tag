from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.codex_agent_backend import CodexAppServer
from scripts.tag_approval_choices import approval_choices, public_approval_choices

COMMAND = "item/commandExecution/requestApproval"


def own_folder(root: Path):
    """Run as a Tag whose own default folder is `root`, where Tag keeps its Codex rules."""
    return patch.dict(os.environ, {"TAG_HOME": str(root / "install"), "TAG_INSTANCE_HOME": str(root / ".tag")}, clear=False)


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

    def test_saved_rules_go_to_the_tag_not_codexs_global_file(self):
        prefix = {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["git", "show", "a b"]}}
        network = {"applyNetworkPolicyAmendment": {
            "network_policy_amendment": {"host": "forms.google.com", "action": "allow"}}}
        context = {"host": "forms.google.com", "protocol": "https"}
        choices = approval_choices(COMMAND, {"availableDecisions": ["accept", "acceptForSession", prefix, network],
                                             "networkApprovalContext": context})
        saved = [c for c in choices if c["persistent"]]
        # Codex gets a plain approval; the rule is Tag's to save in the Tag's own file.
        self.assertEqual(["acceptForSession", "acceptForSession"], [c["result"]["decision"] for c in saved])
        self.assertEqual([{"kind": "command", "words": ["git", "show", "a b"], "decision": "allow"},
                          {"kind": "host", "host": "forms.google.com", "protocol": "https", "decision": "allow"}],
                         [c["save"] for c in saved])
        self.assertIn("this Tag: git show 'a b'", saved[0]["detail"])
        self.assertIn("forms.google.com", saved[1]["detail"])
        public = json.dumps(public_approval_choices(choices))
        self.assertNotIn("result", public)
        self.assertNotIn("save", public)
        # Without a plain answer to send, or the host's protocol, there is nothing Tag can save.
        self.assertEqual([], approval_choices(COMMAND, {"availableDecisions": [prefix]}))
        self.assertEqual(["Allow once"], [c["label"] for c in approval_choices(
            COMMAND, {"availableDecisions": ["accept", network]})])

    def test_main_row_is_allow_once_first_saved_rule_and_deny(self):
        choices = approval_choices("item/commandExecution/requestApproval", {"availableDecisions": [
            "accept", "acceptForSession", {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["git", "push"]}},
            "decline", "cancel"]})
        self.assertEqual(["Allow once", "Always allow this prefix", "Deny"],
                         [c["label"] for c in choices if c["primary"]])
        plain = approval_choices("item/commandExecution/requestApproval", {
            "availableDecisions": ["accept", "acceptForSession", "decline", "cancel"]})
        self.assertEqual(["Allow once", "Allow for this task", "Deny"],
                         [c["label"] for c in plain if c["primary"]])

    def test_older_server_proposals_and_deny_host_are_supported(self):
        choices = approval_choices(COMMAND, {
            "proposedExecpolicyAmendment": ["git", "status"],
            "proposedNetworkPolicyAmendments": [{"host": "blocked.example", "action": "deny"}],
            "networkApprovalContext": {"host": "blocked.example", "protocol": "http"},
        })
        self.assertEqual(6, len(choices))
        self.assertEqual("Always deny this host", choices[-1]["label"])
        self.assertEqual(("decline", "block"), (choices[-1]["result"]["decision"], choices[-1]["save"]["decision"]))
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
        self.assertEqual(("approved_for_session", ["git"]), (legacy[2]["result"]["decision"], legacy[2]["save"]["words"]))

    def test_wire_response_uses_only_the_selected_offered_payload(self):
        decisions = ["accept", "acceptForSession", "decline", "cancel", {
            "acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["git", "status"]}}, {
            "applyNetworkPolicyAmendment": {"network_policy_amendment": {
                "host": "forms.google.com", "action": "allow"}}}]
        sent = ["accept", "acceptForSession", "decline", "cancel", "acceptForSession", "acceptForSession"]
        saved = ["", "", "", "", 'prefix_rule(pattern=["git", "status"], decision="allow")\n',
                 'network_rule(host="forms.google.com", protocol="https", decision="allow")\n']
        for index, expected in enumerate(sent):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as raw, own_folder(Path(raw)):
                root = Path(raw)
                server = CodexAppServer(["codex"], cwd=root, timeout=5, approval_dir=root)
                server._send = MagicMock()
                events = []
                def emit(event):
                    events.append(event)
                    if event["type"] == "approval_request":
                        (root / (event["approval_id"] + ".json")).write_text(json.dumps({"choice": str(index)}), encoding="utf-8")
                server._resolve_server_request({"id": 8, "method": COMMAND, "params": {
                    "availableDecisions": decisions, "networkApprovalContext": {"host": "forms.google.com", "protocol": "https"}}},
                    emit=emit, deadline=time.monotonic() + 5)
                server._send.assert_called_once_with({"id": 8, "result": {"decision": expected}})
                rules = root / ".codex/rules/tag.rules"
                self.assertEqual(saved[index], rules.read_text(encoding="utf-8") if rules.exists() else "")
                self.assertEqual("approval_expired", events[-1]["type"])

    def run_choice(self, root: Path, params: dict, label: str) -> tuple[dict, list[dict], list[dict]]:
        """Answer one approval request in Slack by the choice's label; returns what Codex got."""
        server = getattr(self, "server", None) or CodexAppServer(["codex"], cwd=root, timeout=5, approval_dir=root)
        self.server = server
        server._send = MagicMock()
        events: list[dict] = []
        offered: list[dict] = []

        def emit(event):
            events.append(event)
            if event["type"] == "approval_request":
                offered.extend(event["choices"])
                picked = next(c["id"] for c in event["choices"] if c["label"] == label)
                (root / (event["approval_id"] + ".json")).write_text(json.dumps({"choice": picked}), encoding="utf-8")

        server._resolve_server_request({"id": 4, "method": COMMAND, "params": params}, emit=emit, deadline=time.monotonic() + 5)
        return server._send.call_args.args[0]["result"], events, offered

    def test_a_saved_host_answers_the_same_host_and_protocol_only(self):
        host = {"applyNetworkPolicyAmendment": {"network_policy_amendment": {"host": "api.example.com", "action": "allow"}}}
        params = {"availableDecisions": ["accept", "acceptForSession", "decline", host],
                  "networkApprovalContext": {"host": "api.example.com", "protocol": "https"}}
        with tempfile.TemporaryDirectory() as raw, own_folder(Path(raw)):
            root = Path(raw)
            self.server = None
            result, _, _ = self.run_choice(root, params, "Always allow this host")
            self.assertEqual({"decision": "acceptForSession"}, result)
            self.assertIn('network_rule(host="api.example.com", protocol="https", decision="allow")',
                          (root / ".codex/rules/tag.rules").read_text(encoding="utf-8"))
            again, events, _ = self.run_choice(root, params, "Deny")
            self.assertEqual(({"decision": "acceptForSession"}, []), (again, events))  # Not asked again.
            plain_http = dict(params, networkApprovalContext={"host": "api.example.com", "protocol": "http"})
            asked, events, _ = self.run_choice(root, plain_http, "Deny")
            self.assertEqual({"decision": "decline"}, asked)
            self.assertEqual("approval_request", events[0]["type"])

    def test_a_rule_that_cant_be_saved_allows_once_and_says_so(self):
        prefix = {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["git", "status"]}}
        with tempfile.TemporaryDirectory() as raw, own_folder(Path(raw)):
            root = Path(raw)
            (root / ".codex/rules").mkdir(parents=True)
            (root / ".codex/rules/tag.rules").write_text("prefix_rule(pattern=[\"git\", \"status\"]); prefix_rule(pattern=[\"rm\"], decision=\"forbidden\")\n", encoding="utf-8")
            self.server = None
            result, events, _ = self.run_choice(root, {"availableDecisions": ["accept", "acceptForSession", "decline", prefix]},
                                                "Always allow this prefix")
            self.assertEqual({"decision": "accept"}, result)
            self.assertTrue(any(e["type"] == "status" and "Couldn't save" in e["text"] for e in events))
            self.assertIn('"rm"', (root / ".codex/rules/tag.rules").read_text(encoding="utf-8"))  # The block is kept.

    def test_a_tag_working_in_your_folder_offers_no_saved_codex_rules(self):
        prefix = {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["git", "status"]}}
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.server = None
            result, _, offered = self.run_choice(root, {"availableDecisions": ["accept", "acceptForSession", "decline", prefix]}, "Allow once")
            self.assertEqual({"decision": "accept"}, result)
            self.assertNotIn("Always allow this prefix", [c["label"] for c in offered])
            self.assertEqual([str(i) for i in range(len(offered))], [c["id"] for c in offered])
            self.assertFalse((root / ".codex").exists())

    def test_a_prefix_saved_in_this_run_answers_plain_matching_commands_only(self):
        with tempfile.TemporaryDirectory() as raw, own_folder(Path(raw)):
            root = Path(raw)
            server = CodexAppServer(["codex"], cwd=root, timeout=5, approval_dir=root)
            server._send = MagicMock()
            asked = []
            decisions = ["accept", "acceptForSession", "decline",
                         {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["git", "status"]}}]

            def emit(event):
                if event["type"] == "approval_request":
                    asked.append(event)
                    (root / (event["approval_id"] + ".json")).write_text(json.dumps({"choice": "2"}), encoding="utf-8")

            def request(command):
                server._send.reset_mock()
                server._resolve_server_request({"id": 3, "method": COMMAND, "params": {
                    "command": command, "availableDecisions": decisions}}, emit=emit, deadline=time.monotonic() + 5)
                return server._send.call_args.args[0]["result"]["decision"]

            server._save_rule({"kind": "command", "words": ["git", "status"], "decision": "allow"})
            self.assertEqual("accept", request("git status --short"))
            self.assertEqual([], asked)
            for unsafe in ("git status && rm -rf ~", "git status; curl x", "git statusx", "git log"):
                with self.subTest(command=unsafe):
                    self.assertEqual("decline", request(unsafe))  # Asked in Slack; this test answers Deny.
            self.assertEqual(4, len(asked))

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
