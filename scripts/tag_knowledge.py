# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""A Tag's own knowledge: markdown memory files with a hidden change history.

Each Tag keeps its memory as plain markdown in ``<Tag home>/memory``::

    core.md                       always loaded, for all channels
    notes/<key>.md                read on demand, for all channels
    channels/<CHANNEL>/core.md    always loaded in one channel
    channels/<CHANNEL>/notes/<key>.md

Every file starts with a short front-matter header (``id``, ``kind``,
``written_by``, ``from_thread``, ``updated``). ``core.md`` holds one ``## key``
section per entry; ``.entries.json`` beside it records who saved each section
and which keys were forgotten.

The history is a bare git repository written with Dulwich, kept outside the
Tag's working folder (``<installation>/state/knowledge/<id>.git``), so the agent
never edits it. The agent's memory helper runs inside the agent's sandbox and
cannot reach it, so writers append each change, with the files it produced, to
``.journal.jsonl``; :func:`sync` (the bridge after every run, and every
``tag knowledge`` command) turns the journal into commits. Anything that changed
the files without a journal entry is committed as a change made outside Tag, so
history stays evident (ADR 0001) rather than tamper-proof.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1
FRONT_FIELDS = ("id", "kind", "written_by", "from_thread", "updated")
GLOBAL = "global"
KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
CHANNEL_PATTERN = re.compile(r"^[A-Z0-9]{2,32}$")
SECTION = re.compile(r"^## ([a-z0-9][a-z0-9-]{0,63})[ \t]*$")
ESCAPED = re.compile(r"^(\\*)## ")
SIDE = ".entries.json"
JOURNAL = ".journal.jsonl"
FORMAT_FILE = ".format.json"
BRANCH = b"refs/heads/main"
PURGED = "(removed from history at someone's request)"
SKILL_ROOTS = (("claude", ".claude/skills"), ("codex", ".agents/skills"))
THREAD_LINK = "hover-tag://tag/{tag}/thread/{channel}/{ts}"
LOCK_TIMEOUT_SECONDS = 10.0


