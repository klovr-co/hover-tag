from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import agent_summary
from scripts.codex_agent_backend import CodexAppServer
from scripts.opentag_process_env import text_only_environment
from scripts.tag_activity import ActivityStore, recent_activity
from tests.test_claude_agent_backend import FAKE_SDK, FakeClient, ResultMessage


class SummaryTests(unittest.TestCase):
    def test_both_backends_summarize_the_reply_not_the_excerpt(self):
        answer = "I looked into the launch plan.\n" + "Context. " * 1000 + "\nLaunch is blocked by missing approval."
        for backend, adapter in (("codex", "CodexAppServer"), ("claude", "ClaudeAgentRun")):
            def run(prompt, *, model, reasoning_effort, emit):
                self.assertEqual(json.loads(prompt)["reply"], answer)
                self.assertEqual(model, "selected-model")
                emit({"type": "message_complete", "phase": "commentary", "text": "Private reasoning"})
                emit({"type": "message_complete", "phase": "final_answer",
                      "text": "Launch remains blocked by missing approval."})
                return "completed", ""
            with self.subTest(backend=backend), patch.object(agent_summary, adapter) as factory, \
                    patch("scripts.opentag_agent.backend_command", side_effect=lambda name: [name]):
                factory.return_value.run.side_effect = run
                self.assertEqual(agent_summary.summarize_reply(answer, backend, "selected-model"),
                                 "Launch remains blocked by missing approval.")
                options = factory.call_args.kwargs
                if backend == "codex":
                    self.assertIn("features.memories=false", factory.call_args.args[0])
                self.assertEqual(options["text_only_instructions"], agent_summary.INSTRUCTIONS)
                self.assertEqual(options["max_timeout"], agent_summary.SUMMARY_TIMEOUT)
                self.assertFalse(options["cwd"].exists())

    def test_failed_partial_empty_and_oversized_results_are_not_summaries(self):
        for status, answer in (("failed", "Success."), ("timeout", "Partial."), ("completed", ""),
                               ("completed", "A" * (agent_summary.MAX_SUMMARY_CHARS + 1))):
            def run(_prompt, **kwargs):
                kwargs["emit"]({"type": "message_complete", "phase": "final_answer", "text": answer})
                return status, ""
            with self.subTest(status=status, answer=answer), patch.object(agent_summary, "CodexAppServer") as factory, \
                    patch("scripts.opentag_agent.backend_command", side_effect=lambda name: [name]):
                factory.return_value.run.side_effect = run
                self.assertEqual("", agent_summary.summarize_reply("Delivered answer", "codex", None))

    def test_long_source_retains_ending_and_redacts_credentials(self):
        source = agent_summary.summary_source("First finding. " + "x" * 40_000 + "\nUnfinished. password&#61;secret-value")
        self.assertLessEqual(len(source), agent_summary.MAX_SOURCE_CHARS)
        self.assertIn("First finding.", source)
        self.assertIn("Unfinished.", source)
        self.assertNotIn("secret-value", source)

    def test_worker_returns_before_model_finishes_and_caches_once(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory))
            run = store.create(team="T", channel="C", thread_ts="1", request_ts="1", requester="U")
            worker = agent_summary.ReplySummaryWorker()
            self.assertFalse(worker.submit(store, run, "Still running", "codex", None))
            store.finish(run, "completed")
            store.save_reply(run, "Here are the findings in detail.")
            started, release = threading.Event(), threading.Event()

            def summarize(*_args):
                started.set()
                release.wait(3)
                return "Launch needs one final approval."

            with patch.object(agent_summary, "summarize_reply", side_effect=summarize) as model:
                try:
                    self.assertTrue(worker.submit(store, run, "Delivered full answer", "claude", "opus"))
                    self.assertTrue(started.wait(2))
                    self.assertNotIn("reply_summary", store.get(run))
                    self.assertEqual("pending", store.get(run)["reply_summary_status"])
                    self.assertFalse(worker.submit(store, run, "Duplicate", "claude", "opus"))
                finally:
                    release.set()
                    worker.jobs.join()
                self.assertFalse(worker.submit(store, run, "Duplicate", "claude", "opus"))
                model.assert_called_once_with("Delivered full answer", "claude", "opus")
            reloaded = recent_activity(store.root)[0]
            self.assertEqual(reloaded["reply_summary"], "Launch needs one final approval.")
            self.assertEqual(reloaded["reply_preview"], "Here are the findings in detail.")
            store.save_reply_summary(run, "Overwrite attempt")
            self.assertEqual(store.get(run)["reply_summary"], reloaded["reply_summary"])

    def test_worker_failure_preserves_reply_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory))
            run = store.create(team="T", channel="C", thread_ts="1", request_ts="1", requester="U")
            store.finish(run, "completed")
            store.save_reply(run, "Fallback")
            worker = agent_summary.ReplySummaryWorker()
            with patch.object(agent_summary, "summarize_reply", side_effect=RuntimeError("offline")):
                worker.submit(store, run, "Full answer", "codex", None)
                worker.jobs.join()
            self.assertEqual(store.get(run)["reply_preview"], "Fallback")
            self.assertEqual(store.get(run)["reply_summary_status"], "unavailable")
            self.assertNotIn("reply_summary", store.get(run))
            with patch.object(agent_summary, "summarize_reply", return_value="Generated summary."):
                self.assertTrue(worker.submit(store, run, "Full answer", "codex", None))
                worker.jobs.join()
            self.assertEqual(store.get(run)["reply_summary"], "Generated summary.")

    def test_queue_capacity_and_enqueue_errors_leave_delivery_unaffected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory))
            ids = [store.create(team="T", channel="C", thread_ts="1", request_ts="1", requester="U") for _ in range(2)]
            for run in ids:
                store.finish(run, "completed")
            worker = agent_summary.ReplySummaryWorker(capacity=1)
            with patch("scripts.agent_summary.threading.Thread"):
                self.assertTrue(worker.submit(store, ids[0], "Reply", "codex", None))
                self.assertFalse(worker.submit(store, ids[1], "Reply", "codex", None))
            with patch.object(agent_summary._worker, "submit", side_effect=OSError("disk unavailable")):
                self.assertFalse(agent_summary.queue_reply_summary(store, ids[0], "Reply", "codex"))


