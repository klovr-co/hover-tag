from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.tag_error_reporting import (
    ErrorReportStore,
    FailureCategory,
    HealthCheck,
    ReportOrigin,
    build_troubleshooting_prompt,
    classify_failure,
    collect_health_checks,
    make_error_report,
)


class FailureClassificationTests(unittest.TestCase):
    def test_structured_code_wins_over_conflicting_text(self) -> None:
        result = classify_failure(
            "the backend exited unexpectedly after a request",
            "rate_limit",
        )

        self.assertEqual(FailureCategory.RATE_LIMIT, result.category)
        self.assertEqual("rate_limit", result.backend_code)

    def test_known_text_categories_do_not_infer_a_network_cause(self) -> None:
        idle = classify_failure("Tag backend timed out: no backend activity for 420s")
        unknown = classify_failure("RuntimeError: private prompt text")

        self.assertEqual(FailureCategory.IDLE_TIMEOUT, idle.category)
        self.assertNotIn("network", idle.explanation.lower())
        self.assertEqual(FailureCategory.UNKNOWN, unknown.category)
        self.assertEqual("Cause not identified.", unknown.explanation)

    def test_authentication_mapping_does_not_claim_expiration(self) -> None:
        result = classify_failure("authentication failed: invalid api key")

        self.assertEqual(FailureCategory.AUTHENTICATION, result.category)
        self.assertNotIn("expired", result.explanation.lower())


class ErrorReportTests(unittest.TestCase):
    def report(self, detail: str = "backend exited with code 17"):
        return make_error_report(
            "ABC12345",
            detail,
            backend="codex",
            backend_version_value="codex 0.1",
            tag_version_value="0.2.0",
            origin=ReportOrigin(
                team_id="TSECRET",
                channel_id="CSECRET",
                thread_ts="1.2",
                request_ts="1.3",
                requester_id="USECRET",
            ),
            health_checks=(HealthCheck("local memory service", "healthy", "2026-09-23T00:00:01Z"),),
        )

    def test_public_report_is_allowlisted_and_keeps_health_timestamp(self) -> None:
        report = self.report()
        text = report.report_text("I was trying to use token=xoxb-secret to summarize a launch plan")

        self.assertIn("ABC12345", text)
        self.assertIn("2026-09-23T00:00:01Z", text)
        self.assertIn("<redacted>", text)
        self.assertIn("launch plan", text)
        for secret in ("TSECRET", "CSECRET", "USECRET", "xoxb-secret"):
            self.assertNotIn(secret, text)
        self.assertNotIn("private prompt", text)

    def test_correlated_error_events_are_bounded_and_publicly_rendered(self) -> None:
        report = make_error_report(
            "ABC12345",
            "backend failed",
            backend="codex",
            error_events=("Observed backend error code: rate_limit.",) * 10,
        )

        self.assertEqual(5, len(report.error_events))
        self.assertIn("Correlated error events:", report.report_text())

    def test_health_probe_failure_still_returns_basic_result(self) -> None:
        def failing_opener(*args: object, **kwargs: object) -> object:
            raise RuntimeError("probe implementation failed")

        checks = collect_health_checks(
            "http://127.0.0.1:8000",
            opener=failing_opener,
            checked_at="2026-09-23T00:00:01Z",
        )

        self.assertEqual("unavailable", checks[0].status)
        self.assertEqual("2026-09-23T00:00:01Z", checks[0].checked_at)

    def test_troubleshooting_prompt_is_self_contained_without_claiming_local_access(self) -> None:
        prompt = build_troubleshooting_prompt(self.report(), skill_available=False)

        self.assertIn("tag-troubleshoot skill was not found", prompt)
        self.assertIn("tag inspect --json", prompt)
        self.assertIn("must not claim it inspected the local installation", prompt)
        self.assertIn("ABC12345", prompt)
        self.assertIn("BEGIN TAG REPORT", prompt)

    def test_retry_linkage_accepts_only_opaque_references(self) -> None:
        report = make_error_report(
            "ABC12345",
            "backend failed",
            backend="codex",
            related_reference="not-a-reference",
        )

        self.assertIsNone(report.related_reference)


class ErrorReportStoreTests(unittest.TestCase):
    def test_record_survives_restart_and_expires(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            now = [1_000_000.0]
            report = make_error_report(
                "ABC12345",
                "backend failed with exit code 7",
                backend="codex",
                failure_at="1970-01-12T13:46:40Z",
            )
            first = ErrorReportStore(Path(raw_dir), retention_seconds=60, clock=lambda: now[0])
            first.save(report)
            second = ErrorReportStore(Path(raw_dir), retention_seconds=60, clock=lambda: now[0])

            self.assertEqual("ABC12345", second.get("ABC12345").reference)
            now[0] += 61
            self.assertIsNone(second.get("ABC12345"))
            self.assertFalse((Path(raw_dir) / "ABC12345.json").exists())

    def test_record_is_bounded_and_stored_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            report = make_error_report(
                "ABC12345",
                "RuntimeError: token=xoxb-secret and private prompt",
                backend="codex",
                origin=ReportOrigin(channel_id="C123", requester_id="U123"),
            )
            store = ErrorReportStore(Path(raw_dir), max_records=1)
            store.save(report)
            payload = json.loads((Path(raw_dir) / "ABC12345.json").read_text())

            self.assertEqual("C123", payload["origin"]["channel_id"])
            self.assertNotIn("xoxb-secret", report.report_text())
            self.assertNotIn("private prompt", report.report_text())

    def test_unavailable_or_invalid_references_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            store = ErrorReportStore(Path(raw_dir))

            self.assertIsNone(store.get("../../settings"))
            with patch.object(Path, "read_text", side_effect=PermissionError):
                self.assertIsNone(store.get("ABC12345"))

    def test_in_memory_availability_uses_the_same_retention_policy(self) -> None:
        report = make_error_report(
            "ABC12345",
            "backend failed",
            backend="codex",
            failure_at="1970-01-12T13:46:40Z",
        )
        store = ErrorReportStore(retention_seconds=60, clock=lambda: 1_000_061.0)

        self.assertFalse(store.is_available(report))


if __name__ == "__main__":
    unittest.main()
