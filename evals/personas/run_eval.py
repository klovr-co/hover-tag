#!/usr/bin/env python3
# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""Golden tasks for personas (ADR 0012): run them through the bridge and grade the outcome.

    python3 evals/personas/run_eval.py --variant smoke                    # fake model, offline, $0
    python3 evals/personas/run_eval.py --variant baseline --reps 2 \\
        --real-backend --budget-usd 2                                      # the Tag's real backend; paid

Each task gets a fresh Tag folder with three personas and a fake Slack, so no
live Slack is involved. Grading looks at what Tag did (handovers and proposals,
its writes) and what it said, plus who answered, since a wrong handover is
something people see. Which persona answered is also recorded per task as
``by_agent``, as a diagnostic.

Results land in ``evals/personas/results/<variant>/`` as ``results.jsonl``,
``run.json`` and one trace per task and rep, in the layout of Anthropic's
single-agent-loop-kit. Compare two runs with the kit's own script:

    python3 single-agent-loop-kit/skill/scripts/compare.py evals/personas/results baseline v1

A real-backend run calls the Tag's configured model once per turn (13 turns per
rep). It refuses to start without ``--budget-usd``; agree the budget first.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import slack_socket_agent, tag_personas  # noqa: E402
from scripts.tag_handoff import HandoffStore  # noqa: E402

HERE = Path(__file__).resolve().parent
TAG_NAME = "Maya"
TAG_USER = "UTAGEVAL1"
PERSON = "UPERSON01"
CHANNEL = "C0EVAL001"
PERSONAS = [
    {"name": "Finance", "description": "Month-end close, invoices and the VAT return.",
     "skills": ["month-end-close"], "voice": "brisk and exact"},
    {"name": "Ops", "description": "Shift rota, on-call and facilities.", "voice": "friendly, short replies"},
    {"name": "Designer", "description": "Banners, slides and brand assets.", "voice": "playful"},
]


class FakeApp:
    def __init__(self) -> None:
        self.events: dict[str, Any] = {}

    def event(self, name: str):
        def register(handler: Any) -> Any:
            self.events[name] = handler
            return handler
        return register

    def action(self, _name: Any):
        return lambda handler: handler

    def view(self, _name: str):
        return lambda handler: handler


class FakeSlack:
    """Just enough Slack for one channel: posts, edits, deletes and thread replies."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.counter = 0
        self.client = MagicMock(name="slack")
        self.client.chat_postMessage.side_effect = self.post
        self.client.chat_update.side_effect = self.update
        self.client.chat_delete.side_effect = self.delete
        self.client.conversations_replies.side_effect = self.replies
        self.client.auth_test.return_value = {"ok": True, "user_id": TAG_USER}
        self.client.assistant_threads_setStatus.side_effect = RuntimeError("no native status")
        self.client.users_info.side_effect = lambda user: {"ok": True, "user": {
            "id": user, "is_bot": user == TAG_USER, "profile": {"display_name": "Eval person"}}}

    def ts(self) -> str:
        self.counter += 1
        return f"{1800000000 + self.counter}.000100"

    def post(self, channel: str, text: str = "", thread_ts: str | None = None, **kwargs: Any) -> dict[str, Any]:
        message = {"channel": channel, "ts": self.ts(), "user": TAG_USER, "bot_id": "BEVAL", "text": text,
                   "username": kwargs.get("username")}
        if thread_ts:
            message["thread_ts"] = thread_ts
        self.messages.append(message)
        return {"ok": True, "channel": channel, "ts": message["ts"]}

    def update(self, channel: str, ts: str, text: str = "", **_kwargs: Any) -> dict[str, Any]:
        for message in self.messages:
            if message["ts"] == ts:
                message["text"] = text
        return {"ok": True}

    def delete(self, channel: str, ts: str, **_kwargs: Any) -> dict[str, Any]:
        self.messages = [m for m in self.messages if m["ts"] != ts]
        return {"ok": True}

    def replies(self, channel: str, ts: str, latest: str | None = None, **_kwargs: Any) -> dict[str, Any]:
        thread = [dict(m) for m in self.messages if m["ts"] == ts or m.get("thread_ts") == ts]
        if latest is not None:
            thread = [m for m in thread if float(m["ts"]) <= float(latest)]
        return {"ok": True, "messages": thread, "has_more": False}

    def ask(self, text: str, thread_ts: str | None) -> dict[str, Any]:
        message = {"channel": CHANNEL, "ts": self.ts(), "user": PERSON, "text": f"<@{TAG_USER}> {text}"}
        if thread_ts:
            message["thread_ts"] = thread_ts
        self.messages.append(message)
        return message


def _logger() -> Any:
    """Warnings from the bridge go to stderr, so a task that never ran says why."""
    logger = MagicMock(name="logger")
    for level in ("warning", "error", "exception"):
        getattr(logger, level).side_effect = lambda message, *values, **_kw: print(
            f"  bridge: {message % values if values else message}", file=sys.stderr)
    return logger


def _arg_matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.lower() in actual.lower()
    return expected == actual


def _wanted(want: dict, action: dict) -> bool:
    return (action["name"] == want["name"]
            and all(_arg_matches(v, action["args"].get(k)) for k, v in want.get("args", {}).items()))


def grade(checks: dict, answer: str, actions: list[dict], answered_as: str) -> list[dict]:
    """The kit's checks, plus who answered. One entry per check, so a failure says which."""
    out = []
    if "answered_as" in checks:
        out.append({"check": f"answered as {checks['answered_as']}", "ok": answered_as == checks["answered_as"]})
    for pattern in checks.get("answer_includes", []):
        out.append({"check": f"answer includes /{pattern}/", "ok": bool(re.search(pattern, answer, re.I))})
    for want in checks.get("actions", []):
        out.append({"check": f"did {want['name']} {json.dumps(want.get('args', {}))}",
                    "ok": any(_wanted(want, a) for a in actions)})
    for name in checks.get("forbidden_actions", []):
        out.append({"check": f"did not {name}", "ok": all(a["name"] != name for a in actions)})
    for pattern in checks.get("answer_excludes", []):
        out.append({"check": f"answer leaves out /{pattern}/", "ok": not re.search(pattern, answer, re.I)})
    if checks.get("only_expected_writes"):
        extra = sorted({a["name"] for a in actions if not any(_wanted(w, a) for w in checks.get("actions", []))})
        out.append({"check": "made no other writes" + (f" (made {', '.join(extra)})" if extra else ""),
                    "ok": not extra})
    return out


