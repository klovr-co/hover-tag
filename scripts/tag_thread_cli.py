# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""``tag [TAG] threads`` and ``tag [TAG] thread CHANNEL THREAD_TS [reply|answer]``.

Tag.app runs the same commands with ``--json``. Reading never changes a file.
A reply or an answer is handed to the running bridge, which records it in the
thread's transcript; the commands refuse to run inside a Tag's own run, so the
agent can't answer its own questions.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

try:
    import tag_display as display
    import tag_threads as threads_lib
except ImportError:
    from scripts import tag_display as display
    from scripts import tag_threads as threads_lib

NOT_HERE = "This thread isn't on this computer. Open the link on the computer that runs the Tag."
NOT_RUNNING = "Start the Tag first. It replies and answers only while it runs."
INSIDE_RUN = "A Tag can't reply to or answer its own threads. Use Tag.app or the terminal."


def _parsers() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    listing = argparse.ArgumentParser(prog="tag [TAG] threads", description=(
        "List the Slack threads this Tag has worked in, newest first. "
        "`threads retention [DAYS]` shows or sets how long they are kept."))
    listing.add_argument("action", nargs="?", choices=("retention",))
    listing.add_argument("days", nargs="?", type=int, help="retention: keep threads this many days after their last message")
    listing.add_argument("--channel", help="only threads in this Slack channel ID")
    listing.add_argument("--limit", type=int, default=threads_lib.DEFAULT_LIST_LIMIT,
                         help=f"how many threads (1–{threads_lib.MAX_LIST_LIMIT}, default {threads_lib.DEFAULT_LIST_LIMIT})")
    listing.add_argument("--json", action="store_true", dest="json_output", help="structured output")
    one = argparse.ArgumentParser(prog="tag [TAG] thread", description=(
        "Show one thread's conversation, reply in it, or answer a question the Tag is waiting on."))
    one.add_argument("channel", help="Slack channel ID")
    one.add_argument("thread_ts", help="the thread's first message time, such as 1729000000.000100")
    one.add_argument("action", nargs="?", choices=("reply", "answer"))
    one.add_argument("prompt", nargs="?", help="answer: the question's ID")
    one.add_argument("choice", nargs="?", help="answer: the choice's ID")
    one.add_argument("--text", help="reply: what to send; - reads it from stdin")
    one.add_argument("--post-to-slack", action="store_true", help="reply: also post it in the Slack thread")
    one.add_argument("--json", action="store_true", dest="json_output", help="structured output")
    return listing, one


