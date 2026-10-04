from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import agent_connection as connections, agent_models, agent_usage, tag_config, tag_display
from scripts.claude_agent_backend import ClaudeAgentRun, ClaudeEventMapper
from scripts.codex_agent_backend import CodexAppServer, CodexEventMapper
from scripts.opentag_agent import emit_event, run_rich_events


def api(backend="codex", mode="api"):
    prefix = f"OPENTAG_{backend.upper()}_"
    values = {prefix + "AUTH": mode, prefix + "API_KEY": "test-private-credential",
              prefix + "MODELS": "deployment-one,model-two"}
    if mode == "azure":
        values[prefix + "BASE_URL"] = "https://example.openai.azure.com/openai"
        values[prefix + "API_VERSION"] = "2025-04-01-preview"
    return values


class ConnectionTests(unittest.TestCase):
    def test_inherited_installations_do_not_change(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(connections.codex_options(), [])
            self.assertEqual(connections.claude_environment(), {})
            for backend in ("codex", "claude"):
                connections.validate(backend)
                self.assertFalse(connections.active(backend))

    def test_azure_options_are_toml_and_use_environment_header(self):
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with patch.dict(os.environ, api(mode="azure"), clear=True):
            options = connections.codex_options()
            parsed = tomllib.loads("\n".join(options[1::2]))
            provider = parsed["model_providers"]["tag_api"]
            self.assertEqual(provider["env_http_headers"], {"api-key": "OPENTAG_CODEX_API_KEY"})
            self.assertEqual(provider["query_params"], {"api-version": "2025-04-01-preview"})
            self.assertFalse(provider["requires_openai_auth"])
            self.assertNotIn("test-private-credential", str(options))
            self.assertEqual(parsed["model"], "deployment-one")
            self.assertEqual(provider["wire_api"], "responses")

    def test_api_mode_ignores_chatgpt_store_and_sets_provider(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, api(), clear=True), \
                patch("scripts.codex_agent_backend.tag_chatgpt.Store") as store, \
                patch("scripts.codex_agent_backend.subprocess.Popen", side_effect=OSError("no process")) as popen:
            server = CodexAppServer(["codex", "app-server", "--stdio"], cwd=Path(tmp), timeout=1)
            with self.assertRaises(Exception):
                server._start()
            store.return_value.identity.assert_not_called()
            command = popen.call_args.args[0]
            self.assertIn('model_provider="tag_api"', command)
            self.assertNotIn("test-private-credential", str(command))

    def test_gateway_launch_disables_hosted_search_without_changing_direct_openai(self):
        for endpoint, selected, disabled in (
            ("http://localhost:8000/v1", "api", True),
            ("https://closedrouter-two.vercel.app/v1", "api", True),
            ("https://example.openai.azure.com/openai/v1", "azure", True),
            ("https://api.openai.com/v1", "api", False),
            ("", "api", False),
        ):
            with self.subTest(endpoint=endpoint), tempfile.TemporaryDirectory() as tmp, \
                    patch.dict(os.environ, dict(api(mode=selected), OPENTAG_CODEX_BASE_URL=endpoint), clear=True), \
                    patch("scripts.codex_agent_backend.subprocess.Popen", side_effect=OSError("no process")) as popen:
                server = CodexAppServer(["codex", "app-server", "--stdio"], cwd=Path(tmp), timeout=1)
                with self.assertRaises(Exception):
                    server._start()
                self.assertEqual('web_search="disabled"' in popen.call_args.args[0], disabled)
                for setting in ("features.image_generation=false", "agents.enabled=false", "features.multi_agent=false",
                                "features.code_mode=true", "features.code_mode_only=true"):
                    self.assertEqual(setting in popen.call_args.args[0], disabled)

    def test_claude_gateway_keeps_sdk_tool_configuration(self):
        values = dict(api("claude"), OPENTAG_CLAUDE_BASE_URL="https://gateway.example/v1")
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, values, clear=True), \
                patch.object(ClaudeAgentRun, "_sdk", return_value=(None, lambda **kw: kw, None, None)):
            options = ClaudeAgentRun(cwd=Path(tmp), timeout=1).options(
                model=None, reasoning_effort=None, fast_mode=False, emit=None, deadline=10)
            self.assertEqual(options["env"]["ANTHROPIC_BASE_URL"], values["OPENTAG_CLAUDE_BASE_URL"])
            self.assertNotIn("web_search", options)
            self.assertNotIn("disallowed_tools", options)
            self.assertTrue(callable(options["can_use_tool"]))

    def test_claude_sdk_options_receive_api_environment(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, api("claude"), clear=True), \
                patch.object(ClaudeAgentRun, "_sdk", return_value=(None, lambda **kw: kw, None, None)):
            options = ClaudeAgentRun(cwd=Path(tmp), timeout=1).options(
                model=None, reasoning_effort=None, fast_mode=False, emit=None, deadline=10)
            self.assertEqual(options["env"]["ANTHROPIC_API_KEY"], "test-private-credential")
            self.assertEqual(options["env"]["CLAUDE_CODE_OAUTH_TOKEN"], "")
            self.assertEqual(options["model"], "deployment-one")

    def test_invalid_configuration_fails_closed(self):
        for backend in ("codex", "claude"):
            values = api(backend)
            for missing in ("API_KEY", "MODELS"):
                bad = dict(values)
                del bad[f"OPENTAG_{backend.upper()}_{missing}"]
                with self.assertRaises(ValueError):
                    connections.validate(backend, bad)
            bad = dict(values, **{f"OPENTAG_{backend.upper()}_TRANSPORT": "exec"})
            with self.assertRaises(ValueError):
                connections.validate(backend, bad)
        for url in ("https://user:pass@example.com", "https://example.com?api-key=x", "http://example.com", "https://exa mple.com"):
            with self.assertRaises(ValueError):
                connections.validate_url(url)
        connections.validate_url("http://localhost:8080/v1")

    def test_custom_catalog_does_not_require_subscription_or_borrow_models(self):
        for backend in ("codex", "claude"):
            with patch.dict(os.environ, api(backend), clear=True), \
                    patch("scripts.agent_models.shutil.which", return_value="/cli"), \
                    patch("importlib.util.find_spec", return_value=object()), \
                    patch("scripts.agent_models.subprocess.run") as run:
                self.assertTrue(agent_models.backend_signed_in(backend))
                models = agent_models.discover_models(backend)
                self.assertEqual([m.model_id for m in models], ["deployment-one", "model-two"])
                self.assertTrue(models[0].is_default)
                self.assertTrue(all(not m.supports_fast_mode for m in models))
                run.assert_not_called()

    def test_secret_config_and_event_redaction(self):
        values = api()
        self.assertEqual(tag_config.public_config(values)["OPENTAG_CODEX_API_KEY"], "[set]")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            tag_config.update_config(path, values)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with patch.dict(os.environ, values, clear=True), contextlib.redirect_stdout(io.StringIO()) as out:
            emit_event("error", "failed test-private-credential", nested={"text": "test-private-credential"})
        self.assertNotIn("test-private-credential", out.getvalue())

    def test_status_distinguishes_configured_from_authenticated(self):
        with patch("scripts.tag_display.shutil.which", return_value="/cli"), patch("scripts.tag_display.subprocess.run") as run:
            message, ready = tag_display.backend_status(values=api())
            self.assertTrue(ready)
            self.assertIn("authentication and task not tested", message)
            run.assert_not_called()


