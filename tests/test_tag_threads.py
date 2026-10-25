"""Thread transcripts: the store, redaction, expiry, prompts and app requests."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import tag_activity, tag_cli, tag_config, tag_instances, tag_threads
from scripts.tag_threads import RunTranscript, ThreadStore

C, TS = "C0LAUNCH1", "1792487642.000100"
NAMES = {"U0MAYA001": "Maya", "U0SAM0001": "Sam", "U0TAG0001": "Tag"}
# Built at run time so the repository holds nothing shaped like a real token.
TOKEN = "xox" + "b-1234567890-abcdefghijkl"


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.store = ThreadStore(self.home)

    def run_transcript(self, run_id: str = "c" * 32, **kwargs) -> RunTranscript:
        return RunTranscript(self.store, team="T0KLOVR1", channel=C, thread_ts=TS, run_id=run_id,
                             requester={"id": "U0MAYA001"}, **kwargs)

    def raw(self) -> str:
        return self.store.path(C, TS).read_text(encoding="utf-8")

    def test_one_file_per_thread_with_messages_answers_steps_files_and_memory_in_order(self) -> None:
        run = self.run_transcript()
        run.slack_messages([{"ts": TS, "user": "U0MAYA001", "text": "<@U0TAG0001> make a <https://x.example|launch> checklist"}],
                           own_user="U0TAG0001", name_of=NAMES.get)
        run.start()
        run.step({"type": "activity_start", "activity_id": "a", "label": "Searching Slack…",
                  "details": {"input": "secret query", "output": "raw tool output"}})
        run.step({"type": "activity_complete", "activity_id": "a", "status": "completed",
                  "details": {"output": "raw tool output"}})
        run.finish("completed")
        run.answer("**Done.**\n\n```python\n    indented = True\n```", outcome="completed", ts="1792487701.000200")
        run.files([{"name": "checklist.md", "kind": "file", "delivery": "local", "local_path": "/tmp/checklist.md"}])
        run.memory([{"action": "saved", "scope": C, "key": "owner", "text": "Maya owns launch.", "version": "3f2a9c1b7d4e"}])

        self.assertTrue((self.home / "state/threads" / f"{C}-{TS}.jsonl").is_file())
        thread = self.store.read(C, TS)
        self.assertEqual([item["type"] for item in thread["items"]], ["message", "work", "answer"])
        message, work, answer = thread["items"]
        self.assertEqual(message["author"], {"id": "U0MAYA001", "name": "Maya"})
        self.assertEqual(message["text"], "@Tag make a [launch](https://x.example) checklist")
        self.assertEqual((work["status"], work["steps"]), ("completed", [
            {"id": work["steps"][0]["id"], "label": "Searching Slack…", "status": "completed"}]))
        self.assertIn("    indented = True", answer["text"], "Markdown layout is kept")
        self.assertEqual([f["name"] for f in answer["files"]], ["checklist.md"])
        self.assertEqual(answer["memory"][0]["key"], "owner")
        self.assertEqual(answer["memory"][0]["version"], "3f2a9c1b7d4e")
        self.assertEqual((thread["title"], thread["status"], thread["people"]),
                         ("Make a launch checklist", "done", ["Maya"]))
        # Steps keep their public label and status only: never tool input or output.
        self.assertNotIn("raw tool output", self.raw())
        self.assertNotIn("secret query", self.raw())

    def test_people_messages_are_recorded_once_and_own_messages_only_when_the_transcript_starts(self) -> None:
        earlier = [{"ts": "1792487600.000100", "user": "U0TAG0001", "text": "Earlier answer from before transcripts"},
                   {"ts": TS, "user": "U0MAYA001", "text": "first"}]
        self.run_transcript().slack_messages(earlier, own_user="U0TAG0001", name_of=NAMES.get)
        later = earlier + [{"ts": "1792487800.000100", "user": "U0TAG0001", "text": "Tag's new answer"},
                           {"ts": "1792487900.000100", "user": "U0SAM0001", "text": "second"},
                           {"ts": "1792487950.000100", "user": "U0MAYA001", "text": "from the app", "tag_app": True}]
        self.run_transcript("d" * 32).slack_messages(later, own_user="U0TAG0001", name_of=NAMES.get)
        texts = [item["text"] for item in self.store.read(C, TS)["items"]]
        self.assertEqual(texts, ["Earlier answer from before transcripts", "first", "second"])

    def test_text_is_redacted_but_keeps_its_layout(self) -> None:
        run = self.run_transcript()
        run.slack_messages([{"ts": TS, "user": "U0MAYA001", "text": f"use token={TOKEN} please"}],
                           own_user=None, name_of=NAMES.get)
        run.answer(f"Here:\n\n- one\n  - nested\n\nAuthorization: Bearer {TOKEN}", outcome="completed")
        self.store.open_prompt(channel=C, thread_ts=TS, kind="approval", text=f"Run with {TOKEN}?",
                               choices=[{"id": "approve", "label": "Approve"}])
        raw = self.raw()
        self.assertNotIn(TOKEN, raw)
        self.assertIn("<redacted>", raw)
        answer = next(i for i in self.store.read(C, TS)["items"] if i["type"] == "answer")
        self.assertIn("- one\n  - nested", answer["text"])

    def test_unknown_step_labels_become_generic_and_long_text_is_bounded(self) -> None:
        run = self.run_transcript()
        run.start()
        run.step({"type": "activity_start", "activity_id": "a", "label": "cat ~/.ssh/id_rsa"})
        run.answer("x" * (tag_threads.MAX_ANSWER_CHARS + 500), outcome="completed")
        thread = self.store.read(C, TS)
        self.assertEqual(thread["items"][0]["steps"][0]["label"], "Using a connected tool…")
        self.assertLessEqual(len(thread["items"][1]["text"]), tag_threads.MAX_ANSWER_CHARS)
        self.assertNotIn("id_rsa", self.raw())

    def test_a_torn_last_line_is_skipped(self) -> None:
        self.store.message(C, TS, ts=TS, author={"id": "U0MAYA001", "name": "Maya"}, text="hello")
        with self.store.path(C, TS).open("a", encoding="utf-8") as stream:
            stream.write('{"type": "message", "text": "half')
        self.assertEqual([i["text"] for i in self.store.read(C, TS)["items"]], ["hello"])

    def test_invalid_threads_are_refused(self) -> None:
        for channel, ts in (("../x", TS), (C, "../../x"), ("C0LAUNCH1/..", TS), (C, "1.2/3")):
            with self.assertRaises(tag_threads.ThreadError):
                self.store.message(channel, ts, ts=None, author=None, text="x")
        self.assertFalse((self.home / "state/threads").exists())

    def test_a_run_cut_off_by_a_stopped_bridge_reads_as_stopped(self) -> None:
        self.run_transcript().start()
        later = datetime.now(timezone.utc) + tag_threads.STALE_RUN + timedelta(minutes=1)
        self.assertEqual(self.store.read(C, TS)["status"], "working")
        self.assertEqual(self.store.read(C, TS, now=later)["status"], "stopped")


class PromptTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.store = ThreadStore(self.home)

    def ask(self, **kwargs) -> str:
        options = {"kind": "run_request", "text": "Sam asked your Tag to run: add the FAQ",
                   "choices": [{"id": "run_once", "label": "Run this once", "primary": True},
                               {"id": "allow_thread", "label": "Let Sam run tasks in this thread"},
                               {"id": "ignore", "label": "Ignore"}, {"id": "bad id!", "label": "x"}],
                   "requester": {"id": "U0MAYA001", "name": "Maya"}, **kwargs}
        return self.store.open_prompt(team="T0KLOVR1", channel=C, thread_ts=TS, **options)

    def test_a_waiting_question_marks_the_thread_needs_you_until_answered_once(self) -> None:
        prompt_id = self.ask(flagged=True, reason="instruction", nested={"dropped": True})
        thread = self.store.read(C, TS)
        self.assertEqual((thread["status"], thread["pending_prompts"]), ("needs_you", 1))
        prompt = thread["prompts"][0]
        self.assertEqual([c["id"] for c in prompt["choices"]], ["run_once", "allow_thread", "ignore"])
        self.assertEqual((prompt["flagged"], prompt["reason"]), (True, "instruction"))
        self.assertNotIn("nested", prompt)
        self.assertTrue(self.store.close_prompt(C, TS, prompt_id, choice="run_once",
                                                by={"id": "U0MAYA001", "name": "Maya"}, via="app"))
        self.assertFalse(self.store.close_prompt(C, TS, prompt_id, choice="ignore", via="slack"))
        thread = self.store.read(C, TS)
        self.assertEqual(thread["status"], "done")
        self.assertEqual(thread["prompts"][0]["answer"]["label"], "Run this once")
        self.assertEqual(thread["prompts"][0]["answer"]["via"], "app")

    def test_prompts_expire_by_time_and_with_their_run(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.ask(prompt_id="old", expires_at=past)
        self.assertEqual(self.store.read(C, TS)["status"], "done")
        self.assertIsNone(self.store.pending_prompt(C, TS, "old"))
        run = RunTranscript(self.store, team="T0KLOVR1", channel=C, thread_ts=TS, run_id="e" * 32, requester=None)
        run.start()
        self.ask(prompt_id="approval-1", kind="approval", run_id=run.run_id)
        self.assertEqual(self.store.read(C, TS)["status"], "needs_you")
        run.finish("completed")
        self.assertEqual(self.store.read(C, TS)["prompts"][-1]["status"], "expired")
        self.assertEqual(self.store.read(C, TS)["status"], "done")

    def test_a_prompt_needs_a_valid_choice(self) -> None:
        with self.assertRaises(ValueError):
            self.ask(choices=[{"id": "no spaces allowed", "label": "x"}])

    def test_answers_from_the_app_go_through_a_request_the_bridge_takes(self) -> None:
        prompt_id = self.ask()
        with self.assertRaises(tag_threads.ThreadError):
            tag_threads.request_answer(self.home, C, TS, prompt_id, "maybe", by="U0MAYA001")
        tag_threads.request_answer(self.home, C, TS, prompt_id, "run_once", by="U0MAYA001")
        tag_threads.request_reply(self.home, C, TS, "  also add the FAQ ", post_to_slack=True, by="U0MAYA001")
        requests = tag_threads.take_requests(self.home)
        self.assertEqual([(r["kind"], r["stale"]) for r in requests], [("answer", False), ("reply", False)])
        self.assertEqual((requests[1]["text"], requests[1]["post_to_slack"]), ("also add the FAQ", True))
        self.assertEqual(tag_threads.take_requests(self.home), [], "taken requests are gone")
        tag_threads.request_reply(self.home, C, TS, "late", post_to_slack=False, by=None)
        later = datetime.now(timezone.utc) + tag_threads.REQUEST_MAX_AGE + timedelta(seconds=1)
        self.assertTrue(tag_threads.take_requests(self.home, now=later)[0]["stale"])
        with self.assertRaises(tag_threads.ThreadError):
            tag_threads.request_reply(self.home, C, TS, "   ", post_to_slack=False, by=None)


class ExpiryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.store = ThreadStore(self.home)

    def write_at(self, channel: str, ts: str, when: datetime) -> None:
        with patch.object(tag_threads, "_now", lambda: when.isoformat(timespec="seconds")):
            self.store.message(channel, ts, ts=ts, author={"id": "U0MAYA001", "name": "Maya"}, text="hi")

    def test_threads_are_deleted_90_days_after_their_last_message_by_default(self) -> None:
        now = datetime(2026, 10, 26, tzinfo=timezone.utc)
        self.write_at(C, "1.000001", now - timedelta(days=91))
        self.write_at(C, "2.000001", now - timedelta(days=89))
        self.write_at(C, "3.000001", now - timedelta(days=120))
        self.write_at(C, "3.000001", now - timedelta(days=1))  # a later message keeps it
        self.assertEqual(tag_threads.retention_days(self.home), 90)
        listed = tag_threads.list_threads(self.home, "t1", now=now)
        self.assertEqual(sorted(t["thread_ts"] for t in listed["threads"]), ["2.000001", "3.000001"])
        self.assertTrue(self.store.path(C, "1.000001").exists(), "reading never deletes")
        self.assertEqual(self.store.prune(now=now), 1)
        self.assertFalse(self.store.path(C, "1.000001").exists())
        self.assertTrue(self.store.path(C, "2.000001").exists())

    def test_the_owner_can_change_how_long_threads_are_kept(self) -> None:
        now = datetime(2026, 10, 26, tzinfo=timezone.utc)
        self.write_at(C, "1.000001", now - timedelta(days=10))
        self.assertEqual(tag_threads.set_retention_days(self.home, 7), 7)
        self.assertEqual(tag_threads.retention_days(self.home), 7)
        self.assertEqual(self.store.prune(now=now), 1)
        for bad in (0, -1, tag_threads.MAX_RETENTION_DAYS + 1, True):
            with self.assertRaises(tag_threads.ThreadError):
                tag_threads.set_retention_days(self.home, bad)
        (self.home / tag_threads.SETTINGS_FILE).write_text("{broken", encoding="utf-8")
        self.assertEqual(tag_threads.retention_days(self.home), 90)


class LegacyActivityTests(unittest.TestCase):
    """Threads kept in Activity from before transcripts still appear, as a preview."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.activity = tag_activity.ActivityStore(self.home / "state/activity")

    def old_run(self, thread_ts: str, answer: str) -> str:
        run_id = self.activity.create(team="T0KLOVR1", channel=C, thread_ts=thread_ts,
                                      request_ts=thread_ts, requester="U0MAYA001")
        self.activity.finish(run_id, "completed")
        self.activity.save_reply(run_id, answer)
        return run_id

    def test_activity_only_threads_show_a_preview(self) -> None:
        self.old_run("1700000000.000100", "Booked the meeting for Tuesday at 11:00.")
        listed = tag_threads.list_threads(self.home, "t1")["threads"]
        self.assertEqual(len(listed), 1)
        self.assertTrue(listed[0]["preview_only"])
        self.assertEqual(listed[0]["title"], "Booked the meeting for Tuesday at 11:00.")
        detail = tag_threads.thread_detail(self.home, "t1", C, "1700000000.000100")
        self.assertEqual([i["type"] for i in detail["items"]], ["work", "answer"])
        self.assertTrue(detail["items"][1]["preview_only"])

    def test_runs_from_before_the_transcript_come_first(self) -> None:
        old = self.old_run(TS, "An earlier answer.")
        record = self.activity.get(old)
        record["started_at"] = record["finished_at"] = "2026-01-01T00:00:00+00:00"
        self.activity._write(record)
        ThreadStore(self.home).message(C, TS, ts="1792487700.000100", author={"id": "U0MAYA001", "name": "Maya"},
                                       text="follow-up")
        listed = tag_threads.list_threads(self.home, "t1")["threads"]
        self.assertEqual([t["preview_only"] for t in listed], [False])
        detail = tag_threads.thread_detail(self.home, "t1", C, TS)
        self.assertEqual([i["type"] for i in detail["items"]], ["work", "answer", "message"])

    def test_a_thread_that_isnt_here_is_none(self) -> None:
        self.assertIsNone(tag_threads.thread_detail(self.home, "t1", C, "1.000001"))
        self.assertIsNone(tag_threads.thread_detail(self.home, "t1", "../x", TS))


