from __future__ import annotations

import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path

from scripts import tag_activity
from scripts.tag_activity import ActivityStore, MAX_EVENTS
from scripts.tag_activity_details import MAX_DETAIL_CHARS, command_identity, item_activity_details


class ActivityStoreTests(unittest.TestCase):
    def test_reply_preview_is_bounded_plain_text_and_redacted(self) -> None:
        self.assertEqual("Created launch.md. Owners assigned.", tag_activity.reply_preview(
            "**Created [launch.md](https://example.com/file?token=private).**\n\n- Owners assigned."))
        self.assertEqual("Ready.", tag_activity.reply_preview("```sh\ncat secrets\n```\nReady."))
        self.assertNotIn("private-value", tag_activity.reply_preview("Saved password=private-value successfully."))
        self.assertNotIn("private-value", tag_activity.reply_preview("Saved password&#61;private-value successfully."))
        self.assertLessEqual(len(tag_activity.reply_preview("A long answer " * 100)), tag_activity.MAX_REPLY_PREVIEW)
        self.assertTrue(tag_activity.reply_preview("A long answer " * 100).endswith("…"))

    def test_reply_preview_survives_reopen_without_rewriting_older_records(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = ActivityStore(Path(raw))
            run = store.create(team="T1", channel="C1", thread_ts="1.0", request_ts="1.0", requester="U1")
            store.save_reply(run, "Partial answer")
            self.assertNotIn("reply_preview", store.get(run))
            store.finish(run, "completed")
            before = (store.root / f"{run}.json").read_bytes()
            self.assertNotIn("reply_preview", tag_activity.recent_activity(store.root)[0])
            self.assertEqual(before, (store.root / f"{run}.json").read_bytes())
            store.save_reply(run, "Created the launch checklist.")
            self.assertEqual("Created the launch checklist.", tag_activity.recent_activity(store.root)[0]["reply_preview"])
            self.assertEqual("Created the launch checklist.", ActivityStore(store.root).get(run)["reply_preview"])

    def test_model_metadata_survives_reopen_for_both_backends(self):
        for backend, model in (("codex", "gpt-test"), ("claude", "claude-test")):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as raw:
                store = ActivityStore(Path(raw))
                run = store.create(team="T", channel="C", thread_ts="1", request_ts="1", requester="U")
                self.assertNotIn("model", tag_activity.recent_activity(store.root)[0])
                store.save_model(run, backend, model, "Readable model")
                store.finish(run, "completed")
                item = tag_activity.recent_activity(store.root)[0]
                self.assertEqual((backend, model, "Readable model"),
                                 (item["backend"], item["model"], item["model_name"]))
                store.summary_status(run, "pending")
                record = store.get(run)
                record["reply_summary_updated_at"] = "2000-01-01T00:00:00+00:00"
                store._write(record)
                self.assertEqual("unavailable", tag_activity.recent_activity(store.root)[0]["reply_summary_status"])

    def test_runs_in_one_slack_thread_share_an_opaque_activity_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory))
            first = store.create(team="T1", channel="C1", thread_ts="1.1", request_ts="1.1", requester="U")
            second = store.create(team="T1", channel="C1", thread_ts="1.1", request_ts="1.5", requester="U")
            other = store.create(team="T1", channel="C1", thread_ts="2.2", request_ts="2.2", requester="U")
            store.save_session(first, "thread-1")
            store.save_session(second, "../not-an-id")
            threads = {item["run_id"]: item["thread"] for item in tag_activity.recent_activity(store.root)}
            self.assertEqual(threads[first], threads[second])
            self.assertNotEqual(threads[first], threads[other])
            self.assertNotIn("1.1", threads[first])
            self.assertEqual("thread-1", store.get(first)["session_id"])
            self.assertNotIn("session_id", store.get(second))

    def test_short_command_names_keep_code_arguments_and_directories_private(self) -> None:
        cases = {
            "/bin/zsh -lc 'cd /private/work && python scripts/build.py --token secret-value'": "python build.py",
            "python3 - <<'PY'\nprint('private body')\nPY": "python3 (inline)",
            "python -c 'print(123)'": "python (inline)",
            "python -m pytest /private/tests": "python -m pytest",
            "cat /private/runtime-agent.md | head -20": "cat runtime-agent.md | head",
            "API_TOKEN=private-value python script.py --private-argument": "python script.py",
            "node /private/build.js private-argument": "node build.js",
            "sed -n '1,20p' /private/file": "sed · file",
            "$(private-command) argument": "Command",
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(command_identity(command), expected)

    def test_file_change_identity_contains_only_filenames(self) -> None:
        details = item_activity_details({
            "type": "fileChange", "changes": [
                {"path": "/private/work/app.py", "diff": "private content"},
                {"path": "/private/work/test_app.py", "diff": "other content"},
            ],
        }, completed=True)
        self.assertEqual(details["tool"], "File change · app.py, test_app.py")
        self.assertIn("private content", details["output"])

    def test_command_output_and_large_values_are_bounded(self) -> None:
        details = item_activity_details({
            "type": "commandExecution", "command": "echo hello", "cwd": "/workspace",
        }, completed=False)
        file_read = item_activity_details({
            "type": "commandExecution",
            "command": "/bin/zsh -lc 'cat /workspace/references/runtime-agent.md'",
        }, completed=False)
        result = item_activity_details({
            "type": "commandExecution", "aggregatedOutput": "x" * 10_000,
            "exitCode": 0,
        }, completed=True)
        self.assertIn("echo hello", details["input"])
        self.assertEqual("cat runtime-agent.md", file_read["tool"])
        self.assertLessEqual(len(result["output"]), MAX_DETAIL_CHARS)
        self.assertIn("large value omitted", result["output"])
        lines = item_activity_details({
            "type": "commandExecution", "aggregatedOutput": "first line\nsecond line",
            "exitCode": 0,
        }, completed=True)
        self.assertIn("first line\nsecond line", lines["output"])

    def test_record_keeps_redacted_tool_input_and_result(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            store = ActivityStore(Path(raw_dir) / "activity")
            run_id = store.create(
                team="T1", channel="C1", thread_ts="1.0",
                request_ts="1.1", requester="U1",
            )
            store.observe(run_id, {
                "type": "activity_start", "activity_id": "tool-1",
                "label": "Using a connected tool…",
                "details": {
                    "tool": "gmail/send_email",
                    "input": '{"to":"person@example.com","password":"hidden-password"}',
                },
            })
            store.observe(run_id, {
                "type": "activity_complete", "activity_id": "tool-1",
                "label": "Using a connected tool…", "status": "completed",
                "details": {"output": '{"message_id":"sent-123","token":"hidden-token"}'},
            })
            record = ActivityStore(store.root).get(run_id)
            rendered = json.dumps(record)
            self.assertIn("person@example.com", rendered)
            self.assertIn("sent-123", rendered)
            self.assertNotIn("hidden-password", rendered)
            self.assertNotIn("hidden-token", rendered)
            self.assertEqual("gmail/send_email", record["events"][0]["details"]["tool"])


    def test_persists_only_public_labels_and_bounded_events(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            store = ActivityStore(Path(raw_dir) / "activity")
            run_id = store.create(
                team="T1", channel="C1", thread_ts="1.0",
                request_ts="1.1", requester="U1",
            )
            for index in range(MAX_EVENTS + 1):
                store.observe(run_id, {
                    "type": "activity_start", "activity_id": f"private-{index}",
                    "label": "private Gmail address and message body",
                    "arguments": {"password": "sensitive"},
                })
            store.observe(run_id, {
                "type": "activity_complete", "activity_id": "private-0",
                "label": "private Gmail address and message body", "status": "completed",
                "result": "sensitive result",
            })
            store.finish(run_id, "completed")

            persisted = (store.root / f"{run_id}.json").read_text(encoding="utf-8")
            self.assertNotIn("sensitive", persisted)
            self.assertNotIn("private-0", persisted)
            self.assertNotIn("Gmail address", persisted)
            record = ActivityStore(store.root).get(run_id)
            self.assertIsNotNone(record)
            self.assertEqual(MAX_EVENTS, len(record["events"]))
            self.assertEqual(1, record["omitted"])
            self.assertEqual("completed", record["events"][0]["status"])
            self.assertEqual("unknown", record["events"][1]["status"])
            self.assertNotIn("sensitive", json.dumps(record))
            if os.name != "nt":
                self.assertEqual(0o600, (store.root / f"{run_id}.json").stat().st_mode & 0o777)

    def test_rejects_invalid_run_ids_and_expired_records(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            store = ActivityStore(Path(raw_dir) / "activity")
            run_id = store.create(
                team="T1", channel="C1", thread_ts="1.0",
                request_ts="1.1", requester="U1",
            )
            self.assertIsNone(store.get("../outside"))
            path = store.root / f"{run_id}.json"
            os.utime(path, (0, 0))
            self.assertIsNone(store.get(run_id))


class RecentActivityTests(unittest.TestCase):
    """``tag NAME logs --json`` activity: when, where, and how requests ended; nothing else."""

    SCOPES = ("slack://tag-t1-a1/channels/launch__C0LAUNCH,"
              "slack://tag-t1-a1/channels/design-review__G0DESIGN,file://local/notes")

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "activity"
        self.store = ActivityStore(self.root)

    def record(self, channel: str, outcome: str, started: str, finished: str | None = None) -> str:
        run_id = self.store.create(team="T1", channel=channel, thread_ts="1.0", request_ts="1.0",
                                   requester="U0PRIVATE")
        path = self.root / f"{run_id}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(outcome=outcome, started_at=started, finished_at=finished)
        path.write_text(json.dumps(record), encoding="utf-8")
        return run_id

    def test_outcomes_channels_and_order(self) -> None:
        self.record("C0LAUNCH", "completed", "2026-10-04T09:00:00+00:00", "2026-10-04T09:01:00+00:00")
        self.record("G0DESIGN", "failed", "2026-10-03T09:00:00+00:00", "2026-10-03T09:02:00+00:00")
        self.record("D0MAYA", "interrupted", "2026-10-02T09:00:00+00:00", "2026-10-02T09:00:30+00:00")
        self.record("C0UNKNOWN", "running", "2026-10-04T10:00:00+00:00")
        items = tag_activity.recent_activity(self.root, self.SCOPES)
        self.assertTrue(all(tag_activity.RUN_ID_RE.fullmatch(item.pop("run_id")) for item in items))
        self.assertTrue(all(re.fullmatch(r"[a-f0-9]{16}", item.pop("thread")) for item in items))
        self.assertEqual(["2026-10-04T10:00:00+00:00", "2026-10-04T09:00:00+00:00", "2026-10-03T09:00:00+00:00",
                          "2026-10-02T09:00:00+00:00"], [item.pop("started_at") for item in items])
        self.assertEqual([
            {"at": "2026-10-04T10:00:00+00:00", "kind": "working", "channel": "C0UNKNOWN",
             "channel_name": None, "dm": False, "requester": "U0PRIVATE"},
            {"at": "2026-10-04T09:01:00+00:00", "kind": "replied", "channel": "C0LAUNCH",
             "channel_name": "launch", "dm": False, "duration_seconds": 60.0, "requester": "U0PRIVATE"},
            {"at": "2026-10-03T09:02:00+00:00", "kind": "failed", "channel": "G0DESIGN",
             "channel_name": "design-review", "dm": False, "duration_seconds": 120.0, "requester": "U0PRIVATE"},
            {"at": "2026-10-02T09:00:30+00:00", "kind": "stopped", "channel": "D0MAYA",
             "channel_name": None, "dm": True, "duration_seconds": 30.0, "requester": "U0PRIVATE"},
        ], items)

    def test_requester_names_and_summaries_of_the_request_and_thread(self) -> None:
        older = self.record("C0LAUNCH", "completed", "2026-10-04T09:00:00+00:00", "2026-10-04T09:01:00+00:00")
        newer = self.record("C0LAUNCH", "running", "2026-10-04T10:00:00+00:00")
        # Records from before #185 show who asked with no request or session summary.
        people = {"T1": {"U0PRIVATE": {"name": "Maxine", "avatar": "https://avatars.slack-edge.com/m.png"}}}
        items = {item["run_id"]: item for item in tag_activity.recent_activity(self.root, people=people)}
        self.assertEqual("Maxine", items[older]["requester_name"])
        self.assertEqual("https://avatars.slack-edge.com/m.png", items[older]["requester_avatar"])
        self.assertNotIn("request_summary", items[older])
        self.assertNotIn("session_summary", items[older])
        self.store.request_summary_status(newer, "pending")
        self.store.save_request_summary(newer, "Make the empty space at the bottom feel less empty")
        self.store.save_request_summary(newer, "Overwrite attempt")
        self.store.save_session_summary("T1", "C0LAUNCH", "1.0", "Polish Home's empty space")
        self.store.save_session_summary("T1", "C0LAUNCH", "1.0", "Polish Home and its hint row")
        items = {item["run_id"]: item for item in tag_activity.recent_activity(self.root)}
        self.assertEqual("Make the empty space at the bottom feel less empty", items[newer]["request_summary"])
        self.assertEqual("ready", items[newer]["request_summary_status"])
        # Every round of the thread shares its rolling summary; the newest replaces the last.
        self.assertEqual({"Polish Home and its hint row"},
                         {items[older]["session_summary"], items[newer]["session_summary"]})
        self.assertNotIn("requester_name", items[older])
        # Session records live beside runs without being mistaken for one.
        self.assertEqual(2, len(list(self.root.glob("*.json"))))
        self.assertIsNone(self.store.session("T1", "C0OTHER", "1.0"))

    def test_stale_pending_request_summary_reads_as_unavailable(self) -> None:
        run_id = self.record("C0LAUNCH", "running", "2026-10-04T10:00:00+00:00")
        self.store.request_summary_status(run_id, "pending")
        path = self.root / f"{run_id}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["request_summary_updated_at"] = "2026-01-01T00:00:00+00:00"
        path.write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual("unavailable", tag_activity.recent_activity(self.root)[0]["request_summary_status"])

    def test_step_count_includes_omitted_steps(self) -> None:
        run_id = self.record("C0LAUNCH", "completed", "2026-10-04T09:00:00+00:00", "2026-10-04T09:01:00+00:00")
        path = self.root / f"{run_id}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(events=[{"id": "a" * 64, "label": "Browsing connected knowledge…", "status": "completed",
                               "started_at": record["started_at"], "finished_at": record["started_at"]}], omitted=2)
        path.write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(3, tag_activity.recent_activity(self.root, "")[0]["step_count"])

    def test_invalid_records_are_skipped_and_reading_changes_nothing(self) -> None:
        valid = self.record("C0LAUNCH", "completed", "2026-10-04T09:00:00+00:00", "2026-10-04T09:01:00+00:00")
        broken = self.record("C0LAUNCH", "completed", "2026-10-04T08:00:00+00:00", "2026-10-04T08:01:00+00:00")
        path = self.root / f"{broken}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["events"] = [{"label": "not a public label"}]
        path.write_text(json.dumps(record), encoding="utf-8")
        (self.root / "notes.json").write_text("{}", encoding="utf-8")
        expired = self.record("C0LAUNCH", "completed", "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00")
        old = time.time() - tag_activity.RETENTION_SECONDS - 60
        os.utime(self.root / f"{expired}.json", (old, old))
        before = {item.name: item.read_bytes() for item in self.root.iterdir()}
        items = tag_activity.recent_activity(self.root, "")
        self.assertEqual(["2026-10-04T09:01:00+00:00"], [item["at"] for item in items])
        self.assertIsNone(items[0]["channel_name"])
        self.assertEqual(before, {item.name: item.read_bytes() for item in self.root.iterdir()})
        self.assertTrue(valid)
        self.assertEqual([], tag_activity.recent_activity(self.root.parent / "missing"))

    def test_at_most_fifty_newest_first(self) -> None:
        for day in range(1, 31):
            for hour in (1, 2):
                self.record("C0LAUNCH", "completed", f"2026-09-{day:02d}T0{hour}:00:00+00:00",
                            f"2026-09-{day:02d}T0{hour}:30:00+00:00")
        items = tag_activity.recent_activity(self.root, self.SCOPES)
        self.assertEqual(50, len(items))
        self.assertEqual("2026-09-30T02:30:00+00:00", items[0]["at"])
        self.assertEqual(sorted((item["at"] for item in items), reverse=True), [item["at"] for item in items])


if __name__ == "__main__":
    unittest.main()