def run(home: Path, tag_id: str, command: str, argv: list[str], *, values: dict[str, str],
        channels: list[dict[str, Any]], tag_running: Callable[[Path], bool]) -> int:
    listing, one = _parsers()
    args = (listing if command == "threads" else one).parse_args(argv)
    names = {row["id"]: row["name"] for row in channels if row.get("name")}
    owner = next((part.strip() for part in values.get("SLACK_ALLOWED_USER_IDS", "").split(",") if part.strip()), None)

    def output(payload: dict[str, Any]) -> int:
        print(json.dumps({"schema_version": 1, "ok": True, "tag": tag_id, **payload}, indent=2, ensure_ascii=False))
        return 0

    def fail(message: str, code: str) -> int:
        if args.json_output:
            print(json.dumps({"schema_version": 1, "ok": False, "tag": tag_id, "error": message, "code": code}))
        else:
            print(message, file=sys.stderr)
        return 1

    try:
        if command == "threads":
            if args.action == "retention":
                if args.days is not None:
                    if os.environ.get("OPENTAG_CALLER_ID") or os.environ.get("OPENTAG_ROUTINE_ID"):
                        return fail(INSIDE_RUN, "inside_run")
                    threads_lib.set_retention_days(home, args.days)
                days = threads_lib.retention_days(home)
                if args.json_output:
                    return output({"retention_days": days})
                print(f"Threads are kept {days} days after their last message.")
                return 0
            if args.days is not None:
                listing.error("Only `threads retention` takes a number")
            if not 1 <= args.limit <= threads_lib.MAX_LIST_LIMIT:
                listing.error(f"--limit must be between 1 and {threads_lib.MAX_LIST_LIMIT}")
            result = threads_lib.list_threads(home, tag_id, channel=args.channel, limit=args.limit, names=names)
            if args.json_output:
                return output(result)
            _print_list(result)
            return 0
        if args.action is None:
            thread = threads_lib.thread_detail(home, tag_id, args.channel, args.thread_ts, names=names)
            if thread is None:
                return fail(NOT_HERE, "not_found")
            if args.json_output:
                return output({"thread": thread})
            _print_thread(thread)
            return 0
        if os.environ.get("OPENTAG_CALLER_ID") or os.environ.get("OPENTAG_ROUTINE_ID"):
            return fail(INSIDE_RUN, "inside_run")
        if threads_lib.thread_detail(home, tag_id, args.channel, args.thread_ts, names=names) is None:
            return fail(NOT_HERE, "not_found")
        if not tag_running(home):
            return fail(NOT_RUNNING, "not_running")
        if args.action == "reply":
            if args.prompt or args.choice:
                one.error("reply takes its text in --text")
            text = sys.stdin.read() if args.text == "-" else (args.text or "")
            request = threads_lib.request_reply(home, args.channel, args.thread_ts, text,
                                                post_to_slack=args.post_to_slack, by=owner)
            if args.json_output:
                return output({"request_id": request, "queued": True, "post_to_slack": args.post_to_slack})
            print("Sent to the Tag." + (" It's also posted in Slack." if args.post_to_slack else ""))
            return 0
        if not args.prompt or not args.choice:
            one.error("answer needs the question's ID and a choice")
        if args.text or args.post_to_slack:
            one.error("--text and --post-to-slack are only for reply")
        request = threads_lib.request_answer(home, args.channel, args.thread_ts, args.prompt, args.choice, by=owner)
        if args.json_output:
            return output({"request_id": request, "queued": True})
        print("Answer sent to the Tag.")
        return 0
    except threads_lib.ThreadError as exc:
        return fail(str(exc), "invalid")


STATUS_WORDS = {"needs_you": "Needs you", "working": "Working", "failed": "Couldn't finish",
                "stopped": "Stopped", "done": "Done"}


def _print_list(result: dict[str, Any]) -> None:
    display.header("Threads", f"kept {result['retention_days']} days after their last message")
    if not result["threads"]:
        display.info_row("", "No threads yet.")
    for row in result["threads"]:
        place = "DM" if row["dm"] else f"#{row['channel_name'] or row['channel']}"
        title = row["title"] or row["last_answer_preview"] or "Thread"
        display.info_row(STATUS_WORDS.get(row["status"], row["status"]), f"{place} · {title}",
                         good=row["status"] not in ("needs_you", "failed"))
        display.info_row("", f"tag thread {row['channel']} {row['thread_ts']}")


def _print_thread(thread: dict[str, Any]) -> None:
    display.header(thread["title"] or "Thread", STATUS_WORDS.get(thread["status"], thread["status"]))
    for item in thread["items"]:
        if item["type"] == "message":
            who = (item.get("author") or {}).get("name") or "Someone"
            print(f"\n{who}{' (Tag.app)' if item['via'] == 'app' else ''}:\n{item['text']}")
        elif item["type"] == "answer":
            print(f"\nTag{' (preview)' if item.get('preview_only') else ''}:\n{item['text']}")
            for change in item.get("files", []):
                print(f"  File: {change['name']}")
            for change in item.get("memory", []):
                print(f"  Memory {change['action']}: {change['key']}")
        elif item["type"] == "work":
            print(f"\n  {item['status']} · {item['step_count']} steps")
        elif item["type"] == "prompt":
            if item["status"] == "pending":
                print(f"\nWaiting for you: {item['text']}")
                for choice in item["choices"]:
                    print(f"  {choice['label']}: tag thread {thread['channel']} {thread['thread_ts']} "
                          f"answer {item['prompt_id']} {choice['id']}")
            elif item.get("answer"):
                answer = item["answer"]
                who = (answer.get("by") or {}).get("name") or "Someone"
                print(f"\n{item['text']} {who}: {answer.get('label') or answer.get('choice')}")
        elif item["type"] == "notice":
            print(f"\n  {item['text']}")