class ListingTests(unittest.TestCase):
    def test_needs_you_is_never_cut_off_by_the_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            store = ThreadStore(home)
            store.open_prompt(channel=C, thread_ts="1.000001", kind="sign_in", text="Sign in to finish",
                              choices=[{"id": "done", "label": "Done"}], url="https://example.com/login")
            for index in range(2, 6):
                store.message(C, f"{index}.000001", ts=None, author={"id": "U0MAYA001"}, text="later")
            listed = tag_threads.list_threads(home, "t1", limit=2)
            self.assertIn("1.000001", [t["thread_ts"] for t in listed["threads"]])
            self.assertTrue(listed["has_more"])
            self.assertEqual(tag_threads.list_threads(home, "t1", channel="C0OTHER")["threads"], [])


class CommandTests(unittest.TestCase):
    """`tag NAME threads` and `tag NAME thread …`, as Tag.app runs them."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Tag"
        environment = patch.dict(os.environ, {"TAG_HOME": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
        for name in ("OPENTAG_CALLER_ID", "OPENTAG_ROUTINE_ID"):
            os.environ.pop(name, None)
        self.home = tag_instances.create(self.root, "t1-a1").home
        tag_config.save_config(self.home / "config/settings.json", {
            "SLACK_TEAM_ID": "T1", "SLACK_APP_ID": "A1", "SLACK_CHANNEL_IDS": C,
            "SLACK_ALLOWED_USER_IDS": "U0MAYA001,U0SAM0001"})
        self.store = ThreadStore(self.home)
        self.store.message(C, TS, ts=TS, author={"id": "U0MAYA001", "name": "Maya"}, text="hello", team="T1")
        self.prompt = self.store.open_prompt(channel=C, thread_ts=TS, kind="queue_steer", text="Queue or steer?",
                                             choices=[{"id": "queue", "label": "Queue"}, {"id": "steer", "label": "Steer"}])

    def cli(self, *arguments: str, code: int = 0) -> dict:
        with patch.object(sys, "argv", ["tag", *arguments]), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(tag_cli.main(), code)
        return json.loads(output.getvalue())

    def test_listing_and_reading_need_no_running_tag(self) -> None:
        listed = self.cli("t1-a1", "threads", "--json")
        self.assertEqual([t["status"] for t in listed["threads"]], ["needs_you"])
        self.assertEqual(listed["threads"][0]["link"], f"hover-tag://tag/t1-a1/thread/{C}/{TS}")
        thread = self.cli("t1-a1", "thread", C, TS, "--json")["thread"]
        self.assertEqual(thread["prompts"][0]["prompt_id"], self.prompt)
        missing = self.cli("t1-a1", "thread", C, "1.000001", "--json", code=1)
        self.assertEqual((missing["ok"], missing["code"]), (False, "not_found"))
        self.assertIn("isn't on this computer", missing["error"])
        self.assertEqual(self.cli("t1-a1", "threads", "retention", "30", "--json")["retention_days"], 30)

    def test_reply_and_answer_need_the_tag_running_and_queue_a_request(self) -> None:
        with patch.object(tag_cli, "slack_ready", lambda _home: False):
            stopped = self.cli("t1-a1", "thread", C, TS, "reply", "--text", "hi", "--json", code=1)
        self.assertEqual(stopped["code"], "not_running")
        with patch.object(tag_cli, "slack_ready", lambda _home: True):
            replied = self.cli("t1-a1", "thread", C, TS, "reply", "--text", "hi", "--post-to-slack", "--json")
            answered = self.cli("t1-a1", "thread", C, TS, "answer", self.prompt, "steer", "--json")
            wrong = self.cli("t1-a1", "thread", C, TS, "answer", self.prompt, "maybe", "--json", code=1)
        self.assertEqual((replied["queued"], replied["post_to_slack"], answered["queued"]), (True, True, True))
        self.assertEqual(wrong["code"], "invalid")
        requests = tag_threads.take_requests(self.home)
        self.assertEqual([(r["kind"], r["by"]) for r in requests], [("reply", "U0MAYA001"), ("answer", "U0MAYA001")])

    def test_payloads_provide_every_field_the_examples_promise(self) -> None:
        """Tag.app parses protocol/examples/; real output must keep each field they show."""
        examples = Path(__file__).resolve().parents[1] / "protocol/examples"
        example = lambda name: json.loads((examples / name).read_text(encoding="utf-8"))

        def provides(actual: dict, promised: dict, where: str) -> None:
            self.assertEqual(sorted(set(promised) - set(actual)), [], f"{where} lost fields")

        transcript = RunTranscript(self.store, team="T1", channel=C, thread_ts=TS, run_id="d" * 32,
                                   requester={"id": "U0MAYA001"})
        transcript.start()
        transcript.step({"type": "activity_start", "activity_id": "a", "label": "Searching Slack…"})
        transcript.answer("Done.", outcome="completed", ts="1792487650.000200")
        transcript.files([{"name": "plan.md", "local_path": str(self.home / "plan.md"), "delivery": "uploaded",
                           "url": "https://example.slack.com/files/F0PLAN001/plan.md"}])
        transcript.memory([{"action": "saved", "scope": "global", "key": "owner", "version": "3f2a9c1b7d4e",
                            "text": "Sam owns comms"}])
        transcript.finish("completed")
        listed = self.cli("t1-a1", "threads", "--json")
        provides(listed, example("threads.json"), "threads")
        provides(listed["threads"][0], example("threads.json")["threads"][0], "threads row")
        thread = self.cli("t1-a1", "thread", C, TS, "--json")
        provides(thread, example("thread.json"), "thread")
        promised = example("thread.json")["thread"]
        provides(thread["thread"], promised, "thread detail")
        actual = {item["type"]: item for item in thread["thread"]["items"]}
        # Fields only some prompts have: an answer, a time limit, a sign-in link, an editable Slack message.
        OPTIONAL = {"answer", "expires_at", "url", "slack_message"}
        for item in promised["items"]:
            self.assertIn(item["type"], actual)
            provides(actual[item["type"]], {k: v for k, v in item.items() if k not in OPTIONAL},
                     f"{item['type']} item")
        provides(thread["thread"]["files"][0], promised["files"][0], "file")
        provides(thread["thread"]["memory"][0], promised["memory"][0], "memory change")
        provides(self.cli("t1-a1", "thread", C, "1.000001", "--json", code=1), example("thread-not-found.json"),
                 "thread not found")
        with patch.object(tag_cli, "slack_ready", lambda _home: True):
            provides(self.cli("t1-a1", "thread", C, TS, "reply", "--text", "hi", "--json"),
                     example("thread-reply.json"), "reply")
            provides(self.cli("t1-a1", "thread", C, TS, "answer", self.prompt, "queue", "--json"),
                     example("thread-answer.json"), "answer")

    def test_a_tag_cannot_answer_its_own_questions(self) -> None:
        for variable in ("OPENTAG_CALLER_ID", "OPENTAG_ROUTINE_ID"):
            with patch.dict(os.environ, {variable: "U0SAM0001"}), patch.object(tag_cli, "slack_ready", lambda _home: True):
                refused = self.cli("t1-a1", "thread", C, TS, "answer", self.prompt, "queue", "--json", code=1)
                self.assertEqual(refused["code"], "inside_run")
                self.cli("t1-a1", "thread", C, TS, "reply", "--text", "hi", "--json", code=1)
                self.cli("t1-a1", "threads", "retention", "1", "--json", code=1)
        self.assertEqual(tag_threads.take_requests(self.home), [])


if __name__ == "__main__":
    unittest.main()