class SummaryBackendIsolationTests(unittest.TestCase):
    def test_summary_environment_keeps_provider_auth_without_bridge_credentials(self):
        source = {"SLACK_BOT_TOKEN": "secret", "MFS_ALLOWED_SCOPES": "private",
                  "OPENTAG_CALLER_ID": "U123", "TAG_TELEMETRY": "1",
                  "ANTHROPIC_API_KEY": "provider", "CODEX_HOME": "/auth", "PATH": "/bin"}
        clean = text_only_environment(source)
        for key in ("SLACK_BOT_TOKEN", "MFS_ALLOWED_SCOPES", "OPENTAG_CALLER_ID", "TAG_TELEMETRY"):
            self.assertEqual(clean[key], "")
        self.assertEqual(clean["ANTHROPIC_API_KEY"], "provider")
        self.assertEqual(clean["CODEX_HOME"], "/auth")
        self.assertEqual(source["SLACK_BOT_TOKEN"], "secret")

    def test_codex_disables_inherited_tools_and_keeps_normal_task_defaults(self):
        for instructions in (None, agent_summary.INSTRUCTIONS):
            server = CodexAppServer(["codex", "app-server"], cwd=Path.cwd(), timeout=5,
                                    text_only_instructions=instructions)
            def request(method, params, *_args):
                if method == "config/read":
                    return {"config": {"mcp_servers": {"work.server": {"command": "server", "tool_timeout_sec": None}}, "plugins": {"work@market": {}}}}
                if method == "thread/start":
                    if instructions:
                        self.assertEqual(params["approvalPolicy"], "never")
                        self.assertEqual(params["sandbox"], "read-only")
                        self.assertEqual(params["baseInstructions"], instructions)
                        overrides = params["config"]
                        self.assertEqual(overrides["mcp_servers"], {"work.server": {"enabled": False}})
                        self.assertEqual(overrides["plugins"], {"work@market": {"enabled": False}})
                        self.assertFalse(any(key.startswith("mcp_servers.") for key in overrides))
                        self.assertFalse(overrides["features.shell_tool"])
                        self.assertFalse(overrides["features.hooks"])
                    else:
                        self.assertEqual(params["sandbox"], "workspace-write")
                        self.assertNotIn("config", params)
                    return {"thread": {"id": "t"}}
                if method == "turn/start":
                    return {"turn": {"id": "turn"}}
                return {}
            with patch.object(server, "_start"), patch.object(server, "_notify"), patch.object(server, "close"), \
                    patch.object(server, "_request", side_effect=request), \
                    patch.object(server, "_consume_turn", return_value=("completed", "")):
                self.assertEqual(server.run("reply", model=None, reasoning_effort=None, emit=lambda _: None),
                                 ("completed", ""))

    def test_claude_fake_sdk_generates_summary_with_no_tools_settings_or_hooks(self):
        async def script(client):
            options = client.options
            self.assertEqual(options.tools, [])
            self.assertEqual(options.mcp_servers, {})
            self.assertEqual(options.setting_sources, [])
            self.assertEqual(options.permission_mode, "dontAsk")
            self.assertTrue(json.loads(options.settings)["disableAllHooks"])
            self.assertIn("strict-mcp-config", options.extra_args)
            denied = await options.can_use_tool("Bash", {"command": "touch file"}, None)
            self.assertEqual(denied.behavior, "deny")
            yield ResultMessage(result="Launch is ready for review.")
        FakeClient.script = script
        with patch.dict("sys.modules", {"claude_agent_sdk": FAKE_SDK}):
            self.assertEqual(agent_summary.summarize_reply("The launch checklist is complete; please review it.", "claude", None),
                             "Launch is ready for review.")
        self.assertTrue(FakeClient.instances[-1].disconnected)
