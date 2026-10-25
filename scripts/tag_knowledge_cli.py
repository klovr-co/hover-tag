# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""``tag [TAG] knowledge list|read|edit|add|forget|history|show|undo``.

What a Tag knows: its memory (always-loaded ``core.md`` and notes, for all
channels and per channel) and its skills. Tag.app's Knowledge tab runs these
commands with ``--json``. Every change goes into the Tag's history, and undo is
itself a recorded change. Writes keep the memory helper's limits and safety
checks; the agent changes memory only through that helper, when someone asks.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    import tag_display as display
    import tag_knowledge as knowledge
    import tag_memory as memory
except ImportError:
    from scripts import tag_display as display
    from scripts import tag_knowledge as knowledge
    from scripts import tag_memory as memory

ACTIONS = ("list", "read", "edit", "add", "forget", "history", "show", "undo")
WRITES = frozenset({"edit", "add", "forget", "undo"})
OPERATOR = "local-operator"
# The memory store needs a channel for what it shows; an app write to all channels uses this one.
APP_CHANNEL = "C0TAGAPP0"
SKILL_LIMIT = 200_000
CONTEXT_LINES = 3
LINK = re.compile(r"\[\[([a-z0-9][a-z0-9-]{0,63})\]\]")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="tag [TAG] knowledge", description=(
        "A Tag's own memory and skills, with every change kept in its history."))
    actions = root.add_subparsers(dest="action", required=True, metavar="|".join(ACTIONS))

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = actions.add_parser(name, help=help_text, description=help_text)
        sub.add_argument("--json", action="store_true", dest="json_output", help="structured output")
        if name in WRITES:
            sub.add_argument("--via", default="cli", choices=("cli", "tag-app"), help=argparse.SUPPRESS)
        return sub

    command("list", "List the Tag's memory and skills.")
    read = command("read", "Show one memory file or skill.")
    read.add_argument("path", help="such as memory/core.md or memory/channels/C0123/notes/venue.md")
    edit = command("edit", "Replace a memory file's entries or a skill's text.")
    edit.add_argument("path")
    edit.add_argument("--body-file", required=True, type=Path, help="the new markdown, or - for stdin")
    edit.add_argument("--base", required=True, help="the version you read; refused if it changed since")
    add = command("add", "Give the Tag something to remember.")
    add.add_argument("--key", required=True, help="short name, such as report-deadline")
    text = add.add_mutually_exclusive_group(required=True)
    text.add_argument("--text")
    text.add_argument("--text-file", type=Path, help="read the text from a file, or - for stdin")
    add.add_argument("--kind", choices=memory.KINDS, default="core",
                     help="core: always loaded (default); note: read when needed")
    where = add.add_mutually_exclusive_group(required=True)
    where.add_argument("--channel", help="channel ID: remember it for this channel")
    where.add_argument("--all-channels", action="store_true", help="remember it in every channel")
    forget = command("forget", "Make an entry unreachable; history keeps it, so it can be restored.")
    forget.add_argument("path")
    forget.add_argument("--key", help="the entry in a core.md file")
    history = command("history", "Recent changes, newest first.")
    history.add_argument("path", nargs="?", help="only changes to this file")
    history.add_argument("--limit", type=int, default=30, help="how many (1–200, default 30)")
    history.add_argument("--before", help="changes older than this change id")
    show = command("show", "One change, with what it changed.")
    show.add_argument("change", help="change id")
    undo = command("undo", "Put back what one change replaced; the undo is a change too.")
    undo.add_argument("change", help="change id")
    return root


# Reading ------------------------------------------------------------------------


def details(written_by: str, from_thread: str, updated: str) -> dict[str, str | None]:
    return {"written_by": written_by or None, "from_thread": from_thread or None, "updated": updated or None}


def core_node(places: knowledge.Places, scope: str, state: dict[str, Any]) -> dict[str, Any] | None:
    core = [e for e in state["entries"].values() if e["kind"] == "core"]
    if not core:
        return None
    meta = state["core_meta"]
    return {"path": knowledge.scope_prefix(scope) + "core.md", "kind": "core", "entries": len(core),
            "size": sum(len(e["text"]) for e in core), "limit": memory.CORE_LIMIT,
            **details(meta.get("written_by", ""), meta.get("from_thread", ""), meta.get("updated", ""))}


