from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import slack_search


class FakeSlack:
    """Answers the Web API calls slack_search makes, recording them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.history = {
            "C1": [{"ts": "1760000003.000100", "user": "U1", "text": "Launch moved to Monday"},
                   {"ts": "1760000002.000100", "user": "U2", "text": "Lunch is at noon", "reply_count": 1,
                    "latest_reply": "1760000004.000100"},
                   {"ts": "1760000001.000100", "user": "U1", "text": "The launch checklist is ready"}],
            "G2": [{"ts": "1760000005.000100", "user": "U3", "text": "Private launch budget is 40k"}],
        }
        self.replies = {"1760000002.000100": [{"ts": "1760000002.000100", "text": "parent"},
                                              {"ts": "1760000004.000100", "user": "U2", "text": "Launch party after lunch"}]}
        self.search = {"ok": True, "results": {"messages": [
            {"channel_id": "C9", "channel_name": "random", "message_ts": "1760000006.000100", "author_user_id": "U4",
             "content": "Launch photos are up", "permalink": "https://example.slack.com/archives/C9/p1760000006000100"}]}}

    def __call__(self, method: str, parameters: dict, *, post: bool = False) -> dict:
        self.calls.append((method, dict(parameters)))
        if method == "conversations.history":
            return {"ok": True, "messages": self.history[parameters["channel"]], "has_more": False}
        if method == "conversations.replies":
            return {"ok": True, "messages": self.replies.get(parameters["ts"], [])}
        if method == "conversations.info":
            return {"ok": True, "channel": {"name": {"C1": "launch", "G2": "leadership"}.get(parameters["channel"], "x")}}
        if method == "assistant.search.context":
            if isinstance(self.search, Exception):
                raise self.search
            return self.search
        raise AssertionError(method)


class SlackSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.slack = FakeSlack()
        environment = patch.dict(os.environ, {
            "SLACK_BOT_TOKEN": "xoxb-test", "SLACK_TEAM_ID": "T1", "OPENTAG_CURRENT_CHANNEL_ID": "C1",
            "TMPDIR": str(self.tmp), "TMP": str(self.tmp), "TEMP": str(self.tmp)}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        patch.object(slack_search.tempfile, "gettempdir", return_value=str(self.tmp)).start()
        patch.object(slack_search.Slack, "call", side_effect=self.slack).start()
        self.addCleanup(patch.stopall)

    def grant(self, request: str, *, public: bool = True) -> None:
        os.environ["OPENTAG_SLACK_SEARCH_GRANT"] = json.dumps({
            "mode": "all", "request_text": request, "public_search": public,
            "channels": [{"id": "C1", "name": "launch", "private": False},
                         {"id": "G2", "name": "leadership", "private": True}]})

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = slack_search.run(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_the_current_conversation_is_read_live_including_threads(self) -> None:
        code, out, _ = self.run_cli("search", "launch", "--json")
        self.assertEqual(0, code)
        results = json.loads(out)["results"]
        self.assertEqual({"Launch moved to Monday", "The launch checklist is ready", "Launch party after lunch"},
                         {r["text"] for r in results})
        reply = next(r for r in results if r["text"] == "Launch party after lunch")
        self.assertEqual("1760000002.000100", reply["thread_ts"])
        self.assertIn("thread_ts=1760000002.000100", reply["link"])
        self.assertEqual({"conversations.history", "conversations.replies", "conversations.info"},
                         {method for method, _ in self.slack.calls})

    def test_every_word_must_match(self) -> None:
        _, out, _ = self.run_cli("search", "launch monday", "--json")
        self.assertEqual(["Launch moved to Monday"], [r["text"] for r in json.loads(out)["results"]])
        _, out, _ = self.run_cli("search", "nothing like this")
        self.assertIn("No matching messages", out)

    def test_named_channels_must_be_named_in_the_request_and_allowed(self) -> None:
        self.grant("search #leadership for the budget")
        code, out, _ = self.run_cli("search", "budget", "--channel", "leadership", "--json")
        self.assertEqual(0, code)
        self.assertEqual(["Private launch budget is 40k"], [r["text"] for r in json.loads(out)["results"]])
        code, _, err = self.run_cli("search", "budget", "--channel", "launch")
        self.assertEqual(2, code)
        self.assertIn("isn't one of the channels this request may read", err)
        os.environ.pop("OPENTAG_SLACK_SEARCH_GRANT")
        self.assertEqual(2, self.run_cli("search", "budget", "--channel", "leadership")[0])

    def test_all_of_slack_adds_public_channels_with_the_action_token(self) -> None:
        self.grant("search all channels for launch news")
        os.environ["OPENTAG_SLACK_ACTION_TOKEN"] = "action-1"
        code, out, _ = self.run_cli("search", "launch", "--all", "--json")
        self.assertEqual(0, code)
        texts = {r["text"] for r in json.loads(out)["results"]}
        self.assertIn("Launch photos are up", texts)
        self.assertIn("Private launch budget is 40k", texts)
        method, parameters = next(call for call in self.slack.calls if call[0] == "assistant.search.context")
        self.assertEqual(("action-1", "public_channel", "messages"),
                         (parameters["action_token"], parameters["channel_types"], parameters["content_types"]))
        self.assertLessEqual(int(parameters["limit"]), 20)

    def test_all_of_slack_needs_the_persons_own_words(self) -> None:
        self.grant("what did we decide about launch")
        code, _, err = self.run_cli("search", "launch", "--all")
        self.assertEqual(2, code)
        self.assertIn("only when the person asked", err)

    def test_without_an_action_token_or_for_a_guest_public_search_is_skipped_and_said(self) -> None:
        self.grant("search all channels for launch news")
        code, out, _ = self.run_cli("search", "launch", "--all")
        self.assertEqual(0, code)
        self.assertIn("needs a request from Slack itself", out)
        self.grant("search all channels for launch news", public=False)
        os.environ["OPENTAG_SLACK_ACTION_TOKEN"] = "action-1"
        _, out, _ = self.run_cli("search", "launch", "--all")
        self.assertIn("doesn't let apps search for guests", out)
        self.assertNotIn("assistant.search.context", [method for method, _ in self.slack.calls])

    def test_a_missing_search_permission_is_explained(self) -> None:
        self.grant("search all channels for launch news")
        os.environ["OPENTAG_SLACK_ACTION_TOKEN"] = "action-1"
        self.slack.search = slack_search.SlackAPIError("assistant.search.context", "missing_scope")
        _, out, _ = self.run_cli("search", "launch", "--all")
        self.assertIn("search:read.public", out)

    def test_public_searches_stay_inside_slacks_limit(self) -> None:
        for _ in range(slack_search.RTS_LIMIT_PER_MINUTE):
            slack_search.reserve_search_call(now=1000.0)
        with self.assertRaisesRegex(slack_search.SearchError, "about 10 searches a minute"):
            slack_search.reserve_search_call(now=1010.0)
        slack_search.reserve_search_call(now=1061.0)  # A minute later there is room again.

    def test_nothing_from_slack_is_written_to_disk(self) -> None:
        self.grant("search all channels for launch news")
        os.environ["OPENTAG_SLACK_ACTION_TOKEN"] = "action-1"
        self.run_cli("search", "launch", "--all")
        self.run_cli("history")
        written = [path for path in self.tmp.rglob("*") if path.is_file()]
        self.assertEqual([slack_search.limiter_path()], written)
        self.assertNotIn("Launch", slack_search.limiter_path().read_text(encoding="utf-8"))

    def test_history_reads_the_conversation_oldest_first(self) -> None:
        code, out, _ = self.run_cli("history", "--json")
        self.assertEqual(0, code)
        self.assertEqual(["The launch checklist is ready", "Lunch is at noon", "Launch moved to Monday"],
                         [r["text"] for r in json.loads(out)["results"]])

    def test_outside_a_tag_run_it_refuses(self) -> None:
        os.environ.pop("OPENTAG_CURRENT_CHANNEL_ID")
        code, _, err = self.run_cli("search", "launch")
        self.assertEqual(2, code)
        self.assertIn("only available inside a Tag's Slack run", err)


if __name__ == "__main__":
    unittest.main()
