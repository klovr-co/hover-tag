from __future__ import annotations

import io
import json
import os
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


class RealCaseImportTests(unittest.TestCase):
    ALLOWED = frozenset({"CALLOWED"})

    def item(self, **changes) -> dict:
        return {"channel": "CALLOWED", "ts": "100.1", "requester": "UOWNER", "bots": ["UOTHERBOT"],
                "assistant": {"user_id": "UPA", "name": "Owner's PA"},
                "history": [{"user": "UOWNER", "text": "<@UPA|Owner's PA> check <https://x.test/a|the dashboard>"},
                            {"user": "UPA", "text": "Done. Mail ops@example.com or call +1 (555) 010-9999."},
                            {"user": "UOTHERBOT", "text": "Build UABCDEFGH12 passed"}],
                "message": "thanks, now filter out me", "expected": True, "confidence": "high",
                "category": "continuation", "reason": "continues the exchange"} | changes

    def test_minimizes_and_scrubs_before_storing(self) -> None:
        case = harness.real_case(self.item(), self.ALLOWED)
        stored = json.dumps(case)
        for secret in ("UOWNER", "UPA", "UOTHERBOT", "UABCDEFGH12", "x.test", "example.com", "555", "100.1"):
            self.assertNotIn(secret, stored)
        history = case["state"]["thread_history"]
        self.assertEqual(["requester", "tag", "another_bot"], [h["author"] for h in history])
        self.assertEqual("<tag> check <link>", history[0]["text"])
        self.assertEqual("Done. Mail <email> or call <phone>.", history[1]["text"])
        self.assertTrue(case["reachable"])
        self.assertTrue(case["id"].startswith("r-"))

    def test_rejects_other_channels_mentions_and_missing_labels(self) -> None:
        for changes in ({"channel": "CSECRET"}, {"message": "<@UPA> hi"}, {"message": "<@USAM> hi"},
                        {"confidence": "sure"}, {"expected": "yes"}, {"derived": "invented"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                harness.real_case(self.item(**changes), self.ALLOWED)

    def test_derivations_turn_mentions_into_names_or_pseudonyms(self) -> None:
        named = harness.real_case(self.item(message="<@UPA|Owner's PA> what do you think?",
                                            derived="tag-mention-to-name", history=[]), self.ALLOWED)
        self.assertEqual("Owner's PA what do you think?", named["state"]["current_message"])
        self.assertTrue(named["reachable"])
        bare = harness.real_case(self.item(message="<@UPA> can u filter out me", derived="tag-mention-removed"),
                                 self.ALLOWED)
        self.assertEqual("can u filter out me", bare["state"]["current_message"])
        person = harness.real_case(self.item(message="<@USAM|Real Name> do you want to rename it?",
                                             derived="person-mention-to-name", history=[]), self.ALLOWED)
        self.assertNotIn("Real Name", json.dumps(person))
        self.assertIn(person["state"]["current_message"].split(" ")[0], harness.PSEUDONYMS)
        self.assertFalse(person["reachable"])
        self.assertNotEqual(named["id"], harness.real_case(self.item(message="<@UPA> what do you think?",
                            derived="tag-mention-removed", history=[]), self.ALLOWED)["id"])

    def test_stand_in_person_plays_the_tag_and_names_are_swapped(self) -> None:
        item = {"channel": "CALLOWED", "ts": "7.0", "requester": "UANN", "source": "E-stand-in",
                "derived": "stand-in", "assistant": {"user_id": "UBEN", "name": "Tag"},
                "names": {"UBEN": ["Benjamin", "Ben"], "UANN": ["Annabel"], "UCAT": ["Cat"]},
                "history": [{"user": "UANN", "text": "Ben, can you look at the invoice?"},
                            {"user": "UBEN", "text": "sure, Annabel, sending it to Cat too"}],
                "message": "thanks Benjamin, also check the March one",
                "expected": True, "confidence": "high", "category": "continuation"}
        case = harness.real_case(item, self.ALLOWED)
        stored = json.dumps(case)
        for real in ("Benjamin", "Ben,", "Annabel", "Cat ", "UBEN", "UANN"):
            self.assertNotIn(real, stored)
        self.assertEqual("thanks Tag, also check the March one", case["state"]["current_message"])
        self.assertEqual(["requester", "tag"], [h["author"] for h in case["state"]["thread_history"]])
        self.assertEqual("Tag, can you look at the invoice?", case["state"]["thread_history"][0]["text"])
        self.assertEqual(("E-stand-in", "stand-in"), (case["source"], case["derived"]))
        self.assertTrue(case["reachable"])
        for changes in ({"source": "A-tag-channels"}, {"requester": "UBEN"}, {"derived": None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                harness.real_case(item | changes, self.ALLOWED)

    def test_import_writes_private_files_and_loads_as_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "real/cases-a.json"
            added, errors = harness.import_real([self.item(), self.item(channel="CSECRET")], out, self.ALLOWED)
            self.assertEqual((1, 1), (added, len(errors)))
            harness.import_real([self.item(expected=False)], out, self.ALLOWED)  # Same ID replaces.
            if os.name != "nt":  # Windows has no POSIX permission bits.
                self.assertEqual(0o700, out.parent.stat().st_mode & 0o777)
                self.assertEqual(0o600, out.stat().st_mode & 0o777)
            _, cases = harness.load_cases(out)
            self.assertEqual(1, len(cases))
            self.assertFalse(cases[0].expected)
            self.assertTrue(cases[0].reachable)


class OperatingPointTests(unittest.TestCase):
    def test_precision_at_recall_and_recall_at_precision(self) -> None:
        expected = {str(i): i < 10 for i in range(20)}
        # Positives score 0.55-0.99, negatives 0.0-0.6: one negative overlaps at 0.6.
        rows = [{"id": str(i), "outcome": "score", "probability": 0.55 + i * 0.045 if i < 10 else (i - 10) / 15}
                for i in range(20)]
        point = harness.operating_points(expected, rows)
        self.assertEqual(1.0, point["precision_at_recall"])
        self.assertEqual(0.8, point["recall_at_precision"])  # At 0.6 the overlapping negative makes 8/9.
        self.assertEqual(0.61, point["best_threshold"])
        none = harness.operating_points({"a": True, "b": False}, [{"id": "b", "outcome": "score", "probability": 0.9}])
        self.assertEqual((0.0, None), (none["recall_at_precision"], none["best_threshold"]))


class RuleTests(unittest.TestCase):
    @staticmethod
    def state(message: str, *authors: str, name: str = "Tag") -> dict:
        return {"assistant": {"name": name}, "current_message": message,
                "thread_history": [{"author": author, "text": "x"} for author in authors]}

    def test_r1_names_the_tag_outside_code(self) -> None:
        self.assertTrue(harness.rule_name(self.state("Tag, thoughts?")))
        self.assertTrue(harness.rule_name(self.state("what does iris's tag say", name="Iris's Tag")))
        self.assertFalse(harness.rule_name(self.state("run `tag --help`")))
        self.assertFalse(harness.rule_name(self.state("ask Sam")))

    def test_r2_needs_tag_last_and_the_requester_talking_to_it(self) -> None:
        r2 = harness.rule_continuation
        self.assertTrue(r2(self.state("shorter", "requester", "tag")))
        self.assertTrue(r2(self.state("and casual", "requester", "tag", "requester")))  # Requester's own run.
        self.assertFalse(r2(self.state("ok", "tag")))  # Requester never spoke to Tag.
        self.assertFalse(r2(self.state("ok", "participant", "tag")))  # Tag answered someone else.
        self.assertFalse(r2(self.state("sure", "requester", "tag", "participant")))  # Moved on.
        self.assertFalse(r2(self.state("ship", "requester", "tag", "another_bot")))
        self.assertFalse(r2(self.state("hi")))

    def test_rules_table_counts_and_classifier_only_types(self) -> None:
        cases = [
            harness.Case("named", "x", True, {}, [], self.state("Tag, help")),
            harness.Case("continued", "x", True, {}, [], self.state("more", "requester", "tag")),
            harness.Case("anyone", "x", True, {}, [], self.state("does anyone know?")),
            harness.Case("implicit", "x", True, {}, [], self.state("and the rest?", "tag", "participant")),
            harness.Case("aside", "x", False, {}, [], self.state("Tag is great")),
            harness.Case("chat", "x", False, {}, [], self.state("lunch?", "participant")),
        ]
        scores = [{"id": i, "outcome": "score", "probability": p} for i, p in
                  (("named", 0.99), ("continued", 0.2), ("anyone", 0.96), ("implicit", 0.92), ("aside", 0.1), ("chat", 0.93))]
        table = harness.rules_table(cases, "Tag", scores)
        self.assertEqual({"precision": 2 / 3, "recall": 0.5, "tp": 2, "fp": 1, "fn": 2}, table["R1 or R2"])
        self.assertEqual((4, 2), (table["rules or classifier @0.9"]["tp"], table["rules or classifier @0.9"]["fp"]))
        self.assertEqual({"inclusive": 1, "implicit address": 1}, table["classifier-only true positives @0.9"])
        self.assertEqual(1, table["classifier-only false positives @0.9"])
        self.assertEqual({"inclusive": 1}, table["classifier-only true positives @0.95"])


class ChimeInSetTests(unittest.TestCase):
    def test_chime_in_set_loads_with_attachments_mentions_and_every_post_reachable(self) -> None:
        name, cases = harness.load_cases(harness.FIXTURES.with_name("chime-in-synthetic.json"))
        self.assertGreaterEqual(len(cases), 70)
        self.assertTrue(all(case.reachable for case in cases))
        positives = sum(case.expected for case in cases)
        self.assertTrue(20 <= positives <= len(cases) // 2)
        video = next(case for case in cases if case.id == "chime-launch-video-only")
        state = harness.case_state(video, name)
        self.assertEqual("video", state["current_attachments"][0]["kind"])
        thanks = next(case for case in cases if case.id == "chime-team-thanks")
        self.assertIn("<@UPSOMEONE>", thanks.event["text"])
        categories = {case.category for case in cases if not case.expected}
        self.assertTrue({"incident", "sensitive", "request", "addressed", "status"} <= categories)


class ChimeImportTests(unittest.TestCase):
    def test_real_posts_are_scrubbed_described_and_kept_private(self) -> None:
        allowed = frozenset({"C1"})
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "real" / "chime.json"
            added, errors = harness.import_chime([
                {"channel": "C1", "ts": "1.5", "text": "Thanks <@U123|Ann> for shipping <https://app.test/x|this> <!here>",
                 "expected": True, "confidence": "high", "category": "win",
                 "files": [{"mimetype": "video/mp4", "title": "demo", "url_private": "https://files.slack.com/s"}]},
                {"channel": "CSECRET", "ts": "2.0", "text": "x", "expected": False, "confidence": "high",
                 "category": "aside"},
                {"channel": "C1", "ts": "3.0", "text": "", "expected": False, "confidence": "low", "category": "aside"},
            ], out, allowed)
            self.assertEqual((1, 2), (added, len(errors)))
            data = json.loads(out.read_text(encoding="utf-8"))
            [case] = data["cases"]
            self.assertEqual("Thanks {participant} for shipping <link> {group}", case["message"])
            self.assertNotIn("Ann", json.dumps(data))
            self.assertNotIn("files.slack.com", json.dumps(data))
            self.assertEqual("video", case["attachments"][0]["kind"])
            if os.name != "nt":
                self.assertEqual(0o600, out.stat().st_mode & 0o777)
                self.assertEqual(0o700, out.parent.stat().st_mode & 0o777)
            _name, cases = harness.load_cases(out)
            self.assertEqual("<@UPSOMEONE>", cases[0].event["text"].split()[1])


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

    def test_key_file_must_be_owner_only_and_samples_are_small(self) -> None:
        name, cases = harness.load_cases()
        with tempfile.TemporaryDirectory() as directory, patch.object(harness, "score_run", return_value=0.0) as run, \
                patch("sys.stderr", io.StringIO()) as err:
            key = Path(directory) / "key"
            key.write_text("sk-SECRET\n", encoding="utf-8")
            key.chmod(0o644)
            base = ["score", "--provider", "openai-decisions", "--out", str(Path(directory) / "s.jsonl"),
                    "--max-usd", "0.5", "--confirm-paid", "--key-file", str(key)]
            if os.name != "nt":  # Owner-only is a POSIX check.
                with self.assertRaises(SystemExit):
                    harness.main(base)
                run.assert_not_called()
            key.chmod(0o600)
            self.assertEqual(0, harness.main([*base, "--limit", "4"]))
            self.assertEqual("sk-SECRET", run.call_args.kwargs["api_key"])
            self.assertEqual(4, len(run.call_args.args[0]))
            self.assertEqual(0, harness.main([*base, "--cases", "direct-greeting,offer-declined"]))
            self.assertEqual(["direct-greeting", "offer-declined"], [c.id for c in run.call_args.args[0]])
            with self.assertRaises(SystemExit):
                harness.main([*base, "--cases", "nope"])
            self.assertNotIn("SECRET", err.getvalue())

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
        # A quota failure with another status is recognised from the body, which is never printed.
        error = watch_classifier.ClassifierError("failed")
        error.__cause__ = urllib.error.HTTPError(
            "https://example.test", 400, "x", None,
            io.BytesIO(b'{"error":{"code":"insufficient_quota","message":"key sk-SECRET"}}'))
        scorer = MagicMock(side_effect=error)
        log = io.StringIO()
        name, all_cases = harness.load_cases()
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(harness.BudgetStop) as stopped:
            harness.score_run(all_cases[:3], name, provider="openai-decisions", api_key="sk-SECRET",
                              out_path=Path(directory) / "scores.jsonl", max_usd=1.0, log=log, scorer=scorer)
        self.assertEqual(1, scorer.call_count)
        self.assertNotIn("SECRET", str(stopped.exception) + log.getvalue())
        # Other failures are recorded, and three in a row stop the run.
        scorer = MagicMock(side_effect=[
            watch_classifier.ClassifierError("timed out"), watch_classifier.Score(0.5, "m", 10),
            *[watch_classifier.ClassifierError("timed out")] * 3])
        name, all_cases = harness.load_cases()
        with tempfile.TemporaryDirectory() as directory:
            out_path = Path(directory) / "scores.jsonl"
            with self.assertRaisesRegex(harness.BudgetStop, "3 errors in a row"):
                harness.score_run(all_cases[:6], name, provider="openai-decisions", api_key="k",
                                  out_path=out_path, max_usd=1.0, log=io.StringIO(), scorer=scorer)
            self.assertEqual(5, scorer.call_count)
            outcomes = [json.loads(line)["outcome"] for line in out_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(["error", "score", "error", "error", "error"], outcomes)

    def test_parallel_runs_reserve_cost_before_each_call(self) -> None:
        import threading
        name, cases = harness.load_cases()
        lock, active, peak = threading.Lock(), [0], [0]

        def scorer(*_args, **_kwargs):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            threading.Event().wait(0.01)
            with lock:
                active[0] -= 1
            return watch_classifier.Score(0.5, "gpt-6-luna", 1_000_000)  # $0.10 a call.

        with tempfile.TemporaryDirectory() as directory:
            out_path = Path(directory) / "scores.jsonl"
            with self.assertRaisesRegex(harness.BudgetStop, "Stopped before"):
                harness.score_run(cases[:12], name, provider="openai-decisions", api_key="k", out_path=out_path,
                                  max_usd=0.35, log=io.StringIO(), scorer=scorer, workers=4)
            rows = out_path.read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(peak[0], 4)
        # The first 4 go out on small estimates; after that each reservation is the largest call ($0.10).
        self.assertLessEqual(len(rows), 6)
        self.assertLess(sum(json.loads(r)["usd"] for r in rows), 0.65)
        with self.assertRaises(ValueError):
            harness.score_run(cases[:1], name, provider="openai-decisions", api_key="k", out_path=out_path,
                              max_usd=1, log=io.StringIO(), scorer=scorer, workers=5)

    def test_the_question_is_a_parameter_and_resumes_separately(self) -> None:
        name, cases = harness.load_cases()
        scorer = MagicMock(return_value=watch_classifier.Score(0.8, "jev-1.13.0", 10))
        with tempfile.TemporaryDirectory() as directory:
            out_path = Path(directory) / "scores.jsonl"
            harness.score_run(cases[:2], name, provider="typesafe-jev", api_key="k", out_path=out_path,
                              max_usd=1, log=io.StringIO(), scorer=scorer)
            self.assertNotIn("questions", scorer.call_args.kwargs)
            harness.score_run(cases[:2], name, provider="typesafe-jev", api_key="k", out_path=out_path,
                              max_usd=1, log=io.StringIO(), scorer=scorer, question="invites_reactions")
            self.assertEqual((watch_classifier.CHIME_IN,), scorer.call_args.kwargs["questions"])
            self.assertEqual(4, scorer.call_count)  # Addressed scores don't count as chime-in scores.
            rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([None, None, "invites_reactions", "invites_reactions"], [r.get("question") for r in rows])
            with self.assertRaises(ValueError):
                harness.score_run(cases[:1], name, provider="typesafe-jev", api_key="k", out_path=out_path,
                                  max_usd=1, log=io.StringIO(), scorer=scorer, question="invites_reactions",
                                  variant="few-shot")
        out = io.StringIO()
        harness.plan(cases[:2], name, out, "invites_reactions")
        self.assertIn("typesafe-jev: 2 calls", out.getvalue())

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