def note_node(scope: str, entry: dict[str, Any]) -> dict[str, Any]:
    return {"path": knowledge.scope_prefix(scope) + f"notes/{entry['key']}.md", "kind": "note",
            "key": entry["key"], "summary": memory.summarize(entry["text"]), "size": len(entry["text"]),
            "limit": memory.NOTE_LIMIT, **details(entry["saved_by"], entry.get("from_thread", ""), entry["saved_at"])}


def skill_description(text: str) -> str:
    meta, _body = knowledge.parse_front(text)
    return meta.get("description", "").strip("\"'")


def skills(places: knowledge.Places) -> list[dict[str, Any]]:
    rows = []
    for label, folder in knowledge.skill_folders(places):
        for skill in sorted(folder.iterdir()):
            target = skill / "SKILL.md"
            if skill.name.startswith(".") or not target.is_file():
                continue
            text = knowledge.read_text(target) or ""
            rows.append({"path": f"skills/{label}/{skill.name}/SKILL.md", "kind": "skill", "name": skill.name,
                         "backend": label, "summary": skill_description(text), "size": len(text)})
    return rows


def tree(places: knowledge.Places, channel_rows: list[dict[str, Any]]) -> dict[str, Any]:
    names = {row["id"]: row.get("name") for row in channel_rows}
    state = knowledge.load_scope(places, knowledge.GLOBAL)
    result = {"memory": {"core": core_node(places, knowledge.GLOBAL, state),
                         "notes": [note_node(knowledge.GLOBAL, e) for e in sorted(state["entries"].values(), key=lambda e: e["key"])
                                   if e["kind"] == "note"]},
              "channels": [], "skills": skills(places)}
    for channel in dict.fromkeys([*knowledge.channels(places), *names]):
        scope = knowledge.load_scope(places, channel)
        result["channels"].append({
            "id": channel, "name": names.get(channel),
            "core": core_node(places, channel, scope),
            "notes": [note_node(channel, e) for e in sorted(scope["entries"].values(), key=lambda e: e["key"])
                      if e["kind"] == "note"]})
    result["channels"].sort(key=lambda row: (not (row["core"] or row["notes"]), row["name"] is None,
                                             (row["name"] or row["id"]).casefold()))
    return result


def file_version(content: str | None) -> str:
    return knowledge.version_of(content or "")


def parse_path(name: str) -> tuple[str, str | None, str | None]:
    """``(kind, scope, key)`` for a memory path; skills are ``("skill", None, None)``."""
    if name.startswith("skills/"):
        return "skill", None, None
    match = re.fullmatch(r"memory/(?:channels/([A-Z0-9]{2,32})/)?(core\.md|notes/([a-z0-9][a-z0-9-]{0,63})\.md)", name)
    if not match:
        raise knowledge.KnowledgeError("bad_path", f"Unknown knowledge path {name!r}.")
    scope = match.group(1) or knowledge.GLOBAL
    return ("core", scope, None) if match.group(2) == "core.md" else ("note", scope, match.group(3))