class KnowledgeError(Exception):
    """A refused or failed knowledge operation; the message is safe to show."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def version_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def thread_link(tag: str, channel: str, ts: str) -> str:
    if not (tag and channel and re.fullmatch(r"\d+\.\d+", ts or "")):
        return ""
    return THREAD_LINK.format(tag=tag, channel=channel, ts=ts)


# Places ------------------------------------------------------------------------


@dataclass(frozen=True)
class Places:
    """Where one Tag's knowledge lives. The agent's helper needs only the markdown;
    history lives under ``installation`` (or at an explicit ``history`` path)."""

    memory: Path
    legacy: Path | None = None
    history: Path | None = None
    installation: Path | None = None
    workspace: Path | None = None
    tag: str = ""

    @classmethod
    def for_tag(cls, installation_root: Path, home: Path, workspace: Path | None, tag: str) -> "Places":
        return cls(memory=home / "memory", legacy=home / "state/memory", installation=installation_root,
                   workspace=workspace, tag=tag)

    @classmethod
    def from_env(cls, root: Path | None = None) -> "Places":
        """The places a Tag run sees: ``OPENTAG_MEMORY_ROOT`` and friends from the runtime."""
        memory = root or memory_root_from_env()
        legacy_env = os.getenv("OPENTAG_MEMORY_LEGACY_ROOT")
        legacy = Path(legacy_env).expanduser() if legacy_env else memory.parent / "state/memory"
        history_env = os.getenv("OPENTAG_MEMORY_HISTORY")
        installation = os.getenv("TAG_HOME")
        workdir = os.getenv("OPENTAG_WORKDIR")
        return cls(memory=memory, legacy=legacy,
                   history=Path(history_env).expanduser() if history_env else None,
                   installation=Path(installation).expanduser() if installation else None,
                   workspace=Path(workdir).expanduser() if workdir else None,
                   tag=os.getenv("TAG_ID", ""))

    def scope_dir(self, scope: str) -> Path:
        return self.memory if scope == GLOBAL else self.memory / "channels" / validate_channel(scope)

    def history_dir(self) -> Path | None:
        """The history repository: ``<installation>/state/knowledge/<id>.git``, named by
        the id in ``.format.json``, so it follows the Tag through a rename."""
        if self.history is not None:
            return self.history
        if self.installation is None:
            return None
        record = read_format(self.memory)
        ident = (record or {}).get("history")
        if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{32}", ident):
            return None
        return self.installation / "state/knowledge" / f"{ident}.git"


def memory_root_from_env() -> Path:
    configured = os.getenv("OPENTAG_MEMORY_ROOT")
    if configured:
        path = Path(configured).expanduser()
        # Bridges started before the markdown store pointed here at the JSON folder.
        if path.name == "memory" and path.parent.name == "state":
            return path.parent.parent / "memory"
        return path
    try:
        from tag_paths import instance_home
    except ImportError:
        from scripts.tag_paths import instance_home
    return instance_home() / "memory"


def validate_channel(channel: str) -> str:
    if not CHANNEL_PATTERN.fullmatch(channel or ""):
        raise KnowledgeError("bad_channel", f"Memory is unavailable for conversation id {channel!r}.")
    return channel


# Format ------------------------------------------------------------------------


def parse_front(raw: str) -> tuple[dict[str, str], str]:
    """Split a ``---`` header of ``key: value`` lines from the body.

    Tag writes plain ``\n`` line endings. A file saved with Windows line
    endings throughout (for example edited by hand) is read as if it had them.
    """
    if raw.startswith("---\r\n"):
        raw = raw.replace("\r\n", "\n")
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---\n", 3)
    if end < 0:
        return {}, raw
    meta: dict[str, str] = {}
    for line in raw[4:end].split("\n"):
        name, _, value = line.partition(":")
        if name.strip():
            meta[name.strip()] = value.strip()
    body = raw[end + 5:]
    return meta, body[1:] if body.startswith("\n") else body


def render_front(meta: dict[str, str]) -> str:
    lines = [f"{name}: {meta.get(name, '')}".rstrip() for name in FRONT_FIELDS]
    return "---\n" + "\n".join(lines) + "\n---\n\n"


def escape(text: str) -> str:
    """Keep text lines that look like a section heading from starting a section."""
    return "\n".join(ESCAPED.sub(lambda m: "\\" + m.group(1) + "## ", line) for line in text.split("\n"))


def unescape(text: str) -> str:
    return "\n".join(re.sub(r"^\\(\\*## )", r"\1", line) for line in text.split("\n"))


def render_core(sections: dict[str, str]) -> str:
    return "\n".join(f"## {key}\n{escape(text)}\n" for key, text in sections.items())


def parse_core(body: str, *, lenient: bool = False) -> dict[str, str]:
    """``## key`` sections in file order. Canonical files round-trip exactly; a
    person's edit (``lenient``) may add or drop blank lines around entries."""
    if lenient or re.match(r"## [a-z0-9][a-z0-9-]{0,63}[ \t]*\r\n", body):
        body = body.replace("\r\n", "\n")  # A hand edit with Windows line endings throughout.
    sections: dict[str, list[str]] = {}
    current: str | None = None
    preamble: list[str] = []
    for line in body.split("\n"):
        match = SECTION.match(line)
        if match:
            current = match.group(1)
            if current in sections:
                raise KnowledgeError("duplicate_key", f"{current!r} appears twice; each entry needs its own key.")
            sections[current] = []
        elif current is None:
            preamble.append(line)
        else:
            sections[current].append(line)
    if any(line.strip() for line in preamble):
        heading = next((line for line in preamble if line.startswith("#")), "")
        hint = (f" ({heading.strip()!r} is not a valid key: use lowercase letters, numbers and dashes)"
                if heading.startswith("## ") else "")
        raise KnowledgeError("bad_format", "Start each entry with a ## key heading" + hint + ".")
    result = {}
    for key, lines_of in sections.items():
        text = "\n".join(lines_of)
        # Canonical files end each entry with one newline (then a blank line or the end).
        text = text.strip("\n") if lenient else (text[:-1] if text.endswith("\n") else text)
        result[key] = unescape(text)
    return result


def note_text(body: str) -> str:
    return body[:-1] if body.endswith("\n") else body


def note_file(meta: dict[str, str], text: str) -> str:
    return render_front(meta) + text + "\n"


def core_file(meta: dict[str, str], sections: dict[str, str]) -> str:
    return render_front(meta) + render_core(sections)


# Files -------------------------------------------------------------------------