class UsageTests(unittest.TestCase):
    def test_codex_cache_writes_are_counted_and_priced_separately(self):
        event = agent_usage.codex_event({'tokenUsage': {'total': {
            'inputTokens': 100, 'outputTokens': 20, 'cachedInputTokens': 30,
            'cacheWriteInputTokens': 10}}})[0]
        self.assertEqual(event['cache_creation_tokens'], 10)
        rates = {'OPENTAG_CODEX_INPUT_USD_PER_MILLION': '2',
                 'OPENTAG_CODEX_OUTPUT_USD_PER_MILLION': '10',
                 'OPENTAG_CODEX_CACHED_INPUT_USD_PER_MILLION': '1'}
        with patch.dict(os.environ, rates, clear=True):
            self.assertIsNone(agent_usage.estimated_cost('codex', event))
            os.environ['OPENTAG_CODEX_CACHE_WRITE_USD_PER_MILLION'] = '4'
            self.assertAlmostEqual(agent_usage.estimated_cost('codex', event), .00039)
            for invalid in (-1, None, True, 80):
                self.assertIsNone(agent_usage.estimated_cost('codex', dict(event, cache_creation_tokens=invalid)))
            self.assertEqual(agent_usage.estimated_cost('claude', dict(event, cost_usd=.5)), .5)

    def test_backend_normalization_and_missing_usage(self):
        codex = CodexEventMapper().map({"method": "thread/tokenUsage/updated", "params": {
            "threadId": "one", "tokenUsage": {"total": {"inputTokens": 100, "outputTokens": 20,
            "cachedInputTokens": 30, "reasoningOutputTokens": 5}}}})[0]
        claude = ClaudeEventMapper().map({"kind": "ResultMessage", "usage": {
            "input_tokens": 60, "output_tokens": 20, "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 10}, "total_cost_usd": .5})[0]
        self.assertEqual(codex["input_tokens"], claude["input_tokens"])
        self.assertEqual(codex["output_tokens"], claude["output_tokens"])
        self.assertEqual(claude["type"], "usage")
        self.assertEqual(agent_usage.codex_event({}), [])
        self.assertEqual(agent_usage.claude_event({"usage": {"input_tokens": True}}), [])
        self.assertIsNone(agent_usage.money(float("nan")))

    def test_duplicate_snapshots_retry_and_partial_recovery(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "OPENTAG_CODEX_INPUT_USD_PER_MILLION": "2", "OPENTAG_CODEX_OUTPUT_USD_PER_MILLION": "10",
            "OPENTAG_CODEX_CACHED_INPUT_USD_PER_MILLION": "1"}, clear=True):
            home = Path(tmp)
            event = {"scope_id": "thread-one", "input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 30}
            run = agent_usage.Recorder("codex", home=home)
            run.observe(event)
            run.observe(event)
            run.save("failed")
            second = agent_usage.Recorder("claude", home=home)
            second.observe({"input_tokens": 10, "output_tokens": 2, "cost_usd": .5})
            agent_usage.Recorder("codex", home=home)  # Interrupted before any usage event.
            report = agent_usage.report(home, {"OPENTAG_MONTHLY_BUDGET_USD": ".1"})
            self.assertEqual(report["input_tokens"], 110)
            self.assertEqual(report["output_tokens"], 22)
            self.assertAlmostEqual(report["estimated_cost_usd"], .50037)
            self.assertEqual(report["attempts_without_usage"], 1)
            self.assertEqual(report["unfinished_attempts"], 2)
            self.assertTrue(report["recorded_cost_over_budget"])
            self.assertEqual(report["enforcement"], "advisory")
            if os.name != "nt":
                self.assertEqual((home / "state/usage.sqlite3").stat().st_mode & 0o777, 0o600)

    def test_month_boundary_and_read_only_empty_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(agent_usage.report(home, {})["attempts"], 0)
            self.assertFalse((home / "state").exists())
            recorder = agent_usage.Recorder("codex", home=home)
            recorder.observe({"input_tokens": 1, "output_tokens": 1})
            db = agent_usage.connection(home)
            with db:
                db.execute("UPDATE usage SET month='2000-01'")
            db.close()
            self.assertEqual(agent_usage.report(home, {})["attempts"], 0)

    def test_unknown_cost_is_not_zero_cost_claim(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            home = Path(tmp)
            recorder = agent_usage.Recorder("codex", home=home)
            recorder.observe({"input_tokens": 100, "output_tokens": 2, "cached_input_tokens": 0})
            recorder.save("completed")
            self.assertEqual(agent_usage.report(home, {})["attempts_without_cost"], 1)

    def test_concurrent_attempts_and_schema_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            # A process can stop after creating the table but before its version
            # checkpoint. Reinitialization must be safe and preserve its rows.
            db = agent_usage.connection(home)
            db.execute("PRAGMA user_version=0")
            db.close()
            def record(_):
                recorder = agent_usage.Recorder("claude", home=home)
                recorder.observe({"input_tokens": 10, "output_tokens": 2, "cost_usd": .1})
                recorder.save("completed")
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(record, range(12)))
            report = agent_usage.report(home, {})
            self.assertEqual(report["attempts"], 12)
            self.assertEqual(report["input_tokens"], 120)
            self.assertAlmostEqual(report["estimated_cost_usd"], 1.2)
            db = agent_usage.connection(home)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
            db.execute("PRAGMA user_version=2")
            db.close()
            with self.assertRaisesRegex(ValueError, "newer Tag"):
                agent_usage.connection(home)

    def test_storage_failure_does_not_retry_or_fail_a_completed_task(self):
        with patch("scripts.opentag_agent.agent_usage.Recorder", side_effect=sqlite3.OperationalError("disk full")), \
                patch("scripts.opentag_agent.emit_event") as emit:
            calls = []
            def start(forward):
                calls.append(True)
                forward({"type": "usage", "input_tokens": 10, "output_tokens": 1})
                return "completed", ""
            self.assertEqual(run_rich_events(start, errors=(RuntimeError,), backend_name="Claude"), 0)
            self.assertEqual(len(calls), 1)
            self.assertTrue(any("Usage recording unavailable" in call.args[1] for call in emit.call_args_list))

    def test_usage_cli_uses_selected_tag_budget(self):
        from scripts import tag_cli, tag_instances
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"TAG_HOME": tmp}, clear=True):
            context = tag_instances.create(Path(tmp), "api-test")
            tag_config.save_config(context.home / "config/settings.json", {"OPENTAG_MONTHLY_BUDGET_USD": "35"})
            with patch.object(sys, "argv", ["tag", "api-test", "usage", "--json"]), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(tag_cli._run_cli(), 0)
            report = json.loads(out.getvalue())
            self.assertEqual(report["monthly_budget_usd"], 35)
            self.assertEqual(report["attempts"], 0)
            self.assertFalse((context.home / "state/usage.sqlite3").exists())

    def test_runner_records_usage_and_forwards_only_token_counts(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"TAG_INSTANCE_HOME": tmp}, clear=True), \
                patch("scripts.opentag_agent.emit_event") as emit:
            def start(forward):
                forward({"type": "usage", "input_tokens": 10, "output_tokens": 1, "cost_usd": .01})
                return "completed", ""
            self.assertEqual(run_rich_events(start, errors=(RuntimeError,), backend_name="Claude"), 0)
            # Activity receives token counts; cost and provider scope stay in the ledger.
            emit.assert_called_once()
            self.assertEqual("usage", emit.call_args.args[0])
            self.assertEqual({"usage"}, set(emit.call_args.kwargs))
            self.assertEqual(agent_usage.report(Path(tmp), {})["input_tokens"], 10)


if __name__ == "__main__":
    unittest.main()
