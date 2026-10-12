# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""``tag [TAG] routine list|show|add|edit|pause|resume|remove|run-now``.

Tag.app runs the same commands with ``--json``. Creating or editing a routine
here counts as the operator's approval of exactly what was saved; the running
bridge picks up the change on its next check, without a restart.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import tag_display as display
    import tag_routines as routines_lib
    from tag_routine_scheduler import schedule_key
except ImportError:
    from scripts import tag_display as display
    from scripts import tag_routines as routines_lib
    from scripts.tag_routine_scheduler import schedule_key

ACTIONS = ("list", "show", "add", "edit", "pause", "resume", "remove", "run-now")
ATTENTION = frozenset({"failed", "budget_exceeded", "timeout", "blocked", "missed", "interrupted"})
OPERATOR = "local-operator"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="tag [TAG] routine", description=(
        "Routines run a saved prompt on a schedule and post the result in one Slack channel. "
        "Saving a routine here approves exactly what is saved."))
    actions = root.add_subparsers(dest="action", required=True, metavar="|".join(ACTIONS))

    def command(name: str, help_text: str, *, reference: bool = True) -> argparse.ArgumentParser:
        sub = actions.add_parser(name, help=help_text, description=help_text)
        if reference:
            sub.add_argument("routine", help="routine id or name")
        sub.add_argument("--json", action="store_true", dest="json_output", help="structured output")
        return sub

    command("list", "List this Tag's routines.", reference=False)
    command("show", "Show one routine, its next runs and its history.")
    for name, help_text in (("add", "Add a routine."), ("edit", "Change a routine.")):
        sub = command(name, help_text, reference=name == "edit")
        sub.add_argument("--name")
        sub.add_argument("--schedule", help="every 2h · daily 09:00 · weekdays 09:00,13:30 · mon,fri 17:30 · "
                                            "monthly 1 09:00 · monthly last 17:00 (add 'exact' to keep :00)")
        sub.add_argument("--timezone", help="IANA timezone, such as Europe/London (default: this computer's)")
        sub.add_argument("--channel", help="channel ID or #name; one of the Tag's channels")
        prompt = sub.add_mutually_exclusive_group()
        prompt.add_argument("--prompt", help="what the Tag should do each run")
        prompt.add_argument("--prompt-file", type=Path, help="read the prompt from a file, or - for stdin")
        sub.add_argument("--owner", help="Slack member ID the routine acts for (default: the Tag's only allowed user)")
        sub.add_argument("--catch-up", choices=routines_lib.CATCH_UP,
                         help="after a sleep or a stopped Tag: run the latest missed slot once (latest), or skip it")
        sub.add_argument("--max-budget-usd", type=float, help="per-run spend limit")
        sub.add_argument("--max-turns", type=int, help="per-run turn limit")
        sub.add_argument("--timeout-minutes", type=int, help="per-run time limit")
        sub.add_argument("--max-tokens", type=int, help="per-run token limit when cost is unknown")
        sub.add_argument("--via", choices=("cli", "tag-app"), default="cli", help=argparse.SUPPRESS)
        if name == "add":
            sub.add_argument("--paused", action="store_true", help="save it without starting it")
            sub.add_argument("--from-json", type=Path, metavar="FILE",
                             help="read the whole routine as a JSON object from FILE, or - for stdin")
    command("pause", "Stop a routine from running until it is resumed.")
    command("resume", "Let a paused routine run again from its next scheduled time.")
    command("remove", "Delete a routine and its run history.")
    command("run-now", "Run a routine once now. The Tag must be running.")
    return root