def read_file(path: Path) -> str | None:
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def write_file(path: Path, text: str) -> None:
    """Durably replace one file; a crash leaves the old or the new copy."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w",
                  encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(20):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                # Windows refuses to replace a file another process is reading.
                if os.name != "nt" or attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        temporary.unlink(missing_ok=True)
    fsync_dir(path.parent)


def fsync_dir(directory: Path) -> None:
    if os.name == "nt":
        return
    handle = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def remove_file(path: Path) -> None:
    path.unlink(missing_ok=True)
    # Drop folders the removal left empty, but never the memory root itself.
    for parent in (path.parent, path.parent.parent):
        if parent.name in {"notes"} or CHANNEL_PATTERN.fullmatch(parent.name) or parent.name == "channels":
            try:
                parent.rmdir()
            except OSError:
                break


@contextmanager
def memory_lock(root: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Serialize every writer of one Tag's memory across processes."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    guard = root / ".lock"
    handle = os.fdopen(os.open(guard, os.O_RDWR | os.O_CREAT, 0o600), "r+b")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    if guard.stat().st_size == 0:
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise KnowledgeError("busy", "Memory is busy with another change; try again.") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def read_format(memory: Path) -> dict[str, Any] | None:
    raw = read_file(memory / FORMAT_FILE)
    if raw is None:
        return None
    try:
        record = json.loads(raw)
    except ValueError:
        raise KnowledgeError("unreadable", f"Memory format record is damaged: {memory / FORMAT_FILE}") from None
    if not isinstance(record, dict) or record.get("format") != "markdown":
        raise KnowledgeError("unreadable", f"Memory format record is damaged: {memory / FORMAT_FILE}")
    if record.get("version") != FORMAT_VERSION:
        raise KnowledgeError("unreadable", "This memory was written by a newer Tag. Upgrade Tag to read it.")
    return record


def write_format(memory: Path, **extra: Any) -> dict[str, Any]:
    record = {"format": "markdown", "version": FORMAT_VERSION, "history": uuid.uuid4().hex, **extra}
    write_file(memory / FORMAT_FILE, json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


# Scopes ------------------------------------------------------------------------


def logical(places: Places, path: Path) -> str:
    return "memory/" + path.relative_to(places.memory).as_posix()


def disk_path(places: Places, name: str) -> Path:
    """Map a logical path (``memory/…`` or ``skills/claude|codex/NAME/SKILL.md``) to the disk."""
    parts = name.split("/")
    if ".." in parts or "" in parts or any(part.startswith(".") and part != SIDE for part in parts):
        raise KnowledgeError("bad_path", f"Unknown knowledge path {name!r}.")
    if parts[0] == "memory" and len(parts) > 1:
        return places.memory.joinpath(*parts[1:])
    if parts[0] == "skills" and len(parts) == 4 and parts[3] == "SKILL.md":
        roots = dict(SKILL_ROOTS)
        if places.workspace is None or parts[1] not in roots:
            raise KnowledgeError("bad_path", f"Unknown knowledge path {name!r}.")
        return places.workspace / roots[parts[1]] / parts[2] / "SKILL.md"
    raise KnowledgeError("bad_path", f"Unknown knowledge path {name!r}.")


def scope_of(name: str) -> str | None:
    """``global`` or the channel for a memory path, ``None`` for skills."""
    parts = name.split("/")
    if parts[:1] != ["memory"]:
        return None
    if len(parts) >= 3 and parts[1] == "channels":
        return parts[2]
    return GLOBAL


def scope_prefix(scope: str) -> str:
    return "memory/" if scope == GLOBAL else f"memory/channels/{validate_channel(scope)}/"


def channels(places: Places) -> list[str]:
    folder = places.memory / "channels"
    if not folder.is_dir():
        return []
    return sorted(item.name for item in folder.iterdir() if item.is_dir() and CHANNEL_PATTERN.fullmatch(item.name))


def render_side(side: dict[str, Any]) -> str | None:
    if not side["core"] and not side["forgotten"]:
        return None
    return json.dumps({"core": side["core"], "forgotten": side["forgotten"]},
                      ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def load_scope(places: Places, scope: str) -> dict[str, Any]:
    """One scope as ``{"entries": {key: entry}, "forgotten": {...}, "core_meta": {...}}``.

    Entries have the fields the memory helper has always used: ``key``, ``kind``,
    ``text``, ``version``, ``saved_by``, ``saved_at``, plus ``from_thread``.
    """
    directory = places.scope_dir(scope)
    files: dict[str, str | None] = {}
    for path in (directory / "core.md", directory / SIDE):
        files[logical(places, path)] = read_file(path)
    notes = directory / "notes"
    if notes.is_dir():
        for path in sorted(notes.glob("*.md")):
            files[logical(places, path)] = read_file(path)
    try:
        return scope_state(places, scope, files)
    except KnowledgeError as exc:
        if exc.code == "unreadable":
            raise KnowledgeError("unreadable", f"Memory file is damaged: {directory / SIDE}") from None
        raise


def scope_state(places: Places, scope: str, files: dict[str, str | None]) -> dict[str, Any]:
    """A scope's state from file contents by logical path (from disk or from a commit)."""
    prefix = scope_prefix(scope)
    side_raw = files.get(prefix + SIDE)
    side = {"core": {}, "forgotten": {}}
    if side_raw is not None:
        try:
            parsed = json.loads(side_raw)
        except ValueError:
            raise KnowledgeError("unreadable", "Memory file is damaged.") from None
        if not isinstance(parsed, dict):
            raise KnowledgeError("unreadable", "Memory file is damaged.")
        side = {"core": dict(parsed.get("core") or {}), "forgotten": dict(parsed.get("forgotten") or {})}
    entries: dict[str, dict[str, Any]] = {}
    core_meta: dict[str, str] = {}
    raw = files.get(prefix + "core.md")
    if raw is not None:
        core_meta, body = parse_front(raw)
        for key, text in parse_core(body).items():
            source = side["core"].get(key) or {}
            entries[key] = {"key": key, "kind": "core", "text": text, "version": version_of(text),
                            "saved_by": source.get("saved_by") or core_meta.get("written_by", ""),
                            "saved_at": source.get("saved_at") or core_meta.get("updated", ""),
                            "from_thread": source.get("from_thread") or ""}
    for name in sorted(files):
        if not name.startswith(prefix + "notes/") or not name.endswith(".md") or files[name] is None:
            continue
        key = name[len(prefix) + len("notes/"):-3]
        if not KEY_PATTERN.fullmatch(key) or key in entries:
            continue
        meta, body = parse_front(files[name] or "")
        text = note_text(body)
        entries[key] = {"key": key, "kind": "note", "text": text, "version": version_of(text),
                        "saved_by": meta.get("written_by", ""), "saved_at": meta.get("updated", ""),
                        "from_thread": meta.get("from_thread", "")}
    return {"entries": entries, "forgotten": side["forgotten"], "core_meta": core_meta}


