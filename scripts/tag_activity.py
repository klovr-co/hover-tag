"""Bounded, private records of Slack request activity.

Tag stores public activity labels, bounded reply excerpts, and redacted tool-item previews.
Raw App Server items, prompts, and reasoning never enter this store.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

try:
    from .agent_models import SUPPORTED_REASONING_EFFORTS
    from .agent_activity import MCP_SERVICE_NAMES, MCP_TOOL_NAMES, activity_label, mcp_activity_label, token_usage
    from .tag_activity_details import MAX_DETAIL_CHARS, MAX_TOOL_CHARS, sanitize_activity_details, preview
    from .tag_paths import instance_home, restrict_windows_acl
except ImportError:  # Direct script execution does not create a package context.
    from agent_models import SUPPORTED_REASONING_EFFORTS
    from agent_activity import MCP_SERVICE_NAMES, MCP_TOOL_NAMES, activity_label, mcp_activity_label, token_usage
    from tag_activity_details import MAX_DETAIL_CHARS, MAX_TOOL_CHARS, sanitize_activity_details, preview
    from tag_paths import instance_home, restrict_windows_acl


SCHEMA_VERSION = 1
RETENTION_SECONDS = 30 * 24 * 60 * 60
MAX_RECORDS = 200
MAX_EVENTS = 60
MAX_REPLY_PREVIEW = 220
ACTIVITY_DETAIL_ACTION_ID = "opentag_activity_detail"
RUN_ID_RE = re.compile(r"^[a-f0-9]{32}$")
MAX_ARTIFACTS = 20
OUTCOMES = frozenset({"running", "completed", "failed", "interrupted"})
ITEM_STATUSES = frozenset({"running", "completed", "failed", "declined", "interrupted", "unknown"})


def artifact_records(value: object) -> list[dict[str, str]]:
    """Bounded output metadata, with no file contents or temporary download URLs."""
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:MAX_ARTIFACTS]:
        if not isinstance(item, dict) or item.get("delivery") not in ("uploaded", "local", "upload_failed"):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        record = {"name": preview(name.replace("\\", "/").split("/")[-1], limit=180),
                  "kind": "image" if item.get("kind") == "image" else "file",
                  "delivery": item["delivery"]}
        url = item.get("url")
        if isinstance(url, str):
            try:
                parsed = urllib.parse.urlsplit(url)
                if (parsed.scheme == "https" and parsed.hostname
                        and parsed.hostname.endswith(".slack.com") and not parsed.username
                        and not parsed.password and parsed.port in (None, 443)
                        and parsed.path.startswith("/files/")):
                    record["url"] = urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path, "", ""))
            except ValueError:
                pass
        local = item.get("local_path")
        if isinstance(local, str) and Path(local).is_absolute() and "\x00" not in local:
            record["local_path"] = local
        result.append(record)
    return result


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


def reply_preview(answer: object) -> str:
    """A bounded excerpt of the delivered answer, never an inferred task outcome."""
    if not isinstance(answer, str):
        return ""
    text = html.unescape(answer[:8_000])
    text = re.sub(r"```[^\n]*\n.*?(?:```|$)", " ", text, flags=re.S)
    text = re.sub(r"!?\[([^\]]+)\]\([^\n)]*\)", r"\1", text)
    text = re.sub(r"<(?:https?://|mailto:)[^>|]+\|([^>]+)>", r"\1", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)", "", text)
    text = re.sub(r"[*`~]", "", text)
    text = " ".join(preview(text, limit=8_000).split())
    if len(text) <= MAX_REPLY_PREVIEW:
        return text
    clipped = text[:MAX_REPLY_PREVIEW - 1]
    boundary = clipped.rfind(" ")
    if boundary > MAX_REPLY_PREVIEW // 2:
        clipped = clipped[:boundary]
    return clipped.rstrip() + "…"


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
               request_ts: str, requester: str, reasoning_effort: str | None = None) -> str:
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
        if reasoning_effort in SUPPORTED_REASONING_EFFORTS:
            record["reasoning_effort"] = reasoning_effort
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

    def save_artifacts(self, run_id: str, artifacts: list[dict[str, str]]) -> None:
        with self.lock:
            record = self.get(run_id)
            if record is not None:
                record["artifacts"] = artifact_records(artifacts)
                self._write(record)

    def observe(self, run_id: str, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "usage":
            usage = token_usage(event.get("usage"))
            if usage is not None:
                with self.lock:
                    record = self.get(run_id)
                    if record and record["outcome"] == "running":
                        # Provider snapshots are cumulative; duplicates replace,
                        # never add to, the last reported total.
                        record["usage"] = usage
                        self._write(record)
            return
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

    def attach_error(self, run_id: str, reference: str) -> None:
        """Keep the exact failure association, including when a Slack request is retried."""
        if not re.fullmatch(r"[A-F0-9]{8}", reference):
            return
        with self.lock:
            record = self.get(run_id)
            if record is not None:
                record["error_reference"] = reference
                self._write(record)

    def save_model(self, run_id: str, backend: str, model: str, label: str = "", *, reasoning_effort: str | None = None) -> None:
        """Remember the model reported for this run, never a later Tag setting."""
        if backend not in {"codex", "claude"} or not isinstance(model, str) or not model:
            return
        with self.lock:
            record = self.get(run_id)
            if record is not None:
                record.update(backend=backend, model=reply_preview(model)[:128],
                              model_name=reply_preview(label or model)[:128])
                if reasoning_effort in SUPPORTED_REASONING_EFFORTS:
                    record["reasoning_effort"] = reasoning_effort
                self._write(record)

    def summary_status(self, run_id: str, status: str) -> None:
        if status not in {"pending", "unavailable"}:
            return
        with self.lock:
            record = self.get(run_id)
            if record and record["outcome"] == "completed" and not record.get("reply_summary"):
                record.update(reply_summary_status=status, reply_summary_updated_at=_timestamp())
                self._write(record)

    def save_reply(self, run_id: str, answer: str) -> None:
        """Save only a redacted preview after successful Slack delivery."""
        with self.lock:
            record = self.get(run_id)
            if record is not None and record["outcome"] == "completed":
                record["reply_preview"] = reply_preview(answer)
                self._write(record)

    def save_reply_summary(self, run_id: str, summary: str) -> None:
        """Cache a generated TL;DR once, preserving the fallback preview."""
        clean = reply_preview(summary)
        if not clean:
            return
        with self.lock:
            record = self.get(run_id)
            if record is not None and record["outcome"] == "completed" and not record.get("reply_summary"):
                record["reply_summary"] = clean
                record["reply_summary_status"] = "ready"
                self._write(record)


RECENT_KINDS = {"completed": "replied", "failed": "failed", "interrupted": "stopped", "running": "working"}
MAX_RECENT = 50


def channel_names(scopes: str) -> dict[str, str]:
    """Channel names from saved Slack history sources such as ``…/channels/launch__C0123``."""
    names: dict[str, str] = {}
    for scope in scopes.split(","):
        _, separator, rest = scope.strip().partition("/channels/")
        name, _, channel = urllib.parse.unquote(rest.split("/", 1)[0]).rpartition("__")
        if separator and name and re.fullmatch(r"[CG][A-Z0-9]+", channel):
            names[channel] = name
    return names


def _summary_expired(record: dict) -> bool:
    try:
        at = datetime.fromisoformat(record["reply_summary_updated_at"])
        return (datetime.now(timezone.utc) - at).total_seconds() > 30 * 60
    except (KeyError, TypeError, ValueError):
        return True


def generation_seconds(record: dict) -> float | None:
    if record.get("outcome") == "running":
        return None
    try:
        started = datetime.fromisoformat(record["started_at"])
        finished = datetime.fromisoformat(record["finished_at"])
        return max(0, (finished - started).total_seconds())
    except (KeyError, TypeError, ValueError):
        return None


def artifact_thread_url(record: dict) -> str | None:
    if (re.fullmatch(r"T[A-Z0-9]+", record["team"])
            and re.fullmatch(r"[CDG][A-Z0-9]+", record["channel"])
            and re.fullmatch(r"\d+\.\d+", record["thread_ts"])):
        return "slack://channel?" + urllib.parse.urlencode({
            "team": record["team"], "id": record["channel"], "message": record["thread_ts"]})
    return None


def recent_activity(root: Path, scopes: str = "", limit: int = MAX_RECENT, *,
                    cached_names: dict[str, dict[str, str]] | None = None,
                    channel: str | None = None, hide_errors: bool = False) -> list[dict[str, Any]]:
    """What a Tag did recently, with a short excerpt when a delivered reply was saved.

    Records are read through ``ActivityStore.get`` validation, so invalid or
    expired ones are skipped, and nothing is written or pruned. Prompts,
    requesters, and tool steps are never included; only how many steps ran.
    """
    store = ActivityStore(root)
    names = channel_names(scopes)
    items: list[tuple[datetime, dict[str, Any]]] = []
    try:
        paths = list(root.glob("*.json"))
    except OSError:
        return []
    for path in paths:
        record = store.get(path.stem)
        if record is None:
            continue
        if channel is not None and record["channel"] != channel:
            continue
        if hide_errors and record["outcome"] == "failed":
            continue
        finished = record.get("finished_at")
        raw = finished if record["outcome"] != "running" and isinstance(finished, str) else record["started_at"]
        try:
            at = datetime.fromisoformat(raw)
        except ValueError:
            continue
        at = at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)
        record_channel = record["channel"]
        dm = record_channel.startswith("D")
        items.append((at, {
            "run_id": record["run_id"],
            "at": at.isoformat(timespec="seconds"), "kind": RECENT_KINDS[record["outcome"]],
            "channel": record_channel,
            "channel_name": None if dm else (cached_names or {}).get(record["team"], {}).get(record_channel, names.get(record_channel)),
            "dm": dm,
            **({"step_count": steps} if (steps := len(record["events"]) + record["omitted"]) else {}),
            **({"duration_seconds": duration} if (duration := generation_seconds(record)) is not None else {}),
            **({"reasoning_effort": effort} if (effort := record.get("reasoning_effort")) in SUPPORTED_REASONING_EFFORTS else {}),
            **({"usage": usage} if (usage := token_usage(record.get("usage"))) is not None else {}),
            **({"artifacts": artifacts} if (artifacts := artifact_records(record.get("artifacts"))) else {}),
            **({"artifact_thread_url": url} if artifacts and (url := artifact_thread_url(record)) else {}),
            **{key: record[key] for key in ("backend", "model", "model_name")
               if isinstance(record.get(key), str)},
            **({"reply_summary_status": (
                "unavailable" if record.get("reply_summary_status") == "pending"
                and _summary_expired(record) else record["reply_summary_status"])}
               if record.get("reply_summary_status") in {"pending", "unavailable", "ready"} else {}),
            **({"reply_preview": reply_preview(record["reply_preview"])}
               if record["outcome"] == "completed" and record.get("reply_preview") else {}),
            **({"reply_summary": reply_preview(record["reply_summary"])}
               if record["outcome"] == "completed" and record.get("reply_summary") else {}),
        }))
    items.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in items[:limit]]


def activity_details(root: Path, run_id: str, *, report_directory: Path | None = None) -> dict[str, Any] | None:
    """Read one retained run, with sanitized previews and a matching failure report.

    Older records need no rewrite: they already contain the normalized events
    from both backends. Legacy reports are matched only when routing and the
    run's time window identify one unambiguous failure. Reads never prune files.
    """
    try:
        from .tag_error_reporting import ErrorReport, ERROR_REPORT_RETENTION_SECONDS, redact_sensitive_text
    except ImportError:
        from tag_error_reporting import ErrorReport, ERROR_REPORT_RETENTION_SECONDS, redact_sensitive_text
    record = ActivityStore(root).get(run_id)
    if record is None:
        return None
    events = [{key: event.get(key) for key in ("label", "status", "started_at", "finished_at")}
              | {"details": sanitize_activity_details(event.get("details"))} for event in record["events"]]
    reports = []
    directory = report_directory or root.parent / "error-reports"
    if record["outcome"] == "failed":
        for path in directory.glob("*.json"):
            try:
                report = ErrorReport.from_dict(json.loads(path.read_text(encoding="utf-8")))
                if report is None:
                    continue
                at = datetime.fromisoformat(report.failure_at.replace("Z", "+00:00"))
                if not 0 <= (datetime.now(timezone.utc) - at).total_seconds() <= ERROR_REPORT_RETENTION_SECONDS:
                    continue
                origin = report.origin
                if (origin.team_id, origin.channel_id, origin.thread_ts, origin.request_ts, origin.requester_id) != (
                    record["team"], record["channel"], record["thread_ts"], record["request_ts"], record["requester"]):
                    continue
                if record.get("error_reference"):
                    matches = report.reference == record["error_reference"]
                else:
                    start = datetime.fromisoformat(record["started_at"])
                    end = datetime.fromisoformat(record["finished_at"])
                    # Old activity timestamps have second precision. Reports are
                    # created immediately after finish, before health checks.
                    matches = start <= at < end + timedelta(seconds=2)
                if matches:
                    reports.append(report)
            except (OSError, ValueError, TypeError, OverflowError):
                continue
    report = reports[0] if len(reports) == 1 else None
    return {"run_id": run_id, "outcome": record["outcome"], "started_at": record["started_at"],
            "finished_at": record.get("finished_at"), "team": record["team"], "channel": record["channel"],
            "thread_ts": record["thread_ts"], "events": events, "omitted": record["omitted"],
            "error": {"reference": report.reference, "text": redact_sensitive_text(report.report_text())} if report else None}


def activity_details_text(details: dict[str, Any]) -> str:
    lines = [f"Request {details['run_id']} · {details['outcome']}", f"Started: {details['started_at']}"]
    if details["finished_at"]:
        lines.append(f"Finished: {details['finished_at']}")
    for event in details["events"]:
        lines.append(f"\n{event['label']} · {event['status']}")
        for key, value in event["details"].items():
            lines.append(f"{key.capitalize()}: {value}")
    if not details["events"]:
        lines.append("No tool activity was recorded for this request.")
    if details["omitted"]:
        lines.append(f"{details['omitted']} later steps omitted.")
    if details["error"]:
        lines.append("\n" + details["error"]["text"])
    elif details["outcome"] == "failed":
        lines.append("No matching error report is available for this request.")
    return "\n".join(lines)


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
