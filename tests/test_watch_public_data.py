from __future__ import annotations

import json
import unittest

from scripts import watch_eval, watch_public_data as public

LOG = [f"[00:00] <ctx{i}> context {i}" for i in range(1000)] + [
    "[10:00] <alice> anyone know why apt hangs?",          # 1000 start, inclusive
    "[10:01] <bob> alice: check /var/lib/dpkg/lock",        # 1001 -> 1000 (named alice)
    "[10:02] <alice> thanks, that was it",                  # 1002 -> 1001 (continuation to bob)
    "[10:03] <carol> the lock is the usual culprit, like bob said",  # 1003 -> 1000: to alice, bob in third person
    "[10:04] <dave> !language | alice",                     # 1004 bot command
    "[10:05] <erin> heading to lunch",                      # 1005 start, aside
    "[10:06] <bob> alice carol: both fixed?",               # 1006 -> 1002, names two
]
LINKS = ["1000 1000 -", "1000 1001 -", "1001 1002 -", "1000 1003 -", "1004 1004 -", "1005 1005 -", "1002 1006 -"]


class IrcMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.messages = public.parse_log(LOG)
        self.parents, self.starts = public.parse_links(LINKS)
        found = public.candidates("dev/x", self.messages, self.parents, self.starts)
        self.found = {(c["index"], c["assistant"]): c for c in found}

    def test_reply_links_become_labels(self) -> None:
        self.assertEqual(("named", True), self.label(1001, "alice"))
        self.assertEqual(("continuation", True), self.label(1002, "bob"))
        self.assertEqual(("third_person", False), self.label(1003, "bob"))
        self.assertEqual(("continuation", True), self.label(1003, "alice"))
        self.assertEqual(("inclusive", True), self.label(1000, "ctx999"))
        self.assertEqual(("aside_start", False), self.label(1005, "dave"))

    def test_bot_commands_and_double_addressing_are_left_out(self) -> None:
        self.assertFalse([key for key in self.found if key[0] == 1004])
        self.assertNotIn((1006, "carol"), self.found)  # Named at the start alongside alice: ambiguous.

    def test_case_hides_nicks_with_unique_pseudonyms(self) -> None:
        case = public.build_case(self.found[(1003, "bob")], self.messages)
        text = json.dumps(case)
        for nick in ("alice", "bob", "carol"):
            self.assertNotIn(nick, text.lower())
        self.assertIn("like Tag said", case["state"]["current_message"])
        authors = [h["author"] for h in case["state"]["thread_history"]]
        self.assertEqual(["participant"], authors)  # Alice's question; bob isn't in this conversation.
        names = [w.strip(":") for h in case["state"]["thread_history"] for w in h["text"].split()[:1]]
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual("C-public-irc", case["source"])
        self.assertTrue(case["reachable"])  # Names the stand-in Tag.

    def test_one_case_per_message_so_labels_cannot_conflict(self) -> None:
        chosen = public.sample(list(self.found.values()), {s: 99 for s in public.STRATA}, seed=1)
        keys = [(c["source_file"], c["index"]) for c in chosen]
        self.assertEqual(len(keys), len(set(keys)))

    def test_pseudonyms_are_unique_within_a_case(self) -> None:
        names = watch_eval.Pseudonyms("x")
        self.assertEqual(45, len({names(f"user{i}") for i in range(45)}))
        self.assertEqual(names("USER1"), names("user1"))

    def label(self, index: int, assistant: str) -> tuple[str, bool]:
        case = self.found[(index, assistant)]
        return case["stratum"], case["expected"]


if __name__ == "__main__":
    unittest.main()