@dataclass
class Change:
    """Who made a change and why; becomes one history commit."""

    action: str
    summary: str
    by: str
    via: str = "slack"
    thread: str = ""
    entries: list[str] = field(default_factory=list)
    undo_of: str = ""
    purge: list[dict[str, Any]] = field(default_factory=list)
    at: str = ""
    ident: str = ""


def side_of(state: dict[str, Any]) -> dict[str, Any]:
    core = {k: e for k, e in state["entries"].items() if e["kind"] == "core"}
    return {"core": {k: {"saved_by": e.get("saved_by", ""), "saved_at": e.get("saved_at", ""),
                         **({"from_thread": e["from_thread"]} if e.get("from_thread") else {})}
                     for k, e in core.items()},
            "forgotten": dict(state.get("forgotten") or {})}


def scope_files(places: Places, scope: str, before: dict[str, Any], after: dict[str, Any],
                change: Change) -> dict[str, str | None]:
    """The files that change when a scope goes from ``before`` to ``after``
    (``{"entries", "forgotten"}`` states): logical path → new content, or None to delete."""
    prefix = scope_prefix(scope)
    files: dict[str, str | None] = {}
    old, new = before["entries"], after["entries"]
    old_core = {k: e["text"] for k, e in old.items() if e["kind"] == "core"}
    new_core = {k: e["text"] for k, e in new.items() if e["kind"] == "core"}
    # Keep the file's order; new entries go at the end.
    order = [k for k in old_core if k in new_core] + [k for k in new_core if k not in old_core]
    if list(old_core.items()) != [(k, new_core[k]) for k in order]:
        meta = {"id": "core", "kind": "core", "written_by": change.by,
                "from_thread": change.thread, "updated": change.at}
        files[prefix + "core.md"] = core_file(meta, {k: new_core[k] for k in order}) if new_core else None
    for key in set(old) | set(new):
        was, now = old.get(key), new.get(key)
        if was is now or ((was or {}).get("kind") != "note" and (now or {}).get("kind") != "note"):
            continue
        path = f"{prefix}notes/{key}.md"
        if now is None or now["kind"] != "note":
            files[path] = None
        elif was is None or was["kind"] != "note" or was["text"] != now["text"]:
            meta = {"id": key, "kind": "note", "written_by": now.get("saved_by", ""),
                    "from_thread": now.get("from_thread", ""), "updated": now.get("saved_at", "")}
            files[path] = note_file(meta, now["text"])
    side = render_side(side_of(after))
    if side != render_side(side_of(before)):
        files[prefix + SIDE] = side
    return files


def apply_files(places: Places, files: dict[str, str | None]) -> None:
    for name, content in files.items():
        path = disk_path(places, name)
        if content is None:
            remove_file(path)
        else:
            write_file(path, content)


