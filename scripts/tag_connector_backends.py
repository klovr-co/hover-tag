"""Write Tag's connectors into Claude's and Codex's user-level MCP config (ADR 0014).

Each backend owns its file, so Tag changes it only through that backend's CLI
(``claude mcp`` and ``codex mcp``). Tag records a fingerprint of each entry it
wrote and later updates or removes the entry only while it still matches. An
entry changed or removed outside Tag is reported and left alone.

Per-backend states, for ``--json`` and Tag.app:

- ``written``: Tag's entry is in the backend's config, as Tag wrote it.
- ``pending``: not written yet (the CLI is missing or failed); ``sync`` retries.
- ``changed_outside_tag``: Tag wrote it, but it has been edited since.
- ``missing``: Tag wrote it, but it has been removed since.
- ``name_in_use``: not written, because the backend has another server by that name.
- ``unsupported``: this backend can't take this connector through its CLI.
- ``unknown``: the backend's config can't be read right now.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

try:
    from .opentag_process_env import without_telemetry_environment
except ImportError:
    from opentag_process_env import without_telemetry_environment

TIMEOUT_SECONDS = 30
# Fields that describe status rather than the definition Tag wrote.
VOLATILE_FIELDS = {"name", "auth_status", "disabled_reason"}


class BackendError(RuntimeError):
    """A backend CLI call failed; the message is safe to show."""


def fingerprint(definition: dict) -> str:
    return hashlib.sha256(json.dumps(_canonical(definition), sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _canonical(value):
    # Drop empty and status fields so a backend upgrade that adds null
    # defaults doesn't look like an edit.
    if isinstance(value, dict):
        items = {key: _canonical(item) for key, item in value.items() if key not in VOLATILE_FIELDS}
        return {key: item for key, item in items.items() if item not in (None, [], {})}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _run(command: list[str], *, saved: str | None = None) -> str:
    """Run a backend CLI and return its output, or raise BackendError.

    ``codex mcp add`` saves the server and then starts a sign-in flow for OAuth
    servers, which waits for a browser. With ``saved``, the call stops once that
    text appears or the time runs out, and the caller checks what was saved.
    """
    program = Path(command[0]).name
    with tempfile.TemporaryFile("w+") as stdout, tempfile.TemporaryFile("w+") as stderr:
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       text=True, env=without_telemetry_environment(os.environ),
                                       start_new_session=os.name == "posix")
        except OSError as exc:
            raise BackendError(f"Couldn't run {program}: {exc.strerror}.") from None
        deadline = time.monotonic() + TIMEOUT_SECONDS
        stopped = False
        while process.poll() is None:
            stdout.seek(0)
            stderr.seek(0)
            if saved and (saved in stdout.read() or saved in stderr.read()) or time.monotonic() >= deadline:
                _stop(process)
                stopped = True
                break
            time.sleep(0.05)
        stdout.seek(0)
        stderr.seek(0)
        out, err = stdout.read(), stderr.read()
    if stopped and not saved:
        raise BackendError(f"{program} didn't answer within {TIMEOUT_SECONDS} seconds.")
    if not stopped and process.returncode != 0:
        lines = [line for line in (err or out).splitlines() if line.strip() and not line.startswith("WARNING:")]
        raise BackendError((lines[-1] if lines else f"{program} failed.")[:200])
    return out


def _stop(process: subprocess.Popen) -> None:
    # The codex launcher runs the real binary as a child, so stop the whole group.
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()
        process.wait()


class Claude:
    name = "claude"

    def executable(self) -> str | None:
        return shutil.which("claude")

    def config_path(self) -> Path:
        base = os.environ.get("CLAUDE_CONFIG_DIR")
        return Path(base).expanduser() / ".claude.json" if base else Path.home() / ".claude.json"

    def supports(self, entry: dict) -> str | None:
        return None

    def definition(self, entry: dict) -> dict:
        if entry["transport"] == "http":
            return {"type": "http", "url": entry["url"]}
        # ${NAME} is expanded by Claude when it starts the server, so no value is written.
        return {"type": "stdio", "command": entry["command"], "args": entry.get("args", []),
                "env": {name: "${" + name + "}" for name in entry.get("env", [])}}

    def read_all(self) -> dict | None:
        """The user-level servers by name, or None when the config can't be read."""
        try:
            data = json.loads(self.config_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, UnicodeError):
            return None
        servers = data.get("mcpServers", {}) if isinstance(data, dict) else None
        return servers if isinstance(servers, dict) else None

    def add(self, executable: str, name: str, entry: dict) -> None:
        _run([executable, "mcp", "add-json", "--scope", "user", name,
              json.dumps(self.definition(entry), separators=(",", ":"))])

    def remove(self, executable: str, name: str) -> None:
        _run([executable, "mcp", "remove", "--scope", "user", name])

    def remove_hint(self, name: str) -> str:
        return f"claude mcp remove --scope user {name}"


