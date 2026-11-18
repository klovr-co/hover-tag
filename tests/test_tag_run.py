"""tag NAME run: one prompt through the Tag's agent, streamed as JSON lines.

A fake agent process stands in for opentag_agent.py and prints the normalized
events both backends emit, so these tests cover the CLI contract for Codex and
Claude alike without either provider.
"""
from __future__ import annotations

import io
import json
import os
import signal
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import tag_activity, tag_cli, tag_config, tag_instances, tag_run_cli

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "protocol/examples/run.jsonl"

FAKE_AGENT = textwrap.dedent('''
    import json, os, sys, time
    from pathlib import Path

    args = sys.argv[1:]
    option = lambda name: args[args.index(name) + 1] if name in args else None
    Path(os.environ["FAKE_LOG"]).write_text(json.dumps({
        "argv": args, "env": sorted(os.environ),
        "question": Path(option("--question-file")).read_text(encoding="utf-8")}), encoding="utf-8")

    def emit(kind, text="", **fields):
        print(json.dumps({"type": kind, "text": text, **fields}), flush=True)

    mode = os.environ["FAKE_MODE"]
    if mode == "resume":
        emit("status", "Continuing")
        emit("message_delta", "Again.", phase="final_answer")
        emit("session", session_id=option("--resume-session"))
        sys.exit(0)
    emit("session", session_id="sess-1")
    emit("run_info", model="claude-opus-5-5", reasoning_effort="high")
    if mode == "complete":
        emit("status", "Working")
        emit("activity_start", activity_id="think-1", label="Thinking…", details={})
        emit("activity_complete", activity_id="think-1", label="Thinking…", status="completed",
             details={"output": "Read the file first."})
        emit("activity_start", activity_id="tool-1", label="Reading files…",
             details={"tool": "Read", "input": "notes.md"})
        emit("activity_complete", activity_id="tool-1", label="Reading files…", status="completed",
             details={"output": "3 lines"})
        emit("message_start", phase="commentary")
        emit("message_complete", "Checking.", phase="commentary")
        emit("message_start", phase="final_answer")
        emit("message_delta", "Three ", phase="final_answer")
        emit("message_delta", "lines.", phase="final_answer")
        emit("message_complete", "Three lines.", phase="final_answer")
        emit("usage", usage={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150})
        emit("turn_complete", status="completed")
        sys.exit(0)
    if mode == "approval":
        emit("approval_request", approval_id="a" * 32, label="Run rm -rf build")
        answer = Path(option("--approval-dir")) / ("a" * 32 + ".json")
        while not answer.exists():
            time.sleep(0.02)
        emit("status", json.loads(answer.read_text(encoding="utf-8"))["decision"])
        emit("turn_complete", status="completed")
        sys.exit(0)
    if mode == "wait":
        emit("status", "Working")
        control = Path(option("--control-file"))
        while control.read_text(encoding="utf-8") != option("--run-id"):
            time.sleep(0.02)
        emit("turn_complete", status="interrupted")
        sys.exit(130)
    if mode == "fail":
        emit("error", "Reached the limit", code="error_max_turns")
        emit("turn_complete", status="failed", text="Reached the limit", code="error_max_turns")
        sys.exit(1)
    if mode == "timeout":
        emit("error", "Tag backend timed out: no progress")
        sys.exit(124)
''')


class RunCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dir = Path(temporary.name)
        self.root = self.dir / "Tag"
        self.home = tag_instances.create(self.root, "t1-a1").home
        tag_config.save_config(self.home / "config/settings.json", {
            "OPENTAG_BACKEND": "claude", "SLACK_BOT_TOKEN": "xoxb-1", "SLACK_TEAM_ID": "T1"})
        agent = self.dir / "fake_agent.py"
        agent.write_text(FAKE_AGENT, encoding="utf-8")
        self.prompt = self.dir / "prompt.txt"
        self.prompt.write_text("How many lines are in notes.md?", encoding="utf-8")
        self.log = self.dir / "agent.json"
        for patcher in (
            patch.dict(os.environ, {"TAG_HOME": str(self.root), "FAKE_LOG": str(self.log)}),
            patch.object(tag_run_cli, "AGENT", agent),
            patch.object(tag_run_cli, "discover_tag_models", return_value=[]),
            patch.object(tag_run_cli, "rich_events_selected", return_value=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_tag(self, mode: str, *extra: str, during=None) -> tuple[int, list[dict]]:
        os.environ["FAKE_MODE"] = mode
        output = io.StringIO()
        if during is not None:
            threading.Thread(target=during, daemon=True).start()
        with patch.object(sys, "argv", ["tag", "t1-a1", "run", "--prompt-file", str(self.prompt), *extra, "--json"]), \
                patch.dict(os.environ), redirect_stdout(output):
            code = tag_cli.main()
        return code, [json.loads(line) for line in output.getvalue().splitlines()]

    def agent(self) -> dict:
        return json.loads(self.log.read_text(encoding="utf-8"))

    def test_streams_started_events_and_result_and_keeps_the_run_in_activity(self) -> None:
        code, lines = self.run_tag("complete")
        self.assertEqual(0, code)
        self.assertTrue(all(line["schema_version"] == 1 for line in lines))
        self.assertEqual({"type": "started", "session_id": "sess-1", "schema_version": 1}, lines[0])
        events = [line for line in lines if line["type"] == "event"]
        self.assertEqual(list(range(1, len(events) + 1)), [event["seq"] for event in events])
        self.assertEqual(["status", "thinking", "thinking", "tool_use", "tool_result", "text", "text", "text"],
                         [event["kind"] for event in events])
        self.assertEqual("Read the file first.", events[2]["text"])
        self.assertEqual({"tool": "Read", "input": "notes.md"}, events[3]["details"])
        self.assertEqual(("completed", {"output": "3 lines"}), (events[4]["status"], events[4]["details"]))
        self.assertEqual(events[3]["id"], events[4]["id"])
        self.assertEqual([("commentary", "Checking."), ("final_answer", "Three "), ("final_answer", "lines.")],
                         [(event["phase"], event["delta"]) for event in events[5:]])
        result = lines[-1]
        self.assertEqual(("result", "complete", "sess-1", "Three lines."),
                         (result["type"], result["status"], result["session_id"], result["text"]))
        self.assertEqual(150, result["usage"]["total_tokens"])
        self.assertNotIn("error", result)
        record = tag_activity.ActivityStore(self.home / "state/activity").get(result["run_id"])
        self.assertEqual(({"type": "run"}, "completed", "sess-1", "claude-opus-5-5"),
                         (record["trigger"], record["outcome"], record["session_id"], record["model"]))
        self.assertEqual(["Thinking…", "Reading files…"], [event["label"] for event in record["events"]])
        rows = tag_activity.recent_activity(self.home / "state/activity")
        self.assertEqual(({"type": "run"}, "replied"), (rows[0]["trigger"], rows[0]["kind"]))
        self.assertFalse((self.home / "state/activity-now.json").exists())

    def test_both_backends_run_through_the_same_contract(self) -> None:
        for backend in ("codex", "claude"):
            with self.subTest(backend=backend):
                tag_config.save_config(self.home / "config/settings.json", {"OPENTAG_BACKEND": backend})
                code, lines = self.run_tag("complete")
                argv = self.agent()["argv"]
                self.assertEqual((0, backend), (code, argv[argv.index("--backend") + 1]))
                self.assertEqual(("started", "complete"), (lines[0]["type"], lines[-1]["status"]))

    def test_runs_the_tag_agent_with_no_slack_access(self) -> None:
        self.run_tag("complete", "--max-turns", "5", "--timeout-minutes", "2")
        agent = self.agent()
        argv = agent["argv"]
        self.assertEqual("claude", argv[argv.index("--backend") + 1])
        self.assertEqual(("120", "120"), (argv[argv.index("--timeout") + 1], argv[argv.index("--max-timeout") + 1]))
        self.assertEqual("How many lines are in notes.md?", agent["question"])
        self.assertNotIn("--resume-session", argv)
        self.assertFalse([name for name in agent["env"] if name.startswith("SLACK_")])
        self.assertIn("OPENTAG_RUN_MAX_TURNS", agent["env"])

    def test_resume_continues_the_session_and_starts_with_its_id(self) -> None:
        code, lines = self.run_tag("resume", "--resume", "sess-0")
        self.assertEqual(0, code)
        self.assertEqual("sess-0", self.agent()["argv"][self.agent()["argv"].index("--resume-session") + 1])
        self.assertEqual({"type": "started", "session_id": "sess-0", "schema_version": 1}, lines[0])
        self.assertEqual(("complete", "sess-0", "Again."),
                         (lines[-1]["status"], lines[-1]["session_id"], lines[-1]["text"]))

    def test_approvals_are_denied_because_nobody_can_answer_them(self) -> None:
        code, lines = self.run_tag("approval")
        self.assertEqual(0, code)
        texts = [line.get("text") for line in lines if line.get("kind") == "status"]
        self.assertEqual(["Denied, because nobody can approve during a run: Run rm -rf build", "deny"], texts)

    def test_failures_and_timeouts_end_with_a_result_that_says_why(self) -> None:
        code, lines = self.run_tag("fail")
        self.assertEqual((1, "failed"), (code, lines[-1]["status"]))
        self.assertEqual({"code": "error_max_turns", "message": "Reached the limit"}, lines[-1]["error"])
        record = tag_activity.ActivityStore(self.home / "state/activity").get(lines[-1]["run_id"])
        self.assertEqual("failed", record["outcome"])
        code, lines = self.run_tag("timeout")
        self.assertEqual((1, "timeout"), (code, lines[-1]["status"]))
        self.assertIn("timed out", lines[-1]["error"]["message"])

    @unittest.skipIf(os.name == "nt", "POSIX signals; Windows sends CTRL_BREAK_EVENT to the process group")
    def test_a_stop_signal_cancels_cleanly_and_still_prints_the_result(self) -> None:
        def stop_when_working() -> None:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not self.log.exists():
                time.sleep(0.02)
            time.sleep(0.2)
            os.kill(os.getpid(), signal.SIGTERM)

        code, lines = self.run_tag("wait", during=stop_when_working)
        self.assertEqual((1, "cancelled", "sess-1"), (code, lines[-1]["status"], lines[-1]["session_id"]))
        self.assertIn("Stopped", lines[-1]["error"]["message"])
        record = tag_activity.ActivityStore(self.home / "state/activity").get(lines[-1]["run_id"])
        self.assertEqual("interrupted", record["outcome"])
        self.assertEqual(signal.default_int_handler, signal.getsignal(signal.SIGINT))
        promised = json.loads((ROOT / "protocol/examples/run-cancelled.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual([], sorted(set(promised) - set(lines[-1])))
        self.assertEqual([], sorted(set(promised["error"]) - set(lines[-1]["error"])))

    def test_an_unset_tag_or_bad_options_never_start_the_agent(self) -> None:
        (self.home / "config/settings.json").unlink()
        code, lines = self.run_tag("complete")
        self.assertEqual((1, "failed"), (code, lines[-1]["status"]))
        self.assertFalse(self.log.exists())
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
            tag_run_cli.parse(["--prompt-file", str(self.prompt), "--json", "--max-turns", "0"])
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
            tag_run_cli.parse(["--prompt-file", str(self.prompt)])

    def test_installations_receive_the_command(self) -> None:
        shipped = json.loads((ROOT / "scripts/runtime-files.json").read_text(encoding="utf-8"))["files"]
        self.assertIn("scripts/tag_run_cli.py", shipped)
        self.assertIn("run-jsonl", tag_cli.CAPABILITIES)

    def test_real_output_provides_what_the_example_promises(self) -> None:
        _code, lines = self.run_tag("complete")
        promised = [json.loads(line) for line in EXAMPLE.read_text(encoding="utf-8").splitlines()]
        actual = {(line["type"], line.get("kind"), line.get("status") if line["type"] == "result" else None): line
                  for line in lines}
        for line in promised:
            key = (line["type"], line.get("kind"), line.get("status") if line["type"] == "result" else None)
            if key[0] == "result" and key[2] != "complete":
                continue
            self.assertIn(key, actual, f"tag run --json no longer prints {key}")
            missing = sorted(set(line) - set(actual[key]))
            self.assertEqual([], missing, f"{key} no longer provides {missing}")


if __name__ == "__main__":
    unittest.main()