def record(places: Places, files: dict[str, str | None], change: Change) -> None:
    """Write ``files`` and queue ``change`` for history. Call under :func:`memory_lock`."""
    change.at = change.at or utc_now()
    change.ident = change.ident or uuid.uuid4().hex
    apply_files(places, files)
    for target in change.purge:
        scrub_journal(places, target)
    entry = journal_line(change, files)
    places.memory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(os.open(places.memory / JOURNAL, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a",
              encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def journal_line(change: Change, files: dict[str, str | None]) -> dict[str, Any]:
    return {"id": change.ident, "at": change.at, "action": change.action, "summary": change.summary,
            "by": change.by, "via": change.via, "thread": change.thread, "entries": change.entries,
            "undo_of": change.undo_of, "purge": change.purge, "files": files}


def scrub_journal(places: Places, target: dict[str, Any]) -> None:
    """Remove earlier text of one entry from changes still waiting for history."""
    journal = read_journal(places.memory)
    changed = False
    for item in journal:
        content = item["files"].get(target["path"])
        if content is None:
            continue
        scrubbed = purge_content(content, target.get("key"), target.get("keep"))
        if scrubbed != content:
            item["files"][target["path"]] = scrubbed
            changed = True
    if changed:
        write_file(places.memory / JOURNAL, "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in journal))


def purge_content(content: str, key: str | None, keep: str | None) -> str:
    meta, body = parse_front(content)
    if key:
        sections = parse_core(body)
        if key in sections and sections[key] != keep:
            sections[key] = PURGED
            return core_file(meta, sections)
        return content
    return content if note_text(body) == keep else note_file(meta, PURGED)


def read_journal(memory: Path) -> list[dict[str, Any]]:
    raw = read_file(memory / JOURNAL) or ""
    entries = []
    for line in raw.split("\n"):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue  # A torn last line from a crash; its files were not all written either.
        if isinstance(item, dict) and isinstance(item.get("files"), dict):
            entries.append(item)
    return entries


def snapshot(places: Places) -> dict[str, bytes]:
    """Every file history keeps: memory files and skills' SKILL.md, by logical path."""
    files: dict[str, bytes] = {}
    if places.memory.is_dir():
        for path in sorted(places.memory.rglob("*")):
            relative = path.relative_to(places.memory)
            if not path.is_file() or any(p.startswith(".") and p != SIDE for p in relative.parts):
                continue
            if relative.suffix == ".md" or relative.name == SIDE:
                files["memory/" + relative.as_posix()] = path.read_bytes()
    for label, folder in skill_folders(places):
        for skill in sorted(folder.iterdir()):
            target = skill / "SKILL.md"
            if skill.is_dir() and target.is_file() and not skill.name.startswith("."):
                files[f"skills/{label}/{skill.name}/SKILL.md"] = target.read_bytes()
    return files


def skill_folders(places: Places) -> list[tuple[str, Path]]:
    if places.workspace is None:
        return []
    return [(label, places.workspace / folder) for label, folder in SKILL_ROOTS
            if (places.workspace / folder).is_dir()]


def managed(places: Places, name: str) -> bool:
    return name.startswith("memory/") or (name.startswith("skills/") and places.workspace is not None)


# History -----------------------------------------------------------------------


def dulwich_available() -> bool:
    try:
        import dulwich  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass(frozen=True)
class Commit:
    id: str
    parent: str | None
    at: str
    trailers: dict[str, str]
    summary: str

    def as_json(self) -> dict[str, Any]:
        t = self.trailers
        return {"id": self.id, "at": self.at, "action": t.get("Action", "changed"), "summary": self.summary,
                "written_by": t.get("Written-by") or None, "via": t.get("Via") or None,
                "from_thread": t.get("Thread") or None, "entries": [e for e in t.get("Entry", "").split(" ") if e],
                "undo_of": t.get("Undo-of") or None}


class History:
    """A linear, bare git history of one Tag's knowledge."""

    def __init__(self, path: Path, tag: str = ""):
        from dulwich.repo import Repo
        self.path = path
        self.tag = tag
        if not (path / "HEAD").is_file():
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.repo = Repo.init_bare(str(path), mkdir=not path.exists())
            self.repo.refs.set_symbolic_ref(b"HEAD", BRANCH)
            (path / "description").write_text("Hover Tag knowledge history\n", encoding="utf-8")
        else:
            self.repo = Repo(str(path))

    @property
    def store(self):
        return self.repo.object_store

    def head(self) -> bytes | None:
        try:
            return self.repo.refs[BRANCH]
        except KeyError:
            return None

    def files(self, commit: bytes | None) -> dict[str, bytes]:
        """Logical path → blob id at ``commit``."""
        if commit is None:
            return {}
        from dulwich.object_store import iter_tree_contents
        tree = self.store[commit].tree
        return {entry.path.decode(): entry.sha for entry in iter_tree_contents(self.store, tree)}

    def blob(self, sha: bytes | None) -> str | None:
        return None if sha is None else self.store[sha].data.decode("utf-8")

    def content(self, commit: bytes | None, name: str) -> str | None:
        return self.blob(self.files(commit).get(name))

    def _tree(self, files: dict[str, bytes]) -> bytes:
        """Build the nested trees for ``files``; folders that did not change are reused."""
        from dulwich.objects import Tree
        cache: dict[tuple, bytes] = self.__dict__.setdefault("_trees", {})
        root: dict[str, Any] = {}
        for name, sha in files.items():
            node = root
            *folders, leaf = name.split("/")
            for folder in folders:
                node = node.setdefault(folder, {})
            node[leaf] = sha

        def build(node: dict[str, Any]) -> bytes:
            items = tuple(sorted((name, build(child) if isinstance(child, dict) else child, isinstance(child, dict))
                                 for name, child in node.items()))
            found = cache.get(items)
            if found is None:
                tree = Tree()
                for name, sha, folder in items:
                    tree.add(name.encode(), 0o040000 if folder else 0o100644, sha)
                self.store.add_object(tree)
                found = cache[items] = tree.id
            return found

        return build(root)

    def _blob(self, data: bytes) -> bytes:
        from dulwich.objects import Blob
        blob = Blob.from_string(data)
        self.store.add_object(blob)
        return blob.id

    def commit(self, files: dict[str, bytes], change: Change, parent: bytes | None) -> bytes:
        from dulwich.objects import Commit as GitCommit
        commit = GitCommit()
        commit.tree = self._tree(files)
        commit.parents = [parent] if parent else []
        who = re.sub(r"[<>\n]", "", change.by or "unknown")
        commit.author = f"{who} <{who}@hover-tag.invalid>".encode()
        tag = re.sub(r"[<>\n]", "", self.tag or "tag")
        commit.committer = f"Tag {tag} <{tag}@hover-tag.invalid>".encode()
        moment = parse_time(change.at)
        commit.author_time = commit.commit_time = moment
        commit.author_timezone = commit.commit_timezone = 0
        commit.encoding = b"UTF-8"
        commit.message = message(change, tag).encode("utf-8")
        self.store.add_object(commit)
        return commit.id

    def move(self, old: bytes | None, new: bytes | None) -> None:
        """Point the branch at ``new`` once a batch of commits is written."""
        if new is not None and new != old and not self.repo.refs.set_if_equals(BRANCH, old, new):
            raise KnowledgeError("busy", "Knowledge history changed during this change; try again.")

    def log(self, start: bytes | None = None, limit: int | None = None) -> Iterator[Commit]:
        current = start if start is not None else self.head()
        count = 0
        while current is not None and (limit is None or count < limit):
            obj = self.store[current]
            parent = obj.parents[0] if obj.parents else None
            text = obj.message.decode("utf-8", "replace")
            yield Commit(current.decode(), parent.decode() if parent else None,
                         datetime.fromtimestamp(obj.commit_time, timezone.utc).isoformat().replace("+00:00", "Z"),
                         trailers(text), text.split("\n", 1)[0])
            current = parent
            count += 1

    def changed(self, commit: str) -> tuple[dict[str, bytes], dict[str, bytes]]:
        obj = self.store[commit.encode()]
        parent = obj.parents[0] if obj.parents else None
        return self.files(parent), self.files(commit.encode())


def parse_time(value: str) -> int:
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except (ValueError, AttributeError):
        return int(time.time())


def message(change: Change, tag: str) -> str:
    lines = [change.summary.replace("\n", " ").strip() or "Changed memory", ""]
    fields = (("Action", change.action), ("Entry", " ".join(change.entries)), ("Written-by", change.by),
              ("Via", change.via), ("Thread", change.thread), ("Tag", tag), ("Undo-of", change.undo_of),
              ("Change-Id", change.ident))
    lines += [f"{name}: {value}" for name, value in fields if value]
    return "\n".join(lines) + "\n"


def trailers(text: str) -> dict[str, str]:
    block = text.rstrip("\n").rsplit("\n\n", 1)[-1]
    found = {}
    for line in block.split("\n"):
        name, sep, value = line.partition(": ")
        if sep and re.fullmatch(r"[A-Z][A-Za-z-]+", name):
            found[name] = value
    return found


def open_history(places: Places, *, create: bool = True) -> History | None:
    """The Tag's history, or None without Dulwich. Readers pass ``create=False``."""
    path = places.history_dir()
    if path is None or not dulwich_available():
        return None
    if not create and not (path / "HEAD").is_file():
        return None
    return History(path, places.tag)


def section_text(content: str | None, section: str | None) -> str | None:
    if content is None:
        return None
    _meta, body = parse_front(content)
    if not section:
        return note_text(body)
    try:
        return parse_core(body).get(section)
    except KnowledgeError:
        return None


def entry_versions(places: Places, scope: str, key: str, kind: str, *, limit: int = 10,
                   scan: int = 2000) -> list[dict[str, Any]]:
    """Values one entry has had, newest first: queued changes, then history.

    Each value carries who set it and when it was first set. Text removed from
    history at someone's request is skipped.
    """
    path, _, section = entry_ref(places, scope, key, kind).partition("#")
    versions: list[dict[str, Any]] = []

    def add(text: str | None, by: str, at: str, thread: str) -> bool:
        if text is None or text == PURGED:
            return False
        value = {"text": text, "version": version_of(text), "saved_by": by, "saved_at": at, "from_thread": thread}
        if versions and versions[-1]["text"] == text:
            versions[-1] = value  # An older commit set this same text first.
            return False
        versions.append(value)
        return len(versions) > limit

    for item in reversed(read_journal(places.memory)):
        if path in item["files"] and add(section_text(item["files"][path], section or None),
                                         item.get("by", ""), item.get("at", ""), item.get("thread", "")):
            return versions[:limit]
    history = open_history(places, create=False)
    if history is None:
        return versions[:limit]
    previous_sha: bytes | None = None
    for count, commit in enumerate(history.log()):
        if count >= scan:
            break
        sha = history.files(commit.id.encode()).get(path)
        if sha is None or sha == previous_sha:
            previous_sha = sha
            continue
        previous_sha = sha
        if add(section_text(history.blob(sha), section or None), commit.trailers.get("Written-by", ""),
               commit.at, commit.trailers.get("Thread", "")):
            break
    return versions[:limit]


def sync(places: Places) -> dict[str, Any]:
    """Commit queued changes, then anything else that changed the files.

    Safe to repeat after a crash: journal entries already committed are skipped
    by their ``Change-Id``. Returns ``{"committed": n, "pending": bool}``.
    """
    history = open_history(places)
    if history is None:
        return {"committed": 0, "pending": bool(read_journal(places.memory)), "available": False}
    committed = 0
    with memory_lock(places.memory):
        journal = read_journal(places.memory)
        start = head = history.head()
        purged = False
        recent = {c.trailers.get("Change-Id") for c in history.log(limit=max(50, len(journal) + 10))}
        tree = history.files(head)
        for item in journal:
            if item.get("id") in recent:
                continue
            for name, content in item["files"].items():
                if content is None:
                    tree.pop(name, None)
                else:
                    tree[name] = history._blob(content.encode("utf-8"))
            change = Change(action=item.get("action", "changed"), summary=item.get("summary", ""),
                            by=item.get("by", ""), via=item.get("via", ""), thread=item.get("thread", ""),
                            entries=list(item.get("entries") or []), undo_of=item.get("undo_of", ""),
                            at=item.get("at", ""), ident=item.get("id", ""))
            head = history.commit(tree, change, head)
            committed += 1
            for target in item.get("purge") or []:
                head = purge(history, head, target["path"], target.get("key"), target.get("keep"))
                tree = history.files(head)
                scrub_legacy(places, target)
                purged = True
        current = snapshot(places)
        expected = {name: sha for name, sha in tree.items() if not managed(places, name)}
        expected.update({name: history._blob(data) for name, data in current.items()})
        if expected != tree or head is None:
            outside = sorted(name for name in set(expected) | set(tree)
                             if expected.get(name) != tree.get(name) and not name.endswith(SIDE))
            first = head is None
            change = Change(action="started" if first else "outside",
                            summary="Started keeping history" if first else
                            "Changed outside Tag: " + ", ".join(outside[:3]) + (" …" if len(outside) > 3 else ""),
                            by="" if not first else "tag", via="files", entries=outside,
                            at=utc_now(), ident=uuid.uuid4().hex)
            head = history.commit(expected, change, head)
            committed += 1
        history.move(start, head)
        if purged:
            # Old versions are unreachable now; delete them so the text is really gone.
            from dulwich.gc import prune_unreachable_objects
            prune_unreachable_objects(history.store, history.repo.refs, grace_period=0)
        if journal:
            write_file(places.memory / JOURNAL, "")
    return {"committed": committed, "pending": False, "available": True}


def purge(history: History, head: bytes, name: str, key: str | None, keep: str | None) -> bytes:
    """Rewrite history so earlier text of one entry is gone, then delete the old objects.

    ``key`` names a ``core.md`` section; without it the whole note is purged.
    The current text ``keep`` stays. Commit ids change; ``Undo-of`` trailers follow.
    Returns the new head; the caller moves the branch and deletes the old objects.
    """
    from dulwich.objects import Commit as GitCommit
    chain = list(reversed(list(history.log(head))))
    mapping: dict[str, bytes] = {}
    parent: bytes | None = None
    for item in chain:
        obj = history.store[item.id.encode()]
        files = history.files(item.id.encode())
        if name in files:
            raw = history.blob(files[name]) or ""
            scrubbed = purge_content(raw, key, keep)
            if scrubbed != raw:
                files[name] = history._blob(scrubbed.encode())
        text = obj.message.decode("utf-8")
        undo = trailers(text).get("Undo-of")
        if undo and undo in mapping:
            text = text.replace(f"Undo-of: {undo}", f"Undo-of: {mapping[undo].decode()}")
        commit = GitCommit()
        commit.tree = history._tree(files)
        commit.parents = [parent] if parent else []
        commit.author, commit.committer = obj.author, obj.committer
        commit.author_time, commit.commit_time = obj.author_time, obj.commit_time
        commit.author_timezone, commit.commit_timezone = obj.author_timezone, obj.commit_timezone
        commit.encoding = b"UTF-8"
        commit.message = text.encode("utf-8")
        history.store.add_object(commit)
        mapping[item.id] = commit.id
        parent = commit.id
    return parent  # type: ignore[return-value]


def scrub_legacy(places: Places, target: dict[str, Any]) -> None:
    """Remove purged text from the JSON backup that the markdown replaced."""
    if places.legacy is None:
        return
    scope = scope_of(target["path"])
    if scope is None:
        return
    path = places.legacy / ("global.json" if scope == GLOBAL else f"channels/{scope}.json")
    raw = read_file(path)
    if raw is None:
        return
    try:
        document = json.loads(raw)
    except ValueError:
        return
    key = target.get("key") or Path(target["path"]).stem
    changed = document.get("history", {}).pop(key, None) is not None
    entry = document.get("entries", {}).get(key)
    if entry is not None and entry.get("text") != target.get("keep"):
        del document["entries"][key]
        changed = True
    if changed:
        write_file(path, json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


# Migration ---------------------------------------------------------------------


def legacy_scopes(legacy: Path) -> list[tuple[str, dict[str, Any]]]:
    """The JSON scope documents an earlier Tag wrote; damaged ones stop the migration."""
    found: list[tuple[str, Path]] = []
    if (legacy / "global.json").is_file():
        found.append((GLOBAL, legacy / "global.json"))
    folder = legacy / "channels"
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            if CHANNEL_PATTERN.fullmatch(path.stem):
                found.append((path.stem, path))
    scopes = []
    for scope, path in found:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise KnowledgeError("unreadable", f"Memory file is damaged: {path}") from None
        if not isinstance(document, dict) or document.get("schema") != 1 or not all(
                isinstance(document.get(name), dict) for name in ("entries", "history", "forgotten")):
            raise KnowledgeError("unreadable", f"Memory file has an unsupported format: {path}")
        scopes.append((scope, document))
    return scopes


def migrate(places: Places, verify: Callable[[], None] | None = None) -> bool:
    """Turn the JSON memory into markdown files once; the last ten versions of each
    entry become the first history entries. Returns whether it migrated anything.

    The JSON stays in place as a backup. Re-running before ``.format.json`` is
    written starts over, so an interrupted migration never leaves half a memory.
    ``verify`` runs before that record is written; if it raises, the JSON stays
    the memory and the next attempt starts over.
    """
    if read_format(places.memory) is not None:
        return False
    with memory_lock(places.memory):
        if read_format(places.memory) is not None:
            return False
        scopes = legacy_scopes(places.legacy) if places.legacy is not None and places.legacy.is_dir() else []
        if not scopes:
            write_format(places.memory)
            return False
        events = []
        for order, (scope, document) in enumerate(scopes):
            for key, entry in document["entries"].items():
                versions = [*document["history"].get(key, []), entry]
                for step, version in enumerate(versions):
                    events.append((version.get("saved_at", ""), order, key, step, scope, entry["kind"], version))
            for key, marker in document["forgotten"].items():
                events.append((marker.get("at", ""), order, key, -1, scope, None, marker))
        events.sort(key=lambda item: item[:4])
        state = {scope: {"entries": {}, "forgotten": {}} for scope, _ in scopes}
        latest: dict[str, str | None] = {}
        lines = []
        for at, _order, key, step, scope, kind, version in events:
            before = state[scope]
            entries, forgotten = dict(before["entries"]), dict(before["forgotten"])
            if kind is not None:
                entries[key] = {"key": key, "kind": kind, "text": version["text"],
                                "saved_by": version.get("saved_by", ""), "saved_at": version.get("saved_at", "")}
                forgotten.pop(key, None)
                action, who = ("saved" if step == 0 else "changed"), version.get("saved_by", "")
            else:
                entries.pop(key, None)
                forgotten[key] = {"by": version.get("by", ""), "at": version.get("at", "")}
                action, who = "forgot", version.get("by", "")
            after = {"entries": dict(sorted(entries.items())), "forgotten": forgotten}
            change = Change(action=action, by=who, via="migration", at=at or utc_now(), ident=uuid.uuid4().hex,
                            summary=f"{action.capitalize()} {key} for "
                                    f"{'all channels' if scope == GLOBAL else 'channel ' + scope}",
                            entries=[entry_ref(places, scope, key, kind or "note")])
            files = scope_files(places, scope, before, after, change)
            state[scope] = after
            latest.update(files)
            lines.append(journal_line(change, files))
        # Start over from a clean slate (nothing else writes before the format record
        # exists), write the final files and the journal, then the format record last.
        for scope, _ in scopes:
            directory = places.scope_dir(scope)
            for path in [directory / "core.md", directory / SIDE, *(directory / "notes").glob("*.md")]:
                path.unlink(missing_ok=True)
        apply_files(places, {name: content for name, content in latest.items() if content is not None})
        write_file(places.memory / JOURNAL, "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines))
        if verify is not None:
            verify()
        write_format(places.memory, migrated_from="json", migrated_at=utc_now())
    return True


def entry_ref(places: Places, scope: str, key: str, kind: str) -> str:
    directory = places.scope_dir(scope)
    if kind == "core":
        return logical(places, directory / "core.md") + "#" + key
    return logical(places, directory / "notes" / f"{key}.md")


def iter_entries(places: Places) -> Iterable[tuple[str, dict[str, Any]]]:
    for scope in [GLOBAL, *channels(places)]:
        for entry in load_scope(places, scope)["entries"].values():
            yield scope, entry
