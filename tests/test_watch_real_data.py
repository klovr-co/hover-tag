from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from scripts import watch_eval, watch_real_data as data

BOTS = {"UPA": "Owner's PA", "UOTHERTAG": "Sam's Tag"}


def first_pass(ts: str, message: str, *, derived=None, expected=True, history=None) -> dict:
    """A case as the first labeller imported it, with shortened history."""
    return watch_eval.real_case({
        "channel": "CALLOWED", "ts": ts, "requester": "UOWNER", "assistant": {"user_id": "UPA", "name": "Owner's PA"},
        "bots": ["UOTHERTAG"], "history": history or [], "message": message, "derived": derived,
        "expected": expected, "confidence": "high", "category": "continuation"}, frozenset({"CALLOWED"}))


class RuleTests(unittest.TestCase):
    def test_noise_and_test_traffic(self) -> None:
        for text in ("<@U1|Ann> has joined the channel", "set the channel topic: launch", "started a huddle",
                     "<https://x.test|x.test>", ":tada: :+1:", "🎉", "", "<@U1>"):
            self.assertIsNotNone(data.is_noise(text), text)
        self.assertIsNone(data.is_noise("can we ship today?"))
        for text in ("retest 13", "2+2", "Steer test", "queue test please", "test", "testing 123"):
            self.assertTrue(data.is_test(text), text)
        for text in ("we should test the release", "the latest test results look good", "what's 2+2 in base 3"):
            self.assertFalse(data.is_test(text), text)

    def test_flags_are_raised_not_dropped(self) -> None:
        self.assertIn("code or log", data.flags("```\nx = 1\n```"))
        self.assertIn("code or log", data.flags("Traceback (most recent call last):\n  File x"))
        self.assertIn("long document", data.flags("word " * 400))
        self.assertIn("non-English", data.flags("这个可以吗 明天再说"))
        self.assertIn("non-English", data.flags("boleh tak kita buat ini"))
        self.assertIn("mostly scrubbed", data.flags("<link> <link> ok"))
        self.assertEqual([], data.flags("can you summarise the thread so far?"))


class RebuildTests(unittest.TestCase):
    def thread(self, count: int = 14) -> list[dict]:
        messages = [{"index": 0, "ts": "100.0", "user": "UOWNER", "text": "<@UPA> draft the launch note"}]
        for index in range(1, count):
            user = "UPA" if index % 2 else "UOWNER"
            text = ("Long reply " + "x" * 3000) if user == "UPA" else f"follow-up {index}"
            messages.append({"index": index, "ts": f"{100 + index}.0", "user": user, "text": text, "bot": user == "UPA"})
        messages.append({"index": count, "ts": f"{100 + count}.0", "user": "UGITHUB", "text": "CI passed", "bot": True})
        messages.append({"index": count + 1, "ts": f"{101 + count}.0", "user": "UOWNER", "text": "<@UPA> and shorter"})
        return messages

    def test_restores_full_context_with_the_production_window(self) -> None:
        thread = self.thread()
        current = thread[-1]
        case = first_pass(current["ts"], current["text"], derived="tag-mention-removed",
                          history=[{"user": "UPA", "text": "Long reply x…"}])
        done, removed = {}, Counter()
        self.assertEqual(1, data.rebuild({case["id"]: case}, [{"channel": "CALLOWED", "thread": True, "messages": thread}],
                                         BOTS, done, removed))
        history = done[case["id"]]["state"]["thread_history"]
        self.assertEqual(10, len(history))
        self.assertIn("draft the launch note", history[0]["text"])  # The parent stays.
        self.assertEqual(2000, max(len(h["text"]) for h in history))  # Production's limit, not the shortened copy.
        self.assertNotIn("CI passed", json.dumps(history))
        self.assertEqual(1, removed["integration post in history"])
        self.assertEqual("and shorter", done[case["id"]]["state"]["current_message"])
        self.assertNotIn("UOWNER", json.dumps(done))

    def test_drops_cases_whose_thread_dump_is_incomplete(self) -> None:
        thread = self.thread()
        current = thread[-1]
        case = first_pass(current["ts"], current["text"], derived="tag-mention-removed")
        gappy = [m for m in thread if m["index"] not in {11, 12}]
        done, removed = {}, Counter()
        self.assertEqual(0, data.rebuild({case["id"]: case}, [{"channel": "CALLOWED", "thread": True, "messages": gappy}],
                                         BOTS, done, removed))
        self.assertEqual(1, removed["thread context incomplete"])
        no_parent = [m for m in thread if m["index"] != 0]
        data.rebuild({case["id"]: case}, [{"channel": "CALLOWED", "thread": True, "messages": no_parent}], BOTS, done, removed)
        self.assertEqual({}, done)
        # A tail run long enough to fill the window is enough, even without the early replies.
        tail = [thread[0], *thread[-12:]]
        self.assertEqual(1, data.rebuild({case["id"]: case}, [{"channel": "CALLOWED", "thread": True, "messages": tail}],
                                         BOTS, done, removed))

    def test_refuses_channels_outside_the_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "allowed-channels.txt").write_text("CALLOWED\n", encoding="utf-8")
            (root / "bots.json").write_text(json.dumps({"conversational": BOTS}), encoding="utf-8")
            case = first_pass("5.0", "Owner's PA, what do you think?")
            (root / "cases-A.json").write_text(json.dumps({"cases": [case]}), encoding="utf-8")
            dumps = [{"channel": "CSECRET", "thread": False, "messages": [{"ts": "5.0", "user": "UOWNER", "text": "x"}]}]
            with patch("sys.stdin", io.StringIO(json.dumps(dumps))), patch("sys.stderr", io.StringIO()) as err:
                data.main(["--dir", str(root), "rebuild"])
            self.assertIn("Refused 1", err.getvalue())
            self.assertEqual({}, json.loads((root / "work/rebuilt.json").read_text(encoding="utf-8"))["cases"])
            if os.name != "nt":  # Windows has no POSIX permission bits.
                self.assertEqual(0o600, (root / "work/rebuilt.json").stat().st_mode & 0o777)