def local_timezone() -> str | None:
    """This computer's IANA timezone, when it can be told without guessing."""
    candidates = [os.environ.get("TZ", "").lstrip(":")]
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            candidates.append(target.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    for name in candidates:
        if not name:
            continue
        try:
            ZoneInfo(name)
            return name
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return None


def resolve_channel(raw: str, channels: list[dict[str, Any]], policy: str) -> str:
    value = raw.strip()
    if routines_lib.CHANNEL_RE.fullmatch(value):
        channel = value
    else:
        name = value.lstrip("#").casefold()
        matches = [row["id"] for row in channels if (row.get("name") or "").casefold() == name]
        if len(matches) != 1:
            raise routines_lib.RoutineError(f"No channel named {raw!r} among this Tag's channels; use its ID")
        channel = matches[0]
    allowed = {row["id"] for row in channels}
    if allowed and channel not in allowed and policy != "invited":
        raise routines_lib.RoutineError("Routines can post only in the Tag's own channels")
    return channel


def _prompt(args: argparse.Namespace) -> str | None:
    if getattr(args, "prompt_file", None) is not None:
        stream = sys.stdin if str(args.prompt_file) == "-" else None
        return stream.read() if stream else args.prompt_file.read_text(encoding="utf-8")
    return args.prompt


def _fields(args: argparse.Namespace, values: dict[str, str], channels: list[dict[str, Any]],
            current: dict[str, Any] | None) -> dict[str, Any]:
    """The routine fields given on the command line, checked against this Tag."""
    fields: dict[str, Any] = {}
    for key in ("name", "schedule", "timezone"):
        if getattr(args, key) is not None:
            fields[key] = getattr(args, key)
    if (prompt := _prompt(args)) is not None:
        fields["prompt"] = prompt
    if args.channel is not None:
        fields["channel"] = resolve_channel(args.channel, channels, values.get("SLACK_CHANNEL_POLICY", ""))
    allowed = [item.strip() for item in values.get("SLACK_ALLOWED_USER_IDS", "").split(",") if item.strip()]
    if args.owner is not None:
        if allowed and args.owner not in allowed:
            raise routines_lib.RoutineError("The owner must be one of the people allowed to use this Tag")
        fields["owner"] = args.owner
    elif current is None:
        if len(allowed) != 1:
            raise routines_lib.RoutineError("Name the person this routine acts for with --owner U…")
        fields["owner"] = allowed[0]
    if args.catch_up is not None:
        fields["catch_up"] = args.catch_up
    limits = dict((current or {}).get("limits") or routines_lib.DEFAULT_LIMITS)
    changed_limits = False
    for option, key, scale in (("max_budget_usd", "max_budget_usd", 1), ("max_turns", "max_turns", 1),
                               ("timeout_minutes", "timeout_seconds", 60), ("max_tokens", "max_tokens", 1)):
        value = getattr(args, option)
        if value is not None:
            limits[key] = value * scale
            changed_limits = True
    if changed_limits or current is None:
        fields["limits"] = limits
    if current is None:
        fields.setdefault("timezone", local_timezone())
        if not fields.get("timezone"):
            raise routines_lib.RoutineError("Give --timezone, such as Europe/London")
        if getattr(args, "paused", False):
            fields["enabled"] = False
    return fields


def summary(routine: dict[str, Any], state: dict[str, Any], channels: dict[str, str | None],
            now: datetime) -> dict[str, Any]:
    history = state.get("history") or []
    last = history[-1] if history else None
    next_due = None
    if routine["enabled"]:
        saved = state.get("next_due")
        if isinstance(saved, str) and state.get("schedule_key") == schedule_key(routine):
            next_due = saved
        else:
            next_due = routines_lib.next_run(routine, now).isoformat()
    approved = routines_lib.approved(routine)
    return {
        "id": routine["id"],
        "name": routine["name"],
        "schedule": routine["schedule"]["text"],
        "timezone": routine["timezone"],
        "channel": routine["channel"],
        "channel_name": channels.get(routine["channel"]),
        "prompt": routine["prompt"],
        "owner": routine["owner"],
        "enabled": routine["enabled"],
        "paused_reason": routine.get("paused_reason"),
        "catch_up": routine["catch_up"],
        "limits": routine["limits"],
        "approved": approved,
        "approval": routine.get("approval"),
        "running": isinstance(state.get("running"), dict),
        "next_run": next_due,
        "last_run": last,
        "consecutive_failures": state.get("consecutive_failures", 0),
        "attention": (not approved or bool(routine.get("paused_reason"))
                      or bool(last and last.get("outcome") in ATTENTION)),
    }


def run(home: Path, tag_id: str, argv: list[str], *, values: dict[str, str],
        channels: list[dict[str, Any]], tag_running: Callable[[Path], bool],
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> int:
    args = parser().parse_args(argv)
    store = routines_lib.RoutineStore(home)
    states = routines_lib.RoutineStateStore(home)
    names = {row["id"]: row.get("name") for row in channels}
    moment = now()

    def describe(routine: dict[str, Any]) -> dict[str, Any]:
        return summary(routine, states.load(routine["id"]), names, moment)

    def output(payload: dict[str, Any]) -> None:
        print(json.dumps({"schema_version": 1, "ok": True, "tag": tag_id, **payload}, indent=2, ensure_ascii=False))

    running = tag_running(home)
    if args.action == "list":
        rows = [describe(r) for r in store.load()]
        if args.json_output:
            output({"tag_running": running, "routines": rows, "invalid": store.invalid_count(),
                    "limits": {"per_tag": routines_lib.MAX_ROUTINES_PER_TAG,
                               "per_channel": routines_lib.MAX_ROUTINES_PER_CHANNEL,
                               "min_interval_seconds": routines_lib.MIN_INTERVAL_SECONDS}})
            return 0
        display.header("Routines", f"Tag '{tag_id}'")
        if not rows:
            display.info_row("Routines", "None yet")
        for row in rows:
            state = "running now" if row["running"] else ("paused" if not row["enabled"] else
                                                           f"next {_local(row['next_run'], row['timezone'])}")
            where = f"#{row['channel_name']}" if row["channel_name"] else row["channel"]
            display.info_row(row["name"], f"{row['schedule']} · {where} · {state}", good=not row["attention"])
        if rows and not running:
            display.paragraph("This Tag is stopped, so its routines won't run until it starts.", "tag start")
        display.next_action("Add a routine", f'tag {tag_id} routine add --name "…" --schedule "weekdays 09:00" '
                                             '--channel "#…" --prompt "…"')
        return 0

    if args.action == "add":
        if args.from_json is not None:
            text = sys.stdin.read() if str(args.from_json) == "-" else args.from_json.read_text(encoding="utf-8")
            try:
                raw = json.loads(text)
            except ValueError:
                raise routines_lib.RoutineError("--from-json needs one JSON object") from None
            if not isinstance(raw, dict):
                raise routines_lib.RoutineError("--from-json needs one JSON object")
            raw.pop("approval", None)
            if "channel" in raw:
                raw["channel"] = resolve_channel(str(raw["channel"]), channels, values.get("SLACK_CHANNEL_POLICY", ""))
            raw.setdefault("timezone", local_timezone())
            allowed = [item.strip() for item in values.get("SLACK_ALLOWED_USER_IDS", "").split(",") if item.strip()]
            if "owner" not in raw and len(allowed) == 1:
                raw["owner"] = allowed[0]
            if allowed and raw.get("owner") not in allowed:
                raise routines_lib.RoutineError("The owner must be one of the people allowed to use this Tag")
        else:
            missing = [flag for flag, value in (("--name", args.name), ("--schedule", args.schedule),
                                                ("--channel", args.channel)) if value is None]
            if args.prompt is None and args.prompt_file is None:
                missing.append("--prompt")
            if missing:
                raise routines_lib.RoutineError("routine add needs " + ", ".join(missing))
            raw = _fields(args, values, channels, None)
        routine = store.add(raw, by=OPERATOR, via=args.via, now=moment)
        return _done(args, tag_id, "Added", describe(routine), running, output)

    routine = store.get(args.routine)
    if args.action == "show":
        row = describe(routine)
        state = states.load(routine["id"])
        upcoming = ([t.isoformat() for t in routines_lib.upcoming(routine, moment)] if routine["enabled"] else [])
        if args.json_output:
            output({"tag_running": running, "routine": row, "upcoming": upcoming,
                    "history": list(reversed(state.get("history") or []))})
            return 0
        display.header("Routine", row["name"])
        display.info_row("Schedule", f"{row['schedule']} ({row['timezone']})")
        display.info_row("Channel", f"#{row['channel_name']}" if row["channel_name"] else row["channel"])
        display.info_row("Owner", row["owner"])
        display.info_row("State", "paused" + (f": {row['paused_reason']}" if row["paused_reason"] else "")
                         if not row["enabled"] else "active", good=row["enabled"])
        display.info_row("Approved", f"by {routine['approval']['by']} at {routine['approval']['at']}"
                         if row["approved"] else "No: changed after approval", good=row["approved"])
        limits = routine["limits"]
        display.info_row("Limits", f"${limits['max_budget_usd']:.2f} · {limits['max_turns']} turns · "
                                   f"{limits['timeout_seconds'] // 60} min per run")
        for moment_text in upcoming:
            display.info_row("Next", _local(moment_text, routine["timezone"]))
        for entry in list(reversed(state.get("history") or []))[:10]:
            when = entry.get("started") or entry.get("scheduled_for") or ""
            detail = entry["outcome"].replace("_", " ") + (f": {entry['diagnostic']}" if entry.get("diagnostic") else "")
            display.info_row(_local(when, routine["timezone"]) if when else "—", detail,
                             good=entry["outcome"] not in ATTENTION)
        display.paragraph("Prompt:\n" + routine["prompt"])
        return 0
    if args.action == "edit":
        changes = _fields(args, values, channels, routine)
        if not changes:
            raise routines_lib.RoutineError("Nothing to change. Give at least one option, such as --schedule")
        updated = store.update(routine["id"], changes, by=OPERATOR, via=args.via, now=moment)
        return _done(args, tag_id, "Saved", describe(updated), running, output)
    if args.action in {"pause", "resume"}:
        updated = store.set_enabled(routine["id"], args.action == "resume")
        return _done(args, tag_id, "Paused" if args.action == "pause" else "Resumed", describe(updated),
                     running, output)
    if args.action == "remove":
        removed = store.remove(routine["id"])
        states.remove(removed["id"])
        if args.json_output:
            output({"removed": removed["id"]})
        else:
            display.completion(f"Removed {removed['name']}")
        return 0
    # run-now
    if not running:
        raise routines_lib.RoutineError(f"Start the Tag first: tag {tag_id} start")
    if not routines_lib.approved(routine):
        raise routines_lib.RoutineError("This routine changed after it was approved. Save it again to approve it")
    routines_lib.request_run(home, routine["id"])
    if args.json_output:
        output({"requested": routine["id"]})
    else:
        display.completion(f"Asked the Tag to run {routine['name']} now",
                           f"The result posts in the routine's channel. See tag {tag_id} routine show")
    return 0


def _done(args: argparse.Namespace, tag_id: str, verb: str, row: dict[str, Any], running: bool,
          output: Callable[[dict[str, Any]], None]) -> int:
    if args.json_output:
        output({"tag_running": running, "routine": row})
        return 0
    detail = (f"Next run {_local(row['next_run'], row['timezone'])}" if row["next_run"]
              else "Paused: it won't run until you resume it")
    display.completion(f"{verb} {row['name']}", detail)
    if not running:
        display.paragraph("This Tag is stopped, so its routines won't run until it starts.", f"tag {tag_id} start")
    return 0


def _local(value: str | None, zone: str) -> str:
    if not value:
        return "—"
    try:
        moment = datetime.fromisoformat(value).astimezone(ZoneInfo(zone))
    except (ValueError, ZoneInfoNotFoundError):
        return value
    return moment.strftime("%a %d %b %H:%M %Z")
