from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts import opentag_setup, setup_ui


class ProtocolHarness:
    """Run setup prompts as a graphical client would see them."""

    def __init__(self, answers: list[object]):
        self.stdin = StringIO("".join(json.dumps({"answer": answer}) + "\n" for answer in answers))
        self.stdout = StringIO()

    def __enter__(self):
        self.patches = [
            patch.dict(os.environ, {setup_ui.PROTOCOL_ENV: "jsonl"}),
            patch.object(sys, "stdin", self.stdin),
            patch.object(sys, "__stdout__", self.stdout),
            patch.object(sys, "stdout", sys.stdout),
        ]
        for item in self.patches:
            item.start()
        setup_ui.enter_protocol()
        return self

    def __exit__(self, *_):
        for item in reversed(self.patches):
            item.stop()

    def events(self) -> list[dict]:
        return [json.loads(line) for line in self.stdout.getvalue().splitlines()]


class SetupProtocolTests(unittest.TestCase):
    def test_choose_accepts_label_or_index_and_reports_options(self):
        with ProtocolHarness(["Use an existing app", 0]) as client:
            self.assertEqual(setup_ui.choose("Slack app", ["Create a new Tag app", "Use an existing app", "Save and exit"]), 1)
            self.assertEqual(setup_ui.choose("Again", ["A", "B"], default=1), 0)
        question = client.events()[0]
        self.assertEqual(question["type"], "question")
        self.assertEqual(question["kind"], "choose")
        self.assertEqual(question["options"], ["Create a new Tag app", "Use an existing app", setup_ui.SAVE_AND_EXIT])

    def test_unknown_owner_lists_people_and_returns_member_id(self):
        person = {"id": "U222", "name": "Jamie", "username": "jamie", "image_url": "https://example.com/avatar.png"}
        with ProtocolHarness(["U222"]) as client, patch.object(
            opentag_setup.shutil, "which", return_value=None
        ), patch.object(opentag_setup, "slack_people", return_value=[person]) as directory:
            self.assertEqual(opentag_setup.choose_allowed_users("T123", token="private-token"), "U222")
        directory.assert_called_once_with("private-token", "T123")
        questions = [event for event in client.events() if event["type"] == "question"]
        self.assertEqual([q["kind"] for q in questions], ["people"])
        self.assertEqual(questions[0]["prompt"], "Which one is you?")
        self.assertEqual(questions[0]["people"], [person])
        self.assertNotIn("private-token", client.stdout.getvalue())

    def test_people_picker_rejects_unoffered_ids_and_accepts_manual_or_pause(self):
        people = [{"id": "U222", "name": "Jamie", "username": "jamie", "image_url": ""}]
        for answer in ("UOTHER", 0, None, True):
            with self.subTest(answer=answer), ProtocolHarness([answer]):
                with self.assertRaises(RuntimeError):
                    opentag_setup.choose_slack_person(people)
        with ProtocolHarness(["manual"]):
            self.assertIsNone(opentag_setup.choose_slack_person(people))
        with ProtocolHarness([]):
            with self.assertRaises(setup_ui.Paused):
                opentag_setup.choose_slack_person(people)

    def test_people_directory_failure_and_empty_list_keep_manual_fallback(self):
        for failure in ([], opentag_setup.slack_channels.SlackChannelError("offline"),
                        opentag_setup.slack_permissions.MissingScope("users.list", "users:read")):
            with self.subTest(failure=failure), ProtocolHarness(["U222"]) as client, patch.object(
                opentag_setup.shutil, "which", return_value=None
            ), patch.object(opentag_setup, "slack_people", side_effect=[failure]):
                self.assertEqual(opentag_setup.choose_allowed_users("T123", token="private-token"), "U222")
            self.assertEqual([e["kind"] for e in client.events() if e["type"] == "question"], ["text"])

    def test_signed_in_owner_asks_nothing(self):
        listing = subprocess.CompletedProcess([], 0, "Team (Team ID: T123)\nUser ID: U111\n", "")
        with ProtocolHarness([]) as client, patch.object(opentag_setup.shutil, "which", return_value="/bin/slack"), patch.object(
            opentag_setup.subprocess, "run", return_value=listing
        ), patch.object(opentag_setup, "slack_people") as directory:
            self.assertEqual(opentag_setup.choose_allowed_users("T123", token="private-token"), "U111")
        directory.assert_not_called()
        self.assertEqual([e for e in client.events() if e["type"] == "question"], [])

    def test_save_and_exit_and_closed_input_pause_setup(self):
        with ProtocolHarness([setup_ui.SAVE_AND_EXIT]):
            with self.assertRaises(setup_ui.Paused):
                setup_ui.choose("Slack app", ["Create", "Save and exit"])
            with self.assertRaises(setup_ui.Paused):
                setup_ui.choose("Slack app", ["Create", "Save and exit"])

    def test_unoffered_option_is_rejected(self):
        with ProtocolHarness(["Delete everything"]):
            with self.assertRaises(RuntimeError):
                setup_ui.choose("Slack app", ["Create", "Save and exit"])

    def test_checklist_returns_selected_indices(self):
        with ProtocolHarness([["#general", 2]]) as client:
            self.assertEqual(setup_ui.checklist(["#general", "#random", "#launch"], {1}), {0, 2})
        self.assertEqual(client.events()[0]["selected"], [1])

    def test_secret_questions_never_echo_a_default(self):
        with ProtocolHarness(["xoxb-1"]) as client:
            self.assertEqual(opentag_setup.read_secret("Bot token (xoxb-…)"), "xoxb-1")
        question = client.events()[0]
        self.assertEqual(question["kind"], "secret")
        self.assertNotIn("default", question)

    def test_text_and_confirm_use_the_client(self):
        with ProtocolHarness(["", True]):
            self.assertEqual(opentag_setup.ask("Workspace alias", "acme"), "acme")
            self.assertTrue(opentag_setup.confirm("Switch?", default=False))

    def test_prose_becomes_message_events_without_color(self):
        with ProtocolHarness([]) as client:
            setup_ui.message("✓ App linked")
            print("\x1b[1mBold\x1b[0m")
        self.assertEqual(client.events(), [
            {"type": "message", "text": "✓ App linked"},
            {"type": "message", "text": "Bold"},
        ])

    def test_slack_sign_in_relays_ticket_line_and_never_echoes_the_code(self):
        started = subprocess.CompletedProcess([], 0, "Run this in Slack:\n/slackauthticket TICKET123\n", "")
        finished = subprocess.CompletedProcess([], 0, b"secret output", b"")
        with ProtocolHarness(["CODE9876"]) as client, patch.object(
            opentag_setup.shutil, "which", return_value="/bin/slack"
        ), patch.object(opentag_setup.subprocess, "run", side_effect=[started, finished]) as run:
            self.assertTrue(opentag_setup.slack_login_with_client())
        question = client.events()[0]
        self.assertEqual(question["kind"], "slack_login")
        self.assertEqual(question["sign_in_line"], "/slackauthticket TICKET123")
        self.assertEqual(run.call_args_list[1].args[0][:6],
                         ["/bin/slack", "auth", "login", "--ticket", "TICKET123", "--challenge"])
        self.assertNotIn("CODE9876", client.stdout.getvalue())
        self.assertNotIn("secret output", client.stdout.getvalue())

    def test_slack_sign_in_gives_up_after_repeated_rejections(self):
        started = subprocess.CompletedProcess([], 0, "/slackauthticket T1\n", "")
        rejected = subprocess.CompletedProcess([], 1, b"", b"")
        with ProtocolHarness(["CODE1", "CODE2", "CODE3"]), patch.object(
            opentag_setup.shutil, "which", return_value="/bin/slack"
        ), patch.object(opentag_setup.subprocess, "run", side_effect=[started, rejected, rejected, rejected]):
            self.assertFalse(opentag_setup.slack_login_with_client())

    @patch.object(opentag_setup.tag_dependencies, "ensure_slack", return_value=Path("/bin/slack"))
    def test_workspace_picker_signs_in_through_the_client(self, _ensure_slack):
        listings = [subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess([], 0, "Example Team (Team ID: T123)\n", "")]
        with ProtocolHarness(["Connect Slack", "Example Team"]), patch.object(
            opentag_setup.shutil, "which", return_value="/bin/slack"
        ), patch.object(opentag_setup.subprocess, "run", side_effect=listings), patch.object(
            opentag_setup, "slack_login_with_client", return_value=True
        ) as login, patch.object(opentag_setup, "run_slack_cli") as terminal_login:
            self.assertEqual(opentag_setup.connect_slack_workspace()[:2], ("T123", "Example Team"))
        login.assert_called_once()
        terminal_login.assert_not_called()

    def test_interactive_slack_cli_output_stays_off_the_protocol_stream(self):
        with ProtocolHarness([]), patch.object(opentag_setup.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0)
            opentag_setup.run_slack_cli(["app", "install"], interactive=True)
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(run.call_args.kwargs["stdout"], sys.stderr)


