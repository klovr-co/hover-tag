"""Bounded, private records of Slack request activity.

Tag stores public activity labels and bounded, redacted tool-item previews.
Raw App Server items, prompts, and reasoning never enter this store.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .codex_app_server import MCP_SERVICE_NAMES, MCP_TOOL_NAMES, activity_label, mcp_activity_label
    from .tag_activity_details import MAX_DETAIL_CHARS, MAX_TOOL_CHARS, sanitize_activity_details
    from .tag_paths import instance_home, restrict_windows_acl
except ImportError:  # Direct script execution does not create a package context.
    from codex_app_server import MCP_SERVICE_NAMES, MCP_TOOL_NAMES, activity_label, mcp_activity_label
    from tag_activity_details import MAX_DETAIL_CHARS, MAX_TOOL_CHARS, sanitize_activity_details
    from tag_paths import instance_home, restrict_windows_acl


SCHEMA_VERSION = 1
RETENTION_SECONDS = 30 * 24 * 60 * 60
MAX_RECORDS = 200
MAX_EVENTS = 60
ACTIVITY_DETAIL_ACTION_ID = "opentag_activity_detail"
RUN_ID_RE = re.compile(r"^[a-f0-9]{32}$")
OUTCOMES = frozenset({"running", "completed", "failed", "interrupted"})
ITEM_STATUSES = frozenset({"running", "completed", "failed", "declined", "interrupted", "unknown"})


def _public_labels() -> frozenset[str]:
    examples: list[dict[str, Any]] = [
        {"type": "webSearch"}, {"type": "fileChange"},
        {"type": "imageView"}, {"type": "imageGeneration"},
        {"type": "dynamicToolCall"},
    ]
    examples.extend({"type": "commandExecution", "command": command} for command in (
        "cat file", "cp source destination", "pytest", "python mfs_search.py query",
        "python mfs_cat.py path", "python mfs_ls.py", "python slack_canvas.py",
        "python slack_post_message.py", "create-document.py", "unknown-command",
    ))
    labels = {label for item in examples if (label := activity_label(item))}
    for server in (*MCP_SERVICE_NAMES, "unrecognized"):
        for tool in (*MCP_TOOL_NAMES, "unrecognized"):
            labels.add(mcp_activity_label({"server": server, "tool": tool}))
    return frozenset(labels)


PUBLIC_LABELS = _public_labels()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ActivityStore:
    """One JSON file per run, with atomic updates and bounded retention."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or instance_home() / "state/activity"
        self.lock = threading.RLock()

    def _path(self, run_id: str) -> Path | None:
        return self.root / f"{run_id}.json" if RUN_ID_RE.fullmatch(run_id) else None

    def _write(self, record: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        restrict_windows_acl(self.root)
        path = self._path(record["run_id"])
        assert path is not None
        temporary = self.root / f".{record['run_id']}.{uuid.uuid4().hex}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(record, stream, separators=(",", ":"))
                stream.write("\n")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _prune(self) -> None:
        cutoff = time.time() - RETENTION_SECONDS
        files = sorted(self.root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for index, path in enumerate(files):
            if index >= MAX_RECORDS or path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)

    def create(self, *, team: str, channel: str, thread_ts: str,
               request_ts: str, requester: str) -> str:
        run_id = uuid.uuid4().hex
        record = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "team": team,
            "channel": channel,
            "thread_ts": thread_ts,
            "request_ts": request_ts,
            "requester": requester,
            "started_at": _timestamp(),
            "finished_at": None,
            "outcome": "running",
            "events": [],
            "omitted": 0,
        }
        with self.lock:
            self._write(record)
            self._prune()
        return run_id

    def get(self, run_id: str) -> dict[str, Any] | None:
        path = self._path(run_id)
        if path is None:
            return None
        try:
            if time.time() - path.stat().st_mtime > RETENTION_SECONDS:
                return None
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return None
        if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
            return None
        if record.get("run_id") != run_id or not isinstance(record.get("outcome"), str) or record["outcome"] not in OUTCOMES:
            return None
        if not all(isinstance(record.get(field), str) for field in (
            "team", "channel", "thread_ts", "request_ts", "requester", "started_at",
        )):
            return None
        events = record.get("events")
        if not isinstance(events, list) or len(events) > MAX_EVENTS:
            return None
        if not isinstance(record.get("omitted"), int) or record["omitted"] < 0:
            return None
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("label"), str) or event["label"] not in PUBLIC_LABELS:
                return None
            if not isinstance(event.get("status"), str) or event["status"] not in ITEM_STATUSES:
                return None
            if not isinstance(event.get("started_at"), str):
                return None
            if not isinstance(event.get("id"), str) or not re.fullmatch(r"[a-f0-9]{64}", event["id"]):
                return None
            details = event.get("details", {})
            if not isinstance(details, dict) or any(
                key not in {"tool", "input", "output"}
                or not isinstance(value, str)
                or len(value) > (MAX_TOOL_CHARS if key == "tool" else MAX_DETAIL_CHARS)
                for key, value in details.items()
            ):
                return None
        return record

    def observe(self, run_id: str, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        item_id = event.get("activity_id")
        label = event.get("label")
        if event_type not in {"activity_start", "activity_complete"}:
            return
        if not isinstance(item_id, str) or not item_id or len(item_id) > 200:
            return
        if not isinstance(label, str) or label not in PUBLIC_LABELS:
            label = "Using a connected tool…"
        item_id = hashlib.sha256(item_id.encode("utf-8")).hexdigest()
        details = sanitize_activity_details(event.get("details"))
        with self.lock:
            record = self.get(run_id)
            if record is None or record["outcome"] != "running":
                return
            events = record["events"]
            if event_type == "activity_start":
                if any(item["id"] == item_id for item in events):
                    return
                if len(events) >= MAX_EVENTS:
                    record["omitted"] += 1
                else:
                    events.append({
                        "id": item_id, "label": label, "started_at": _timestamp(),
                        "finished_at": None, "status": "running", "details": details,
                    })
            else:
                match = next((item for item in events if item["id"] == item_id), None)
                if match is None or match["status"] != "running":
                    return
                status = event.get("status")
                match["status"] = status if isinstance(status, str) and status in ITEM_STATUSES - {"running"} else "unknown"
                match["finished_at"] = _timestamp()
                match.setdefault("details", {}).update(details)
            self._write(record)

    def finish(self, run_id: str, outcome: str) -> None:
        with self.lock:
            record = self.get(run_id)
            if record is None or record["outcome"] != "running":
                return
            record["outcome"] = outcome if outcome in OUTCOMES - {"running"} else "failed"
            record["finished_at"] = _timestamp()
            for event in record["events"]:
                if event["status"] == "running":
                    event["status"] = "interrupted" if outcome == "interrupted" else "unknown"
                    event["finished_at"] = record["finished_at"]
            self._write(record)


def activity_modal(record: dict[str, Any]) -> dict[str, Any]:
    """Render a scannable timeline with requester-only detail actions."""
    status = {"running": "Running", "completed": "Finished", "failed": "Failed",
              "interrupted": "Stopped"}[record["outcome"]]
    blocks: list[dict[str, Any]] = [{
        "type": "section",
        "text": {"type": "mrkdwn", "text": (
            f"*{status}* · {len(record['events'])} observed tool steps\n"
            "Select a step to see its input and result."
        )},
    }, {"type": "divider"}]
    for index, event in enumerate(record["events"]):
        time_label = event["started_at"][11:19] + " UTC"
        state = {"running": "Running", "completed": "Tool finished", "failed": "Failed",
                 "declined": "Declined", "interrupted": "Interrupted", "unknown": "Outcome unknown"}[event["status"]]
        details = event.get("details", {})
        tool = details.get("tool")
        lines = [f"{time_label} · {event['label']}", state + (f" · {tool}" if tool else "")]
        blocks.append({
            "type": "section",
            "text": {"type": "plain_text", "text": "\n".join(lines)},
            "accessory": {
                "type": "button", "action_id": ACTIVITY_DETAIL_ACTION_ID,
                "text": {"type": "plain_text", "text": "Details"},
                "value": str(index),
            },
        })
    if not record["events"]:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
                       "text": "No tool activity was reported for this run."}})
    if record.get("omitted", 0):
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
                       "text": f"{record['omitted']} later events omitted to keep this view bounded."}]})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
                   "text": "Tool completion alone does not confirm an external action's outcome."}]})
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "Tag activity"},
        "close": {"type": "plain_text", "text": "Close"},
        "private_metadata": json.dumps({
            "run_id": record["run_id"], "team": record["team"],
            "channel": record["channel"], "thread_ts": record["thread_ts"],
        }, separators=(",", ":")),
        "blocks": blocks,
    }


