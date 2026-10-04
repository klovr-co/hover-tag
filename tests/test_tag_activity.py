from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from scripts import tag_activity
from scripts.tag_activity import ActivityStore, MAX_EVENTS
from scripts.tag_activity_details import MAX_DETAIL_CHARS, command_identity, item_activity_details


class ActivityStoreTests(unittest.TestCase):
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
        self.assertEqual([
            {"at": "2026-10-04T10:00:00+00:00", "kind": "working", "channel": "C0UNKNOWN",
             "channel_name": None, "dm": False},
            {"at": "2026-10-04T09:01:00+00:00", "kind": "replied", "channel": "C0LAUNCH",
             "channel_name": "launch", "dm": False},
            {"at": "2026-10-03T09:02:00+00:00", "kind": "failed", "channel": "G0DESIGN",
             "channel_name": "design-review", "dm": False},
            {"at": "2026-10-02T09:00:30+00:00", "kind": "stopped", "channel": "D0MAYA",
             "channel_name": None, "dm": True},
        ], items)
        # Prompts, requesters, and tool steps never leave the store.
        self.assertNotIn("U0PRIVATE", json.dumps(items))

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