class Codex:
    name = "codex"

    def executable(self) -> str | None:
        return shutil.which("codex")

    def supports(self, entry: dict) -> str | None:
        if entry["transport"] == "stdio" and entry.get("env"):
            # Codex passes names through with env_vars, but `codex mcp add` can only set values.
            return "The codex CLI can't pass environment variable names to a local connector yet."
        return None

    def read_all(self) -> dict | None:
        executable = self.executable()
        if not executable:
            return None
        try:
            rows = json.loads(_run([executable, "mcp", "list", "--json"]))
        except (BackendError, ValueError):
            return None
        if not isinstance(rows, list):
            return None
        return {row["name"]: row for row in rows if isinstance(row, dict) and isinstance(row.get("name"), str)}

    def add(self, executable: str, name: str, entry: dict) -> None:
        if entry["transport"] == "http":
            _run([executable, "mcp", "add", name, "--url", entry["url"]],
                 saved=f"Added global MCP server '{name}'.")
        else:
            _run([executable, "mcp", "add", name, "--", entry["command"], *entry.get("args", [])])

    def remove(self, executable: str, name: str) -> None:
        _run([executable, "mcp", "remove", name])

    def remove_hint(self, name: str) -> str:
        return f"codex mcp remove {name}"


BACKENDS = (Claude(), Codex())


def snapshot() -> dict:
    """Each backend's current user-level servers, read once per command."""
    return {backend.name: backend.read_all() for backend in BACKENDS}


def state(backend, name: str, entry: dict, current: dict | None) -> dict:
    """Where this connector stands in one backend, without changing anything."""
    record = entry.get("backends", {}).get(backend.name)
    if not record:
        reason = backend.supports(entry)
        if reason:
            return {"state": "unsupported", "detail": reason}
        if current is not None and name in current:
            return {"state": "name_in_use",
                    "detail": f"{backend.name} already has a server named '{name}' that Tag didn't add."}
        detail = entry.get("pending", {}).get(backend.name)
        if not backend.executable():
            detail = f"{backend.name} isn't installed; tag connector sync adds it later."
        return {"state": "pending", "detail": detail} if detail else {"state": "pending"}
    if current is None:
        return {"state": "unknown", "detail": f"Couldn't read {backend.name}'s MCP config."}
    if name not in current:
        return {"state": "missing", "detail": f"Removed from {backend.name} outside Tag."}
    if fingerprint(current[name]) != record.get("fingerprint"):
        return {"state": "changed_outside_tag", "detail": f"Changed in {backend.name} outside Tag; left alone."}
    return {"state": "written"}


def states(name: str, entry: dict, current: dict) -> dict:
    return {backend.name: state(backend, name, entry, current[backend.name]) for backend in BACKENDS}


def foreign_names(name: str, current: dict) -> list[str]:
    """Backends that already have a server by this name. Call before Tag owns the name."""
    return [backend.name for backend in BACKENDS if current[backend.name] and name in current[backend.name]]


def write(name: str, entry: dict, current: dict) -> dict:
    """Write every pending backend that can take this connector. Changes ``entry`` in place.

    A failure leaves that backend pending with a reason, so ``sync`` can retry.
    """
    records = entry.setdefault("backends", {})
    pending = entry.setdefault("pending", {})
    for backend in BACKENDS:
        if state(backend, name, entry, current[backend.name])["state"] != "pending":
            continue
        executable = backend.executable()
        if not executable or current[backend.name] is None:
            # Without a readable config Tag can't tell the name is free, and `codex mcp add` overwrites.
            pending.pop(backend.name, None)
            continue
        try:
            backend.add(executable, name, entry)
            written = backend.read_all()
            if not written or name not in written:
                raise BackendError(f"{backend.name} didn't keep the server.")
        except BackendError as exc:
            pending[backend.name] = str(exc)
            continue
        records[backend.name] = {"fingerprint": fingerprint(written[name])}
        pending.pop(backend.name, None)
        current[backend.name] = written
    if not pending:
        entry.pop("pending")
    return states(name, entry, current)


def remove(name: str, entry: dict, current: dict) -> dict:
    """Remove the entries Tag still owns. Returns what happened in each backend."""
    results = {}
    for backend in BACKENDS:
        found = state(backend, name, entry, current[backend.name])
        if found["state"] != "written":
            # Nothing of Tag's is there, or it changed outside Tag: leave it alone.
            results[backend.name] = found
            continue
        executable = backend.executable()
        try:
            if not executable:
                raise BackendError(f"{backend.name} isn't installed.")
            backend.remove(executable, name)
            results[backend.name] = {"state": "removed"}
        except BackendError as exc:
            results[backend.name] = {"state": "left_behind",
                                     "detail": f"{exc} Remove it yourself: {backend.remove_hint(name)}"}
    return results
