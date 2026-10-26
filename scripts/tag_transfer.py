# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""``tag NAME export`` and ``tag import FILE``: move one Tag to another computer.

The export is one zip file (``.hovertag``) with the Tag's memory and its
history, its skills, its threads and its settings. It never holds passwords or
tokens: credentials, Slack tokens, API keys and machine paths are left out,
and token-shaped text in memory, skills and threads is replaced with
``[removed token]``. Git history can't be edited in place, so an export refuses
when history holds a token-shaped value, unless it is made without history.

Import creates a new Tag on this computer from that file. It has no Slack
connection: the person finishes setup and reconnects the Tag's Slack app.
Routines arrive paused, so nothing runs before someone checks them.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

try:
    import tag_config
    import tag_instances
    import tag_knowledge as knowledge
    import tag_memory as memory
except ImportError:
    from scripts import tag_config, tag_instances
    from scripts import tag_knowledge as knowledge
    from scripts import tag_memory as memory

FORMAT = "hover-tag-export"
VERSION = 1
SUFFIX = ".hovertag"
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 50_000
REMOVED = "[removed token]"
TOKEN_SHAPES = (
    re.compile(r"\bx(?:ox[a-z]|app)-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\b(?:sk|rk)-(?:ant-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)
SECRET_NAME = re.compile(r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|PASSWD|KEY|APIKEY|CREDENTIALS?|AUTH|COOKIE|SESSION)(?:_|$)")
# Settings that only mean something on the computer they were made on.
LOCAL_SETTINGS = re.compile(r"^(?:MFS_.*|OPENTAG_WORKDIR|OPENTAG_PEER_TAGS|.*_(?:PATH|DIR|FILE|HOME|ROOT|URL))$")
# Slack's link to one app and workspace: setup reconnects them on the new computer.
SLACK_LINK = ("SLACK_APP_ID", "SLACK_TEAM_ID", "SLACK_ENTERPRISE_ID")
THREAD_FILES = re.compile(r"^[A-Za-z0-9._-]+\.jsonl$")


class TransferError(Exception):
    """A refused export or import; the message is safe to show."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def redact(text: str) -> tuple[str, int]:
    count = 0
    for pattern in TOKEN_SHAPES:
        text, n = pattern.subn(REMOVED, text)
        count += n
    return text, count


def has_token(data: bytes) -> bool:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return any(pattern.search(text) for pattern in TOKEN_SHAPES)


def safe_settings(values: dict[str, str], home: Path) -> dict[str, str]:
    """The Tag's settings with credentials, Slack's app link and machine paths left out."""
    kept = {}
    for name, value in values.items():
        if not isinstance(value, str) or SECRET_NAME.search(name) or LOCAL_SETTINGS.match(name) or name in SLACK_LINK:
            continue
        if not re.fullmatch(r"(?:OPENTAG|SLACK)_[A-Z0-9_]+", name):
            continue
        if str(home.parent) in value or redact(value)[1]:
            continue
        kept[name] = value
    return kept


# Export -----------------------------------------------------------------------------


class Archive:
    """Writes the zip, counting what it redacts."""

    def __init__(self, path: Path):
        self.zip = zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6)
        self.redacted: dict[str, int] = {}
        self.files = 0
        self.bytes = 0

    def add_bytes(self, name: str, data: bytes) -> None:
        self.files += 1
        self.bytes += len(data)
        if self.bytes > MAX_TOTAL_BYTES:
            raise TransferError("too_large", "This Tag is too large to export in one file.")
        info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = (stat.S_IFREG | 0o600) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        self.zip.writestr(info, data)

    def add_text(self, name: str, text: str, *, scrub: bool = True) -> None:
        if scrub:
            text, count = redact(text)
            if count:
                self.redacted[name] = count
        self.add_bytes(name, text.encode("utf-8"))

    def add_file(self, name: str, path: Path) -> None:
        if path.stat().st_size > MAX_FILE_BYTES:
            return
        data = path.read_bytes()
        try:
            self.add_text(name, data.decode("utf-8"))
        except UnicodeDecodeError:
            if has_token(data):
                self.redacted[name] = -1  # Left out: binary with a token-shaped value.
                return
            self.add_bytes(name, data)

    def close(self) -> None:
        self.zip.close()


def regular_files(folder: Path) -> list[Path]:
    """Files under ``folder``, never following links out of it."""
    found = []
    for root, directories, files in os.walk(folder, followlinks=False):
        directories[:] = sorted(d for d in directories if not (Path(root) / d).is_symlink() and d not in {".git", "node_modules", "__pycache__"})
        for name in sorted(files):
            path = Path(root) / name
            if path.is_file() and not path.is_symlink():
                found.append(path)
    return found


def export_tag(installation_root: Path, context: Any, output: Path, *, history: bool = True) -> dict[str, Any]:
    home, workspace = context.home, context.workspace
    places = knowledge.Places.for_tag(installation_root, home, workspace, context.tag_id)
    memory.prepare(places)
    if history and knowledge.dulwich_available():
        knowledge.sync(places)
    settings_path = home / "config/settings.json"
    values = tag_config.load_config(settings_path) if settings_path.is_file() else {}
    output = output.expanduser()
    if output.is_dir():
        output = output / default_name(values, context.tag_id)
    if output.suffix != SUFFIX:
        output = output.with_name(output.name + SUFFIX)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    counts = {"memory": 0, "history": 0, "skills": 0, "threads": 0, "routines": 0}
    try:
        archive = Archive(temporary)
        try:
            with knowledge.memory_lock(places.memory):
                for path in regular_files(places.memory):
                    relative = path.relative_to(places.memory)
                    if relative.name in {".lock", knowledge.JOURNAL, knowledge.FORMAT_FILE} or relative.name.endswith(".tmp"):
                        continue
                    archive.add_file(f"memory/{relative.as_posix()}", path)
                    counts["memory"] += relative.suffix == ".md"
                repository = places.history_dir()
                if history and repository is not None and (repository / "HEAD").is_file():
                    if (where := history_token(places)) is not None:
                        raise TransferError(
                            "history_has_token",
                            f"The Tag's history holds what looks like a password or token ({where}). Remove it with "
                            "tag knowledge forget/edit and --drop-old, or export without history.")
                    for path in regular_files(repository):
                        relative = path.relative_to(repository)
                        if relative.parts[0] in {"hooks", "logs"}:
                            continue
                        archive.add_bytes(f"history/{relative.as_posix()}", path.read_bytes())
                    counts["history"] = sum(1 for _ in knowledge.History(repository, context.tag_id).log())
            for label, folder in knowledge.SKILL_ROOTS:
                root = workspace / folder
                if not root.is_dir():
                    continue
                for skill in sorted(root.iterdir()):
                    if skill.is_symlink() or not (skill / "SKILL.md").is_file() or skill.name.startswith("."):
                        continue
                    for path in regular_files(skill):
                        archive.add_file(f"skills/{label}/{skill.name}/{path.relative_to(skill).as_posix()}", path)
                    counts["skills"] += 1
            threads = home / "state/threads"
            if threads.is_dir():
                for path in sorted(threads.iterdir()):
                    if path.is_file() and not path.is_symlink() and (THREAD_FILES.match(path.name) or path.name == "settings.json"):
                        archive.add_file(f"threads/{path.name}", path)
                        counts["threads"] += path.suffix == ".jsonl"
            archive.add_text("settings/settings.json", json.dumps(safe_settings(values, home), indent=2, sort_keys=True) + "\n")
            routines = home / "routines.json"
            if routines.is_file():
                archive.add_file("settings/routines.json", routines)
                try:
                    counts["routines"] = len(json.loads(routines.read_text(encoding="utf-8")).get("routines") or [])
                except (OSError, ValueError, AttributeError):
                    counts["routines"] = 0
            claude = workspace / ".claude/settings.local.json"
            if claude.is_file():
                try:
                    permissions = json.loads(claude.read_text(encoding="utf-8")).get("permissions")
                except (OSError, ValueError, AttributeError):
                    permissions = None
                if isinstance(permissions, dict):
                    archive.add_text("settings/claude-permissions.json", json.dumps(permissions, indent=2) + "\n")
            rules = workspace / ".codex/rules"
            if rules.is_dir():
                for path in regular_files(rules):
                    archive.add_file(f"settings/codex-rules/{path.relative_to(rules).as_posix()}", path)
            manifest = {
                "format": FORMAT, "version": VERSION, "exported_at": knowledge.utc_now(),
                "tag": {"id": context.tag_id, "name": values.get("OPENTAG_BOT_NAME") or None,
                        "description": values.get("OPENTAG_BOT_DESCRIPTION") or None},
                "slack": {"team": values.get("SLACK_TEAM_ID") or None, "app": values.get("SLACK_APP_ID") or None,
                          "workspace_name": tag_instances.workspace_name(home)},
                "contents": counts, "history": counts["history"] > 0, "redacted": sorted(archive.redacted),
            }
            archive.add_text("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", scrub=False)
        finally:
            archive.close()
        os.replace(temporary, output)
        os.chmod(output, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(output), "bytes": output.stat().st_size, **manifest}


def history_token(places: knowledge.Places) -> str | None:
    """Where history holds a token-shaped value, or None."""
    found = knowledge.open_history(places, create=False)
    if found is None:
        return None
    for commit in found.log():
        for name, sha in found.files(commit.id.encode()).items():
            if has_token(found.store[sha].data):
                return f"{name}, change {commit.id[:10]}"
    return None


def default_name(values: dict[str, str], tag_id: str) -> str:
    name = tag_instances.slugify(values.get("OPENTAG_BOT_NAME") or "") or tag_id
    return f"{name}-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}{SUFFIX}"


# Import -----------------------------------------------------------------------------


def read_manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError):
        raise TransferError("not_an_export", "This file isn't a Tag export.") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise TransferError("not_an_export", "This file isn't a Tag export.")
    if manifest.get("version") != VERSION:
        raise TransferError("newer_export", "This export was made by a newer Tag. Upgrade Tag, then import it.")
    return manifest


def checked_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Every member, refusing paths that leave the Tag, links, and oversized files."""
    members = archive.infolist()
    if len(members) > MAX_ENTRIES:
        raise TransferError("too_large", "This export has too many files.")
    total = 0
    for info in members:
        path = PurePosixPath(info.filename)
        if (info.filename.startswith("/") or "\\" in info.filename or ".." in path.parts or ":" in info.filename
                or stat.S_ISLNK(info.external_attr >> 16)):
            raise TransferError("unsafe_export", f"This export has an unsafe path: {info.filename!r}.")
        total += info.file_size
        if info.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise TransferError("too_large", "This export is too large.")
    return members


def import_tag(installation_root: Path, file: Path) -> dict[str, Any]:
    file = file.expanduser()
    try:
        archive = zipfile.ZipFile(file)
    except (OSError, zipfile.BadZipFile):
        raise TransferError("not_an_export", f"Can't read {file.name} as a Tag export.") from None
    with archive:
        manifest = read_manifest(archive)
        members = [m for m in checked_members(archive) if not m.is_dir()]
        app = (manifest.get("slack") or {}).get("app")
        if app:
            for item in tag_instances.discover(installation_root):
                home = Path(str(item["home"]))
                settings = home / "config/settings.json"
                try:
                    values = tag_config.load_config(settings) if settings.is_file() else {}
                except (OSError, ValueError):
                    continue
                if values.get("SLACK_APP_ID") == app:
                    raise TransferError("already_here", f"This Tag is already on this computer as '{item['id']}'.")
        name = (manifest.get("tag") or {}).get("name") or "imported tag"
        context = tag_instances.create(installation_root, tag_instances.suggest_name(installation_root, re.sub(r"['’]", "", name)),
                                       provisional=True)
        try:
            restore(installation_root, context, archive, members, manifest)
        except BaseException:
            repository = knowledge.Places.for_tag(installation_root, context.home, None, context.tag_id).history_dir()
            if repository is not None:
                shutil.rmtree(repository, ignore_errors=True)
            shutil.rmtree(context.home.parent if context.home.name == ".tag" else context.home, ignore_errors=True)
            raise
    return {"tag": context.tag_id, "name": (manifest.get("tag") or {}).get("name"), "contents": manifest.get("contents"),
            "redacted": manifest.get("redacted", []),
            "slack": manifest.get("slack"), "history": manifest.get("history", False),
            "next": f"tag {context.tag_id} setup"}


def restore(installation_root: Path, context: Any, archive: zipfile.ZipFile, members: list[zipfile.ZipInfo],
            manifest: dict[str, Any]) -> None:
    home, workspace = context.home, context.workspace
    places = knowledge.Places.for_tag(installation_root, home, workspace, context.tag_id)
    places.memory.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = knowledge.write_format(places.memory, imported_from=manifest["tag"].get("id"), imported_at=knowledge.utc_now())
    repository = places.history_dir()
    assert repository is not None and record["history"] in repository.name

    def target(name: str) -> Path | None:
        parts = PurePosixPath(name).parts
        top, rest = parts[0], parts[1:]
        if top == "memory" and rest:
            return places.memory.joinpath(*rest)
        if top == "history" and rest:
            return repository.joinpath(*rest)
        if top == "skills" and len(rest) >= 3 and rest[0] in dict(knowledge.SKILL_ROOTS):
            return workspace / dict(knowledge.SKILL_ROOTS)[rest[0]] / Path(*rest[1:])
        if top == "threads" and len(rest) == 1 and (THREAD_FILES.match(rest[0]) or rest[0] == "settings.json"):
            return home / "state/threads" / rest[0]
        if top == "settings" and rest[:1] == ("codex-rules",) and len(rest) > 1:
            return workspace / ".codex/rules" / Path(*rest[1:])
        return None

    for info in members:
        path = target(info.filename)
        if path is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with archive.open(info) as source, open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as handle:
            shutil.copyfileobj(source, handle)
    names = {info.filename for info in members}
    if "settings/settings.json" in names:
        try:
            imported = json.loads(archive.read("settings/settings.json").decode("utf-8"))
        except ValueError:
            imported = {}
        settings = safe_settings({k: v for k, v in imported.items() if isinstance(v, str)}, home)
        path = home / "config/settings.json"
        current = tag_config.load_config(path) if path.is_file() else {}
        tag_config.save_config(path, {**settings, **current})
    if "settings/routines.json" in names:
        try:
            data = json.loads(archive.read("settings/routines.json").decode("utf-8"))
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("routines"), list):
            for routine in data["routines"]:
                if isinstance(routine, dict) and routine.get("enabled", True):
                    routine["enabled"] = False
                    routine["paused_reason"] = "Imported from another computer; check it, then resume it"
            path = home / "routines.json"
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            os.chmod(path, 0o600)
    if "settings/claude-permissions.json" in names:
        try:
            permissions = json.loads(archive.read("settings/claude-permissions.json").decode("utf-8"))
        except ValueError:
            permissions = None
        if isinstance(permissions, dict):
            path = workspace / ".claude/settings.local.json"
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_text(json.dumps({"permissions": permissions}, indent=2) + "\n", encoding="utf-8")
    workspace_name = (manifest.get("slack") or {}).get("workspace_name")
    if isinstance(workspace_name, str) and workspace_name.strip():
        tag_instances.record_workspace_name(home, workspace_name.strip()[:120])
    if knowledge.dulwich_available():
        knowledge.sync(places)


# Command line ------------------------------------------------------------------------


def run_export(installation_root: Path, context: Any, argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="tag [TAG] export", description=(
        "Save this Tag's memory and history, skills, threads and settings to one file. "
        "Passwords and tokens are never included."))
    parser.add_argument("--output", type=Path, default=Path.cwd(), help="file or folder (default: this folder)")
    parser.add_argument("--without-history", action="store_true", help="leave out the history of changes")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    try:
        result = export_tag(installation_root, context, args.output, history=not args.without_history)
    except (TransferError, knowledge.KnowledgeError, memory.MemoryRefused) as exc:
        return _failed(args.json_output, exc)
    if args.json_output:
        print(json.dumps({"schema_version": 1, "ok": True, "tag": context.tag_id, "export": result}, indent=2, ensure_ascii=False))
    else:
        counts = result["contents"]
        print(f"Exported {context.tag_id} to {result['path']}")
        print(f"  {counts['memory']} memory files, {counts['history']} changes in history, {counts['skills']} skills, "
              f"{counts['threads']} threads, {counts['routines']} routines. No passwords or tokens.")
        if result["redacted"]:
            print(f"  Removed token-shaped text from: {', '.join(result['redacted'])}")
    return 0


def run_import(installation_root: Path, argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="tag import", description=(
        "Add a Tag from a file made with tag export, then reconnect its Slack app with setup."))
    parser.add_argument("file", type=Path)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    try:
        result = import_tag(installation_root, args.file)
    except (TransferError, knowledge.KnowledgeError, memory.MemoryRefused, ValueError) as exc:
        return _failed(args.json_output, exc)
    if args.json_output:
        print(json.dumps({"schema_version": 1, "ok": True, "imported": result}, indent=2, ensure_ascii=False))
    else:
        print(f"Imported {result['name'] or 'the Tag'} as {result['tag']}.")
        app = (result.get("slack") or {}).get("app")
        print(f"Next: stop it on the old computer, then run {result['next']}"
              + (f" and choose its Slack app {app}." if app else " to connect Slack."))
    return 0


def _failed(json_output: bool, exc: Exception) -> int:
    code = getattr(exc, "code", "failed")
    if json_output:
        print(json.dumps({"schema_version": 1, "ok": False, "error": str(exc), "code": code}))
    else:
        import sys
        print(f"Error: {exc}", file=sys.stderr)
    return 1
