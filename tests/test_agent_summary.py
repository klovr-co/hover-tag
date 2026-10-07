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
    def setUp(self) -> None:
        # Worker tests summarize replies; the thread's title has its own tests.
        session = patch.object(agent_summary, "summarize_session", return_value="")
        session.start()
        self.addCleanup(session.stop)
        configured = patch.object(agent_summary, "configured_summary_model", return_value="auto")
        configured.start()
        self.addCleanup(configured.stop)

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

            def summarize(*_args, **_kwargs):
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
                # Claude's small model summarizes, at the least thinking it offers.
                model.assert_called_once_with("Delivered full answer", "claude", "claude-haiku-4-5", effort=None)
            reloaded = recent_activity(store.root)[0]
            self.assertEqual(reloaded["reply_summary"], "Launch needs one final approval.")
            self.assertEqual(reloaded["reply_preview"], "Here are the findings in detail.")
            store.save_reply_summary(run, "Overwrite attempt")
            self.assertEqual(store.get(run)["reply_summary"], reloaded["reply_summary"])

    def test_summary_names_the_threads_backend_conversation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory))
            run = store.create(team="T", channel="C", thread_ts="1", request_ts="1", requester="U")
            store.save_session(run, "thread-1")
            store.finish(run, "completed")
            worker = agent_summary.ReplySummaryWorker()
            with patch.object(agent_summary, "summarize_reply", return_value="Closed 12 issues."), \
                    patch.object(agent_summary, "name_session") as name:
                worker.submit(store, run, "Answer", "codex", None)
                worker.jobs.join()
            name.assert_called_once_with("codex", "thread-1", "Closed 12 issues.")
            self.assertEqual("Closed 12 issues.", store.get(run)["reply_summary"])

    def test_naming_failure_keeps_the_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory))
            run = store.create(team="T", channel="C", thread_ts="1", request_ts="1", requester="U")
            store.save_session(run, "thread-1")
            store.finish(run, "completed")
            worker = agent_summary.ReplySummaryWorker()
            with patch.object(agent_summary, "summarize_reply", return_value="Closed 12 issues."), \
                    patch.object(agent_summary, "name_session", side_effect=RuntimeError("gone")):
                worker.submit(store, run, "Answer", "claude", None)
                worker.jobs.join()
            self.assertEqual("ready", store.get(run)["reply_summary_status"])

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


def option(backend, model, efforts=(), **extra):
    return agent_summary.ModelOption(model, model, tuple(efforts), backend=backend, **extra)