class CleanAndAgreeTests(unittest.TestCase):
    def rebuilt(self, ts: str, message: str, **changes) -> dict:
        case = first_pass(ts, message, derived=changes.pop("derived", None))
        return case | {"group": changes.pop("group", ts), "current_raw_noise": None, "current_test": False,
                       "history_test": False, "flags": []} | changes

    def test_clean_drops_by_rule_and_separates_flags(self) -> None:
        cases = [
            self.rebuilt("1", "Owner's PA what do you think about the plan?"),
            self.rebuilt("1", "Owner's PA what do you think about the plan?", derived="tag-mention-to-name"),
            self.rebuilt("2", "Owner's PA what do you think about the plan?"),
            self.rebuilt("3", "Owner's PA what do you think about the plans??"),
            self.rebuilt("4", "retest 13", current_test=True),
            self.rebuilt("5", "<link>", current_raw_noise="empty or link only"),
            self.rebuilt("6", "Owner's PA can you check this log", flags=["code or log"]),
            self.rebuilt("7", "Owner's PA, summarise the incident"),
        ]
        kept, flagged, removed = data.clean(cases, [c["id"] for c in cases] + ["r-missing"])
        self.assertEqual(["1", "7"], [c["group"] for c in kept])
        self.assertIsNone(kept[0]["derived"])  # The natural variant wins.
        self.assertEqual(["6"], [c["group"] for c in flagged])
        self.assertEqual({f"A-tag-channels: {rule}": 1 for rule in (
            "context could not be rebuilt", "test traffic", "current message: empty or link only",
            "same message reused across cases", "exact duplicate", "near duplicate")}, removed)

    def test_conflicting_labels_for_one_message_drop_every_copy(self) -> None:
        cases = [self.rebuilt("1", "Tag what now?", group="g", expected=True),
                 self.rebuilt("1b", "Tag what now?", group="g", expected=False, derived="tag-mention-removed"),
                 self.rebuilt("2", "Tag any update?", group="h")]
        kept, _, removed = data.clean(cases, [c["id"] for c in cases])
        self.assertEqual(["h"], [c["group"] for c in kept])
        self.assertEqual(2, sum(v for k, v in removed.items() if k.endswith("same message with conflicting labels")))

    def test_agreement_kappa_and_review_lists(self) -> None:
        cases = [self.rebuilt(str(i), f"Owner's PA question {i}", expected=i < 6) for i in range(10)]
        cases[9]["derived"] = "tag-mention-removed"
        labels = {c["id"]: {"id": c["id"], "expected": c["expected"], "confidence": "high"} for c in cases}
        labels[cases[0]["id"]]["expected"] = False  # Disagreement.
        labels[cases[1]["id"]]["unclear"] = True
        labels[cases[9]["id"]]["derived_natural"] = False
        labels[cases[2]["id"]]["confidence"] = "low"
        del labels[cases[8]["id"]]
        result = data.agree(cases, labels)
        self.assertEqual(7, result["compared"])
        self.assertEqual([cases[0]["id"]], result["disagreements"])
        self.assertEqual([cases[2]["id"]], result["borderline"])
        self.assertAlmostEqual(6 / 7, result["agreement"])
        self.assertEqual({"no second label": 1, "meaning lost in scrubbing or missing context": 1,
                          "unnatural derived message": 1}, result["removed"])
        self.assertAlmostEqual(data.kappa([True, True, False, False], [True, False, False, False]), 0.5)
        self.assertEqual(1.0, data.kappa([True, False], [True, False]))

    def test_blind_hides_the_first_labels(self) -> None:
        blinded = data.blind([self.rebuilt("1", "Owner's PA hi", reason="greeting", category="direct")])[0]
        self.assertEqual({"id", "assistant", "thread_history", "current_message", "derived"}, set(blinded))


if __name__ == "__main__":
    unittest.main()
