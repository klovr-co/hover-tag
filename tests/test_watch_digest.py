from __future__ import annotations

import unittest

from scripts import watch_digest as digest


class DigestRuleTests(unittest.TestCase):
    def verdict(self, text: str, files=None):
        return digest.classify(text, files)

    def test_library_shares_are_digested(self) -> None:
        baidu = self.verdict("ASLEEP 工作汇总 <https://pan.baidu.com/s/1abcDEF|pan.baidu.com/s/1abcDEF> 提取码: ab12")
        self.assertEqual((True, "library share"), (baidu.digest, baidu.reason))
        self.assertTrue(baidu.access_code)
        self.assertEqual(("baidu_share",), tuple(l.type for l in baidu.links))
        drive = self.verdict("all the materials https://drive.google.com/drive/folders/1XYZ?usp=sharing")
        self.assertEqual(("drive_folder", True), (drive.links[0].type, drive.digest))
        files = self.verdict("specs for FLOAT", [{"mimetype": "application/pdf"}])
        self.assertTrue(files.digest)

    def test_plain_link_posts_are_digested(self) -> None:
        post = self.verdict("check this out <https://x.com/someone/status/123|x.com>")
        self.assertEqual(("x_post", True, "link share"), (post.links[0].type, post.digest, post.reason))
        self.assertTrue(self.verdict("https://example.com/launch-notes").digest)

    def test_requests_discussions_and_noise_are_skipped(self) -> None:
        cases = {
            "can you look at the bug on https://example.com/page and fix the login flow before the demo tomorrow please":
                "discussion (17 words)",
            "lol https://giphy.com/gifs/abc": "only gif",
            "see <https://klovr.slack.com/archives/C1/p123|this>": "only slack_message",
            "join https://meet.google.com/abc-defg-hij": "only meeting",
            "we should ship on friday": "no link or file",
        }
        for text, reason in cases.items():
            with self.subTest(text=text):
                verdict = self.verdict(text)
                self.assertEqual((False, reason), (verdict.digest, verdict.reason))
        self.assertEqual("single image", self.verdict("lol", [{"mimetype": "image/png"}]).reason)
        self.assertTrue(self.verdict("", [{"mimetype": "image/png"}, {"mimetype": "image/jpeg"}]).digest)

    def test_library_links_allow_a_short_description_and_cjk_counts(self) -> None:
        text = ("Here is the full ASLEEP design library with spec sheets, logos, awards and product folders "
                "for every code, please use it https://pan.baidu.com/s/1x")
        self.assertTrue(self.verdict(text).digest)
        self.assertFalse(self.verdict(text.replace("https://pan.baidu.com/s/1x", "https://example.com/x")).digest)
        self.assertEqual(5, digest._words("工作汇总作業まとめ"))  # 9 characters ≈ 5 words.

    def test_no_full_urls_or_codes_are_kept(self) -> None:
        verdict = self.verdict("https://pan.baidu.com/s/1secretpath 提取码 ab12")
        flat = repr(verdict)
        self.assertNotIn("1secretpath", flat)
        self.assertNotIn("ab12", flat)
        self.assertEqual(("pan.baidu.com",), verdict.hosts)


class HistoryEvalTests(unittest.TestCase):
    def test_features_only_and_metrics(self) -> None:
        allowed = frozenset({"C1"})
        items = [
            {"channel": "C1", "ts": "1.0", "text": "https://pan.baidu.com/s/1secret 提取码 ab12", "worth": True, "confidence": "high"},
            {"channel": "C1", "ts": "2.0", "text": "lol https://giphy.com/x", "worth": False, "confidence": "high"},
            {"channel": "C1", "ts": "3.0", "text": "https://example.com/meme", "worth": False, "confidence": "medium"},
            {"channel": "C1", "ts": "4.0", "text": "here are the long product notes everyone needs to read before we "
                                                    "decide anything about the launch plan https://example.com/notes",
             "worth": True, "confidence": "high"},
        ]
        cases = [digest.history_case(item, allowed) for item in items]
        self.assertNotIn("1secret", repr(cases))
        self.assertNotIn("ab12", repr(cases))
        self.assertNotIn("text", cases[0])
        two = digest.history_case({"channel": "C1", "ts": "5.0", "files": ["image/png", "image/png"], "worth": True,
                                   "confidence": "low"}, allowed)
        self.assertEqual((2, ["image/png"], "library share"), (two["files"], two["file_types"], two["reason"]))
        result = digest.evaluate_history(cases, days=7)
        self.assertEqual((1, 1, 1), (result["tp"], result["fp"], result["fn"]))
        self.assertEqual({"link share": 1}, result["false_positive_reasons"])
        self.assertEqual({"C1": 2.0}, result["digests_per_week"])
        with self.assertRaises(ValueError):
            digest.history_case(dict(items[0], channel="CSECRET"), allowed)


if __name__ == "__main__":
    unittest.main()
