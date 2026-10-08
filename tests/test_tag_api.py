"""`tag [TAG] settings ai api`: a Tag's own API connection, the contract Tag.app uses."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import tag_ai, tag_api, tag_config

KEY = "sk-secret-0123456789"


class ApiFixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "instance"
        (self.home / "config").mkdir(parents=True)
        self.config = self.home / "config/settings.json"
        tag_config.save_config(self.config, {"OPENTAG_BACKEND": "codex", "OPENTAG_DEFAULT_MODEL": "codex:gpt-5.5",
                                             "OPENTAG_SLACK_STREAMING": "1"})
        environment = patch.dict(os.environ, {"TAG_INSTANCE_HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("OPENTAG_ENV_FILE", None)
        self.running = False
        self.calls: list[str] = []
        self.failing: set[str] = set()

    def lifecycle(self, action: str) -> int:
        self.calls.append(action)
        if action in self.failing:
            self.failing.discard(action)
            return 1
        self.running = action != "stop"
        return 0

    def run_cli(self, *arguments: str, key: str = KEY, restart: bool = True, **options) -> tuple[int, list[dict]]:
        target = tag_ai.Target(self.home, "maya", lambda: self.running, self.lifecycle, "Maya's Tag")
        api = {"backend": None, "kind": None, "base_url": None, "models": None, "api_version": None, **options}
        out = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps({"api_key": key}) + "\n")), redirect_stdout(out):
            code = tag_ai.cli(["api", *arguments], target, json_output=True, restart=restart, api=api)
        self.output = out.getvalue()
        return code, [json.loads(line) for line in self.output.splitlines() if line.strip()]

    def saved(self) -> dict[str, str]:
        return tag_config.read_config(self.config)


class SetTests(ApiFixture):
    def test_claude_api_stops_saves_and_restarts_without_echoing_the_key(self) -> None:
        tag_config.update_config(self.config, {"OPENTAG_DEFAULT_MODEL": "claude"})
        self.running = True
        code, events = self.run_cli("set", backend="claude", kind="anthropic",
                                    base_url="https://gateway.example.com", models="claude-sonnet-5-5, claude-haiku-4-5")
        self.assertEqual(code, 0)
        self.assertEqual([e["step"] for e in events if e["type"] == "progress"], ["stopping", "saving", "restarting"])
        result = events[-1]
        self.assertEqual((result["status"], result["restarted"]), ("saved", True))
        self.assertEqual(result["api"]["host"], "gateway.example.com")
        self.assertEqual(result["api"]["models"], ["claude-sonnet-5-5", "claude-haiku-4-5"])
        self.assertTrue(result["api"]["key_set"])
        self.assertNotIn(KEY, self.output)
        self.assertEqual(self.calls, ["stop", "start"])
        saved = self.saved()
        self.assertEqual(saved["OPENTAG_CLAUDE_AUTH"], "api")
        self.assertEqual(saved["OPENTAG_CLAUDE_API_KEY"], KEY)
        self.assertEqual(saved["OPENTAG_DEFAULT_MODEL"], "claude:claude-sonnet-5-5")
        self.assertEqual(saved["OPENTAG_BACKEND"], "claude")
        self.assertTrue(result["in_use"])
        self.assertEqual(saved["OPENTAG_SLACK_STREAMING"], "1")
        # Tag.app reads protocol/examples/ai-api-set.jsonl; real output must match it.
        example = [json.loads(line) for line in
                   (Path(__file__).resolve().parents[1] / "protocol/examples/ai-api-set.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events, example)

    def test_a_tag_on_the_other_agent_keeps_its_model(self) -> None:
        code, events = self.run_cli("set", backend="claude", kind="anthropic", models="claude-sonnet-5-5")
        self.assertEqual((code, events[-1]["in_use"]), (0, False))
        saved = self.saved()
        self.assertEqual((saved["OPENTAG_DEFAULT_MODEL"], saved["OPENTAG_BACKEND"]), ("codex:gpt-5.5", "codex"))
        self.assertEqual(saved["OPENTAG_CLAUDE_AUTH"], "api")

    def test_azure_needs_codex_a_url_and_keeps_its_api_version(self) -> None:
        code, events = self.run_cli("set", backend="claude", kind="azure", models="x")
        self.assertEqual((code, events[-1]["status"]), (1, "failed"))
        self.assertIn("Codex only", events[-1]["error"])
        code, events = self.run_cli("set", backend="codex", kind="azure", models="my-deployment")
        self.assertIn("--base-url", events[-1]["error"])
        code, events = self.run_cli("set", backend="codex", kind="azure", models="my-deployment",
                                    base_url="https://acme.openai.azure.com/openai", api_version="2025-04-01-preview")
        self.assertEqual(code, 0)
        saved = self.saved()
        self.assertEqual((saved["OPENTAG_CODEX_AUTH"], saved["OPENTAG_CODEX_API_VERSION"]), ("azure", "2025-04-01-preview"))
        self.assertEqual(tag_api.label(events[-1]["api"]), "Codex · Azure (acme.openai.azure.com)")

    def test_bad_input_changes_nothing_and_never_stops_the_tag(self) -> None:
        self.running = True
        before = self.config.read_bytes()
        cases = [
            dict(backend="codex", kind="openai", base_url="http://gateway.example.com", models="m"),
            dict(backend="codex", kind="openai", base_url="https://user:pw@gateway.example.com", models="m"),
            dict(backend="codex", kind="openai", models=" , "),
            dict(backend="codex", kind="anthropic", models="m"),
            dict(backend="codex", kind="openai", models="m", api_version="2025"),
        ]
        for options in cases:
            with self.subTest(options):
                code, events = self.run_cli("set", **options)
                self.assertEqual((code, events[-1]["status"]), (1, "failed"))
        code, events = self.run_cli("set", key="", backend="codex", kind="openai", models="m")
        self.assertIn("API key", events[-1]["error"])
        code, events = self.run_cli("set", key="has space", backend="codex", kind="openai", models="m")
        self.assertIn("whitespace", events[-1]["error"])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.config.read_bytes(), before)

    def test_running_tag_needs_restart(self) -> None:
        self.running = True
        code, events = self.run_cli("set", restart=False, backend="codex", kind="openai", models="gpt-5.5")
        self.assertEqual(code, 1)
        self.assertIn("--restart", events[-1]["error"])
        self.assertNotIn("OPENTAG_CODEX_AUTH", self.saved())

    def test_a_tag_that_cannot_start_gets_its_previous_settings_back(self) -> None:
        self.running = True
        before = self.saved()
        self.failing = {"start"}
        code, events = self.run_cli("set", backend="codex", kind="openai", models="gpt-5.5")
        self.assertEqual(code, 1)
        self.assertIn("previous one was kept", events[-1]["error"])
        self.assertIn("restoring", [e.get("step") for e in events])
        self.assertEqual(self.calls, ["stop", "start", "start"])
        after = self.saved()
        self.assertEqual(after, before)
        self.assertNotIn(KEY, self.output)

    def test_a_failed_start_keeps_a_concurrent_change_and_removes_new_keys(self) -> None:
        self.running = True
        before = self.saved()
        self.failing = {"start"}
        original = tag_config.update_config

        def update_then_other_change(path, changes, **kwargs):
            values = original(path, changes, **kwargs)
            original(path, {"OPENTAG_DEFAULT_MODEL": values["OPENTAG_DEFAULT_MODEL"]})
            current = tag_config.load_config(path)
            current["OPENTAG_OTHER_NOTE"] = "kept"
            tag_config.save_config(path, current)
            return values

        with patch.object(tag_config, "update_config", update_then_other_change):
            code, _ = self.run_cli("set", backend="codex", kind="openai", models="gpt-5.5")
        self.assertEqual(code, 1)
        after = self.saved()
        self.assertEqual(after.get("OPENTAG_OTHER_NOTE"), "kept")
        for key in set(after) - {"OPENTAG_OTHER_NOTE"}:
            self.assertEqual(after[key], before.get(key), key)
        for key in set(before) - set(after):
            self.fail(f"{key} was lost")

    def test_a_failed_stop_changes_nothing(self) -> None:
        self.running = True
        self.failing = {"stop"}
        code, events = self.run_cli("set", backend="codex", kind="openai", models="gpt-5.5")
        self.assertEqual(code, 1)
        self.assertIn("Nothing changed", events[-1]["error"])
        self.assertNotIn("OPENTAG_CODEX_AUTH", self.saved())

    def test_an_empty_key_keeps_the_saved_one_and_repeats_are_idempotent(self) -> None:
        self.run_cli("set", backend="codex", kind="openai", models="gpt-5.5")
        code, events = self.run_cli("set", key="", backend="codex", kind="openai", models="gpt-5.5,gpt-5.5-mini",
                                    base_url="https://gw.example.com/v1")
        self.assertEqual(code, 0)
        saved = self.saved()
        self.assertEqual(saved["OPENTAG_CODEX_API_KEY"], KEY)
        self.assertEqual(saved["OPENTAG_DEFAULT_MODEL"], "codex:gpt-5.5")
        # Saving the same thing again, or clearing what's clear, never restarts a running Tag.
        self.running = True
        self.calls.clear()
        code, events = self.run_cli("set", key="", backend="codex", kind="openai", models="gpt-5.5,gpt-5.5-mini",
                                    base_url="https://gw.example.com/v1")
        self.assertEqual((code, events[-1]["unchanged"], events[-1]["restarted"]), (0, True, False))
        self.assertEqual(self.saved(), saved)
        code, events = self.run_cli("clear", backend="claude")
        self.assertEqual((code, events[-1]["unchanged"]), (0, True))
        self.assertEqual(self.calls, [])


class ClearAndCheckTests(ApiFixture):
    def test_clear_goes_back_to_the_plan_and_removes_the_key(self) -> None:
        self.run_cli("set", backend="codex", kind="openai", models="gpt-5.5", base_url="https://gw.example.com/v1")
        tag_config.update_config(self.config, {"OPENTAG_CODEX_GATEWAY_FORMAT": "provider.only",
                                               "OPENTAG_CODEX_GATEWAY_PROVIDER": "cursor_sdk"})
        self.running = True
        self.calls.clear()
        code, events = self.run_cli("clear", backend="codex")
        self.assertEqual((code, events[-1]["status"], events[-1]["api"]), (0, "saved", None))
        saved = self.saved()
        self.assertEqual((saved["OPENTAG_CODEX_AUTH"], saved["OPENTAG_CODEX_API_KEY"]), ("inherit", ""))
        self.assertEqual(saved["OPENTAG_DEFAULT_MODEL"], "codex")
        self.assertEqual(saved["OPENTAG_CODEX_GATEWAY_FORMAT"], "")
        self.assertEqual(self.calls, ["stop", "start"])

    def test_status_and_check_report_without_the_key(self) -> None:
        self.run_cli("set", backend="claude", kind="anthropic", models="claude-sonnet-5-5")
        target = tag_ai.Target(self.home, "maya", lambda: False, self.lifecycle, "Maya's Tag")
        out = io.StringIO()
        with redirect_stdout(out):
            tag_ai.cli(["api"], target, json_output=True, api={})
        status = json.loads(out.getvalue())
        self.assertEqual([item["backend"] for item in status["api"]], ["claude"])
        self.assertEqual(status["kinds"]["codex"], ["openai", "azure"])
        with patch.object(tag_api.shutil, "which", return_value="/bin/claude"), \
                patch.object(tag_api.importlib.util, "find_spec", return_value=object()):
            code, events = self.run_cli("check", backend="claude")
        self.assertEqual((code, events[-1]["ok"]), (0, True))
        with patch.object(tag_api.shutil, "which", return_value=None):
            code, events = self.run_cli("check", backend="claude")
        self.assertEqual(code, 1)
        self.assertNotIn(KEY, self.output + out.getvalue())

    def test_the_tags_connection_row_says_it_uses_the_api(self) -> None:
        values = {"OPENTAG_CODEX_AUTH": "api", "OPENTAG_CODEX_API_KEY": KEY, "OPENTAG_CODEX_MODELS": "m",
                  "OPENTAG_CODEX_BASE_URL": "https://gateway.example.com/v1"}
        item = {"backend": "codex", "name": "Codex", "state": "connected", "installed": True, "account": "ChatGPT sign-in",
                "actions": ["change_account"], "detail": "", "shared": True}
        row = tag_ai.api_connection(item, values)
        self.assertEqual((row["state"], row["method"], row["account"], row["shared"], row["actions"]),
                         ("connected", "api", "API (gateway.example.com)", False, []))
        self.assertEqual(tag_ai.api_connection(item, {}), item)
        broken = tag_ai.api_connection(item, {**values, "OPENTAG_CODEX_MODELS": ""})
        self.assertEqual(broken["state"], "misconfigured")
        self.assertEqual(tag_ai.status_line(broken).split(" · ")[0], "API needs attention")

    def test_installation_connections_list_tags_using_their_own_api(self) -> None:
        values = {"OPENTAG_CLAUDE_AUTH": "api", "OPENTAG_CLAUDE_API_KEY": KEY, "OPENTAG_CLAUDE_MODELS": "m"}
        target = tag_ai.Target(self.home, "", lambda: False, self.lifecycle, "Your Tags",
                               tags=lambda: [("maya", "Maya's Tag", values), ("ops", "Ops", {})])
        out = io.StringIO()
        with patch.object(tag_ai, "connections", return_value=[]), redirect_stdout(out):
            tag_ai.cli(["connections"], target, json_output=True)
        listed = json.loads(out.getvalue())["api_connections"]
        self.assertEqual([(i["tag"], i["tag_name"], i["host"]) for i in listed], [("maya", "Maya's Tag", "api.anthropic.com")])
        self.assertNotIn(KEY, out.getvalue())
        with self.assertRaises(ValueError):
            tag_ai.cli(["api", "set"], target, json_output=True, api={"backend": "codex"})


if __name__ == "__main__":
    unittest.main()
