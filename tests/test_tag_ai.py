"""AI connections: detection, models, sign-in, setup's AI step, and `tag settings ai`."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts import agent_models, setup_ui, tag_ai, tag_chatgpt, tag_config

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "protocol/examples"


def done(code: int = 0, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, stdout, "")


def codex_store(*, enabled: bool = False, account: dict | None = None) -> MagicMock:
    store = MagicMock()
    store.enabled.return_value = enabled
    store.status.return_value = {"active_account": account, "accounts": [account] if account else []}
    return store


class FakeProcess:
    """A backend's own login command: prints lines, then exits, unless it is stopped first."""

    def __init__(self, lines: list[str], code: int = 0, *, hold: bool = False):
        self.code, self.terminated = code, False
        self.released = threading.Event()
        if not hold:
            self.released.set()

        def output():
            yield from lines
            self.released.wait(5)

        self.stdout = output()

    def wait(self, timeout=None):
        return -15 if self.terminated else self.code

    def terminate(self):
        self.terminated = True
        self.released.set()

    def kill(self):
        self.terminate()


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "instance"
        (self.home / "config").mkdir(parents=True)
        self.config = self.home / "config/settings.json"
        tag_config.save_config(self.config, {"OPENTAG_BACKEND": "codex"})
        environment = patch.dict(os.environ, {
            "TAG_HOME": str(self.root), "TAG_INSTANCE_HOME": str(self.home),
            "CODEX_HOME": str(self.root / "codex"), "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
            "OPENTAG_CLAUDE_TRANSPORT": "print", "OPENTAG_CODEX_TRANSPORT": "app-server",
        })
        environment.start()
        self.addCleanup(environment.stop)

    def machine(self, *, installed=("codex", "claude"), codex_status=done(0, "Logged in using ChatGPT"),
                claude_status=done(0, json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                                                  "subscriptionType": "max", "email": "maya@example.com"})),
                app_server=done(0), store=None):
        """Patch the computer: which agents exist and what their status commands say.

        ``self.status`` can be changed afterwards, as a sign-in would.
        """
        self.status = {"codex": codex_status, "claude": claude_status}

        def run(command, **_):
            arguments = tuple(command[1:])
            if arguments == ("--version",):
                return done(0, f"{Path(command[0]).name} 1.2.3")
            if arguments[-1:] == ("--help",):
                return app_server
            return self.status["codex" if command[0].endswith("codex") else "claude"]

        patches = [
            patch.object(tag_ai.shutil, "which", side_effect=lambda name: f"/bin/{name}" if name in installed else None),
            patch.object(tag_ai, "_run", side_effect=run),
            patch.object(tag_ai.tag_chatgpt, "Store", return_value=store or codex_store()),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)