class SummaryModelTests(unittest.TestCase):
    CODEX = [option("codex", "gpt-5.5", ("low", "medium", "high"), is_default=True),
             option("codex", "gpt-5.4-mini", ("minimal", "low", "medium"))]
    CLAUDE = [option("claude", "opus", ("low", "high"), is_default=True),
              option("claude", "haiku", (), resolved_model="claude-haiku-4-5-20251001")]

    def test_auto_picks_each_accounts_smallest_model_at_its_least_thinking(self):
        self.assertEqual([("codex", "gpt-5.4-mini", "minimal"), ("codex", "gpt-5.5", "low")],
                         agent_summary.summary_attempts("codex", "gpt-5.5", self.CODEX, "auto"))
        self.assertEqual([("claude", "haiku", None), ("claude", "opus", "low")],
                         agent_summary.summary_attempts("claude", "opus", self.CLAUDE, ""))
        # An unreported Claude catalog still offers Haiku.
        self.assertEqual([("claude", "claude-haiku-4-5", None), ("claude", None, None)],
                         agent_summary.summary_attempts("claude", None, [], "auto"))

    def test_without_a_small_model_the_replys_model_summarizes(self):
        catalog = [option("codex", "gpt-5.5", ("low", "high"))]
        self.assertEqual([("codex", "gpt-5.5", "low")],
                         agent_summary.summary_attempts("codex", "gpt-5.5", catalog, "auto"))

    def test_chosen_model_is_used_and_falls_back_to_the_reply_model(self):
        self.assertEqual([("codex", "gpt-5.5", "low")],
                         agent_summary.summary_attempts("codex", "gpt-5.5", self.CODEX, "codex:gpt-5.5"))
        self.assertEqual([("claude", "opus", "low"), ("claude", "haiku", None)],
                         agent_summary.summary_attempts("claude", "haiku", self.CLAUDE, "claude:opus"))
        # Another backend's model needs its account; otherwise Tag picks automatically.
        self.assertEqual([("codex", "gpt-5.4-mini", "minimal"), ("codex", "gpt-5.5", "low")],
                         agent_summary.summary_attempts("codex", "gpt-5.5", self.CODEX, "claude:haiku"))
        self.assertEqual([("claude", "haiku", None), ("codex", "gpt-5.5", "low")],
                         agent_summary.summary_attempts("codex", "gpt-5.5", self.CODEX + self.CLAUDE, "claude:haiku"))

    def test_setting_is_read_when_used_so_changes_need_no_restart(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(agent_summary, "instance_home", return_value=Path(directory)), \
                patch.dict("os.environ", {"OPENTAG_ENV_FILE": ""}):
            settings = Path(directory) / "config/settings.json"
            with patch.object(agent_summary.tag_config, "config_path", return_value=settings):
                self.assertEqual("auto", agent_summary.configured_summary_model())
                settings.parent.mkdir()
                settings.write_text(json.dumps({"OPENTAG_SUMMARY_MODEL": "codex:gpt-5.5"}))
                self.assertEqual("codex:gpt-5.5", agent_summary.configured_summary_model())

    def test_failing_small_model_falls_back_so_summaries_keep_working(self):
        for backend, catalog in (("codex", self.CODEX), ("claude", self.CLAUDE)):
            calls = []

            def summarize(_text, name, model, **kwargs):
                calls.append((name, model, kwargs["effort"]))
                if len(calls) == 1:
                    raise RuntimeError("model not available")
                return "Summarized by the reply model."
            attempts = agent_summary.summary_attempts(backend, catalog[0].model_id, catalog, "auto")
            with self.subTest(backend=backend), patch.object(agent_summary, "summarize_reply", side_effect=summarize):
                self.assertEqual("Summarized by the reply model.", agent_summary.first_summary(
                    attempts, lambda name, model, effort: agent_summary.summarize_reply("x", name, model, effort=effort)))
            self.assertEqual(attempts, calls)


class RequestAndSessionSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        configured = patch.object(agent_summary, "configured_summary_model", return_value="auto")
        configured.start()
        self.addCleanup(configured.stop)

    def test_both_backends_summarize_the_request_in_a_throwaway_session(self):
        for backend, adapter in (("codex", "CodexAppServer"), ("claude", "ClaudeAgentRun")):
            def run(prompt, *, model, reasoning_effort, emit):
                self.assertEqual(json.loads(prompt), {"request": "<@BOT> please close the done issues"})
                emit({"type": "message_complete", "phase": "final_answer", "text": "\"Close the finished issues\""})
                return "completed", ""
            with self.subTest(backend=backend), patch.object(agent_summary, adapter) as factory, \
                    patch("scripts.opentag_agent.backend_command", side_effect=lambda name: [name]):
                factory.return_value.run.side_effect = run
                self.assertEqual("Close the finished issues", agent_summary.summarize_request(
                    "<@BOT> please close the done issues", backend, "small"))
                self.assertEqual(factory.call_args.kwargs["text_only_instructions"], agent_summary.REQUEST_INSTRUCTIONS)

    def test_request_summary_is_saved_and_the_request_text_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory))
            run = store.create(team="T", channel="C", thread_ts="1", request_ts="1", requester="U")
            worker = agent_summary.ReplySummaryWorker()
            with patch.object(agent_summary, "summarize_request", return_value="Close the finished issues") as model:
                self.assertTrue(worker.submit_request(store, run, "secret plan: close the issues", "claude", "opus"))
                worker.jobs.join()
            model.assert_called_once_with("secret plan: close the issues", "claude", "claude-haiku-4-5", effort=None)
            record = store.get(run)
            self.assertEqual("Close the finished issues", record["request_summary"])
            self.assertNotIn("secret plan", json.dumps(record))
            self.assertFalse(worker.submit_request(store, run, "again", "claude", "opus"))

    def test_failed_request_summary_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory))
            run = store.create(team="T", channel="C", thread_ts="1", request_ts="1", requester="U")
            worker = agent_summary.ReplySummaryWorker()
            with patch.object(agent_summary, "summarize_request", return_value=""):
                worker.submit_request(store, run, "Do it", "codex", "gpt-5.5")
                worker.jobs.join()
            self.assertEqual("unavailable", store.get(run)["request_summary_status"])

    def test_each_finished_round_rolls_the_session_summary_forward(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory))
            worker = agent_summary.ReplySummaryWorker()
            seen = []

            def session(previous, request, reply, *_args, **_kwargs):
                seen.append((previous, request, reply))
                return f"Round {len(seen)} title"
            for index in (1, 2):
                run = store.create(team="T", channel="C", thread_ts="1", request_ts=str(index), requester="U")
                store.save_session(run, "thread-1")
                store.save_request_summary(run, f"Ask {index}")
                store.finish(run, "completed")
                with patch.object(agent_summary, "summarize_reply", return_value=f"Reply {index}"), \
                        patch.object(agent_summary, "summarize_session", side_effect=session), \
                        patch.object(agent_summary, "name_session") as name:
                    worker.submit(store, run, "Full answer", "codex", "gpt-5.5")
                    worker.jobs.join()
                # The backend conversation takes the whole thread's title.
                name.assert_called_once_with("codex", "thread-1", f"Round {index} title")
            # Only the previous title and the newest round go in, so cost stays flat.
            self.assertEqual([(None, "Ask 1", "Reply 1"), ("Round 1 title", "Ask 2", "Reply 2")], seen)
            self.assertEqual("Round 2 title", store.session("T", "C", "1")["session_summary"])


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
                        # Throwaway: never listed in the ChatGPT or Codex apps.
                        self.assertTrue(params["ephemeral"])
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
            self.assertIn("no-session-persistence", options.extra_args)
            denied = await options.can_use_tool("Bash", {"command": "touch file"}, None)
            self.assertEqual(denied.behavior, "deny")
            yield ResultMessage(result="Launch is ready for review.")
        FakeClient.script = script
        with patch.dict("sys.modules", {"claude_agent_sdk": FAKE_SDK}):
            self.assertEqual(agent_summary.summarize_reply("The launch checklist is complete; please review it.", "claude", None),
                             "Launch is ready for review.")
        self.assertTrue(FakeClient.instances[-1].disconnected)
