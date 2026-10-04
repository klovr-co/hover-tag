from datetime import datetime, timedelta, timezone
import json
import tempfile
import unittest
from pathlib import Path

from scripts.tag_activity import ActivityStore, activity_details
from scripts.tag_error_reporting import ErrorReportStore, ReportOrigin, make_error_report


class ActivityDetailsTests(unittest.TestCase):
    def test_both_backends_show_saved_steps_and_exact_errors_without_private_routing(self):
        for backend in ("codex", "claude"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as raw:
                home = Path(raw)
                store = ActivityStore(home / "activity")
                run = store.create(team="T1", channel="C1", thread_ts="1.0", request_ts="1.1", requester="UOWNER")
                store.observe(run, {"type": "activity_start", "activity_id": "tool", "label": "Reading a file…",
                                    "details": {"tool": "cat plan.md", "input": '{"password":"hidden-value"}'}})
                store.observe(run, {"type": "activity_complete", "activity_id": "tool", "label": "Reading a file…",
                                    "status": "failed", "details": {"output": "File missing"}})
                store.finish(run, "failed")
                reports = ErrorReportStore(home / "error-reports")
                origin = ReportOrigin(team_id="T1", channel_id="C1", thread_ts="1.0", request_ts="1.1", requester_id="UOWNER")
                for ref in ("AAAABBBB", "CCCCDDDD"):
                    reports.save(make_error_report(ref, "rate limit exceeded", backend=backend, origin=origin))
                # Ambiguous legacy records must not pick another retry's report.
                self.assertIsNone(activity_details(store.root, run)["error"])
                store.attach_error(run, "AAAABBBB")
                detail = activity_details(store.root, run)
                self.assertEqual("AAAABBBB", detail["error"]["reference"])
                self.assertIn(f"Backend: {backend}", detail["error"]["text"])
                self.assertEqual("File missing", detail["events"][0]["details"]["output"])
                self.assertNotIn("hidden-value", json.dumps(detail))
                self.assertNotIn("UOWNER", json.dumps(detail))
                self.assertIsNone(activity_details(store.root, "../escape"))

    def test_legacy_report_is_available_without_rewriting_and_old_attempt_is_excluded(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            store = ActivityStore(home / "activity")
            run = store.create(team="T1", channel="C1", thread_ts="1.0", request_ts="1.0", requester="U1")
            store.finish(run, "failed")
            reports = ErrorReportStore(home / "error-reports")
            origin = ReportOrigin(team_id="T1", channel_id="C1", thread_ts="1.0", request_ts="1.0", requester_id="U1")
            reports.save(make_error_report("AAAABBBB", "timeout", backend="codex", origin=origin))
            reports.save(make_error_report("CCCCDDDD", "earlier attempt", backend="codex", origin=origin,
                                          failure_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()))
            before = {p: p.read_bytes() for p in home.rglob("*.json")}
            self.assertEqual("AAAABBBB", activity_details(store.root, run)["error"]["reference"])
            self.assertEqual(before, {p: p.read_bytes() for p in home.rglob("*.json")})

    def test_error_reference_alone_cannot_match_another_conversation(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            store = ActivityStore(home / "activity")
            run = store.create(team="T1", channel="C1", thread_ts="1.0", request_ts="1.0", requester="U1")
            store.finish(run, "failed")
            store.attach_error(run, "AAAABBBB")
            ErrorReportStore(home / "error-reports").save(make_error_report("AAAABBBB", "timeout", backend="claude",
                origin=ReportOrigin(team_id="TOTHER", channel_id="C1", thread_ts="1.0", request_ts="1.0", requester_id="U1")))
            self.assertIsNone(activity_details(store.root, run)["error"])
