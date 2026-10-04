import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts import agent_summary, slack_socket_agent as bridge
from scripts.tag_activity import ActivityStore, artifact_records, recent_activity
from tests.test_slack_socket_agent import FakeApp


class ArtifactTests(unittest.TestCase):
    def test_metadata_bounds_and_links_and_old_records(self):
        with tempfile.TemporaryDirectory() as raw:
            store = ActivityStore(Path(raw))
            run = store.create(team="T1", channel="C1", thread_ts="1.1", request_ts="1.1", requester="U1")
            self.assertNotIn("artifacts", recent_activity(store.root)[0])
            store.save_artifacts(run, [
                {"name": "../plan.md", "delivery": "uploaded", "url": "https://example.slack.com/files/F1/plan.md?token=private#x"},
                {"name": "bad", "delivery": "uploaded", "url": "https://example.slack.com.evil.test/files/F1"},
                {"name": "bad2", "delivery": "uploaded", "url": "javascript:alert(1)"}])
            row = recent_activity(store.root)[0]
            self.assertEqual(row["artifacts"][0]["name"], "plan.md")
            self.assertEqual(row["artifacts"][0]["url"], "https://example.slack.com/files/F1/plan.md")
            self.assertNotIn("url", row["artifacts"][1])
            self.assertNotIn("url", row["artifacts"][2])
            self.assertEqual(row["artifact_thread_url"], "slack://channel?team=T1&id=C1&message=1.1")
        self.assertEqual(len(artifact_records([{"name": "a", "delivery": "local"}] * 100)), 20)

    def test_output_upload_records_success_local_failure_and_oversized(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            for name in ("ok.txt", "local.txt", "failed.txt", "large.txt"):
                (root / name).write_text("ok", encoding="utf-8")
            (root / "large.txt").write_bytes(b"x" * 11)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps([{"path": str(root / name), "attach": name != "local.txt"}
                for name in ("ok.txt", "local.txt", "failed.txt", "large.txt")]), encoding="utf-8")
            client = MagicMock()
            client.files_upload_v2.side_effect = [{"files": [{"permalink": "https://example.slack.com/files/F1/ok.txt"}]}, RuntimeError("offline")]
            artifacts = []
            with patch.object(bridge, "MAX_OUTPUT_FILE_BYTES", 10):
                bridge.deliver_output_artifacts(client, "C1", "1.1", manifest, root, MagicMock(), artifacts=artifacts)
            self.assertEqual([a["delivery"] for a in artifacts], ["uploaded", "local", "upload_failed", "upload_failed"])
            self.assertEqual(artifacts[0]["url"], "https://example.slack.com/files/F1/ok.txt")
            self.assertEqual(artifacts[2]["local_path"], str(root / "failed.txt"))
            self.assertEqual(client.files_upload_v2.call_count, 2)

    def test_both_summary_backends_receive_status_but_no_paths_or_urls(self):
        for backend, adapter in (("codex", "CodexAppServer"), ("claude", "ClaudeAgentRun")):
            with self.subTest(backend=backend), patch.object(agent_summary, adapter) as factory, \
                    patch("scripts.opentag_agent.backend_command", side_effect=lambda name: [name]):
                def run(prompt, **kwargs):
                    payload = json.loads(prompt)
                    self.assertEqual(payload["artifacts"], [{"name": "plan.pdf", "kind": "file", "delivery": "upload_failed", "available_locally": True}])
                    kwargs["emit"]({"type": "message_complete", "phase": "final_answer", "text": "The plan is ready, but the upload failed."})
                    return "completed", ""
                factory.return_value.run.side_effect = run
                result = agent_summary.summarize_reply("Attached the plan.", backend, None, artifacts=[{
                    "name": "plan.pdf", "kind": "file", "delivery": "upload_failed", "local_path": str(Path("/private/plan.pdf").resolve()),
                    "url": "https://example.slack.com/files/F1/plan.pdf"}])
                self.assertIn("upload failed", result)

    def test_both_backends_save_image_results_before_queuing_summary(self):
        for backend in ("codex", "claude"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                store = ActivityStore(root / "activity")
                app = FakeApp()
                models = [bridge.ModelOption("test-model", "Test", ("low", "high"), backend=backend, is_default=True, default_reasoning_effort="low")]
                def run(*args, **kwargs):
                    kwargs["on_run_info"]({"model": "test-model", **({"reasoning_effort": "high"} if backend == "codex" else {})})
                    (args[7] / "results/images/one.png").write_bytes(b"image")
                    (args[7] / "results/images/two.png").write_bytes(b"image")
                    return "Created two images.", True
                def summarize(saved_store, run_id, answer, selected_backend, model):
                    self.assertEqual(selected_backend, backend)
                    artifacts = saved_store.get(run_id)["artifacts"]
                    self.assertEqual(saved_store.get(run_id)["reasoning_effort"], "high" if backend == "codex" else "low")
                    self.assertEqual([a["delivery"] for a in artifacts], ["uploaded", "upload_failed"])
                    self.assertNotIn("local_path", artifacts[1])
                    self.assertIn("could not be attached", answer)
                with patch.dict(os.environ, {"SLACK_BOT_TOKEN": "fixture", "SLACK_CHANNEL_IDS": "C1",
                        "OPENTAG_SLACK_SETTINGS_FILE": str(root / "settings.json"), "OPENTAG_SLACK_STREAMING": "0",
                        "OPENTAG_WORKDIR": str(root)}, clear=True), \
                     patch.object(bridge, "App", return_value=app), \
                     patch.object(bridge, "discover_tag_models", return_value=models), \
                     patch.object(bridge, "build_thread_text", return_value="thread"), \
                     patch.object(bridge, "run_backend_events", side_effect=run), \
                     patch.object(bridge, "queue_reply_summary", side_effect=summarize) as summary:
                    bridge.create_app(backend, 30, frozenset({"U1"}), activity_store=store)
                    client = MagicMock()
                    client.files_upload_v2.side_effect = [{"files": [{"permalink": "https://example.slack.com/files/F1/one.png"}]}, RuntimeError("offline")]
                    app.events["app_mention"]({"channel": "C1", "ts": "1.1", "user": "U1", "text": "<@BOT> draw"},
                        {"team_id": "T1"}, client, MagicMock())
                    summary.assert_called_once()
                    self.assertEqual(recent_activity(store.root)[0]["artifacts"][0]["url"], "https://example.slack.com/files/F1/one.png")
