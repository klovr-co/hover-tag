from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts import watch_classifier, watch_eval as harness


class FixtureTests(unittest.TestCase):
    def test_fixtures_cover_the_hard_cases_with_both_labels(self) -> None:
        _, cases = harness.load_cases()
        categories = {case.category for case in cases}
        for category in ("another_person", "another_bot", "about_tag", "continuation", "inclusive", "resume"):
            self.assertIn(category, categories)
        self.assertGreaterEqual(sum(case.expected for case in cases), 15)
        self.assertGreaterEqual(sum(not case.expected for case in cases), 15)

    def test_fixtures_run_through_real_identity_minimization(self) -> None:
        name, cases = harness.load_cases()
        for case in cases:
            state = json.dumps(harness.case_state(case, name))
            for member_id in (harness.REQUESTER, harness.TAG, harness.BOT, "UPSAM", "UPPRIYA"):
                self.assertNotIn(member_id, state, case.id)
        continuation = next(case for case in cases if case.id == "continue-two-step")
        history = harness.case_state(continuation, name)["thread_history"]
        self.assertEqual(["requester", "tag", "requester"], [item["author"] for item in history])
        self.assertIn("<tag>", history[0]["text"])

    def test_current_scope_matches_what_watching_sends_to_the_classifier(self) -> None:
        _, cases = harness.load_cases()
        reachable = {case.id: case.reachable for case in cases}
        self.assertFalse(reachable["inclusive-anyone"])  # Top level, no name: channel-wide only.
        self.assertFalse(reachable["person-reply-in-thread"])  # A thread Tag isn't in.
        for case_id in ("inclusive-anyone-tag-thread", "named-but-for-person", "direct-greeting", "continue-two-step"):
            self.assertTrue(reachable[case_id], case_id)
        current = [case for case in cases if case.reachable]
        self.assertGreaterEqual(sum(case.expected for case in current), 10)
        self.assertGreaterEqual(sum(not case.expected for case in current), 10)
        out = io.StringIO()
        with patch("sys.stdout", out):
            harness.main(["plan"])
        self.assertTrue(out.getvalue().startswith(f"{len(current)} cases"))
        out = io.StringIO()
        with patch("sys.stdout", out):
            harness.main(["--scope", "all", "plan"])
        self.assertTrue(out.getvalue().startswith(f"{len(cases)} cases"))

    def test_rejects_messages_that_would_bypass_the_classifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(json.dumps({"cases": [
                {"id": "x", "category": "direct", "expected": True, "message": "<@U123> hi"}
            ]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "never reaches"):
                harness.load_cases(path)


class OfflineTests(unittest.TestCase):
    def test_plan_and_report_never_use_the_network(self) -> None:
        with patch.object(watch_classifier.urllib.request, "urlopen") as urlopen:
            name, cases = harness.load_cases()
            totals = harness.plan(cases, name, io.StringIO())
            with tempfile.TemporaryDirectory() as directory:
                scores = Path(directory) / "scores.jsonl"
                scores.write_text("", encoding="utf-8")
                with patch("sys.stdout", io.StringIO()):
                    self.assertEqual(0, harness.main(["report", str(scores)]))
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(0, harness.main([]))
        urlopen.assert_not_called()
        self.assertEqual(set(watch_classifier.PROVIDERS), set(totals))
        for provider, total in totals.items():
            self.assertAlmostEqual(
                total["tokens"] * watch_classifier.PROVIDERS[provider].usd_per_million_input / 1e6, total["usd"]
            )
            self.assertLess(total["usd"], 0.05)

    def test_metrics_and_threshold_sweep(self) -> None:
        expected = {"a": True, "b": True, "c": False, "d": False, "e": True}
        rows = [
            {"provider": "openai-decisions", "id": "a", "outcome": "score", "probability": 0.95},
            {"provider": "openai-decisions", "id": "b", "outcome": "score", "probability": 0.8},
            {"provider": "openai-decisions", "id": "c", "outcome": "score", "probability": 0.85},
            {"provider": "openai-decisions", "id": "d", "outcome": "refusal"},
            {"provider": "openai-decisions", "id": "e", "outcome": "error"},
        ]
        at_09 = harness.metrics(expected, rows, 0.9)
        self.assertEqual((1, 0, 2, 2), (at_09.tp, at_09.fp, at_09.fn, at_09.tn))
        self.assertEqual(1.0, at_09.precision)
        self.assertAlmostEqual(1 / 3, at_09.recall)
        at_08 = harness.metrics(expected, rows, 0.8)
        self.assertEqual((2, 1, 1, 1), (at_08.tp, at_08.fp, at_08.fn, at_08.tn))
        self.assertAlmostEqual(2 / 3, at_08.precision)
        self.assertIsNone(harness.metrics({"c": False}, rows, 0.99).precision)

        cases = [harness.Case(case_id, "x", label, {}, []) for case_id, label in expected.items()]
        out = io.StringIO()
        results = harness.report(cases, rows + [
            {"provider": "typesafe-jev", "id": "a", "outcome": "score", "probability": 0.99},
        ], out)
        self.assertEqual({"openai-decisions", "typesafe-jev"}, set(results))
        self.assertIn(0.9, [item.threshold for item in results["typesafe-jev"]])
        text = out.getvalue()
        self.assertIn("1 refused, 1 errors", text)
        self.assertIn("4 not scored", text)
        self.assertIn("e [x] expected=True probability=None", text)


class DeploymentTuningTests(unittest.TestCase):
    def test_export_turns_feedback_into_labels_and_scores_without_network(self) -> None:
        from scripts import channel_watch
        with tempfile.TemporaryDirectory() as directory, patch.object(
            watch_classifier.urllib.request, "urlopen"
        ) as urlopen:
            root = Path(directory)
            log = channel_watch.ScoreLog(root / "scores.jsonl")
            state = {"assistant": {"name": "Iris's Tag"}, "current_message": "Iris's Tag?", "thread_history": []}
            liked = log.score(provider="openai-decisions", probability=0.97, trigger="named", outcome="reply", state=state)
            disliked = log.score(provider="openai-decisions", probability=0.92, trigger="thread", outcome="reply", state=state)
            log.score(provider="openai-decisions", probability=0.3, trigger="named", outcome="below threshold", state=state)
            log.score(provider="openai-decisions", trigger="named", outcome="classifier ClassifierError", state=state)
            log.label(liked, True, "take")
            log.label(disliked, True, "thumbsup")
            log.label(disliked, False, "thumbsdown")
            cases_path, scores_path = root / "out/cases.json", root / "out/scores.jsonl"
            with patch("sys.stderr", io.StringIO()):
                self.assertEqual(0, harness.main([
                    "export", str(root / "scores.jsonl"), "--cases", str(cases_path), "--scores", str(scores_path)]))
            fixture = json.loads(cases_path.read_text(encoding="utf-8"))
            self.assertEqual("Iris's Tag", fixture["assistant_name"])
            self.assertEqual([True, False, None, None], [case["expected"] for case in fixture["cases"]])
            self.assertEqual("label from thumbsdown", fixture["cases"][1]["note"])
            self.assertEqual(3, len(scores_path.read_text(encoding="utf-8").splitlines()))
            name, cases = harness.load_cases(cases_path)
            self.assertEqual([liked, disliked], [case.id for case in cases])  # Unlabelled cases wait.
            self.assertEqual(state, harness.case_state(cases[0], name))
            out = io.StringIO()
            harness.report(cases, harness._recorded(scores_path), out)
            self.assertIn("Not enough to tune: 2 labelled", out.getvalue())
        urlopen.assert_not_called()

    def test_recommends_the_lowest_threshold_meeting_the_precision_target(self) -> None:
        expected, rows = {}, []
        for index in range(240):
            positive = index % 2 == 0
            # Positives score 0.80-0.99; negatives spread over 0.00-0.89.
            probability = 0.8 + (index % 20) / 100 if positive else (index % 90) / 100
            expected[str(index)] = positive
            rows.append({"provider": "typesafe-jev", "id": str(index), "outcome": "score", "probability": probability})
        best = harness.recommend(expected, rows, 0.95)
        self.assertGreaterEqual(best.precision, 0.95)
        self.assertLess(harness.metrics(expected, rows, round(best.threshold - 0.01, 2)).precision, 0.95)
        self.assertIsNone(harness.recommend({"a": False}, [{"id": "a", "outcome": "score", "probability": 1.0}], 0.95))
        cases = [harness.Case(case_id, "x", label, {}, []) for case_id, label in expected.items()]
        out = io.StringIO()
        harness.report(cases, rows, out, target=0.95)
        self.assertIn(f"Recommended threshold for precision ≥ 0.95: {best.threshold:.2f}", out.getvalue())


class PaidRunGuardTests(unittest.TestCase):
    def run_score(self, scorer, *, max_usd: float = 1.0, out_path: Path, cases=None) -> float:
        name, all_cases = harness.load_cases()
        return harness.score_run(
            cases if cases is not None else all_cases[:3], name, provider="openai-decisions",
            api_key="sk-test", out_path=out_path, max_usd=max_usd, log=io.StringIO(), scorer=scorer,
        )

    def test_score_requires_confirmation_budget_and_key(self) -> None:
        with patch.object(watch_classifier.urllib.request, "urlopen") as urlopen, patch.dict(
            harness.os.environ, {}, clear=True
        ), patch("sys.stderr", io.StringIO()):
            for argv in (
                ["score", "--provider", "openai-decisions", "--out", "x.jsonl", "--max-usd", "1"],
                ["score", "--provider", "openai-decisions", "--out", "x.jsonl", "--max-usd", "6", "--confirm-paid"],
                ["score", "--provider", "openai-decisions", "--out", "x.jsonl", "--max-usd", "1", "--confirm-paid"],
            ):
                with self.subTest(argv=argv), self.assertRaises(SystemExit):
                    harness.main(argv)
        urlopen.assert_not_called()

    def test_records_scores_refusals_and_resumes_without_repeating_calls(self) -> None:
        scorer = MagicMock(side_effect=[
            watch_classifier.Score(0.97, "gpt-6-luna", 700),
            watch_classifier.ClassifierRefusal("no"),
            watch_classifier.Score(0.2, "gpt-6-luna", None),
        ])
        with tempfile.TemporaryDirectory() as directory:
            out_path = Path(directory) / "scores.jsonl"
            spent = self.run_score(scorer, out_path=out_path)
            rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(["score", "refusal", "score"], [row["outcome"] for row in rows])
            self.assertAlmostEqual(700 * 0.10 / 1e6, rows[0]["usd"])
            self.assertGreater(spent, rows[0]["usd"])
            self.assertEqual(0.0, self.run_score(scorer, out_path=out_path))
            self.assertEqual(3, scorer.call_count)

    def test_stops_before_the_budget(self) -> None:
        scorer = MagicMock(return_value=watch_classifier.Score(0.9, "gpt-6-luna", 1_000_000))
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(harness.BudgetStop, "Stopped before"):
            self.run_score(scorer, max_usd=0.15, out_path=Path(directory) / "scores.jsonl")
        self.assertEqual(1, scorer.call_count)

    def test_stops_on_quota_or_auth_errors_and_records_other_failures(self) -> None:
        for status in (401, 429):
            error = watch_classifier.ClassifierError("failed")
            error.__cause__ = urllib.error.HTTPError("https://example.test", status, "x", None, None)
            scorer = MagicMock(side_effect=error)
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                out_path = Path(directory) / "scores.jsonl"
                with self.assertRaisesRegex(harness.BudgetStop, f"HTTP {status}"):
                    self.run_score(scorer, out_path=out_path)
                self.assertEqual(1, scorer.call_count)
                self.assertEqual("", out_path.read_text(encoding="utf-8"))
        scorer = MagicMock(side_effect=watch_classifier.ClassifierError("timed out"))
        with tempfile.TemporaryDirectory() as directory:
            out_path = Path(directory) / "scores.jsonl"
            self.run_score(scorer, out_path=out_path)
            self.assertEqual(3, scorer.call_count)
            self.assertIn('"outcome": "error"', out_path.read_text(encoding="utf-8"))

    def test_paid_run_uses_mocked_http_end_to_end(self) -> None:
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self):
                return json.dumps({"answers": [
                    {"type": "predicate", "name": "tag_is_addressed", "probability": 0.91}
                ]}).encode()

        name, cases = harness.load_cases()
        with tempfile.TemporaryDirectory() as directory, patch.object(
            watch_classifier.urllib.request, "urlopen", return_value=Response()
        ) as urlopen:
            out_path = Path(directory) / "scores.jsonl"
            harness.score_run(cases[:2], name, provider="openai-decisions", api_key="sk-test",
                              out_path=out_path, max_usd=0.01, log=io.StringIO())
            self.assertEqual(2, urlopen.call_count)
            self.assertEqual("https://api.openai.com/v1/decisions", urlopen.call_args.args[0].full_url)


if __name__ == "__main__":
    unittest.main()