def activity_detail_modal(record: dict[str, Any], index: int) -> dict[str, Any]:
    """Show one tool's input and result in Slack's second modal view."""
    event = record["events"][index]
    status = {"running": "Running", "completed": "Tool finished", "failed": "Failed",
              "declined": "Declined", "interrupted": "Interrupted", "unknown": "Outcome unknown"}[event["status"]]
    details = event.get("details", {})
    blocks: list[dict[str, Any]] = [{
        "type": "section", "text": {"type": "plain_text", "text": (
            f"{event['started_at'][11:19]} UTC · {status}\n{event['label']}"
        )},
    }]
    if details.get("tool"):
        blocks.append({"type": "context", "elements": [{
            "type": "plain_text", "text": f"Tool: {details['tool']}",
        }]})
    for field, heading in (("input", "Input"), ("output", "Result")):
        blocks.append({"type": "header", "text": {"type": "plain_text", "text": heading}})
        blocks.append({"type": "section", "text": {"type": "plain_text", "text": (
            details.get(field) or "Not reported by App Server."
        )}})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": (
        "Common credentials are redacted and previews are shortened. "
        "Private reasoning and prompts are not shown."
    )}]})
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "Tool details"},
        "close": {"type": "plain_text", "text": "Back"},
        "blocks": blocks,
    }