class ConnectionTests(Fixture):
    def test_reports_each_state_with_the_action_to_offer(self) -> None:
        cases = [
            ("codex missing", dict(installed=("claude",)), "codex", "not_installed", ["install"], None),
            ("codex chatgpt", {}, "codex", "connected", ["change_account"], "ChatGPT sign-in"),
            ("codex api key", dict(codex_status=done(0, "Logged in using an API key")), "codex", "connected",
             ["change_account"], "API key"),
            ("codex signed out", dict(codex_status=done(1)), "codex", "signed_out", ["sign_in"], None),
            ("codex too old", dict(app_server=done(2)), "codex", "unsupported", ["update"], None),
            ("claude max", {}, "claude", "connected", ["change_account"], "Claude Max · maya@example.com"),
            ("claude signed out", dict(claude_status=done(1, json.dumps({"loggedIn": False}))), "claude",
             "signed_out", ["sign_in"], None),
            ("claude text status", dict(claude_status=done(0, "Signed in")), "claude", "connected",
             ["change_account"], "Signed in"),
        ]
        for name, machine, backend, state, actions, account in cases:
            with self.subTest(name):
                self.machine(**machine)
                result = tag_ai.connection(self.home, backend)
                self.assertEqual((state, actions), (result["state"], result["actions"]))
                self.assertEqual(account, result["account"])

    def test_lapsed_sign_ins_are_expired_not_signed_out(self) -> None:
        (self.root / "codex").mkdir()
        (self.root / "codex/auth.json").write_text("{}", encoding="utf-8")
        (self.root / "claude").mkdir()
        (self.root / "claude/.claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "x"}}), encoding="utf-8")
        self.machine(codex_status=done(1), claude_status=done(1, json.dumps({"loggedIn": False})))
        self.assertEqual(["expired", "expired"], [item["state"] for item in tag_ai.connections(self.home)])
        self.assertEqual(["reconnect"], tag_ai.connection(self.home, "claude")["actions"])

    def test_chatgpt_plan_is_shared_by_all_tags(self) -> None:
        account = {"id": "oaiapp_one", "email": "one@example.test", "signed_in": True, "plan_enabled": True,
                   "usage_paused": False}
        for name, change, state, actions in (
            ("connected", {}, "connected", ["change_account"]),
            ("expired", {"signed_in": False}, "expired", ["reconnect", "change_account"]),
            ("no permission", {"plan_enabled": False}, "expired", ["reconnect", "change_account"]),
            ("limited", {"usage_paused": True}, "limited", ["resume", "change_account"]),
        ):
            with self.subTest(name):
                self.machine(codex_status=done(1), store=codex_store(enabled=True, account={**account, **change}))
                result = tag_ai.connection(self.home, "codex")
                self.assertEqual((state, actions), (result["state"], result["actions"]))
                self.assertEqual(("chatgpt", True), (result["method"], result["shared"]))
                self.assertEqual("ChatGPT plan · one@example.test", result["account"])
                # The plan connection never consults the computer's Codex sign-in.
                self.assertNotIn(("login", "status"), [tuple(c.args[0][1:]) for c in tag_ai._run.call_args_list])

    def test_missing_claude_sdk_needs_an_update(self) -> None:
        self.machine()
        with patch.dict(os.environ, {"OPENTAG_CLAUDE_TRANSPORT": "sdk"}), patch(
            "importlib.util.find_spec", return_value=None
        ):
            result = tag_ai.connection(self.home, "claude")
        self.assertEqual(("unsupported", ["update"]), (result["state"], result["actions"]))

    def test_report_matches_the_protocol_example(self) -> None:
        self.machine(claude_status=done(1, json.dumps({"loggedIn": False})))
        tag_config.update_config(self.config, {"OPENTAG_DEFAULT_MODEL": "claude:claude-opus-5-5"})
        result = tag_ai.report(self.home, tag_config.load_config(self.config), tag_id="maya", running=True)
        promised = json.loads((EXAMPLES / "ai-status.json").read_text(encoding="utf-8"))
        self.assertEqual(set(promised), set(result))
        self.assertEqual(set(promised["connections"][0]), set(result["connections"][0]))
        self.assertEqual(set(promised["default_model"]), set(result["default_model"]))
        self.assertEqual(["codex"], result["usable"])
        # A default whose agent isn't connected can't be used.
        self.assertIs(False, result["default_model"]["available"])

    def test_restricted_backends_are_not_usable(self) -> None:
        self.machine()
        values = {"OPENTAG_BACKEND": "codex", "OPENTAG_BACKENDS": "codex"}
        with patch.dict(os.environ, {"OPENTAG_BACKENDS": "codex"}):
            report = tag_ai.report(self.home, values, tag_id="maya", running=False)
        self.assertEqual(["codex"], report["usable"])
        self.assertEqual([True, False], [item["allowed"] for item in report["connections"]])


class ModelTests(Fixture):
    CODEX = [agent_models.ModelOption("gpt-5.5", "GPT-5.5", ("high",), is_default=True),
             agent_models.ModelOption("gpt-5.5-mini", "GPT-5.5 mini", ("high",))]
    CLAUDE = [agent_models.ModelOption("claude-opus-5-5", "Opus 5.5", ("high",), backend="claude", is_default=True)]

    def catalog(self, values: dict[str, str], ready=("codex", "claude")) -> dict:
        found = {"codex": self.CODEX, "claude": self.CLAUDE}
        with patch.object(tag_ai.agent_models, "discover_models", side_effect=lambda name: found[name]):
            return tag_ai.models(self.home, values, list(ready))

    def test_groups_by_backend_with_each_account_default(self) -> None:
        result = self.catalog({"OPENTAG_BACKEND": "codex"})
        promised = json.loads((EXAMPLES / "ai-models.json").read_text(encoding="utf-8"))
        self.assertEqual(set(promised), set(result))
        self.assertEqual(["codex", "claude"], [group["backend"] for group in result["groups"]])
        self.assertEqual(["codex", "codex:gpt-5.5", "codex:gpt-5.5-mini"],
                         [entry["value"] for entry in result["groups"][0]["models"]])
        # A Tag that never chose a model is offered its default agent's own default.
        self.assertEqual("codex:gpt-5.5", result["suggested"])
        self.assertEqual("Opus 5.5", agent_models.load_model_names(agent_models.model_names_path(self.home))["claude:claude-opus-5-5"])

    def test_chosen_model_that_is_no_longer_offered_is_unavailable(self) -> None:
        result = self.catalog({"OPENTAG_BACKEND": "codex", "OPENTAG_DEFAULT_MODEL": "codex:gpt-5.4"})
        self.assertIs(False, result["default"]["available"])
        self.assertEqual("codex:gpt-5.5", result["suggested"])
        kept = self.catalog({"OPENTAG_BACKEND": "claude", "OPENTAG_DEFAULT_MODEL": "claude"})
        self.assertTrue(kept["default"]["available"])
        self.assertEqual("claude", kept["suggested"])

    def test_only_connected_backends_are_listed(self) -> None:
        result = self.catalog({"OPENTAG_BACKEND": "codex"}, ready=("claude",))
        self.assertEqual(["claude"], [group["backend"] for group in result["groups"]])
        self.assertEqual([{"backend": "codex", "name": "Codex", "reason": "not_connected"}], result["unavailable"])
        self.assertEqual("claude:claude-opus-5-5", result["suggested"])

    def test_saving_a_model_picks_its_backend(self) -> None:
        self.catalog({"OPENTAG_BACKEND": "codex"})  # Remembers the offered models.
        choice = tag_ai.save_default_model(self.home, "claude:claude-opus-5-5", ready=["codex", "claude"])
        saved = tag_config.load_config(self.config)
        self.assertEqual(("claude", "claude:claude-opus-5-5"), (saved["OPENTAG_BACKEND"], saved["OPENTAG_DEFAULT_MODEL"]))
        self.assertEqual(("Opus 5.5", "Claude"), (choice["label"], choice["backend_name"]))

    def test_saving_refuses_unconnected_or_unoffered_models(self) -> None:
        with self.assertRaisesRegex(ValueError, "Connect Claude"):
            tag_ai.save_default_model(self.home, "claude:claude-opus-5-5", ready=["codex"])
        with patch.object(tag_ai.agent_models, "discover_models", return_value=self.CODEX), \
                self.assertRaisesRegex(ValueError, "gpt-9 isn't available"):
            tag_ai.save_default_model(self.home, "codex:gpt-9", ready=["codex"])
        with self.assertRaises(ValueError):
            tag_ai.save_default_model(self.home, "gemini:pro", ready=["codex"])
        self.assertNotIn("OPENTAG_DEFAULT_MODEL", tag_config.load_config(self.config))


class SignInTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.events: list[dict] = []

    def test_browser_sign_in_reports_progress_and_relays_the_url(self) -> None:
        process = FakeProcess(["Opening browser\n", "Or visit https://claude.ai/oauth/authorize?code=x\n", "Done\n"])
        self.machine()
        with patch.object(tag_ai.subprocess, "Popen", return_value=process) as popen:
            result = tag_ai.sign_in(self.home, "claude", emit=self.events.append)
        self.assertEqual(["/bin/claude", "auth", "login"], popen.call_args.args[0])
        self.assertEqual("connected", result["state"])
        self.assertEqual(["browser", "waiting", "waiting", "verifying"], [e["step"] for e in self.events])
        self.assertEqual("https://claude.ai/oauth/authorize?code=x", self.events[2]["url"])

    def test_cancel_stops_the_login_and_changes_nothing(self) -> None:
        process = FakeProcess(["Waiting\n"], hold=True)
        cancelled = threading.Event()
        self.machine(claude_status=done(1, json.dumps({"loggedIn": False})))
        with patch.object(tag_ai.subprocess, "Popen", return_value=process):
            threading.Timer(0.3, cancelled.set).start()
            with self.assertRaises(tag_ai.SignInCancelled):
                tag_ai.sign_in(self.home, "claude", emit=self.events.append, cancelled=cancelled.is_set)
        self.assertTrue(process.terminated)
        self.assertNotIn("verifying", [e["step"] for e in self.events])

    def test_failures_and_timeouts_explain_and_allow_retry(self) -> None:
        self.machine(claude_status=done(1, json.dumps({"loggedIn": False})))
        with patch.object(tag_ai.subprocess, "Popen", return_value=FakeProcess(["Error: browser closed\n"], 1)):
            with self.assertRaisesRegex(tag_ai.SignInError, "Claude sign-in didn't finish: Error: browser closed"):
                tag_ai.sign_in(self.home, "claude", emit=self.events.append)
        with patch.object(tag_ai.subprocess, "Popen", return_value=FakeProcess([], hold=True)):
            with self.assertRaisesRegex(tag_ai.SignInError, "timed out"):
                tag_ai.sign_in(self.home, "claude", emit=self.events.append, timeout=0.3)
        # The login exited cleanly, but the status check still disagrees.
        with patch.object(tag_ai.subprocess, "Popen", return_value=FakeProcess([])):
            with self.assertRaisesRegex(tag_ai.SignInError, "still isn't ready"):
                tag_ai.sign_in(self.home, "claude", emit=self.events.append)
        with self.assertRaisesRegex(tag_ai.SignInError, "Choose codex or claude"):
            tag_ai.sign_in(self.home, "gemini", emit=self.events.append)

    def test_missing_agent_points_to_its_install_guide(self) -> None:
        self.machine(installed=())
        with self.assertRaisesRegex(tag_ai.SignInError, "code.claude.com/docs/en/setup"):
            tag_ai.sign_in(self.home, "claude", emit=self.events.append)

    def test_codex_methods(self) -> None:
        plan = {"id": "oaiapp_one", "email": "one@example.test", "signed_in": True, "plan_enabled": True,
                "usage_paused": False}
        # Using the computer's Codex sign-in while it is signed in needs no browser.
        store = codex_store(enabled=True, account=plan)
        self.machine(store=store)
        with patch.object(tag_ai.subprocess, "Popen") as popen:
            tag_ai.sign_in(self.home, "codex", method="codex", emit=self.events.append)
        popen.assert_not_called()
        store.use_codex.assert_called_once()
        # A ChatGPT account for this Tag uses Tag's own browser sign-in, with progress and cancel.
        store = codex_store(enabled=True, account=plan)
        store.login.side_effect = lambda *_a, progress, **_k: [progress(s) for s in ("browser", "waiting", "verifying")]
        self.machine(store=store)
        tag_ai.sign_in(self.home, "codex", method="chatgpt", emit=self.events.append)
        self.assertTrue(callable(store.login.call_args.kwargs["cancelled"]))
        self.assertIsNone(store.login.call_args.args[0])  # Choosing a ChatGPT account adds a new one.
        tag_ai.sign_in(self.home, "codex", emit=self.events.append)
        self.assertEqual("oaiapp_one", store.login.call_args.args[0])  # Reconnect renews the saved one.
        store.login.side_effect = tag_chatgpt.SignInCancelled("cancelled")
        with self.assertRaises(tag_ai.SignInCancelled):
            tag_ai.sign_in(self.home, "codex", method="chatgpt", emit=self.events.append)
        store.login.side_effect = tag_chatgpt.ChatGPTError("declined")
        with self.assertRaisesRegex(tag_ai.SignInError, "declined"):
            tag_ai.sign_in(self.home, "codex", method="chatgpt", emit=self.events.append)
        with patch.dict(os.environ, {"OPENTAG_CODEX_TRANSPORT": "exec"}), self.assertRaisesRegex(tag_ai.SignInError, "app-server"):
            tag_ai.sign_in(self.home, "codex", method="chatgpt", emit=self.events.append)

    def test_chatgpt_login_can_be_cancelled_while_waiting(self) -> None:
        store = tag_chatgpt.Store(self.home)
        steps: list[str] = []
        with patch.object(tag_chatgpt.webbrowser, "open", return_value=True), \
                self.assertRaises(tag_chatgpt.SignInCancelled):
            store.login(cancelled=lambda: True, progress=steps.append, timeout=5)
        self.assertEqual(["browser", "waiting"], steps)
        self.assertFalse(store.enabled())


class ProtocolClient:
    """Drive setup over JSON lines with raw client messages."""

    def __init__(self, messages: list[dict]):
        self.stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
        self.stdout = io.StringIO()

    def __enter__(self):
        self.patches = [patch.dict(os.environ, {setup_ui.PROTOCOL_ENV: "jsonl"}),
                        patch.object(sys, "stdin", self.stdin), patch.object(sys, "__stdout__", self.stdout),
                        patch.object(sys, "stdout", sys.stdout)]
        for item in self.patches:
            item.start()
        setup_ui.enter_protocol()
        return self

    def __exit__(self, *_):
        for item in reversed(self.patches):
            item.stop()
        setup_ui._unread.clear()

    def events(self) -> list[dict]:
        return [json.loads(line) for line in self.stdout.getvalue().splitlines()]

    def questions(self) -> list[dict]:
        return [event for event in self.events() if event["type"] == "question"]


class SetupStepTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.discover = patch.object(tag_ai.agent_models, "discover_models", side_effect=lambda name: {
            "codex": ModelTests.CODEX, "claude": ModelTests.CLAUDE}[name])
        self.discover.start()
        self.addCleanup(self.discover.stop)

    def signed_out_claude(self) -> None:
        self.machine(claude_status=done(1, json.dumps({"loggedIn": False})))

    def test_with_an_agent_connected_setup_only_asks_for_the_model(self) -> None:
        self.machine()
        with ProtocolClient([{"answer": "claude:claude-opus-5-5"}]) as client:
            values = tag_ai.setup_step(self.home, self.config)
        [question] = client.questions()
        self.assertEqual(("default_model", "choose"), (question["id"], question["kind"]))
        self.assertEqual(["codex", "claude"], [group["backend"] for group in question["groups"]])
        self.assertEqual("codex:gpt-5.5", question["option_ids"][question["default"]])
        # Connections are managed in Settings; setup doesn't offer account changes.
        self.assertFalse(any(option.startswith("change_account") for option in question["option_ids"]))
        self.assertEqual(("claude", "claude:claude-opus-5-5"), (values["OPENTAG_BACKEND"], values["OPENTAG_DEFAULT_MODEL"]))

    def test_model_question_does_not_offer_connection_actions(self) -> None:
        self.signed_out_claude()
        with ProtocolClient([{"answer": "codex:gpt-5.5"}]) as client, patch.object(tag_ai, "sign_in") as sign_in:
            tag_ai.setup_step(self.home, self.config)
        question = client.questions()[0]
        self.assertEqual(["codex"], [g["backend"] for g in question["groups"]])
        self.assertFalse(any(value.startswith(("sign_in:", "reconnect:", "resume:")) for value in question["option_ids"]))
        sign_in.assert_not_called()

    def test_setup_saves_model_and_thinking_for_both_backends(self) -> None:
        self.machine()
        for model in ("codex:gpt-5.5", "claude:claude-opus-5-5"):
            with self.subTest(model=model), ProtocolClient([{"answer": {"value": model, "effort": "high"}}]) as client:
                saved = tag_ai.setup_step(self.home, self.config)
                self.assertTrue(client.questions()[0]["supports_effort"])
                self.assertEqual(model, saved["OPENTAG_DEFAULT_MODEL"])
                self.assertEqual("high", saved["OPENTAG_DEFAULT_EFFORT"])
            with ProtocolClient([{"answer": {"value": model, "effort": "default"}}]):
                saved = tag_ai.setup_step(self.home, self.config)
                self.assertFalse(saved.get("OPENTAG_DEFAULT_EFFORT"))

    def test_setup_rejects_unsupported_thinking_without_saving(self) -> None:
        self.machine()
        before = self.config.read_text()
        for model in ("codex:gpt-5.5", "claude:claude-opus-5-5"):
            with self.subTest(model=model), ProtocolClient([{"answer": {"value": model, "effort": "max"}}]):
                with self.assertRaisesRegex(ValueError, "doesn't offer"):
                    tag_ai.setup_step(self.home, self.config)
                self.assertEqual(before, self.config.read_text())

    def test_cli_setup_offers_thinking_for_both_backends(self) -> None:
        self.machine()
        for model in ("codex:gpt-5.5", "claude:claude-opus-5-5"):
            def choose(_title, _labels, **details):
                return details["option_ids"].index(model if details["qid"] == "default_model" else "high")
            with self.subTest(model=model), patch.object(setup_ui, "protocol_active", return_value=False), \
                    patch.object(setup_ui, "choose", side_effect=choose):
                saved = tag_ai.setup_step(self.home, self.config)
                self.assertEqual(model, saved["OPENTAG_DEFAULT_MODEL"])
                self.assertEqual("high", saved["OPENTAG_DEFAULT_EFFORT"])

    def test_back_restores_combined_model_and_thinking(self) -> None:
        self.machine()
        setup_ui.start_replay([], ("default_model", {"value": "claude:claude-opus-5-5", "effort": "high"}))
        try:
            with ProtocolClient([{"answer": {"value": "claude:claude-opus-5-5", "effort": "high"}}]) as client:
                tag_ai.setup_step(self.home, self.config)
            question = client.questions()[0]
            self.assertEqual("claude:claude-opus-5-5", question["option_ids"][question["default"]])
            self.assertEqual("high", question["default_effort"])
        finally:
            setup_ui.commit()

    def test_with_nothing_connected_setup_points_to_global_settings(self) -> None:
        self.machine(installed=())
        with ProtocolClient([{"answer": "check"}, {"answer": None, "pause": True}]) as client:
            with self.assertRaises(setup_ui.Paused):
                tag_ai.setup_step(self.home, self.config)
        self.assertEqual(["ai_connection", "ai_connection"], [q["id"] for q in client.questions()])
        self.assertEqual(["check"], client.questions()[0]["option_ids"])
        messages = " ".join(event["text"] for event in client.events() if event["type"] == "message")
        self.assertIn("tag settings ai sign-in codex --restart", messages)

    def test_older_clients_answer_by_index_or_label(self) -> None:
        self.machine()
        with ProtocolClient([{"answer": "Claude · Opus 5.5"}]):
            values = tag_ai.setup_step(self.home, self.config)
        self.assertEqual("claude:claude-opus-5-5", values["OPENTAG_DEFAULT_MODEL"])
        with ProtocolClient([{"answer": 1}]) as client:
            values = tag_ai.setup_step(self.home, self.config)
        self.assertEqual(client.questions()[0]["option_ids"][1], values["OPENTAG_DEFAULT_MODEL"])

    def test_stale_setup_sign_in_answers_cannot_change_shared_accounts(self) -> None:
        self.signed_out_claude()
        with ProtocolClient([{"answer": "sign_in:claude"}]), patch.object(tag_ai, "sign_in") as sign_in:
            with self.assertRaisesRegex(RuntimeError, "not offered"):
                tag_ai.setup_step(self.home, self.config)
        sign_in.assert_not_called()

    def test_unavailable_saved_model_is_explained(self) -> None:
        self.machine()
        tag_config.update_config(self.config, {"OPENTAG_DEFAULT_MODEL": "codex:gpt-5.4"})
        with ProtocolClient([{"answer": "codex:gpt-5.5"}]) as client:
            tag_ai.setup_step(self.home, self.config)
        messages = " ".join(event["text"] for event in client.events() if event["type"] == "message")
        self.assertIn("gpt-5.4 isn't available from your connected accounts", messages)


class CommandTests(Fixture):
    def target(self, *, running: bool = False) -> tuple[tag_ai.Target, list[str]]:
        calls: list[str] = []
        state = {"running": running}

        def lifecycle(action: str) -> int:
            calls.append(action)
            state["running"] = action in {"start", "restart"}
            return 0

        return tag_ai.Target(self.home, "maya", lambda: state["running"], lifecycle, "Maya's Tag"), calls

    def run_cli(self, arguments: list[str], target: tag_ai.Target, **options) -> tuple[int, str]:
        with redirect_stdout(io.StringIO()) as output:
            code = tag_ai.cli(arguments, target, **options)
        return code, output.getvalue()

    def test_model_saves_and_restarts_only_when_asked(self) -> None:
        self.machine()
        names = agent_models.model_names_path(self.home)
        agent_models.remember_model_names(ModelTests.CLAUDE, names)
        target, calls = self.target(running=True)
        code, output = self.run_cli(["model", "claude:claude-opus-5-5"], target, json_output=True)
        result = json.loads(output)
        promised = json.loads((EXAMPLES / "ai-model.json").read_text(encoding="utf-8"))
        self.assertEqual(set(promised), set(result))
        self.assertEqual((0, True, False, []), (code, result["restart_required"], result["restarted"], calls))
        code, output = self.run_cli(["model", "claude:claude-opus-5-5"], target, json_output=True, restart=True)
        self.assertEqual((False, True, ["restart"]), (json.loads(output)["restart_required"], json.loads(output)["restarted"], calls))
        stopped, calls = self.target()
        code, output = self.run_cli(["model", "claude:claude-opus-5-5"], stopped)
        self.assertIn("Maya's Tag uses it next time it starts.", output)
        self.assertEqual([], calls)

    def test_sign_in_streams_json_lines_and_restarts_around_a_plan_change(self) -> None:
        plan = {"id": "oaiapp_one", "email": "one@example.test", "signed_in": True, "plan_enabled": True,
                "usage_paused": False}
        self.machine(store=codex_store(enabled=True, account=plan))
        target, calls = self.target(running=True)
        with self.assertRaisesRegex(ValueError, "--restart"):
            self.run_cli(["sign-in", "codex"], target, json_output=True, method="chatgpt")
        with patch.object(tag_ai, "StdinCancel", return_value=lambda: False), patch.object(
            tag_ai, "sign_in", return_value={"state": "connected", "account": "ChatGPT plan"}
        ):
            code, output = self.run_cli(["sign-in", "codex"], target, json_output=True, method="chatgpt", restart=True)
        events = [json.loads(line) for line in output.splitlines()]
        self.assertEqual(0, code)
        self.assertEqual(["stopping", "restarting"], [e["step"] for e in events if e["type"] == "progress"])
        self.assertEqual(("connected", True, False), (events[-1]["status"], events[-1]["restarted"], events[-1]["restart_required"]))
        self.assertEqual(["stop", "start"], calls)

    def test_failed_sign_in_still_starts_the_tag_again(self) -> None:
        self.machine(claude_status=done(1, json.dumps({"loggedIn": False})))
        target, calls = self.target(running=True)
        with patch.object(tag_ai, "StdinCancel", return_value=lambda: False), patch.object(
            tag_ai, "sign_in", side_effect=tag_ai.SignInError("Claude sign-in didn't finish.")
        ):
            code, output = self.run_cli(["sign-in", "claude"], target, json_output=True, restart=True)
        result = json.loads(output.splitlines()[-1])
        self.assertEqual((1, "failed", True, True), (code, result["status"], result["retry"], result["restarted"]))
        self.assertEqual(["stop", "start"], calls)
        # Signing in to the computer's Claude doesn't need the Tag stopped, just a restart later.
        target, calls = self.target(running=True)
        with patch.object(tag_ai, "StdinCancel", return_value=lambda: False), patch.object(
            tag_ai, "sign_in", return_value={"state": "connected", "account": "Claude Max"}
        ):
            code, output = self.run_cli(["sign-in", "claude"], target, json_output=True)
        self.assertTrue(json.loads(output.splitlines()[-1])["restart_required"])
        self.assertEqual([], calls)

    def test_status_and_models_print_json(self) -> None:
        self.machine()
        target, _ = self.target()
        code, output = self.run_cli([], target, json_output=True)
        self.assertEqual((0, "maya"), (code, json.loads(output)["tag"]))
        with patch.object(tag_ai.agent_models, "discover_models", return_value=[]):
            code, output = self.run_cli(["models"], target, json_output=True)
        self.assertEqual(["codex", "claude"], [group["backend"] for group in json.loads(output)["groups"]])
        with self.assertRaisesRegex(ValueError, "tag \\[TAG\\] settings ai"):
            self.run_cli(["unknown"], target)

    def test_stdin_cancel_watches_for_cancel_or_eof(self) -> None:
        for stream in ("", json.dumps({"other": 1}) + "\n", json.dumps({"cancel": True}) + "\n"):
            with self.subTest(stream=stream), patch.object(sys, "stdin", io.StringIO(stream)):
                cancel = tag_ai.StdinCancel()
                self.assertTrue(cancel.event.wait(2))


class ThinkingLevelTests(Fixture):
    """The Tag's default thinking level: saved with the model, checked against what it offers."""

    CODEX = [agent_models.ModelOption("gpt-5.5", "GPT-5.5", ("low", "medium", "high", "xhigh"),
                                      default_reasoning_effort="medium", is_default=True),
             agent_models.ModelOption("gpt-5.5-mini", "GPT-5.5 mini", ("low", "medium"),
                                      default_reasoning_effort="medium")]
    CLAUDE = [agent_models.ModelOption("claude-opus-5-5", "Opus 5.5", ("low", "medium", "high", "max"),
                                       default_reasoning_effort="high", backend="claude", is_default=True),
              agent_models.ModelOption("claude-haiku-5", "Haiku 5", (), backend="claude")]

    def setUp(self) -> None:
        super().setUp()
        self.machine()
        self.discover = patch.object(tag_ai.agent_models, "discover_models", side_effect=lambda name: {
            "codex": self.CODEX, "claude": self.CLAUDE}[name])
        self.discover.start()
        self.addCleanup(self.discover.stop)

    def saved(self) -> dict[str, str]:
        return tag_config.load_config(self.config)

    def run_cli(self, arguments: list[str], **options) -> dict:
        target = tag_ai.Target(self.home, "maya", lambda: False, lambda action: 0, "Maya's Tag")
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, tag_ai.cli(arguments, target, json_output=True, **options))
        return json.loads(output.getvalue())

    def test_models_report_levels_and_cache_them(self) -> None:
        result = tag_ai.models(self.home, {"OPENTAG_BACKEND": "codex"}, ["codex", "claude"])
        entries = {entry["value"]: entry for group in result["groups"] for entry in group["models"]}
        self.assertEqual((["low", "medium", "high", "xhigh"], "medium"),
                         (entries["codex:gpt-5.5"]["efforts"], entries["codex:gpt-5.5"]["default_effort"]))
        # An account default offers its default model's levels; a model may offer none.
        self.assertEqual(entries["claude:claude-opus-5-5"]["efforts"], entries["claude"]["efforts"])
        self.assertEqual(([], None), (entries["claude:claude-haiku-5"]["efforts"],
                                      entries["claude:claude-haiku-5"]["default_effort"]))
        promised = json.loads((EXAMPLES / "ai-models.json").read_text(encoding="utf-8"))
        self.assertEqual(set(promised["groups"][0]["models"][1]), set(entries["codex:gpt-5.5"]))
        cached = agent_models.load_model_efforts(agent_models.model_efforts_path(self.home))
        self.assertEqual({"efforts": ["low", "medium", "high", "xhigh"], "default": "medium"}, cached["codex:gpt-5.5"])
        self.assertEqual({"efforts": [], "default": None}, cached["claude:claude-haiku-5"])
        self.assertEqual(cached["codex:gpt-5.5"], cached["codex"])

    def test_effective_level_with_and_without_a_saved_catalog(self) -> None:
        values = {"OPENTAG_BACKEND": "codex", "OPENTAG_DEFAULT_MODEL": "codex:gpt-5.5"}
        self.assertIsNone(agent_models.effective_effort(self.home, values))
        # Nothing known about the model yet: the Tag's own level is what it asks for.
        self.assertEqual("high", agent_models.effective_effort(self.home, {**values, "OPENTAG_DEFAULT_EFFORT": "high"}))
        self.assertEqual([], agent_models.effort_levels(self.home, "codex:gpt-5.5"))
        tag_ai.models(self.home, values, ["codex", "claude"])
        self.assertEqual("medium", agent_models.effective_effort(self.home, values))
        self.assertEqual("high", agent_models.effective_effort(self.home, {**values, "OPENTAG_DEFAULT_EFFORT": "high"}))
        self.assertEqual("medium", agent_models.effective_effort(self.home, {**values, "OPENTAG_DEFAULT_EFFORT": "max"}))
        self.assertEqual("medium", agent_models.effective_effort(self.home, {"OPENTAG_BACKEND": "codex"}))
        haiku = {"OPENTAG_BACKEND": "claude", "OPENTAG_DEFAULT_MODEL": "claude:claude-haiku-5",
                 "OPENTAG_DEFAULT_EFFORT": "high"}
        self.assertIsNone(agent_models.effective_effort(self.home, haiku))
        self.assertEqual("No thinking levels", tag_ai.thinking_text(self.home, haiku))
        self.assertEqual("Thinking · Extra high", tag_ai.thinking_text(self.home, {**values, "OPENTAG_DEFAULT_EFFORT": "xhigh"}))

    def test_status_reports_the_level(self) -> None:
        tag_config.update_config(self.config, {"OPENTAG_DEFAULT_MODEL": "codex:gpt-5.5", "OPENTAG_DEFAULT_EFFORT": "high"})
        tag_ai.models(self.home, self.saved(), ["codex", "claude"])
        result = tag_ai.report(self.home, self.saved(), tag_id="maya", running=False)
        self.assertEqual(("high", ["low", "medium", "high", "xhigh"], True),
                         (result["default_effort"], result["effort_levels"], result["effort_chosen"]))

    def test_model_and_level_are_saved_together(self) -> None:
        result = self.run_cli(["model", "claude:claude-opus-5-5"], effort="max")
        self.assertEqual("max", result["default_effort"])
        self.assertEqual(("claude:claude-opus-5-5", "max"),
                         (self.saved()["OPENTAG_DEFAULT_MODEL"], self.saved()["OPENTAG_DEFAULT_EFFORT"]))
        promised = json.loads((EXAMPLES / "ai-model.json").read_text(encoding="utf-8"))
        self.assertEqual(set(promised), set(result))

    def test_switching_models_keeps_an_offered_level_and_clears_another(self) -> None:
        self.run_cli(["model", "codex:gpt-5.5"], effort="low")
        result = self.run_cli(["model", "claude:claude-opus-5-5"])
        self.assertEqual(("low", "low"), (self.saved()["OPENTAG_DEFAULT_EFFORT"], result["default_effort"]))
        self.run_cli(["effort", "max"])
        result = self.run_cli(["model", "codex:gpt-5.5-mini"])
        # GPT-5.5 mini has no max, so it uses its own default.
        self.assertEqual(("", "medium"), (self.saved()["OPENTAG_DEFAULT_EFFORT"], result["default_effort"]))
        result = self.run_cli(["model", "codex:gpt-5.5"], effort="default")
        self.assertEqual(("", "medium"), (self.saved()["OPENTAG_DEFAULT_EFFORT"], result["default_effort"]))

    def test_a_level_the_model_does_not_offer_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "GPT-5.5 mini doesn't offer xhigh thinking"):
            self.run_cli(["model", "codex:gpt-5.5-mini"], effort="xhigh")
        with self.assertRaisesRegex(ValueError, "Haiku 5 has no thinking levels"):
            self.run_cli(["model", "claude:claude-haiku-5"], effort="high")
        with self.assertRaisesRegex(ValueError, "Choose minimal"):
            self.run_cli(["model", "codex:gpt-5.5"], effort="huge")
        self.assertNotIn("OPENTAG_DEFAULT_MODEL", self.saved())
        self.run_cli(["model", "codex:gpt-5.5-mini"])
        with self.assertRaisesRegex(ValueError, "doesn't offer xhigh"):
            self.run_cli(["effort", "xhigh"])
        with self.assertRaisesRegex(ValueError, "--effort is only for"):
            self.run_cli(["effort", "low"], effort="low")
        self.assertNotIn("OPENTAG_DEFAULT_EFFORT", self.saved())

    def test_effort_action_saves_and_default_clears(self) -> None:
        self.run_cli(["model", "codex:gpt-5.5"])
        result = self.run_cli(["effort", "high"])
        promised = json.loads((EXAMPLES / "ai-effort.json").read_text(encoding="utf-8"))
        self.assertEqual(set(promised), set(result))
        self.assertEqual(("high", False, False), (result["default_effort"], result["restart_required"], result["restarted"]))
        self.assertEqual("high", self.saved()["OPENTAG_DEFAULT_EFFORT"])
        result = self.run_cli(["effort", "default"])
        self.assertEqual(("medium", ""), (result["default_effort"], self.saved()["OPENTAG_DEFAULT_EFFORT"]))

    def test_menu_offers_the_models_levels(self) -> None:
        self.run_cli(["model", "codex:gpt-5.5"])
        target = tag_ai.Target(self.home, "maya", lambda: False, lambda action: 0, "Maya's Tag")
        prompts: list[tuple[str, list[str]]] = []

        def choose(prompt, options, **_):
            prompts.append((prompt, options))
            if prompt == "AI & models":
                return options.index("Change thinking level") if len(prompts) == 1 else options.index("Back")
            return options.index("High")

        with patch.object(tag_ai.ui, "choose", side_effect=choose), patch.object(tag_ai.ui, "message"), \
                patch.object(tag_ai.ui.display, "header"):
            tag_ai.settings_menu(target)
        levels = next(options for prompt, options in prompts if prompt == "Thinking level")
        self.assertEqual(["Low", "Medium · model default", "High", "Extra high", "Model default", "Cancel"], levels)
        self.assertEqual("high", self.saved()["OPENTAG_DEFAULT_EFFORT"])

    def test_an_older_config_without_the_new_settings_behaves_as_before(self) -> None:
        tag_config.save_config(self.config, {"OPENTAG_BACKEND": "codex", "OPENTAG_DEFAULT_MODEL": "codex:gpt-5.5"})
        values = tag_config.load_config(self.config)
        self.assertNotIn("OPENTAG_DEFAULT_EFFORT", tag_config.config_errors(values))
        result = tag_ai.report(self.home, values, tag_id="maya", running=False)
        self.assertEqual((None, [], False), (result["default_effort"], result["effort_levels"], result["effort_chosen"]))
        # Saving a model writes no level the Tag never chose.
        self.run_cli(["model", "codex:gpt-5.5-mini"])
        self.assertNotIn("OPENTAG_DEFAULT_EFFORT", self.saved())
        self.assertNotIn("OPENTAG_BOT_DESCRIPTION", self.saved())


class SignInExampleTests(unittest.TestCase):
    def test_example_stream_ends_with_a_result(self) -> None:
        events = [json.loads(line) for line in (EXAMPLES / "ai-sign-in.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all(event["step"] in tag_ai.STEP_TEXT for event in events if event["type"] == "progress"))
        self.assertEqual("sign_in", events[-1]["type"])
        self.assertEqual(tag_ai.STEP_TEXT["waiting"], events[1]["text"])


if __name__ == "__main__":
    unittest.main()