def run_case(case: dict, *, real_backend: bool) -> tuple[dict, dict]:
    with tempfile.TemporaryDirectory(prefix="persona-eval-") as raw:
        root = Path(raw)
        home = root / "maya"
        tag_personas.save(home, [tag_personas.validate(item) for item in PERSONAS])
        environment = {
            "TAG_HOME": str(root), "TAG_INSTANCE_HOME": str(home), "OPENTAG_BOT_NAME": TAG_NAME,
            "SLACK_CHANNEL_IDS": CHANNEL, "OPENTAG_SLACK_STREAMING": "0",
            "OPENTAG_MEMORY_ROOT": str(root / "memory"), "OPENTAG_WORKDIR": str(root / "workspace"),
            # Never a real token: helpers that reach Slack fail instead of posting anywhere.
            "SLACK_BOT_TOKEN": "xoxb-eval-not-a-token",
        }
        if not real_backend:
            environment["OPENTAG_CLAUDE_TRANSPORT"] = "print"
        keep = {key: value for key, value in os.environ.items()
                if real_backend and key.startswith(("OPENTAG_BACKEND", "OPENTAG_DEFAULT_", "OPENTAG_CLAUDE_",
                                                     "OPENTAG_CODEX_", "PATH", "HOME", "ANTHROPIC_", "OPENAI_",
                                                     "CODEX_", "CLAUDE_CONFIG_DIR"))}
        slack, actions, replies, turns = FakeSlack(), [], [], []
        script = list(case.get("fake") or [])

        def fake_backend(_backend: str, _channel: str, _caller: str, question: str, _thread: str,
                         *_args: Any, **kwargs: Any) -> tuple[str, bool]:
            step = script.pop(0) if script else {}
            run_file = kwargs.get("persona_file")
            if run_file and step.get("handover"):
                tag_personas.record_handover(run_file, step["handover"])
            if run_file and step.get("propose"):
                tag_personas.record_proposal(run_file, step["propose"])
            return step.get("answer", "(no scripted answer)"), True

        real_read_handover, real_read_proposal = tag_personas.read_handover, tag_personas.read_proposal

        def read_handover(source: Path) -> str | None:
            found = real_read_handover(source)
            if found:
                actions.append({"name": "handover", "args": {"to": found}})
            return found

        def read_proposal(source: Path) -> dict | None:
            found = real_read_proposal(source)
            if found:
                item = found.get(found.get("kind"), {}) if isinstance(found.get("kind"), str) else {}
                actions.append({"name": f"propose_{found.get('kind')}", "args": {"name": item.get("name", "")}})
            return found

        real_post = slack_socket_agent.post_final_reply

        def post_final_reply(*args: Any, **kwargs: Any) -> str | None:
            identity = kwargs.get("identity") or {}
            replies.append({"text": args[3], "as": identity.get("username") or TAG_NAME})
            return real_post(*args, **kwargs)

        app = FakeApp()
        patches = [
            patch.dict(os.environ, {**keep, **environment}, clear=True),
            patch.object(slack_socket_agent, "slack_customize_granted", return_value=True),
            patch.object(slack_socket_agent, "owns_workspace", return_value=True),
            patch.object(slack_socket_agent, "post_final_reply", side_effect=post_final_reply),
            patch.object(tag_personas, "read_handover", side_effect=read_handover),
            patch.object(tag_personas, "read_proposal", side_effect=read_proposal),
            patch.object(slack_socket_agent, "App", return_value=app),
        ]
        if not real_backend:
            # Both backend paths, so a fake run can never reach a real model.
            patches += [
                patch.object(slack_socket_agent, "run_backend", side_effect=fake_backend),
                patch.object(slack_socket_agent, "run_backend_events", side_effect=lambda backend, _team, channel,
                             _thread_ts, caller, question, thread, *rest, **kwargs: fake_backend(
                                 backend, channel, caller, question, thread, **kwargs)),
            ]
        for item in patches:
            item.start()
        try:
            slack_socket_agent.create_app(os.getenv("OPENTAG_BACKEND", "claude"), 300, frozenset({PERSON}), peers=[],
                                          handoff_store=HandoffStore(root / "handoffs"))
            started, thread_ts = time.monotonic(), None
            for turn in case["turns"]:
                event = slack.ask(turn["text"], thread_ts if turn.get("thread") == "same" else None)
                thread_ts = event.get("thread_ts") or event["ts"]
                app.events["app_mention"]({"type": "app_mention", **event}, {"team_id": "T0EVAL"},
                                          slack.client, _logger())
                turns.append({"turn": turn["text"], "replies": list(replies)})
            latency = time.monotonic() - started
        finally:
            for item in reversed(patches):
                item.stop()
    handover_lines = {tag_personas.HANDOVER_TEXT.format(name=item["name"]) for item in PERSONAS}
    final = next((reply for reply in reversed(replies) if reply["text"] not in handover_lines),
                 {"text": "", "as": ""})
    checks = grade(case["checks"], final["text"], actions, final["as"])
    by_agent: dict[str, dict[str, int]] = {}
    for reply in replies:
        if reply["text"] not in handover_lines:
            by_agent.setdefault(reply["as"], {"calls": 0, "input_tokens": 0, "output_tokens": 0})["calls"] += 1
    row = {
        "prompt_id": case["id"], "prompt": "\n".join(turn["text"] for turn in case["turns"]), "tags": case["tags"],
        "status": "ok", "grade": {"task_complete": 1 if all(c["ok"] for c in checks) else 0}, "checks": checks,
        "answer": final["text"], "answered_as": final["as"], "latency_s": round(latency, 2),
        "model_calls": len(case["turns"]), "by_agent": by_agent, "actions": actions,
        "model": "fake" if not real_backend else os.getenv("OPENTAG_DEFAULT_MODEL", "configured"),
    }
    return row, {"turns": turns, "actions": actions, "slack": slack.messages}


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    except OSError:
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variant", required=True, help="results folder name, such as baseline or v1")
    parser.add_argument("--reps", type=int, default=2, help="runs per task (default 2; compare flipped tasks)")
    parser.add_argument("--only", help="comma-separated task ids")
    parser.add_argument("--cases", type=Path, default=HERE / "golden.jsonl")
    parser.add_argument("--out", type=Path, default=HERE / "results")
    parser.add_argument("--real-backend", action="store_true", help="call the configured model (paid)")
    parser.add_argument("--budget-usd", type=float, help="required with --real-backend: the agreed budget")
    args = parser.parse_args(argv)
    if args.real_backend and not args.budget_usd:
        parser.error("--real-backend calls a paid model; agree a budget and pass --budget-usd first")
    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.only:
        wanted = {item.strip() for item in args.only.split(",")}
        cases = [case for case in cases if case["id"] in wanted]
    folder = args.out / args.variant
    (folder / "traces").mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows, errors = 0, 0
    with open(folder / "results.jsonl", "w", encoding="utf-8") as results, \
            open(folder / "errors.jsonl", "w", encoding="utf-8") as failures:
        for case in cases:
            for rep in range(args.reps):
                try:
                    row, trace = run_case(case, real_backend=args.real_backend)
                except Exception as exc:  # noqa: BLE001 - record and keep going
                    errors += 1
                    failures.write(json.dumps({"prompt_id": case["id"], "rep": rep, "error": repr(exc)}) + "\n")
                    continue
                row["rep"] = rep
                results.write(json.dumps(row, ensure_ascii=False) + "\n")
                (folder / "traces" / f"{case['id']}_rep{rep}.json").write_text(
                    json.dumps(trace, indent=2, ensure_ascii=False), encoding="utf-8")
                rows += 1
                print(f"{case['id']} rep {rep}: {'pass' if row['grade']['task_complete'] else 'FAIL'}"
                      + "".join(f"\n  - {c['check']}" for c in row["checks"] if not c["ok"]))
    (folder / "run.json").write_text(json.dumps({"runs": [{
        "started_at": started, "ended_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": "fake" if not args.real_backend else os.getenv("OPENTAG_DEFAULT_MODEL", "configured"),
        "reps": args.reps, "only": args.only, "cases": str(args.cases.relative_to(ROOT)) if args.cases.is_relative_to(ROOT)
        else str(args.cases), "git_commit": _git_commit(), "rows_written": rows, "errors": errors,
    }]}, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {rows} rows ({errors} errors) to {folder}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
