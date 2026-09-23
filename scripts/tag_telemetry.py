"""Privacy-bounded, installation-wide telemetry for the Tag CLI.

CLI callers can only use the event-specific functions in this module. There is
deliberately no public generic capture API: event names, property names, and
values are constrained here before anything reaches disk or the network.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

try:
    import tag_telemetry_config as build_config
except ImportError:
    from scripts import tag_telemetry_config as build_config


SCHEMA_VERSION = 1
QUEUE_LIMIT = 64
QUEUE_TTL_SECONDS = 24 * 60 * 60
DURATION_BUCKETS = ("<5s", "5–30s", "30–120s", ">120s")
INVOCATION_KINDS = frozenset({"interactive", "non_interactive"})
SETUP_ENTRY_POINTS = frozenset({"setup", "add", "test"})
SETUP_STEPS = frozenset({"notice", "slack", "app", "channels", "finish"})
BACKEND_KINDS = frozenset({"codex", "claude"})
COMMAND_GROUPS = frozenset({
    "configuration", "diagnostics", "lifecycle", "memory", "setup",
    "tag_management", "update",
})
COMMAND_OUTCOMES = frozenset({"succeeded", "failed", "interrupted"})
ERROR_CATEGORIES = frozenset({
    "configuration", "dependency", "interrupted", "network", "permission",
    "runtime", "unknown", "validation",
})


def duration_bucket(seconds: float) -> str:
    """Reduce elapsed time to a deliberately coarse, closed value."""
    if seconds < 5:
        return DURATION_BUCKETS[0]
    if seconds < 30:
        return DURATION_BUCKETS[1]
    if seconds < 120:
        return DURATION_BUCKETS[2]
    return DURATION_BUCKETS[3]


def hard_disabled(environment: dict[str, str] | None = None) -> bool:
    source = os.environ if environment is None else environment
    return source.get("TAG_TELEMETRY", "").strip().lower() == "off"


def preference_path(installation_root: Path) -> Path:
    return installation_root / "config/telemetry.json"


def identifier_path(installation_root: Path) -> Path:
    return installation_root / "state/telemetry-id"


def queue_path(installation_root: Path) -> Path:
    return installation_root / "state/telemetry-queue"


def worker_lock_path(installation_root: Path) -> Path:
    return installation_root / "state/telemetry-worker.lock"


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def saved_preference(installation_root: Path) -> bool | None:
    try:
        value = json.loads(preference_path(installation_root).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    enabled = value.get("enabled") if isinstance(value, dict) else None
    return enabled if isinstance(enabled, bool) else None


def status(installation_root: Path) -> dict[str, object]:
    saved = saved_preference(installation_root)
    return {
        "schema_version": SCHEMA_VERSION,
        "enabled": False if hard_disabled() else saved is True,
        "saved_preference": "on" if saved is True else "off" if saved is False else "not_set",
        "process_override": "off" if hard_disabled() else None,
        "privacy_notice": build_config.PRIVACY_NOTICE_URL or None,
    }


def _write_preference(installation_root: Path, enabled: bool) -> None:
    _atomic_json(preference_path(installation_root), {
        "schema_version": SCHEMA_VERSION,
        "enabled": enabled,
    })


def enable(installation_root: Path) -> bool:
    """Persist opt-in; return false when the process hard-stop is active."""
    if hard_disabled() or not collection_available():
        return False
    previously_enabled = saved_preference(installation_root) is True
    _write_preference(installation_root, True)
    _installation_identifier(installation_root)
    if not previously_enabled:
        telemetry_preference_enabled(installation_root)
    return True


def disable(installation_root: Path) -> bool:
    """Persist opt-out and remove all pseudonymous telemetry state."""
    if hard_disabled():
        return False
    _write_preference(installation_root, False)
    identifier_path(installation_root).unlink(missing_ok=True)
    shutil.rmtree(queue_path(installation_root), ignore_errors=True)
    worker_lock_path(installation_root).unlink(missing_ok=True)
    return True


def _installation_identifier(installation_root: Path) -> str:
    path = identifier_path(installation_root)
    try:
        existing = path.read_text(encoding="ascii").strip()
        return str(uuid.UUID(existing))
    except (OSError, ValueError, UnicodeError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    identifier = str(uuid.uuid4())
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return str(uuid.UUID(path.read_text(encoding="ascii").strip()))
    try:
        os.write(descriptor, (identifier + "\n").encode("ascii"))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
    return identifier


def collection_available() -> bool:
    return bool(
        build_config.POSTHOG_PROJECT_TOKEN
        and build_config.POSTHOG_HOST.startswith("https://")
        and build_config.PRIVACY_NOTICE_URL.startswith("https://")
    )


def _version() -> str:
    try:
        return (Path(__file__).resolve().parents[1] / "VERSION").read_text(
            encoding="utf-8"
        ).strip()
    except (OSError, UnicodeError):
        return "unknown"


def _enqueue(
    installation_root: Path, event: str, properties: dict[str, str],
    *, launch_worker: bool = True,
) -> None:
    if hard_disabled() or saved_preference(installation_root) is not True or not collection_available():
        return
    try:
        identifier = _installation_identifier(installation_root)
        directory = queue_path(installation_root)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        _prune(directory)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "created_at": time.time(),
            "event": event,
            "distinct_id": identifier,
            "properties": properties,
        }
        path = directory / f"{time.time_ns()}-{uuid.uuid4().hex}.json"
        _atomic_json(path, payload)
        _prune(directory)
        if launch_worker:
            _launch_flush_worker(installation_root)
    except (OSError, ValueError):
        return


def _prune(directory: Path, now: float | None = None) -> None:
    current = time.time() if now is None else now
    try:
        files = sorted(directory.glob("*.json"), key=lambda item: item.name)
    except OSError:
        return
    retained: list[Path] = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            created = float(payload["created_at"])
            if current - created > QUEUE_TTL_SECONDS or created > current + 60:
                path.unlink(missing_ok=True)
            else:
                retained.append(path)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
    for path in retained[:-QUEUE_LIMIT]:
        path.unlink(missing_ok=True)


def _launch_flush_worker(installation_root: Path) -> None:
    if not collection_available():
        return
    lock = worker_lock_path(installation_root)
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            if time.time() - lock.stat().st_mtime <= 60:
                return
            lock.unlink()
            descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except (FileExistsError, FileNotFoundError, OSError):
            return
    os.close(descriptor)
    environment = {
        key: value for key, value in os.environ.items()
        if "TELEMETRY" not in key.upper() and "POSTHOG" not in key.upper()
    }
    options = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
        if os.name == "nt" else {"start_new_session": True}
    )
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--flush", str(installation_root)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            **options,
        )
    except OSError:
        lock.unlink(missing_ok=True)


def _post(payload: dict[str, object]) -> None:
    body = json.dumps({
        "api_key": build_config.POSTHOG_PROJECT_TOKEN,
        "event": payload["event"],
        "properties": {
            "distinct_id": payload["distinct_id"],
            "$process_person_profile": False,
            "$geoip_disable": True,
            **payload["properties"],
        },
    }, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        build_config.POSTHOG_HOST.rstrip("/") + "/capture/",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        if not 200 <= response.status < 300:
            raise urllib.error.HTTPError(
                request.full_url, response.status, "telemetry rejected", response.headers, None
            )


def flush(installation_root: Path, sender: Callable[[dict[str, object]], None] = _post) -> None:
    """Best-effort worker: every attempted event is removed, even on failure."""
    if hard_disabled() or saved_preference(installation_root) is not True or not collection_available():
        return
    directory = queue_path(installation_root)
    _prune(directory)
    for path in sorted(directory.glob("*.json"), key=lambda item: item.name):
        if hard_disabled() or saved_preference(installation_root) is not True:
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            sender(payload)
        except (OSError, KeyError, TypeError, ValueError, urllib.error.URLError, json.JSONDecodeError):
            pass
        finally:
            path.unlink(missing_ok=True)


def tui_started(installation_root: Path, invocation_kind: str) -> None:
    if invocation_kind not in INVOCATION_KINDS:
        return
    _enqueue(installation_root, "tui_started", {
        "tag_version": _version(),
        "os_family": platform.system().lower() or "unknown",
        "cpu_architecture": platform.machine().lower() or "unknown",
        "invocation_kind": invocation_kind,
    })


def setup_started(installation_root: Path, entry_point: str) -> None:
    if entry_point in SETUP_ENTRY_POINTS:
        _enqueue(installation_root, "setup_started", {"entry_point": entry_point})


def setup_step_completed(installation_root: Path, step: str, elapsed_seconds: float) -> None:
    if step in SETUP_STEPS:
        _enqueue(installation_root, "setup_step_completed", {
            "step": step, "elapsed_time": duration_bucket(elapsed_seconds),
        })


def setup_abandoned(installation_root: Path, last_step: str, elapsed_seconds: float) -> None:
    if last_step in SETUP_STEPS:
        _enqueue(installation_root, "setup_abandoned", {
            "last_step": last_step, "elapsed_time": duration_bucket(elapsed_seconds),
        })


def setup_completed(installation_root: Path, elapsed_seconds: float, backend_kind: str) -> None:
    if backend_kind in BACKEND_KINDS:
        _enqueue(installation_root, "setup_completed", {
            "elapsed_time": duration_bucket(elapsed_seconds), "backend_kind": backend_kind,
        })


def command_completed(
    installation_root: Path, command_group: str, outcome: str, elapsed_seconds: float,
) -> None:
    if command_group in COMMAND_GROUPS and outcome in COMMAND_OUTCOMES:
        _enqueue(installation_root, "command_completed", {
            "command_group": command_group,
            "outcome": outcome,
            "duration": duration_bucket(elapsed_seconds),
        })


def command_failed(installation_root: Path, command_group: str, error_category: str) -> None:
    if command_group in COMMAND_GROUPS and error_category in ERROR_CATEGORIES:
        _enqueue(installation_root, "command_failed", {
            "command_group": command_group, "error_category": error_category,
        })


def telemetry_preference_enabled(installation_root: Path) -> None:
    _enqueue(installation_root, "telemetry_preference_changed", {"preference": "enabled"})


class SetupSession:
    """Track setup stages without accepting arbitrary labels or error details."""

    def __init__(self, installation_root: Path, entry_point: str) -> None:
        self.installation_root = installation_root
        self.entry_point = entry_point if entry_point in SETUP_ENTRY_POINTS else "setup"
        self.started_at = time.monotonic()
        self.step_started_at = self.started_at
        self.current_step = "notice"
        self.finished = False

    def start(self) -> None:
        setup_started(self.installation_root, self.entry_point)

    def enter(self, step: str) -> None:
        if self.finished or step not in SETUP_STEPS or step == self.current_step:
            return
        now = time.monotonic()
        setup_step_completed(
            self.installation_root, self.current_step, now - self.step_started_at
        )
        self.current_step = step
        self.step_started_at = now

    def complete(self, backend_kind: str) -> None:
        if self.finished:
            return
        now = time.monotonic()
        setup_step_completed(
            self.installation_root, self.current_step, now - self.step_started_at
        )
        setup_completed(
            self.installation_root, now - self.started_at, backend_kind
        )
        self.finished = True

    def abandon(self) -> None:
        if self.finished:
            return
        setup_abandoned(
            self.installation_root,
            self.current_step,
            time.monotonic() - self.started_at,
        )
        self.finished = True


def _main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--flush":
        installation_root = Path(sys.argv[2]).expanduser().resolve()
        try:
            flush(installation_root)
        finally:
            worker_lock_path(installation_root).unlink(missing_ok=True)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
