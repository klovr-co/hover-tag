"""The Slack bridge keeps each thread's transcript, takes replies and answers from Tag.app,
and closes a waiting question in both places, for Codex and Claude alike."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

try:
    import slack_bolt  # noqa: F401
except ModuleNotFoundError:
    raise unittest.SkipTest("slack_bolt is installed by the Slack bridge runtime")

from scripts import slack_socket_agent, tag_activity, tag_threads
from scripts.tag_threads import ThreadStore

C, TS = "C0LAUNCH1", "1792487642.000100"
BACKENDS = ("codex", "claude")


NAMES = {"U0MAYA001": "Maya", "U0SAM0001": "Sam"}


def slack_client() -> MagicMock:
    client = MagicMock()
    client.users_info.side_effect = lambda user: {"user": {"profile": {"display_name": NAMES.get(user, "")}}}
    client.chat_postMessage.return_value = {"ok": True, "ts": "1792487700.000100"}
    return client


class FakeApp:
    def __init__(self) -> None:
        self.events: dict[str, Any] = {}
        self.actions: dict[str, Any] = {}
        self.views: dict[str, Any] = {}
        self.client = slack_client()
        self.client.auth_test.return_value = {"user_id": "UTAGBOT01", "team_id": "T0KLOVR1"}
        self.logger = MagicMock()

    def event(self, name: str):
        return lambda handler: self.events.__setitem__(name, handler) or handler

    def action(self, name: str):
        return lambda handler: self.actions.__setitem__(name, handler) or handler

    def view(self, name: str):
        return lambda handler: self.views.__setitem__(name, handler) or handler


def finish_replies() -> None:
    """Wait for the replies the bridge started from Tag.app requests."""
    for thread in threading.enumerate():
        if thread.name == "tag-app-reply":
            thread.join(10)


class BridgeTranscriptTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        for module in (slack_socket_agent, tag_activity, tag_threads):
            redirect = patch.object(module, "instance_home", lambda: self.home)
            redirect.start()
            self.addCleanup(redirect.stop)
        environment = patch.dict(os.environ, {
            "TAG_HOME": str(self.home), "SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": C, "SLACK_TEAM_ID": "T0KLOVR1",
            "SLACK_ALLOWED_USER_IDS": "U0MAYA001", "OPENTAG_SLACK_STREAMING": "0", "OPENTAG_WORKDIR": str(self.home),
            "OPENTAG_SLACK_SETTINGS_FILE": str(self.home / "settings.json"),
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        for patcher in (patch.object(slack_socket_agent, "queue_reply_summary"),
                        patch.object(slack_socket_agent, "rich_events_selected", return_value=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(slack_socket_agent.WAITING_OFFERS.clear)
        self.addCleanup(slack_socket_agent.QUEUED_REQUESTS.clear)
        self.addCleanup(slack_socket_agent.PROMPT_HANDLERS.clear)
        self.store = ThreadStore(self.home)

    def make(self, backend: str) -> FakeApp:
        app = FakeApp()
        with patch.object(slack_socket_agent, "App", return_value=app):
            slack_socket_agent.create_app(backend, 30, frozenset({"U0MAYA001"}), thread_store=self.store)
        return app

    def thread_messages(self, _client, channel: str, thread_ts: str, attachment_dir: Path, *, request=None, on_messages=None, **_):
        messages = [
            {"ts": TS, "user": "U0MAYA001", "text": "<@UTAGBOT01> turn the <https://x.example/notes|launch notes> into a checklist"},
            {"ts": "1792487643.000100", "user": "U0SAM0001", "text": "and add owners please"},
        ]
        if on_messages:
            on_messages(messages)
        return "Maya: turn the launch notes into a checklist"

    def mention(self, app: FakeApp, client: MagicMock, backend_result) -> None:
        with patch.object(slack_socket_agent, "build_thread_text", side_effect=self.thread_messages), \
                patch.object(slack_socket_agent, "run_backend_events", side_effect=backend_result):
            app.events["app_mention"]({"channel": C, "ts": TS, "user": "U0MAYA001", "text": "<@UTAGBOT01> turn the notes into a checklist"},
                                      {"team_id": "T0KLOVR1", "authorizations": [{"user_id": "UTAGBOT01", "is_bot": True}]},
                                      client, MagicMock())

    def test_a_run_saves_messages_steps_the_full_answer_and_memory(self) -> None:
        answer = "## Launch checklist\n\n| Item | Owner |\n| --- | --- |\n| Press kit | Maya |\n\nToken xoxb-1234567890-abcdefghijkl stays out."
        for backend in BACKENDS:
            with self.subTest(backend=backend):
                for path in self.store.root.glob("*.jsonl"):
                    path.unlink()
                app = self.make(backend)

                def run(*args, **kwargs):
                    kwargs["on_trace_event"]({"type": "activity_start", "activity_id": "s1", "label": "Searching Slack…",
                                              "details": {"tool": "search", "output": "secret tool output"}})
                    kwargs["on_trace_event"]({"type": "activity_complete", "activity_id": "s1", "status": "completed"})
                    kwargs["memory_receipts"].write_text('{"action": "saved", "scope": "C0LAUNCH1", "key": "launch-owner", '
                                                         '"version": "3f2a9c1b7d4e", "text": "Maya owns the launch."}\n', encoding="utf-8")
                    return answer, True

                self.mention(app, slack_client(), run)
                thread = self.store.read(C, TS)
                kinds = [item["type"] for item in thread["items"]]
                self.assertEqual(kinds, ["message", "message", "work", "answer"])
                first, second, work, reply = thread["items"]
                self.assertEqual((first["author"]["name"], second["author"]["name"]), ("Maya", "Sam"))
                self.assertIn("[launch notes](https://x.example/notes)", first["text"])
                self.assertEqual(work["status"], "completed")
                self.assertEqual([(s["label"], s["status"]) for s in work["steps"]], [("Searching Slack…", "completed")])
                self.assertIn("| Press kit | Maya |", reply["text"])
                self.assertNotIn("xoxb-1234567890", reply["text"])
                self.assertEqual(reply["memory"][0]["key"], "launch-owner")
                raw = self.store.path(C, TS).read_text(encoding="utf-8")
                self.assertNotIn("secret tool output", raw)
                self.assertNotIn("Maya: turn the launch notes", raw, "the prompt sent to the agent is never stored")

    def test_a_reply_from_the_app_stays_out_of_slack_unless_asked(self) -> None:
        for backend in BACKENDS:
            for post in (False, True):
                with self.subTest(backend=backend, post_to_slack=post):
                    for path in self.store.root.glob("*.jsonl"):
                        path.unlink()
                    app = self.make(backend)
                    self.store.message(C, TS, ts=TS, author={"id": "U0MAYA001", "name": "Maya"}, text="hello", team="T0KLOVR1")
                    tag_threads.request_reply(self.home, C, TS, "Add the FAQ too.", post_to_slack=post, by="U0MAYA001")
                    seen: list[str] = []

                    def run(_backend, _team, _channel, _thread, _user, question, *args, **kwargs):
                        seen.append(question)
                        return "Added the **FAQ**.", True

                    with patch.object(slack_socket_agent, "build_thread_text", side_effect=self.thread_messages), \
                            patch.object(slack_socket_agent, "run_backend_events", side_effect=run):
                        app.tag_thread_requests()
                        finish_replies()
                    self.assertEqual(seen, ["Add the FAQ too."])
                    in_thread = [call for call in app.client.chat_postMessage.call_args_list if call.kwargs.get("channel") == C]
                    thread = self.store.read(C, TS)
                    mine = next(item for item in thread["items"] if item["type"] == "message" and item["via"] == "app")
                    reply = thread["items"][-1]
                    self.assertEqual((mine["text"], mine["posted_to_slack"]), ("Add the FAQ too.", post))
                    self.assertEqual((reply["type"], reply["text"]), ("answer", "Added the **FAQ**."))
                    if post:
                        self.assertIn("from Tag.app", in_thread[0].kwargs["text"])
                        self.assertEqual(reply["posted_to"], "slack")
                    else:
                        self.assertEqual(in_thread, [], "an app-only reply posts nothing in the Slack thread")
                        self.assertEqual(reply["posted_to"], "app")

    def test_an_approval_waits_in_both_places_and_answering_in_the_app_closes_both(self) -> None:
        for backend in BACKENDS:
            with self.subTest(backend=backend):
                for path in self.store.root.glob("*.jsonl"):
                    path.unlink()
                app = self.make(backend)
                client = slack_client()
                states: list[str] = []

                def run(*args, **kwargs):
                    kwargs["on_approval"]({"approval_id": "appr-1", "label": "Run `rm -rf build`"})
                    states.append(self.store.read(C, TS)["status"])
                    prompt = self.store.read(C, TS)["prompts"][0]
                    tag_threads.request_answer(self.home, C, TS, prompt["prompt_id"], "approve", by="U0MAYA001")
                    with patch.object(slack_socket_agent, "resolve_active_approval", return_value=True) as resolve:
                        app.tag_thread_requests()
                    resolve.assert_called_once()
                    self.assertEqual(resolve.call_args.args[1], "appr-1")
                    self.assertTrue(resolve.call_args.kwargs["approved"])
                    return "Done.", True

                self.mention(app, client, run)
                self.assertEqual(states, ["needs_you"])
                # Slack got the private prompt, then a private note that it was answered in the app.
                self.assertTrue(any("approval" in str(call.kwargs.get("blocks", "")).lower() or call.kwargs.get("blocks")
                                    for call in client.chat_postEphemeral.call_args_list))
                self.assertTrue(any("Answered in Tag.app" in str(call.kwargs.get("text", ""))
                                    for call in app.client.chat_postEphemeral.call_args_list))
                prompt = self.store.read(C, TS)["prompts"][0]
                self.assertEqual((prompt["status"], prompt["answer"]["via"], prompt["answer"]["by"]["name"]), ("answered", "app", "Maya"))
                self.assertNotEqual(self.store.read(C, TS)["status"], "needs_you")

    def test_answering_an_approval_in_slack_closes_it_in_the_app(self) -> None:
        app = self.make("codex")
        prompt = slack_socket_agent.approval_prompt_id("appr-2")
        self.store.open_prompt(team="T0KLOVR1", channel=C, thread_ts=TS, kind="approval", text="Approve?", prompt_id=prompt,
                               choices=[{"id": "approve", "label": "Approve once"}, {"id": "deny", "label": "Deny"}])
        self.assertEqual(self.store.read(C, TS)["status"], "needs_you")
        approve = next(handler for name, handler in app.actions.items() if "approval" in name)
        value = slack_socket_agent.approval_button_blocks(team="T0KLOVR1", channel=C, thread_ts=TS, user_id="U0MAYA001",
                                                          approval_id="appr-2", label="Run tests", backend="codex")
        button = next(e for block in value if block.get("type") == "actions" for e in block["elements"] if "approve" in e.get("action_id", "") or e.get("style") == "primary")
        with patch.object(slack_socket_agent, "resolve_active_approval", return_value=True):
            approve(ack=MagicMock(), client=app.client, logger=MagicMock(), respond=MagicMock(),
                    body={"user": {"id": "U0MAYA001"}, "channel": {"id": C}, "team": {"id": "T0KLOVR1"},
                          "actions": [{"action_id": button["action_id"], "value": button["value"]}]})
        closed = self.store.read(C, TS)["prompts"][0]
        self.assertEqual((closed["status"], closed["answer"]["via"]), ("answered", "slack"))
        self.assertEqual(self.store.read(C, TS)["status"], "done")

    def test_queue_or_steer_shows_in_the_app_and_either_side_closes_it(self) -> None:
        key = slack_socket_agent.RunKey("T0KLOVR1", C, TS)
        self.addCleanup(slack_socket_agent.release_thread, key)
        for side in ("app", "slack"):
            with self.subTest(answered_in=side):
                for path in self.store.root.glob("*.jsonl"):
                    path.unlink()
                slack_socket_agent.WAITING_OFFERS.clear()
                slack_socket_agent.QUEUED_REQUESTS.clear()
                slack_socket_agent.release_thread(key)
                app = self.make("claude")
                client = slack_client()
                self.assertTrue(slack_socket_agent.reserve_thread(key))
                app.events["app_mention"]({"channel": C, "ts": "1792487650.000100", "thread_ts": TS, "user": "U0MAYA001", "text": "<@BOT> also B"},
                                          {"team_id": "T0KLOVR1"}, client, MagicMock())
                prompt = self.store.read(C, TS)["prompts"][0]
                self.assertEqual((prompt["kind"], prompt["status"]), ("queue_steer", "pending"))
                if side == "app":
                    tag_threads.request_answer(self.home, C, TS, prompt["prompt_id"], "queue", by="U0MAYA001")
                    app.tag_thread_requests()
                else:
                    button = client.chat_postEphemeral.call_args.kwargs["blocks"][1]["elements"][0]
                    app.actions[slack_socket_agent.WAITING_QUEUE_ACTION_ID](
                        ack=MagicMock(), client=client, logger=MagicMock(), respond=MagicMock(),
                        body={"user": {"id": "U0MAYA001"}, "channel": {"id": C}, "team": {"id": "T0KLOVR1"},
                              "actions": [{"action_id": slack_socket_agent.WAITING_QUEUE_ACTION_ID, "value": button["value"]}]})
                closed = self.store.read(C, TS)["prompts"][0]
                self.assertEqual((closed["status"], closed["answer"]["choice"], closed["answer"]["via"]), ("answered", "queue", side))
                self.assertEqual(slack_socket_agent.release_thread(key).event["ts"], "1792487650.000100")
                self.assertEqual(slack_socket_agent.WAITING_OFFERS, {}, "the Slack buttons stop working once answered")

    def test_a_request_made_while_the_tag_was_stopped_is_dropped_with_a_note(self) -> None:
        app = self.make("codex")
        self.store.message(C, TS, ts=TS, author={"id": "U0MAYA001", "name": "Maya"}, text="hello")
        tag_threads.request_reply(self.home, C, TS, "late reply", post_to_slack=True, by="U0MAYA001")
        (request,) = (self.home / tag_threads.REQUESTS_DIR).glob("*.json")
        data = json.loads(request.read_text(encoding="utf-8"))
        data["at"] = (datetime.now(timezone.utc) - tag_threads.REQUEST_MAX_AGE * 2).isoformat()
        request.write_text(json.dumps(data), encoding="utf-8")
        app.tag_thread_requests()
        app.client.chat_postMessage.assert_not_called()
        self.assertIn("while the Tag was stopped", self.store.read(C, TS)["items"][-1]["text"])


if __name__ == "__main__":
    unittest.main()