class QuestionIdTests(unittest.TestCase):
    SETUP_MODULES = ("opentag_setup", "slack_app_create", "slack_channels", "slack_permissions")

    def test_questions_carry_explicit_or_derived_ids(self):
        with ProtocolHarness([0, True, "Maya"]) as client:
            setup_ui.choose("Slack history window", ["Last 30 days"], qid="history_days")
            setup_ui.confirm("Switch now?", False)
            setup_ui.text("Assistant name", "Tag", qid="assistant_name")
        ids = [event["id"] for event in client.events() if event["type"] == "question"]
        self.assertEqual(ids, ["history_days", "switch_now", "assistant_name"])

    def test_every_setup_question_names_a_stable_id(self):
        # Clients match answers by id, so a prompt without one would silently
        # change its id whenever its wording changes.
        import ast
        prompts = {"choose", "checklist", "text", "confirm", "ask_client", "ask", "ask_validated",
                   "ask_secret", "read_secret", "absolute_directory"}
        wrappers = {"ask", "ask_required", "ask_secret", "read_secret", "confirm", "ask_validated",
                    "absolute_directory", "choose_slack_person"}
        missing = []
        for module in self.SETUP_MODULES:
            path = Path(__file__).resolve().parents[1] / "scripts" / f"{module}.py"
            tree = ast.parse(path.read_text())
            for function in ast.walk(tree):
                if not isinstance(function, ast.FunctionDef) or function.name in wrappers:
                    continue
                for node in ast.walk(function):
                    if not isinstance(node, ast.Call):
                        continue
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                    if isinstance(node.func, ast.Attribute) and getattr(node.func.value, "id", "") not in {"ui"}:
                        continue
                    if name in prompts and name != "checklist" and not any(k.arg == "qid" for k in node.keywords):
                        missing.append(f"{module}.py:{node.lineno}")
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