def read(places: knowledge.Places, name: str, channel_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    kind, scope, key = parse_path(name)
    path = knowledge.disk_path(places, name)
    raw = knowledge.read_text(path)
    if raw is None:
        if kind == "core":
            raw = ""  # An empty core.md is a place to add the first entry.
        else:
            raise knowledge.KnowledgeError("not_found", f"There is no {name}.")
    names = {row["id"]: row.get("name") for row in channel_rows or []}
    base = {"path": name, "kind": kind, "version": file_version(raw)}
    if kind == "skill":
        parts = name.split("/")
        return {**base, "name": parts[2], "backend": parts[1], "body": raw, "size": len(raw),
                "summary": skill_description(raw), "limit": SKILL_LIMIT, **details("", "", "")}
    state = knowledge.load_scope(places, scope)
    where = {"scope": "all" if scope == knowledge.GLOBAL else "channel",
             "channel": None if scope == knowledge.GLOBAL else scope,
             "channel_name": None if scope == knowledge.GLOBAL else names.get(scope)}
    meta, body = knowledge.parse_front(raw)
    if kind == "core":
        entries = [{"key": e["key"], "text": e["text"], "version": e["version"],
                    "links": LINK.findall(e["text"]),
                    **details(e["saved_by"], e.get("from_thread", ""), e["saved_at"])}
                   for e in state["entries"].values() if e["kind"] == "core"]
        return {**base, **where, "body": body, "entries": entries, "size": sum(len(e["text"]) for e in entries),
                "limit": memory.CORE_LIMIT,
                **details(meta.get("written_by", ""), meta.get("from_thread", ""), meta.get("updated", ""))}
    entry = state["entries"][key]
    return {**base, **where, "key": key, "body": entry["text"], "links": LINK.findall(entry["text"]),
            "size": len(entry["text"]), "limit": memory.NOTE_LIMIT,
            **details(entry["saved_by"], entry.get("from_thread", ""), entry["saved_at"])}


# Writing ------------------------------------------------------------------------


def refused(exc: memory.MemoryRefused) -> knowledge.KnowledgeError:
    return knowledge.KnowledgeError(exc.code, str(exc))


def store(places: knowledge.Places, scope: str, via: str) -> memory.MemoryStore:
    channel = APP_CHANNEL if scope == knowledge.GLOBAL else scope
    return memory.MemoryStore(places.memory, channel, OPERATOR, places=places, via=via, thread="")


def add(places: knowledge.Places, key: str, text: str, kind: str, scope: str, via: str) -> str:
    try:
        receipt = store(places, scope, via).save(key, text, kind=kind, all_channels=scope == knowledge.GLOBAL)
    except memory.MemoryRefused as exc:
        raise refused(exc) from None
    if receipt.action == "unchanged" and receipt.key != key:
        raise knowledge.KnowledgeError("exists", receipt.message)
    entry_kind = kind if receipt.action == "saved" else knowledge.load_scope(places, scope)["entries"][receipt.key]["kind"]
    return knowledge.entry_ref(places, scope, receipt.key, entry_kind).partition("#")[0]


def forget(places: knowledge.Places, name: str, key: str | None, via: str) -> None:
    kind, scope, note_key = parse_path(name)
    if kind == "skill":
        raise knowledge.KnowledgeError("bad_path", "Skills are removed by deleting their folder.")
    target = note_key or key
    if not target:
        raise knowledge.KnowledgeError("bad_key", "Name the entry to forget with --key.")
    try:
        store(places, scope, via).forget(target, all_channels=scope == knowledge.GLOBAL)
    except memory.MemoryRefused as exc:
        raise refused(exc) from None


def edit(places: knowledge.Places, name: str, body: str, base: str, via: str) -> None:
    kind, scope, key = parse_path(name)
    path = knowledge.disk_path(places, name)
    when = knowledge.utc_now()
    change = knowledge.Change(action="edited", summary="", by=OPERATOR, via=via, at=when)
    try:
        with knowledge.memory_lock(places.memory):
            raw = knowledge.read_text(path)
            if kind == "skill" and raw is None:
                raise knowledge.KnowledgeError("not_found", f"There is no {name}.")
            if file_version(raw if raw is not None else "") != base:
                raise knowledge.KnowledgeError("conflict", "This file changed since you opened it. Reload it, then edit again.")
            if kind == "skill":
                if len(body) > SKILL_LIMIT:
                    raise knowledge.KnowledgeError("too_long", f"A skill can hold up to {SKILL_LIMIT:,} characters.")
                if not body.strip():
                    raise knowledge.KnowledgeError("empty", "A skill can't be empty; delete its folder to remove it.")
                if body == raw:
                    return
                change.summary = f"Edited skill {name.split('/')[2]}"
                change.entries = [name]
                knowledge.record(places, {name: body}, change)
                return
            before = knowledge.load_scope(places, scope)
            entries = dict(before["entries"])
            forgotten = dict(before["forgotten"])
            if kind == "core":
                sections = knowledge.parse_core(body.replace("\r\n", "\n"), lenient=True)
                old = {k: e for k, e in entries.items() if e["kind"] == "core"}
                for k in old:
                    if k not in sections:
                        del entries[k]
                        forgotten[k] = {"by": OPERATOR, "at": when}
                for k, text in sections.items():
                    if k in entries and entries[k]["kind"] == "note":
                        raise knowledge.KnowledgeError("exists", f"A note already uses the key {k!r}; choose another.")
                    if k in old and old[k]["text"] == text:
                        continue
                    entries[k] = {"key": k, "kind": "core", "text": text, "saved_by": OPERATOR, "saved_at": when}
                    forgotten.pop(k, None)
                for k, text in sections.items():
                    if k not in old or old[k]["text"] != text:
                        memory.check_text(entries, k, text, "core")
                # Keep the person's order of entries.
                entries = {**{k: entries[k] for k in sections}, **{k: e for k, e in entries.items() if k not in sections}}
                added = [k for k in sections if k not in old]
                changed = [k for k in sections if k in old and old[k]["text"] != sections[k]]
                removed = [k for k in old if k not in sections]
                touched = added + changed + removed
                if not touched and list(old) == [k for k in sections]:
                    return
                parts = [f"{verb} {', '.join(keys)}" for verb, keys in
                         (("added", added), ("changed", changed), ("removed", removed)) if keys]
                change.summary = f"Edited {'all channels' if scope == knowledge.GLOBAL else 'channel ' + scope} memory: " + \
                    ("; ".join(parts) or "reordered entries")
                change.entries = [knowledge.entry_ref(places, scope, k, "core") for k in touched]
            else:
                text = body.replace("\r\n", "\n").rstrip("\n")
                if not text.strip():
                    raise knowledge.KnowledgeError("empty", "A note can't be empty; use Forget to remove it.")
                entry = entries.get(key)
                if entry is None or entry["kind"] != "note":
                    raise knowledge.KnowledgeError("not_found", f"There is no {name}.")
                if entry["text"] == text:
                    return
                memory.check_text(entries, key, text, "note")
                entries[key] = {**entry, "text": text, "saved_by": OPERATOR, "saved_at": when, "from_thread": ""}
                change.summary = f"Edited note {key}"
                change.entries = [name]
            files = knowledge.scope_files(places, scope, before, {"entries": entries, "forgotten": forgotten}, change)
            knowledge.record(places, files, change)
    except memory.MemoryRefused as exc:
        raise refused(exc) from None


# History ------------------------------------------------------------------------


def history_or_fail(places: knowledge.Places) -> knowledge.History:
    knowledge.sync(places)
    history = knowledge.open_history(places)
    if history is None:
        raise knowledge.KnowledgeError("history_unavailable",
                                       "History needs Tag's runtime to be complete. Re-run the Tag installer.")
    return history


def commit_json(history: knowledge.History, commit: knowledge.Commit, undone: dict[str, str]) -> dict[str, Any]:
    row = commit.as_json()
    row["undoable"] = row["action"] != "started"
    row["undone_by"] = undone.get(commit.id)
    return row


def changes(places: knowledge.Places, name: str | None, limit: int, before: str | None) -> dict[str, Any]:
    history = history_or_fail(places)
    start = None
    if before:
        start = commit_parent(history, before)
        if start is None:
            return {"changes": [], "has_more": False}
    rows: list[dict[str, Any]] = []
    undone: dict[str, str] = {}
    has_more = False
    for commit in history.log(start):
        if commit.trailers.get("Undo-of"):
            undone.setdefault(commit.trailers["Undo-of"], commit.id)
        if name is not None:
            old, new = history.changed(commit.id)
            if old.get(name) == new.get(name):
                continue
        if len(rows) >= limit:
            has_more = True
            break
        rows.append(commit)
    return {"changes": [commit_json(history, c, undone) for c in rows], "has_more": has_more}


def find_commit(history: knowledge.History, ident: str) -> knowledge.Commit:
    if not re.fullmatch(r"[0-9a-f]{40}", ident or ""):
        raise knowledge.KnowledgeError("not_found", f"No change {ident!r} in this Tag's history.")
    for commit in history.log():
        if commit.id == ident:
            return commit
    raise knowledge.KnowledgeError("not_found", f"No change {ident!r} in this Tag's history.")


def commit_parent(history: knowledge.History, ident: str) -> bytes | None:
    commit = find_commit(history, ident)
    return commit.parent.encode() if commit.parent else None


def diff_lines(before: str, after: str) -> list[dict[str, str]]:
    old, new = before.split("\n") if before else [], after.split("\n") if after else []
    rows: list[dict[str, str]] = []
    for group in difflib.SequenceMatcher(None, old, new, autojunk=False).get_grouped_opcodes(CONTEXT_LINES):
        if rows:
            rows.append({"op": "…", "text": ""})
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                rows.extend({"op": " ", "text": line} for line in old[i1:i2])
                continue
            rows.extend({"op": "-", "text": line} for line in old[i1:i2])
            rows.extend({"op": "+", "text": line} for line in new[j1:j2])
    return rows


def show(places: knowledge.Places, ident: str) -> dict[str, Any]:
    history = history_or_fail(places)
    commit = find_commit(history, ident)
    old, new = history.changed(commit.id)
    files = []
    for name in sorted(set(old) | set(new)):
        if old.get(name) == new.get(name) or name.endswith(knowledge.SIDE):
            continue
        before = knowledge.parse_front(history.blob(old.get(name)) or "")[1]
        after = knowledge.parse_front(history.blob(new.get(name)) or "")[1]
        status = "added" if name not in old else "removed" if name not in new else "changed"
        files.append({"path": name, "status": status, "before": before, "after": after,
                      "diff": diff_lines(before, after)})
    undone = {c.trailers["Undo-of"]: c.id for c in history.log(limit=500) if c.trailers.get("Undo-of")}
    return {"change": commit_json(history, commit, undone), "files": files}


def undo(places: knowledge.Places, ident: str, via: str) -> str:
    """Revert one change entry by entry; refuse if an entry changed again since."""
    history = history_or_fail(places)
    commit = find_commit(history, ident)
    if commit.trailers.get("Action") == "started":
        raise knowledge.KnowledgeError("not_undoable", "That is where the history starts; there is nothing to undo.")
    old_ids, new_ids = history.changed(commit.id)
    old = {name: history.blob(sha) for name, sha in old_ids.items()}
    new = {name: history.blob(sha) for name, sha in new_ids.items()}
    names = sorted(n for n in set(old) | set(new) if old.get(n) != new.get(n))
    scopes = sorted({knowledge.scope_of(n) for n in names if n.startswith("memory/")} - {None})
    skill_names = [n for n in names if n.startswith("skills/")]
    when = knowledge.utc_now()
    summary = commit.summary
    change = knowledge.Change(action="undid", summary=f"Undid: {summary}"[:200], by=OPERATOR, via=via,
                              undo_of=commit.id, at=when)
    conflict = knowledge.KnowledgeError(
        "conflict", "This changed again after that change. Undo the later change first.")
    try:
        with knowledge.memory_lock(places.memory):
            files: dict[str, str | None] = {}
            for scope in scopes:
                was = knowledge.scope_state(places, scope, old)
                then = knowledge.scope_state(places, scope, new)
                now = knowledge.load_scope(places, scope)
                keys = sorted(k for k in set(was["entries"]) | set(then["entries"]) | set(was["forgotten"]) | set(then["forgotten"])
                              if same(was["entries"].get(k)) != same(then["entries"].get(k))
                              or was["forgotten"].get(k) != then["forgotten"].get(k))
                entries, forgotten = dict(now["entries"]), dict(now["forgotten"])
                for k in keys:
                    if same(now["entries"].get(k)) != same(then["entries"].get(k)):
                        raise conflict
                    restored = was["entries"].get(k)
                    if restored is None:
                        entries.pop(k, None)
                    else:
                        if restored["text"] == knowledge.PURGED:
                            raise knowledge.KnowledgeError("not_undoable", "That text was removed from history at someone's request.")
                        entries[k] = restored
                    if was["forgotten"].get(k):
                        forgotten[k] = was["forgotten"][k]
                    else:
                        forgotten.pop(k, None)
                for k in keys:
                    if k in entries and same(entries[k]) != same(now["entries"].get(k)):
                        memory.check_text(entries, k, entries[k]["text"], entries[k]["kind"])
                change.entries += [knowledge.entry_ref(places, scope, k, (was["entries"].get(k) or then["entries"].get(k) or {"kind": "note"})["kind"])
                                   for k in keys]
                files.update(knowledge.scope_files(places, scope, now, {"entries": entries, "forgotten": forgotten}, change))
            for name in skill_names:
                current = knowledge.read_text(knowledge.disk_path(places, name))
                if current != new.get(name):
                    raise conflict
                if old.get(name) is None:
                    raise knowledge.KnowledgeError("not_undoable", "Tag doesn't remove skills; delete the skill's folder yourself.")
                files[name] = old[name]
                change.entries.append(name)
            if not files:
                raise knowledge.KnowledgeError("not_undoable", "That change has nothing left to undo.")
            knowledge.record(places, files, change)
    except memory.MemoryRefused as exc:
        raise refused(exc) from None
    return change.ident


def same(entry: dict[str, Any] | None) -> tuple[str, str] | None:
    return None if entry is None else (entry["kind"], entry["text"])


def latest(places: knowledge.Places, ident: str | None = None) -> dict[str, Any] | None:
    """The newest change after a write (or the one with this Change-Id), for the reply."""
    knowledge.sync(places)
    history = knowledge.open_history(places)
    if history is None:
        return None
    for commit in history.log(limit=5):
        if ident is None or commit.trailers.get("Change-Id") == ident:
            return commit_json(history, commit, {})
    return None


# Command line ------------------------------------------------------------------------


def body_from(path: Path) -> str:
    if str(path) == "-":
        return sys.stdin.read()
    return path.read_text(encoding="utf-8")


def run(places: knowledge.Places, tag_id: str, argv: list[str], *,
        channel_rows: list[dict[str, Any]] | None = None) -> int:
    args = parser().parse_args(argv)
    rows = channel_rows or []

    def output(payload: dict[str, Any]) -> None:
        print(json.dumps({"schema_version": 1, "ok": True, "tag": tag_id, **payload}, indent=2, ensure_ascii=False))

    try:
        if args.action in WRITES and os.getenv("OPENTAG_CALLER_ID"):
            raise knowledge.KnowledgeError(
                "use_helper", "A Tag run changes memory only through the memory helper, when someone asks.")
        memory.prepare(places)
        if args.action == "list":
            status = knowledge.sync(places)
            payload = {**tree(places, rows),
                       "limits": {"core": memory.CORE_LIMIT, "note": memory.NOTE_LIMIT, "notes": memory.MAX_NOTES},
                       "history": {"available": status["available"]}}
            if args.json_output:
                output(payload)
            else:
                print_tree(payload, tag_id)
            return 0
        if args.action == "read":
            item = read(places, args.path, rows)
            if args.json_output:
                output({"file": item})
            else:
                display.header("Knowledge", item["path"])
                print(item["body"])
            return 0
        if args.action in {"edit", "add", "forget", "undo"}:
            # Record what is there now (and anything changed outside Tag) before this change.
            knowledge.sync(places)
            if args.action == "edit":
                edit(places, args.path, body_from(args.body_file), args.base, args.via)
                path = args.path
            elif args.action == "add":
                text = args.text if args.text is not None else body_from(args.text_file).rstrip("\n")
                scope = knowledge.GLOBAL if args.all_channels else knowledge.validate_channel(args.channel)
                path = add(places, args.key, text, args.kind, scope, args.via)
            elif args.action == "forget":
                forget(places, args.path, args.key, args.via)
                path = args.path
            else:
                undo(places, args.change, args.via)
                path = None
            change = latest(places)
            if args.json_output:
                found = None
                if path is not None:
                    try:
                        found = read(places, path, rows)
                    except knowledge.KnowledgeError:
                        found = None
                output({"change": change, "file": found})
            else:
                display.completion(change["summary"] if change else "Saved",
                                   "History is unavailable until Tag's runtime is complete." if change is None else "")
            return 0
        if args.action == "history":
            if not 1 <= args.limit <= 200:
                raise knowledge.KnowledgeError("bad_limit", "--limit must be between 1 and 200.")
            if args.path:
                parse_path(args.path)
            payload = changes(places, args.path, args.limit, args.before)
            if args.json_output:
                output(payload)
            else:
                display.header("History", args.path or f"Tag '{tag_id}'")
                for row in payload["changes"]:
                    display.info_row(row["at"].replace("T", " ")[:16], f"{row['summary']} · {row['id'][:10]}")
            return 0
        payload = show(places, args.change)
        if args.json_output:
            output(payload)
        else:
            display.header("Change", payload["change"]["summary"])
            for item in payload["files"]:
                print(f"--- {item['path']} ({item['status']})")
                for line in item["diff"]:
                    print(f"{line['op']} {line['text']}")
        return 0
    except (knowledge.KnowledgeError, memory.MemoryRefused) as exc:
        if getattr(args, "json_output", False):
            print(json.dumps({"schema_version": 1, "ok": False, "error": str(exc), "code": exc.code}))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


def print_tree(payload: dict[str, Any], tag_id: str) -> None:
    display.header("Knowledge", f"Tag '{tag_id}'")
    core = payload["memory"]["core"]
    display.info_row("All channels", f"{core['entries']} always loaded · {core['size']:,}/{core['limit']:,}" if core else "Nothing always loaded")
    for note in payload["memory"]["notes"]:
        display.info_row("", f"{note['path']} · {note['summary']}")
    for row in payload["channels"]:
        if not (row["core"] or row["notes"]):
            continue
        label = f"#{row['name']}" if row["name"] else row["id"]
        display.info_row(label, f"{row['core']['entries']} always loaded" if row["core"] else "No always-loaded entries")
        for note in row["notes"]:
            display.info_row("", f"{note['path']} · {note['summary']}")
    for skill in payload["skills"]:
        display.info_row("Skill", f"{skill['name']} ({skill['backend']})")
