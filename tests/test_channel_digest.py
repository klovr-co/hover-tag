from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

from scripts import channel_digest as digest
from scripts import watch_digest

PUBLIC = lambda host, _port: [(2, 1, 6, "", ("93.184.216.34", 0))]  # noqa: E731
NAMES = ("Tag", "Iris's Tag")


def response(body: bytes, content_type: str = "text/html") -> MagicMock:
    item = MagicMock()
    item.__enter__.return_value = item
    item.headers.get_content_type.return_value = content_type
    item.read.return_value = body
    return item


class TriggerTests(unittest.TestCase):
    def test_shares_pass_and_questions_requests_and_own_links_dont(self) -> None:
        ok = digest.should_digest("<https://x.com/ann/status/1>", [], names=NAMES)
        self.assertIsNotNone(ok)
        for text in ("should I buy this? <https://shop.test/x>", "Tag, read this <https://a.test/x>",
                     "<@UTAG> summarise <https://a.test/x>", "lol <https://giphy.com/x>"):
            self.assertIsNone(digest.should_digest(text, [], names=NAMES, tag_user_id="UTAG"), text)
        own = frozenset({"hover.team"})
        self.assertIsNone(digest.should_digest("<https://www.hover.team/tag>", [], names=NAMES, own_hosts=own))
        self.assertIsNotNone(digest.should_digest("<https://example.com/post>", [], names=NAMES, own_hosts=own))
        self.assertEqual(frozenset({"hover.team", "github.com/klovr-co"}),
                         digest.own_hosts({"OPENTAG_DIGEST_OWN_HOSTS": "www.hover.team, github.com/klovr-co"}))


class FetchTests(unittest.TestCase):
    def test_private_addresses_schemes_ports_and_credentials_are_refused(self) -> None:
        opener = MagicMock()
        for address in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "192.168.1.2"):
            with self.subTest(address=address), self.assertRaisesRegex(digest.FetchError, "private"):
                digest.fetch("https://a.test/", resolve=lambda h, p, a=address: [(2, 1, 6, "", (a, 0))],
                             opener=opener)
        for url in ("file:///etc/passwd", "ftp://a.test/x", "https://u:p@a.test/", "https://a.test:8080/"):
            with self.subTest(url=url), self.assertRaises(digest.FetchError):
                digest.fetch(url, resolve=PUBLIC, opener=opener)
        opener.open.assert_not_called()

    def test_redirects_are_checked_each_hop_and_bodies_are_capped(self) -> None:
        redirect = urllib.error.HTTPError("https://a.test/", 302, "Found", {"Location": "http://127.0.0.1/admin"},
                                          io.BytesIO())
        opener = MagicMock()
        opener.open.side_effect = [redirect]

        def resolve(host, _port):
            return [(2, 1, 6, "", ("127.0.0.1" if host == "127.0.0.1" else "93.184.216.34", 0))]

        with self.assertRaisesRegex(digest.FetchError, "private"):
            digest.fetch("https://a.test/", resolve=resolve, opener=opener)
        opener = MagicMock()
        opener.open.return_value = response(b"x" * 50)
        _url, _type, body = digest.fetch("https://a.test/", resolve=PUBLIC, opener=opener, max_bytes=10)
        self.assertEqual(10, len(body))
        headers = opener.open.call_args.args[0].headers
        self.assertNotIn("Cookie", headers)
        opener.open.side_effect = urllib.error.HTTPError("https://a.test/", 403, "x", {}, io.BytesIO())
        with self.assertRaisesRegex(digest.FetchError, "login"):
            digest.fetch("https://a.test/", resolve=PUBLIC, opener=opener)

    def test_pages_x_posts_and_login_walls(self) -> None:
        page = (b"<html><head><title>Ignored</title><meta property='og:title' content='Launch &amp; notes'>"
                b"<meta name='description' content='What shipped'><script>alert(1)</script></head>"
                b"<body><p>Hello <b>world</b></p></body></html>")
        read = digest.read_link("https://a.test/x", "web_page", fetcher=lambda url, **_: (url, "text/html", page))
        self.assertEqual(("Launch & notes", "What shipped"), (read["title"], read["description"]))
        self.assertNotIn("alert", read["text"])
        oembed = json.dumps({"author_name": "Ann", "html": "<blockquote><p>We launched v2</p></blockquote>"})
        seen = []
        read = digest.read_link("https://x.com/ann/status/1", "x_post",
                                fetcher=lambda url, **_: (seen.append(url), (url, "application/json", oembed.encode()))[1])
        self.assertEqual(("Ann", "We launched v2"), (read["author"], read["text"]))
        self.assertTrue(seen[0].startswith(digest.X_OEMBED))
        wall = b"<html><title>Sign in</title><body>Please log in to continue</body></html>"
        with self.assertRaisesRegex(digest.FetchError, "login wall"):
            digest.read_link("https://a.test/x", "web_page", fetcher=lambda url, **_: (url, "text/html", wall))


class StoreAndDigestTests(unittest.TestCase):
    def test_digest_reads_cheap_things_summarises_once_and_notes_what_it_couldnt_read(self) -> None:
        text = ("<https://x.com/ann/status/1> <https://pan.baidu.com/s/1abc> 提取码 ab12 "
                "<https://blocked.test/a>")
        verdict = watch_digest.classify(text, [])

        def reader(url, kind):
            if "blocked" in url:
                raise digest.FetchError("the site asks for a login")
            return {"kind": kind, "text": "We launched v2"}

        summarize = MagicMock(return_value="Ann launched v2\n- a point")
        entry = digest.digest({"channel": "C1", "ts": "1.0", "user": "UOWNER", "text": text}, verdict,
                              summarize=summarize, reader=reader)
        summarize.assert_called_once()
        content = summarize.call_args.args[0]
        self.assertNotIn("ab12", json.dumps(content))
        self.assertNotIn("1abc", json.dumps(content))
        self.assertIn("not read in this version", json.dumps(content))
        self.assertEqual((["blocked.test"], "Ann launched v2", True),
                         (entry["blocked"], entry["title"], entry["access_code"]))
        self.assertNotIn("ab12", json.dumps(entry))

    def test_store_is_private_searchable_and_keeps_90_days(self) -> None:
        now = [1_000_000_000.0]
        with tempfile.TemporaryDirectory() as directory:
            store = digest.DigestStore(Path(directory) / "state" / "digests.jsonl", clock=lambda: now[0])
            store.add({"channel": "C1", "ts": "1.0", "title": "Launch notes", "summary": "v2 shipped"})
            store.add({"channel": "C2", "ts": "2.0", "title": "Pricing page", "summary": "tiers"})
            store.add({"channel": "C1", "ts": "1.0", "title": "Launch notes again", "summary": "v2 shipped"})
            self.assertEqual(["Launch notes again"], [e["title"] for e in store.search("v2")])
            self.assertTrue(all(e["unverified"] for e in store.search()))
            self.assertEqual(["C2"], [e["channel"] for e in store.search(channel="C2")])
            if os.name != "nt":
                self.assertEqual(0o600, store.path.stat().st_mode & 0o777)
            now[0] += 91 * 24 * 3600
            self.assertEqual([], store.search())
            store.add({"channel": "C3", "ts": "3.0", "title": "New"})
            self.assertEqual(1, len(store.path.read_text(encoding="utf-8").splitlines()))


if __name__ == "__main__":
    unittest.main()
