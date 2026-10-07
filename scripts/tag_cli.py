"""Cross-platform TAG lifecycle. Installed launchers use the release's Python."""
from __future__ import annotations

from contextlib import nullcontext
import argparse
import contextlib
import ipaddress
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path

try:
    from tag_paths import initialize_instance, initialize_workspace, runtime_environment, tag_home
    import tag_instances
    import tag_telemetry
    from tag_locks import LifecycleLock
    import tag_locks
    from tag_config import read_config
    import tag_credentials
    import tag_welcome
    import tag_mfs_runtime
    import tag_slack_backoff
    import tag_display as display
    import tag_autostart as autostart
    import agent_models
except ImportError:
    from scripts.tag_paths import initialize_instance, initialize_workspace, runtime_environment, tag_home
    from scripts import tag_instances, tag_telemetry
    from scripts.tag_locks import LifecycleLock
    from scripts import tag_locks
    from scripts import agent_models
    from scripts.tag_config import read_config
    from scripts import tag_credentials
    from scripts import tag_welcome
    from scripts import tag_mfs_runtime, tag_slack_backoff
    from scripts import tag_display as display
    from scripts import tag_autostart as autostart

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DEPENDENCIES = ("mfs_server", "psutil", "slack_bolt")
UPGRADE_CHANNELS = ("stable", "beta", "alpha", "edge")
# How long tag start waits for a setup, start or stop that holds the Tag's locks.
START_LOCK_WAIT_SECONDS = 90
APP_IDENTITY_WAIT_SECONDS = 10
# Contract between this CLI and desktop apps; see docs/reference/app-protocol.md.
# Bump only for incompatible changes; add a capability for anything new.
APP_PROTOCOL = 1
CAPABILITIES = (
    "list", "setup-jsonl", "setup-back", "rename", "workspace-lifecycle",
    "autostart", "autostart-keep", "logs-json", "upgrade-json", "install-progress",
    "ai-connections", "shared-ai-connections", "thinking-level", "logs-activity", "activity-details", "setup-v2", "abandon-setup", "remove-tag",
    "describe", "telemetry-events", "start-progress",
)
UPDATE_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
COMMANDS = tuple(sorted(tag_instances.RESERVED_NAMES))
STARTUP_ATTEMPT_ENV_KEYS = (
    "OPENTAG_MFS_STARTUP_ATTEMPTS", "OPENTAG_STARTUP_ATTEMPTS"
)
TOKEN_PATTERN = re.compile(r"\b(?:xox[a-z]-|xapp-)[A-Za-z0-9-]+")
MFS_HISTORY_CREDENTIAL_MESSAGE = (
    "MFS is already running without Tag's Slack-history credential. "
    "Stop that MFS server, then run tag start so Tag can start its managed MFS with the approved credential."
)
MFS_SLACK_CONNECTOR_MESSAGE = (
    "MFS Slack connector support is not installed. Run ./install.sh --dependencies-only, "
    "then retry tag start."
)


class MfsHistoryCredentialUnavailable(RuntimeError):
    """MFS cannot resolve Tag's private history credential in its process."""


class MfsSlackConnectorUnavailable(RuntimeError):
    """The MFS server was installed without its optional Slack connector."""


def missing_runtime_dependencies() -> tuple[str, ...]:
    return tuple(
        dependency
        for dependency in RUNTIME_DEPENDENCIES
        if importlib.util.find_spec(dependency) is None
    )


def runtime_dependency_message(missing: tuple[str, ...]) -> str:
    names = ", ".join(missing)
    return (
        f"Tag runtime is incomplete (missing: {names}). Re-run the Tag installer. "
        "For a source checkout, run ./install.sh --dependencies-only."
    )


def legacy_process_running(home: Path) -> bool | None:
    """Return whether the recorded legacy checkout has a live Tag process."""
    import psutil

    try:
        record = json.loads(
            (home / "state/legacy-command.json").read_text(encoding="utf-8")
        )
        raw_command = record["command"]
        if not isinstance(raw_command, str) or not raw_command:
            return None
        command = Path(raw_command).expanduser().resolve()
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None

    expected = {
        command,
        command.parent / "scripts/tag_cli.py",
        command.parent / "scripts/slack_socket_agent.py",
    }
    try:
        for process in psutil.process_iter(attrs=["cmdline"]):
            command_line = process.info.get("cmdline") or ()
            for argument in command_line:
                if not isinstance(argument, str) or not argument:
                    continue
                try:
                    candidate = Path(argument).expanduser().resolve()
                except (OSError, RuntimeError):
                    continue
                if candidate in expected:
                    return True
    except psutil.Error:
        return None
    return False


def legacy_slack_ready(home: Path, maximum_age: float = 15.0) -> bool:
    """Detect a live pre-supervisor Tag release with a current heartbeat."""
    try:
        record = json.loads((home / "runtime/slack-connected.json").read_text(encoding="utf-8"))
        heartbeat = float(
            record.get(
                "time",
                record.get("timestamp", record.get("updated_at", record.get("heartbeat"))),
            )
        )
        fresh = bool(record.get("connected")) and 0 <= time.time() - heartbeat <= maximum_age
        if not fresh:
            return False
        return legacy_process_running(home) is not False
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def legacy_stop_command(home: Path) -> str:
    try:
        record = json.loads(
            (home / "state/legacy-command.json").read_text(encoding="utf-8")
        )
        command = record["command"]
        if isinstance(command, str) and Path(command).is_file():
            return f'"{command}" stop'
    except (OSError, KeyError, json.JSONDecodeError):
        pass
    return "the original legacy Tag launcher's stop command"


def runtime_identity(home: Path) -> dict[str, object]:
    identity: dict[str, object] = {
        "mode": "source",
        "version": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        "root": str(ROOT),
        "python": sys.executable,
        "active_release": False,
    }
    try:
        record = json.loads((home / "current.json").read_text(encoding="utf-8"))
        selected = (home / "releases" / record["release"]).resolve()
        active = ROOT.resolve() == selected
        identity.update(
            mode="managed" if active else "source",
            active_release=active,
            selected_release=str(selected),
            selected_python=record["python"],
        )
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        pass
    return identity


def redact_log_text(content: str) -> str:
    redacted = TOKEN_PATTERN.sub("<redacted>", content)
    for key, value in os.environ.items():
        if (
            len(value) >= 8
            and any(marker in key.upper() for marker in ("TOKEN", "SECRET", "PASSWORD", "KEY"))
        ):
            redacted = redacted.replace(value, "<redacted>")
    return redacted


def process_for(path: Path):
    import psutil
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        process = psutil.Process(record["pid"])
        marker = record.get("command_marker")
        command_matches = True
        if isinstance(marker, str) and marker:
            command_matches = any(marker in part for part in process.cmdline())
        if (process.create_time() == record["created"]
                and process.status() != psutil.STATUS_ZOMBIE and command_matches):
            return process
    except (OSError, ValueError, KeyError, psutil.Error):
        pass
    return None


def start_process(
    home: Path,
    name: str,
    command: list[str],
    *,
    environment: dict[str, str] | None = None,
    metadata: dict[str, object] | None = None,
    state_dir: Path | None = None,
    cwd: Path | None = None,
) -> bool:
    import psutil
    try:
        from opentag_process_env import without_telemetry_environment
    except ImportError:
        from scripts.opentag_process_env import without_telemetry_environment
    state = state_dir or home / "state"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = state / f"{name}.json"
    if process_for(record):
        return False
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS} if os.name == "nt" else {"start_new_session": True}
    with (state / f"{name}.log").open("ab") as log:
        process_cwd = cwd or Path((environment or os.environ).get("OPENTAG_WORKDIR", str(home / "workspace")))
        if cwd is None:
            process_cwd.mkdir(parents=True, exist_ok=True, mode=0o700)
        child_environment = without_telemetry_environment(environment or os.environ)
        child = subprocess.Popen(command, cwd=process_cwd, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT,
                                 env=child_environment, **options)
    process = psutil.Process(child.pid)
    marker_source = command[1] if len(command) > 1 and command[1].endswith(".py") else command[0]
    identity = {"pid": child.pid, "created": process.create_time(),
                "command_marker": Path(marker_source).name, **(metadata or {})}
    record.write_text(json.dumps(identity), encoding="utf-8")
    time.sleep(0.3)
    if child.poll() is not None:
        record.unlink(missing_ok=True)
        detail = log_tail(home, name, 20, state_dir=state)
        raise RuntimeError(
            f"{name} exited during startup"
            + (f":\n{detail}" if detail else "; run tag logs")
        )
    # This command intentionally leaves a detached service alive on return.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        del child
    return True


def stop_process(home: Path, name: str, *, state_dir: Path | None = None) -> None:
    import psutil
    state = state_dir or home / "state"
    record = state / f"{name}.json"
    process = process_for(record)
    if process:
        children = process.children(recursive=True)
        for item in [*reversed(children), process]:
            try:
                item.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs([process, *children], timeout=5)
        for item in alive:
            try:
                item.kill()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(alive, timeout=5)
        if alive:
            raise RuntimeError(f"Could not stop {name}; retaining its process record")
    record.unlink(missing_ok=True)
    if name == "slack":
        (home / "state/slack.ready").unlink(missing_ok=True)


def local_mfs_endpoint(url: str) -> bool:
    """Return whether Tag may manage the process behind this loopback endpoint."""
    try:
        parsed = urllib.parse.urlsplit(url)
        return (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost"}
            and parsed.port == 13619
            and parsed.path in {"", "/"}
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def local_mfs_listener(url: str):
    """Find an identifiable MFS server listening at a configured local endpoint."""
    import psutil

    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port or 80
        addresses = {
            address[4][0]
            for address in socket.getaddrinfo(
                parsed.hostname, port, type=socket.SOCK_STREAM
            )
        }
    except (OSError, TypeError, ValueError):
        return None
    candidate_pids: set[int] = set()
    if os.name != "nt" and shutil.which("lsof"):
        for address in addresses:
            endpoint = f"[{address}]" if ":" in address else address
            result = subprocess.run(
                [
                    "lsof",
                    "-nP",
                    f"-iTCP@{endpoint}:{port}",
                    "-sTCP:LISTEN",
                    "-t",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            candidate_pids.update(
                int(value) for value in result.stdout.split() if value.isdigit()
            )
    else:
        try:
            connections = psutil.net_connections(kind="tcp")
        except psutil.Error:
            connections = []
        candidate_pids.update(
            connection.pid
            for connection in connections
            if connection.status == psutil.CONN_LISTEN
            and connection.pid is not None
            and connection.laddr
            and connection.laddr.port == port
            and connection.laddr.ip in addresses
        )
    candidates = []
    for pid in sorted(candidate_pids):
        try:
            process = psutil.Process(pid)
            command = " ".join(process.cmdline()).casefold()
        except psutil.Error:
            continue
        if "mfs-server" in command or "mfs_server" in command:
            candidates.append(process)
    return candidates[0] if len(candidates) == 1 else None


def replace_unmanaged_local_mfs(
    home: Path, url: str, *, state_dir: Path | None = None
) -> bool:
    """Replace an untracked loopback MFS so Tag owns runtime and cleanup."""
    state = state_dir or home / "state"
    record = state / "mfs.json"
    if not local_mfs_endpoint(url) or process_for(record) is not None or not healthy(url):
        return False
    process = local_mfs_listener(url)
    if process is None:
        raise RuntimeError(
            "The local MFS endpoint is healthy but its process is not an identifiable "
            "mfs-server. Stop that service or configure a separate MFS_URL before starting Tag."
        )
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    record.write_text(
        json.dumps({"pid": process.pid, "created": process.create_time(), "adopted": True}),
        encoding="utf-8",
    )
    if state_dir is None:
        stop_process(home, "mfs")
    else:
        stop_process(home, "mfs", state_dir=state)
    return True


def slack_ready(home: Path, maximum_age: float = 5.0) -> bool:
    """Confirm the managed Slack process is publishing a current heartbeat."""
    record_path = home / "state/slack.json"
    process = process_for(record_path)
    if process is None:
        return False
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        instance_id, raw_pid, raw_heartbeat = (home / "state/slack.ready").read_text(
            encoding="utf-8"
        ).split()
        ready_pid = int(raw_pid)
        heartbeat = float(raw_heartbeat)
        return (
            instance_id == record["instance_id"]
            and ready_pid == process.pid
            and 0 <= time.time() - heartbeat <= maximum_age
        )
    except (OSError, ValueError, KeyError):
        return False


def log_tail(home: Path, name: str, lines: int = 50, *, state_dir: Path | None = None) -> str:
    try:
        content = ((state_dir or home / "state") / f"{name}.log").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return ""
    return redact_log_text("\n".join(content.splitlines()[-lines:]))


def follow_logs(home: Path, existing: list[Path]) -> None:
    """Stream appended log bytes, tolerating newly created and rotated files."""
    positions = {}
    for log in existing:
        try:
            positions[log] = log.stat().st_size
        except OSError:
            pass
    display.section("Following")
    display.info_row("Mode", "Waiting for new entries · Ctrl-C to stop")
    while True:
        time.sleep(0.5)
        for log in sorted((home / "state").glob("*.log")):
            try:
                size = log.stat().st_size
                previous = positions.get(log, 0)
                if size < previous:
                    previous = 0
                if size == previous:
                    continue
                with log.open("rb") as handle:
                    handle.seek(previous)
                    chunk = handle.read()
                positions[log] = size
            except OSError:
                continue
            if log not in existing:
                display.section(log.stem)
                existing.append(log)
            content = redact_log_text(chunk.decode("utf-8", errors="replace"))
            for line in content.splitlines():
                print("    " + line, flush=True)


def source_snapshot(root: Path = ROOT) -> dict[Path, tuple[int, int]]:
    """Capture the source files that can change the running Slack bridge."""
    snapshot = {}
    for path in (root / "scripts").rglob("*.py"):
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[path] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def changed_sources(
    previous: dict[Path, tuple[int, int]],
    current: dict[Path, tuple[int, int]],
) -> list[Path]:
    """Return added, removed, and modified source paths in stable order."""
    return sorted(
        path
        for path in previous.keys() | current.keys()
        if previous.get(path) != current.get(path)
    )


def start_development_slack(home: Path) -> None:
    """Start a managed bridge from this checkout and wait for Socket Mode."""
    instance_id = uuid.uuid4().hex
    ready_file = home / "state/slack.ready"
    environment = os.environ.copy()
    environment["OPENTAG_PROCESS_ID"] = instance_id
    command = [
        sys.executable,
        str(ROOT / "scripts/slack_socket_agent.py"),
        "--backend",
        os.environ["OPENTAG_BACKEND"],
        "--ready-file",
        str(ready_file),
        "--process-id",
        instance_id,
    ]
    with LifecycleLock(tag_home() / "state/ai-connection.lock", shared=True), \
            LifecycleLock(tag_home() / "state/ai-start.lock", shared=True), LifecycleLock(home / "state/start.lock"):
        start_process(
            home,
            "slack",
            command,
            environment=environment,
            metadata={"instance_id": instance_id},
        )
    attempts = int(os.getenv("OPENTAG_STARTUP_ATTEMPTS", "30"))
    for _ in range(attempts):
        if slack_ready(home):
            return
        if process_for(home / "state/slack.json") is None:
            detail = log_tail(home, "slack")
            raise RuntimeError(
                "Slack bridge exited before becoming ready"
                + (f":\n{detail}" if detail else "; fix the source and save again")
            )
        time.sleep(1)
    detail = log_tail(home, "slack")
    raise RuntimeError(
        "Slack bridge did not become ready"
        + (f":\n{detail}" if detail else "; fix the source and save again")
    )


def stream_new_log_bytes(path: Path, position: int) -> int:
    """Print newly appended bridge output and return the next byte position."""
    try:
        size = path.stat().st_size
        if size < position:
            position = 0
        if size == position:
            return position
        with path.open("rb") as handle:
            handle.seek(position)
            chunk = handle.read()
    except OSError:
        return position
    content = redact_log_text(chunk.decode("utf-8", errors="replace"))
    for line in content.splitlines():
        print("    " + line, flush=True)
    return size


def development_loop(home: Path) -> int:
    """Run the Slack bridge with source watching and foreground log output."""
    if runtime_identity(home)["active_release"]:
        raise RuntimeError(
            "tag dev is only available from a source checkout. "
            "Run ./install.sh --dependencies-only, then ./tag dev in the repository."
        )

    display.header(
        "Dev",
        selected_target(home, suffix="Watching Python source and reloading the Slack bridge"),
    )
    display.section("Bootstrap")
    tag_id = os.getenv("TAG_ID", "default")
    target = [] if tag_id == "default" else [tag_id]
    command = [sys.executable, str(ROOT / "scripts/tag_cli.py"), *target, "start"]
    result = subprocess.call(command, env=os.environ.copy())
    if result:
        return result

    # Take ownership of the bridge even when `tag start` found one already
    # running, so leaving this foreground command has predictable cleanup.
    stop_process(home, "slack")
    start_development_slack(home)
    display.info_row("Slack", "Connected from this checkout", good=True)
    display.info_row("Watching", "scripts/**/*.py")
    display.info_row("Exit", "Ctrl-C stops the development bridge")

    snapshot = source_snapshot()
    log = home / "state/slack.log"
    try:
        log_position = log.stat().st_size
    except OSError:
        log_position = 0
    try:
        while True:
            time.sleep(0.35)
            log_position = stream_new_log_bytes(log, log_position)
            current = source_snapshot()
            changes = changed_sources(snapshot, current)
            if not changes:
                continue
            # Editors commonly replace a file atomically. A short debounce folds
            # that remove/add pair and a multi-file save into one bridge reload.
            time.sleep(0.2)
            settled = source_snapshot()
            changes = changed_sources(snapshot, settled)
            snapshot = settled
            display.section("Reloading")
            names = ", ".join(path.name for path in changes[:3])
            if len(changes) > 3:
                names += f" +{len(changes) - 3} more"
            display.info_row("Changed", names)
            stop_process(home, "slack")
            try:
                start_development_slack(home)
            except (OSError, ValueError, RuntimeError, ImportError) as exc:
                display.failure("Reload failed", str(exc))
                continue
            display.info_row("Slack", "Reloaded and connected", good=True)
            try:
                log_position = log.stat().st_size
            except OSError:
                log_position = 0
    finally:
        stop_process(home, "slack")
        stop_process(home, "mfs")


def healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def mfs_server_executable() -> str | None:
    """Prefer a bundled runtime, but support an independently installed server."""
    name = "mfs-server.exe" if os.name == "nt" else "mfs-server"
    bundled = Path(sys.executable).parent / name
    return str(bundled) if bundled.is_file() else shutil.which(name)


def instance_environment(
    context: tag_instances.InstanceContext,
    values: dict[str, str] | None = None,
    *,
    source: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build a fresh child environment without cross-instance Tag settings."""
    inherited = os.environ if source is None else source
    blocked = ("SLACK_", "MFS_", "OPENTAG_")
    cli_overrides = {
        key: inherited[key]
        for key in STARTUP_ATTEMPT_ENV_KEYS
        if key in inherited
    }
    environment = {
        key: value for key, value in inherited.items()
        if not key.startswith(blocked) and key not in {"TAG_INSTANCE_HOME", "TAG_ID"}
    }
    environment.update(runtime_environment(
        context.home,
        installation_root=context.installation_root,
        tag_id=context.tag_id,
        workspace=context.workspace,
    ))
    if values:
        environment.update(values)
    environment.update(cli_overrides)
    environment["OPENTAG_ENV_FILE"] = str(context.home / "config/settings.json")
    return environment


def migrate_legacy_mfs_record(context: tag_instances.InstanceContext) -> None:
    """Recoverably transfer the default home’s managed MFS identity."""
    if not context.is_default:
        return
    legacy = context.installation_root / "state/mfs.json"
    shared = context.shared_mfs_home
    destination = shared / "mfs.json"
    pending = shared / "mfs.migrating"
    if destination.exists():
        return
    source = pending if pending.exists() else legacy
    if not source.exists():
        return
    process = process_for(source)
    if process is None:
        source.unlink(missing_ok=True)
        return
    try:
        command = " ".join(process.cmdline()).lower()
    except Exception:
        raise RuntimeError("Could not verify the legacy MFS process; stop it with the older Tag CLI before upgrading") from None
    if "mfs-server" not in command and "mfs_server" not in command:
        raise RuntimeError("Legacy MFS process identity does not match mfs-server; refusing ownership migration")
    shared.mkdir(parents=True, exist_ok=True, mode=0o700)
    if source == legacy:
        # Moving first makes the old record unavailable to an older CLI before
        # the shared record becomes authoritative. A crash leaves a recoverable
        # `.migrating` record rather than two shutdown authorities.
        os.replace(legacy, pending)
    os.replace(pending, destination)
    old_log = context.installation_root / "state/mfs.log"
    if old_log.exists() and not (shared / "mfs.log").exists():
        os.replace(old_log, shared / "mfs.log")


def ensure_shared_memory(
    context: tag_instances.InstanceContext, environment: dict[str, str]
) -> None:
    """Start the configured shared MFS if needed, wait until it is healthy, then finish queued removals."""
    _ensure_shared_memory_running(context, environment)
    finish_connector_removals(context.shared_mfs_home, environment)


def _ensure_shared_memory_running(
    context: tag_instances.InstanceContext, environment: dict[str, str]
) -> None:
    url = environment.get("MFS_URL", "http://127.0.0.1:13619")
    local_mfs = local_mfs_endpoint(url)
    if local_mfs:
        migrate_legacy_mfs_record(context)
        replace_unmanaged_local_mfs(
            context.home, url, state_dir=context.shared_mfs_home
        )
    shared = context.shared_mfs_home
    process = process_for(shared / "mfs.json") if local_mfs else None
    if healthy(url) and (not local_mfs or process is None or tag_mfs_runtime.active(shared, process)):
        return
    if not local_mfs:
        raise RuntimeError(
            "Configured external MFS endpoint is unavailable; start that server first"
        )
    if not mfs_server_executable():
        raise RuntimeError("MFS server is unavailable; run ./install.sh --dependencies-only")
    shared.mkdir(parents=True, exist_ok=True, mode=0o700)
    mfs_lock = LifecycleLock(shared / "start.lock").acquire()
    try:
        process = process_for(shared / "mfs.json")
        if process is not None and not tag_mfs_runtime.active(shared, process):
            # Runtime migrations only restart an identity-verified Tag-owned process.
            # Stored indexes, connector settings, and credentials stay in place.
            stop_process(context.home, "mfs", state_dir=shared)
        if not healthy(url):
            instance_id = uuid.uuid4().hex
            child_environment = dict(environment, TAG_MFS_STATE_DIR=str(shared), TAG_MFS_PROCESS_ID=instance_id)
            start_process(
                context.home, "mfs",
                [sys.executable, str(ROOT / "scripts/tag_mfs_server.py"), "run"],
                environment=child_environment, state_dir=shared, cwd=context.workspace,
                metadata={"slack_runtime_version": tag_mfs_runtime.VERSION, "instance_id": instance_id},
            )
        attempts = int(environment.get("OPENTAG_MFS_STARTUP_ATTEMPTS", "90"))
        for _ in range(attempts):
            process = process_for(shared / "mfs.json")
            if healthy(url) and tag_mfs_runtime.active(shared, process):
                break
            if process is None:
                detail = log_tail(context.home, "mfs", state_dir=shared)
                raise RuntimeError(
                    "Shared MFS exited before becoming healthy"
                    + (f":\n{detail}" if detail else "; run tag memory status")
                )
            time.sleep(1)
        else:
            raise RuntimeError(
                "Shared MFS did not verify its Slack rate-limit runtime; run tag memory status, then retry tag start"
            )
        # Commit only after the running adapter and HTTP health are verified.
        try:
            from tag_config import save_config
        except ImportError:
            from scripts.tag_config import save_config
        save_config(shared / "slack-runtime-migration-v1.json", {"version": tag_mfs_runtime.VERSION})
    finally:
        mfs_lock.release()


def bridge_processes(installation_root: Path) -> list[str]:
    running = []
    for item in tag_instances.discover(installation_root):
        if not item.get("valid"):
            continue
        home = Path(str(item["home"]))
        if process_for(home / "state/slack.json"):
            running.append(str(item["id"]))
    return running


def assert_unique_slack_app(context: tag_instances.InstanceContext, values: dict[str, str]) -> None:
    """Reject one Slack App ID being activated by two local Tag instances."""
    app_id = values.get("SLACK_APP_ID", "")
    if not app_id:
        return
    shared = context.installation_root / "shared"
    shared.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = shared / "app-identity.lock"
    # The check is brief: let another Tag starting now finish it first.
    deadline = time.monotonic() + APP_IDENTITY_WAIT_SECONDS
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Another Tag is activating a Slack app. If interrupted, remove {lock} and retry."
                ) from None
            time.sleep(0.2)
    try:
        for item in tag_instances.discover(context.installation_root):
            if not item.get("valid") or item["id"] == context.tag_id:
                continue
            try:
                other = read_config(Path(str(item["home"])) / "config/settings.json")
            except (OSError, ValueError):
                continue
            if other.get("SLACK_APP_ID") == app_id:
                raise RuntimeError(
                    f"Slack app {app_id} is already configured for Tag '{item['id']}'. "
                    "Use a separate Slack app for each Tag."
                )
            uri = values.get("MFS_SLACK_CONNECTOR_URI", "")
            if uri and other.get("MFS_SLACK_CONNECTOR_URI") == uri:
                raise RuntimeError(
                    f"Slack connector {uri} is already owned by Tag '{item['id']}'. "
                    "Refusing to update a colliding connector root."
                )
    finally:
        lock.rmdir()


def ensure_connector_credential(home: Path, values: dict[str, str]) -> None:
    """Migrate an owned local connector from process-env to a private file."""
    raw = values.get("MFS_SLACK_CONNECTOR_CONFIG", "")
    if not raw:
        return
    connector = Path(raw).expanduser()
    url = values.get("MFS_URL", "http://127.0.0.1:13619")
    local = local_mfs_endpoint(url)
    try:
        owned = connector.resolve().is_relative_to((home / "integrations").resolve())
    except OSError:
        owned = False
    if not connector.is_file() or connector.is_symlink() or not owned:
        raise RuntimeError("Slack connector configuration is missing or is not owned by the selected Tag")
    content = connector.read_text(encoding="utf-8")
    if not local:
        if 'token = "file:' in content:
            raise RuntimeError(
                "Remote MFS cannot resolve this Tag's local Slack credential file; "
                "configure a server-resolvable credential reference."
            )
        return
    token = values.get("MFS_SLACK_TOKEN", "")
    if not token:
        raise RuntimeError("Slack history credential is missing")
    credential = tag_credentials.write_slack_history(home, token)
    replacement = "token = " + json.dumps("file:" + str(credential))
    migrated, count = re.subn(r'(?m)^token = "(?:env:MFS_SLACK_TOKEN|file:[^"]+)"$', replacement, content)
    if count != 1:
        raise RuntimeError("Slack connector credential reference is malformed")
    if migrated != content:
        temporary = connector.with_suffix(connector.suffix + ".credential.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(migrated)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, connector)
        finally:
            temporary.unlink(missing_ok=True)


def selected_target(home: Path, tag_id: str | None = None, *, suffix: str = "") -> str:
    """Return a consistent visible identity for the selected Tag and Slack app."""
    try:
        values = read_config(home / "config/settings.json")
    except FileNotFoundError:
        values = {}
    except (OSError, ValueError):
        return display.target_detail(
            tag_id or os.getenv("TAG_ID", "default"), suffix="Configuration unreadable"
        )
    return display.target_detail(
        tag_id or os.getenv("TAG_ID", "default"),
        values.get("SLACK_TEAM_ID", ""),
        values.get("SLACK_APP_ID", ""),
        values.get("OPENTAG_BOT_NAME", ""),
        suffix=suffix,
    )


def load_connector_config(path: Path) -> dict[str, object]:
    """A connector's TOML configuration, as the JSON object MFS expects."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    with path.open("rb") as handle:
        return tomllib.load(handle)


def sync_configured_slack_memory(environment: dict[str, str] | None = None) -> None:
    """Register and incrementally sync the connector approved during setup.

    Talks to the MFS server's HTTP API directly (POST /v1/add), as the `mfs`
    client did, so no separate client program is needed on any platform.
    """
    source = environment if environment is not None else os.environ
    uri = source.get("MFS_SLACK_CONNECTOR_URI", "").strip()
    config = Path(source.get("MFS_SLACK_CONNECTOR_CONFIG", "")).expanduser()
    if not uri or not config.is_file():
        return
    settings = load_connector_config(config)
    try:
        mfs_post_json("/v1/add", {"target": uri, "config": settings, "full": False, "process": False}, source)
        return
    except MfsRequestError as error:
        failure = error
    if failure.code == "connector_already_registered":
        # The managed connector survives MFS restarts. Re-adding it reports a
        # conflict, so update that same registered connector instead of
        # pretending that its old configuration describes new channel consent.
        try:
            mfs_post_json("/v1/add", {"target": uri, "update": True, "config": settings}, source)
            return
        except MfsRequestError as error:
            failure = error
    if failure.code == "sync_already_running":
        return
    if "environment variable MFS_SLACK_TOKEN is not set" in failure.detail:
        raise MfsHistoryCredentialUnavailable(MFS_HISTORY_CREDENTIAL_MESSAGE)
    if "no plugin for slack" in failure.detail.lower():
        raise MfsSlackConnectorUnavailable(MFS_SLACK_CONNECTOR_MESSAGE)
    raise RuntimeError(f"Slack history indexing could not start ({failure.detail}); run tag logs and tag memory")


def reconcile_invitation_memory(home: Path) -> None:
    """Safely register current invitation-following memory before preflight.

    The bridge normally owns this reconciliation. Startup needs one synchronous
    pass, though: preflight verifies the registered scopes before the bridge can
    be launched. Running it here avoids treating the old saved connector as
    current membership.
    """
    try:
        from slack_invitation_memory import InvitationMemory
    except ImportError:
        from scripts.slack_invitation_memory import InvitationMemory

    InvitationMemory(home).tick()
    try:
        status = json.loads((home / "state/slack-memory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        status = {}
    if status.get("check") == "mfs_history_credential":
        raise MfsHistoryCredentialUnavailable(MFS_HISTORY_CREDENTIAL_MESSAGE)
    if status.get("check") == "mfs_slack_connector":
        raise MfsSlackConnectorUnavailable(MFS_SLACK_CONNECTOR_MESSAGE)
    if (
        status.get("state") not in {"sync_requested", "no_joined_channels"}
        and status.get("check") != "index_submission"
    ):
        raise RuntimeError(
            "Invitation memory could not be prepared. Check Slack membership and history access, then retry tag start."
        )


def doctor_command(offline: bool, *, json_output: bool) -> list[str]:
    cmd = [sys.executable, str(ROOT / "scripts/opentag_doctor.py")]
    if json_output:
        cmd.append("--json")
    if offline:
        cmd.append("--offline")
    else:
        configured_channels = os.getenv("SLACK_CHANNEL_IDS", "") or os.getenv("SLACK_CHANNEL_ID", "")
        for channel_id in (item.strip() for item in configured_channels.split(",")):
            if channel_id:
                cmd.extend(["--channel-id", channel_id])
    return cmd


def doctor_report(offline: bool) -> tuple[int, dict[str, object]]:
    completed = subprocess.run(
        doctor_command(offline, json_output=True),
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError):
        detail = completed.stderr.strip() or "Doctor did not return a valid report"
        raise RuntimeError(detail)
    if not isinstance(report, dict):
        raise RuntimeError("Doctor did not return a valid report")
    return completed.returncode, report


def authenticated_mfs_url(raw: str | None = None) -> str:
    """Return an MFS base URL that is safe to receive a bearer token."""
    raw = (raw or os.getenv("MFS_URL") or "http://127.0.0.1:13619").rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(raw)
        host = parsed.hostname
        parsed.port  # Validate malformed port syntax before request construction.
    except ValueError as error:
        raise RuntimeError("MFS_URL must be a valid HTTP(S) URL") from error
    if (
        not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            "MFS_URL must be a valid HTTP(S) URL without credentials, query, or fragment"
        )
    if parsed.scheme.casefold() == "https":
        return raw
    loopback = host.casefold() == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
    if parsed.scheme.casefold() != "http" or not loopback:
        raise RuntimeError(
            "MFS_URL must use HTTPS unless it points to localhost or a loopback IP; "
            "refusing to send the MFS bearer token"
        )
    return raw


class RejectMfsRedirects(urllib.request.HTTPRedirectHandler):
    """Prevent bearer-authenticated MFS requests from following redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            "MFS bearer-authenticated requests do not follow redirects",
            headers,
            fp,
        )


def mfs_token(source: dict[str, str] | os._Environ | None = None) -> str | None:
    """MFS_TOKEN, else the token a local mfs-server wrote under MFS_HOME (default ~/.mfs)."""
    source = os.environ if source is None else source
    token = source.get("MFS_TOKEN", "").strip()
    if token:
        return token
    home = Path(source.get("MFS_HOME") or Path.home() / ".mfs").expanduser()
    try:
        return (home / "server.token").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


class MfsRequestError(RuntimeError):
    """MFS answered with its {code, detail} error envelope."""

    def __init__(self, status: int, code: str, detail: str):
        super().__init__(f"MFS error {status} {code}: {detail}")
        self.status, self.code, self.detail = status, code, detail


class MfsUnreachable(RuntimeError):
    """Nothing answered at MFS_URL, for example because memory isn't running."""


def _mfs_send(method: str, path: str, *, body: dict[str, object] | None = None,
              query: dict[str, str] | None = None, environment: dict[str, str] | None = None,
              timeout: float = 120) -> dict[str, object]:
    """Call an authenticated MFS endpoint, enforcing its transport boundary and error envelope."""
    source = os.environ if environment is None else environment
    base = authenticated_mfs_url(source.get("MFS_URL"))
    headers = {"Content-Type": "application/json"} if body is not None else {}
    token = mfs_token(source)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body, default=str).encode("utf-8") if body is not None else None
    url = f"{base}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(RejectMfsRedirects())
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        try:
            envelope = json.loads(error.read().decode("utf-8"))
        except (OSError, ValueError):
            envelope = {}
        envelope = envelope if isinstance(envelope, dict) else {}
        raise MfsRequestError(error.code, str(envelope.get("code") or "error"),
                              str(envelope.get("detail") or error.reason)) from None
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise MfsUnreachable(f"Memory isn't reachable at {base}; run tag memory status") from error
    return payload if isinstance(payload, dict) else {}


def mfs_post_json(path: str, body: dict[str, object], environment: dict[str, str] | None = None,
                  *, timeout: float = 120) -> dict[str, object]:
    """POST to an authenticated MFS endpoint, under the same rules as mfs_request_json."""
    return _mfs_send("POST", path, body=body, environment=environment, timeout=timeout)


def mfs_remove_connector(uri: str, environment: dict[str, str] | None = None) -> None:
    """Remove a registered connector and everything it indexed. Raises MfsUnreachable when memory is down."""
    try:
        _mfs_send("DELETE", "/v1/connectors", query={"target": uri}, environment=environment)
    except MfsRequestError as error:
        # Never registered, or already removed: nothing is left to delete.
        if error.status == 404 or "remove_requires_connector_root" in (error.code, error.detail) \
                or "not found" in error.detail.lower():
            return
        raise RuntimeError(f"Memory couldn't remove {uri} ({error.detail}); run tag memory status and retry") from None


PENDING_REMOVALS = "pending-connector-removals.json"


def queue_connector_removal(shared: Path, uri: str, url: str) -> None:
    """Remember a connector to remove the next time memory at `url` is running."""
    try:
        from tag_config import save_config
    except ImportError:
        from scripts.tag_config import save_config
    shared.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LifecycleLock(shared / "removals.lock"):
        pending = _pending_removals(shared)
        entry = {"uri": uri, "url": url.rstrip("/")}
        if entry not in pending:
            save_config(shared / PENDING_REMOVALS, {"version": 1, "removals": [*pending, entry]})


def _pending_removals(shared: Path) -> list[dict[str, str]]:
    try:
        record = json.loads((shared / PENDING_REMOVALS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = record.get("removals") if isinstance(record, dict) else None
    return [row for row in rows or [] if isinstance(row, dict) and isinstance(row.get("uri"), str)
            and isinstance(row.get("url"), str)]


def finish_connector_removals(shared: Path, environment: dict[str, str]) -> None:
    """Remove connectors of Tags removed while memory was down. Failures stay queued for the next start."""
    if not (shared / PENDING_REMOVALS).is_file():
        return
    try:
        from tag_config import save_config
    except ImportError:
        from scripts.tag_config import save_config
    url = environment.get("MFS_URL", "http://127.0.0.1:13619").rstrip("/")
    with LifecycleLock(shared / "removals.lock"):
        remaining = []
        for entry in _pending_removals(shared):
            if entry["url"] != url:
                remaining.append(entry)
                continue
            try:
                mfs_remove_connector(entry["uri"], environment)
            except RuntimeError:
                remaining.append(entry)
        if remaining:
            save_config(shared / PENDING_REMOVALS, {"version": 1, "removals": remaining})
        else:
            (shared / PENDING_REMOVALS).unlink(missing_ok=True)


def mfs_request_json(path: str, parameters: dict[str, str]) -> dict[str, object] | None:
    """Call an authenticated MFS endpoint after enforcing its transport boundary."""
    base = authenticated_mfs_url()
    token = mfs_token()
    if not token:
        return None
    query = urllib.parse.urlencode(parameters)
    request = urllib.request.Request(
        f"{base}{path}?{query}", headers={"Authorization": f"Bearer {token}"}
    )
    opener = urllib.request.build_opener(RejectMfsRedirects())
    try:
        with opener.open(request, timeout=2) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return payload if isinstance(payload, dict) else None


def resolve_indexed_mfs_scope(scope: str) -> str | None:
    """Resolve a Slack scope by stable channel ID and require indexed messages."""
    parsed = urllib.parse.urlsplit(scope)
    final_segment = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    _, marker, channel_id = final_segment.rpartition("__")
    current_scope = scope.rstrip("/")
    if marker and re.fullmatch(r"[CG][A-Z0-9]+", channel_id):
        parent_path = parsed.path.rstrip("/").rsplit("/", 1)[0] or "/"
        parent_scope = parsed._replace(path=parent_path, query="", fragment="").geturl()
        parent = mfs_request_json("/v1/ls", {"path": parent_scope})
        entries = parent.get("entries") if isinstance(parent, dict) else None
        if not isinstance(entries, list):
            return None
        stable_suffix = f"__{channel_id}"
        current_scope = ""
        for entry in entries:
            candidate = entry.get("path") if isinstance(entry, dict) else None
            if not isinstance(candidate, str):
                continue
            candidate_parsed = urllib.parse.urlsplit(candidate)
            if (
                candidate_parsed.scheme.casefold() == parsed.scheme.casefold()
                and candidate_parsed.netloc.casefold() == parsed.netloc.casefold()
                and candidate_parsed.path.rstrip("/")
                .rsplit("/", 1)[-1]
                .endswith(stable_suffix)
            ):
                current_scope = candidate.rstrip("/")
                break
        if not current_scope:
            return None
    listing = mfs_request_json("/v1/ls", {"path": current_scope})
    children = listing.get("entries") if isinstance(listing, dict) else None
    if not isinstance(children, list):
        return None
    if any(
        isinstance(child, dict)
        and child.get("name") == "messages.jsonl"
        and child.get("search_status") == "indexed"
        for child in children
    ):
        return current_scope
    return None


def mfs_scope_indexed(scope: str) -> bool:
    """Return whether MFS exposes indexed Slack messages below a scope."""
    return resolve_indexed_mfs_scope(scope) is not None


def wait_for_configured_mfs_scopes(*, attempts: int | None = None) -> list[str]:
    """Wait for an asynchronous connector sync to make every scope readable."""
    configured = [
        scope.strip()
        for scope in os.getenv("MFS_ALLOWED_SCOPES", "").split(",")
        if scope.strip()
    ]
    scopes = list(
        dict.fromkeys(
            scope
            for scope in configured
            if urllib.parse.urlsplit(scope).scheme == "slack"
        )
    )
    remaining = scopes
    resolved: dict[str, str] = {}
    limit = attempts if attempts is not None else int(
        os.getenv("OPENTAG_MFS_STARTUP_ATTEMPTS", "90")
    )
    last_cooldown = 0.0
    cooldown_state = tag_home() / "shared/mfs/slack-cooldowns-v1"
    deadline = time.monotonic() + max(1, limit)
    for attempt in range(max(1, limit)):
        if attempt and time.monotonic() >= deadline:
            break
        retry_at = tag_slack_backoff.cooldown(cooldown_state, os.getenv("SLACK_TEAM_ID", ""))
        if retry_at > time.time():
            if retry_at != last_cooldown:
                display.pending_row("Channel memory", f"Indexing paused by Slack; retrying in {max(1, int(retry_at - time.time()))} seconds…")
                sys.stdout.flush()
                last_cooldown = retry_at
            if attempt + 1 < max(1, limit):
                time.sleep(1)
            continue
        resolved = {
            scope: current
            for scope in scopes
            if (current := resolve_indexed_mfs_scope(scope)) is not None
        }
        remaining = [scope for scope in scopes if scope not in resolved]
        if not remaining:
            os.environ["MFS_ALLOWED_SCOPES"] = ",".join(
                resolved.get(scope, scope) for scope in configured
            )
            return []
        if attempt + 1 < max(1, limit):
            time.sleep(1)
    if last_cooldown and tag_slack_backoff.cooldown(
        cooldown_state, os.getenv("SLACK_TEAM_ID", "")
    ) > time.time():
        raise RuntimeError(
            "Slack history indexing is waiting on a rate limit. Memory will retry automatically in the background; "
            "run tag start again after indexing finishes. Do not repeat setup."
        )
    return remaining


def doctor(home: Path, offline: bool, json_output: bool = False, *, tag_id: str = "default") -> int:
    if json_output:
        result, report = doctor_report(offline)
        report.setdefault("schema_version", 1)
        report["tag"] = tag_id
        print(json.dumps(report, indent=2))
        return result
    result, report = doctor_report(offline)
    display.doctor_summary(report)
    display.info_row("Target", selected_target(home, tag_id))
    return result


def upgrade_reminder(
    installation_root: Path,
    *,
    now: float | None = None,
    max_age: float = UPDATE_CHECK_INTERVAL_SECONDS,
) -> dict[str, str] | None:
    """Return a best-effort cached reminder without authorizing an upgrade."""
    try:
        from tag_install import atomic_text, release_version_key, resolve_channel
    except ImportError:
        from scripts.tag_install import atomic_text, release_version_key, resolve_channel

    current_path = installation_root / "current.json"
    try:
        current = json.loads(current_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        try:
            current = {
                "installed_version": (ROOT / "VERSION").read_text(encoding="utf-8").strip()
            }
        except (OSError, UnicodeError):
            return None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(current, dict) or current.get("selection") == "version":
        return None
    saved_channel = current.get("channel")
    channel = saved_channel if saved_channel in UPGRADE_CHANNELS else "alpha"
    current_version = current.get("installed_version")
    current_commit = current.get("installed_commit")

    checked_at = time.time() if now is None else now
    cache_path = installation_root / "state/update-check.json"
    cached: dict[str, object] = {}
    try:
        candidate = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(candidate, dict):
            cached = candidate
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    fresh = (
        cached.get("channel") == channel
        and isinstance(cached.get("checked_at"), (int, float))
        and 0 <= checked_at - float(cached["checked_at"]) < max_age
    )
    if not fresh:
        try:
            release = resolve_channel(channel, timeout=2, page_limit=1)
            target_version = (
                "edge" if channel == "edge"
                else str(release["tag_name"]).removeprefix("v")
            )
            cached = {
                "schema_version": 1,
                "checked_at": checked_at,
                "channel": channel,
                "target_version": target_version,
                "target_commit": release.get("target_commitish"),
            }
            cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_text(cache_path, json.dumps(cached, indent=2, sort_keys=True) + "\n")
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            previous = cached if cached.get("channel") == channel else {}
            cached = {
                "schema_version": 1,
                "checked_at": checked_at,
                "channel": channel,
            }
            for key in ("target_version", "target_commit"):
                if key in previous:
                    cached[key] = previous[key]
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                atomic_text(cache_path, json.dumps(cached, indent=2, sort_keys=True) + "\n")
            except OSError:
                pass

    target_version = cached.get("target_version")
    target_commit = cached.get("target_commit")
    if channel == "edge":
        available = bool(target_commit and target_commit != current_commit)
    else:
        try:
            available = release_version_key(str(target_version)) > release_version_key(
                str(current_version)
            )
        except ValueError:
            return None
    if not available:
        return None
    return {
        "status": "available",
        "version": str(target_version),
        "command": (
            "tag upgrade"
            if saved_channel in UPGRADE_CHANNELS
            else "tag upgrade --channel alpha"
        ),
    }


def show_upgrade_reminder(installation_root: Path) -> None:
    reminder = upgrade_reminder(installation_root)
    if not reminder:
        return
    display.section("Updates")
    label = "New edge build" if reminder["version"] == "edge" else f"Tag v{reminder['version']}"
    display.info_row("Available", label, good=False)
    display.next_action("Upgrade when ready", reminder["command"])


def upgrade_command(
    home: Path,
    *,
    channel: str | None = None,
    version: str | None = None,
    dry_run: bool = False,
    no_restart: bool = False,
    allow_downgrade: bool = False,
    json_output: bool = False,
    dependencies: bool = True,
) -> int:
    """Check or install a verified release using the saved update policy."""
    try:
        from tag_install import atomic_text, fetch_release, install, release_version_key
    except ImportError:
        from scripts.tag_install import atomic_text, fetch_release, install, release_version_key

    current_path = home / "current.json"
    try:
        current = json.loads(current_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(
            "Tag is not managed by the installer. Install it once before using tag upgrade."
        ) from error
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Invalid managed release record: {current_path}") from error
    if not isinstance(current, dict):
        raise RuntimeError(f"Invalid managed release record: {current_path}")

    current_version = current.get("installed_version")
    if current_version is None:
        release_name = current.get("release")
        if not isinstance(release_name, str) or not release_name:
            raise RuntimeError("The managed release record has no active release")
        version_path = home / "releases" / release_name / "VERSION"
        try:
            current_version = version_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as error:
            raise RuntimeError(
                f"Cannot read the installed release version: {version_path}"
            ) from error
    current_commit = current.get("installed_commit")
    current_channel = current.get("channel")
    selection = current.get("selection", "channel")
    if channel is None and version is None and selection == "version":
        result = {
            "schema_version": 1,
            "ok": True,
            "status": "pinned",
            "current": {
                "version": current_version,
                "commit": current_commit,
                "channel": current_channel,
                "selection": "version",
            },
            "next_command": f"tag upgrade --channel {current_channel or 'alpha'}",
        }
        if json_output:
            print(json.dumps(result, indent=2))
        else:
            display.header("Upgrade", "Checking the managed Tag release.")
            display.section("Release")
            display.info_row("Current", f"Tag v{current_version or 'unknown'}")
            display.info_row("Updates", "Pinned to an exact version")
            display.next_action("Follow an update channel", result["next_command"])
        return 0

    selected_channel = channel or (None if version is not None else current_channel)
    if version is None and selected_channel not in UPGRADE_CHANNELS:
        raise RuntimeError(
            "The installed release has no saved update channel. "
            "Run tag upgrade --channel alpha (or stable, beta, edge)."
        )
    if not json_output:
        display.header("Upgrade", "Checking for a verified Tag release.")
        display.section("Selection")
        display.info_row("Current", f"Tag v{current_version or 'unknown'}")
        display.info_row(
            "Requested",
            f"v{version}" if version is not None else f"{selected_channel} channel",
        )

    with tempfile.TemporaryDirectory(prefix="tag-upgrade-") as temporary:
        fetched = fetch_release(version, Path(temporary), selected_channel)
        target = fetched.selection
        policy_matches = (
            current_channel == target.channel
            and selection == target.selector
        )
        artifact_matches = (
            current_commit == target.commit_sha
            and current_version == target.version
        )
        try:
            downgrade = release_version_key(target.version) < release_version_key(
                str(current_version)
            )
        except ValueError as error:
            raise RuntimeError(
                f"Cannot compare installed release version: {current_version or 'missing'}"
            ) from error
        running_tags = bridge_processes(home)
        legacy_running = process_for(home / "state/slack.json") is not None
        if legacy_running and "default" not in running_tags:
            running_tags.insert(0, "default")
        running = bool(running_tags)
        result = {
            "schema_version": 1,
            "ok": True,
            "status": "current" if artifact_matches and policy_matches else "available",
            "dry_run": dry_run,
            "current": {
                "version": current_version,
                "commit": current_commit,
                "channel": current_channel,
                "selection": selection,
            },
            "target": {
                "version": target.version,
                "commit": target.commit_sha,
                "channel": target.channel,
                "selection": target.selector,
            },
            "services_running": running,
            "running_tags": running_tags,
            "restart_commands": [
                "tag restart" if tag_id == "default" else f"tag {tag_id} restart"
                for tag_id in running_tags
            ],
            "restart_required": False,
            "downgrade": downgrade,
        }
        if downgrade and not allow_downgrade:
            policy_updated = bool(
                channel is not None and not dry_run and not policy_matches
            )
            if policy_updated:
                current.update({
                    "channel": target.channel,
                    "selection": target.selector,
                    "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                })
                atomic_text(
                    current_path,
                    json.dumps(current, indent=2, sort_keys=True) + "\n",
                )
            exact_version_blocked = version is not None
            result.update({
                "ok": not exact_version_blocked,
                "status": "downgrade-blocked" if exact_version_blocked else (
                    "channel-updated" if policy_updated else "ahead"
                ),
                "policy_updated": policy_updated,
                "next_command": (
                    f"tag upgrade --version {target.version} --allow-downgrade"
                    if exact_version_blocked
                    else f"tag upgrade --channel {target.channel} --allow-downgrade"
                ),
            })
            if json_output:
                print(json.dumps(result, indent=2))
            else:
                display.section("Decision")
                display.info_row("Installed", f"Tag v{current_version}", good=True)
                display.info_row("Candidate", f"Tag v{target.version}", good=False)
                display.info_row("Action", "Kept the newer installed release", good=True)
                if policy_updated:
                    display.info_row(
                        "Channel", f"Now following {target.channel}; waiting for it to catch up"
                    )
                display.next_action(
                    "Install the older release anyway",
                    result["next_command"],
                    detail="Older code may not understand data written by a newer Tag release.",
                )
            return 2 if exact_version_blocked else 0
        if dry_run:
            if json_output:
                print(json.dumps(result, indent=2))
            elif result["status"] == "current":
                display.completion("Tag is up to date", "No installation changes were made.")
            else:
                display.completion(
                    "Upgrade available",
                    f"Tag v{target.version} passed checksum and provenance verification.",
                    next_label="Install it",
                    next_command="tag upgrade",
                )
            return 0

        if artifact_matches:
            if not policy_matches:
                current.update({
                    "channel": target.channel,
                    "selection": target.selector,
                    "installed_version": target.version,
                    "installed_commit": target.commit_sha,
                    "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                })
                atomic_text(
                    current_path,
                    json.dumps(current, indent=2, sort_keys=True) + "\n",
                )
                result["status"] = "policy-updated"
            if json_output:
                print(json.dumps(result, indent=2))
            else:
                detail = (
                    f"Future upgrades will follow the {target.channel} channel."
                    if result["status"] == "policy-updated"
                    else "The installed commit already matches the selected release."
                )
                display.completion("Tag is up to date", detail)
            return 0

        raw_bin_dir = current.get("bin_dir")
        if raw_bin_dir is not None and not isinstance(raw_bin_dir, str):
            raise RuntimeError("The managed release record contains an invalid bin_dir")
        bin_dir = (
            Path(raw_bin_dir).expanduser()
            if raw_bin_dir
            else (home / "bin" if os.name == "nt" else Path.home() / ".local/bin")
        )
        # Upgrade owns the summary; suppress the installer's first-install screen.
        with contextlib.redirect_stdout(io.StringIO()):
            install(
                fetched.source,
                home,
                bin_dir.resolve(),
                dependencies=dependencies,
                selection=target,
            )
        result["status"] = "upgraded"
        result["restart_required"] = running and no_restart
        result["restarted"] = False
        if running and not no_restart:
            failures = []
            # Captured restarts report only their error, not the whole progress screen.
            environment = {**os.environ, PLAIN_ERRORS_ENV: "1"} if json_output else None
            for tag_id in running_tags:
                arguments = [] if tag_id == "default" else [tag_id]
                command = [sys.executable, str(home / "bin/tag-launch.py"), *arguments, "restart"]
                completed = subprocess.run(command, capture_output=json_output, text=True, check=False, env=environment)
                if completed.returncode:
                    lines = (completed.stdout or "").strip().splitlines()
                    detail = (completed.stderr or "").strip() or (lines[-1].strip() if lines else "")
                    detail = redact_log_text(detail.removeprefix("Error: "))
                    failures.append(tag_id + (f": {detail}" if detail else ""))
            if failures:
                raise RuntimeError(
                    "Tag was upgraded, but these Tags need attention:\n" + "\n".join(failures)
                    + "\nRun tag NAME doctor, resolve the reported requirement, then retry tag NAME start."
                )
            result["restarted"] = True

    if json_output:
        print(json.dumps(result, indent=2))
    else:
        display.completion(
            "Tag upgraded",
            f"Now using Tag v{result['target']['version']}. Configuration and workspace data were preserved.",
            next_label="Activate the new release" if result["restart_required"] else "",
            next_command=" && ".join(result["restart_commands"]) if result["restart_required"] else "",
        )
    return 0


SETUP_PROTOCOL_ENV = "TAG_SETUP_PROTOCOL"


# Set by a parent command that captures output: report failures as one Error line.
PLAIN_ERRORS_ENV = "TAG_PLAIN_ERRORS"


DEFER_RENAME_ENV = "TAG_DEFER_RENAME"


def _workspace_tags(installation_root: Path, workspace: str) -> list[str]:
    """Tags whose Slack workspace matches a team ID or (case-insensitively) its name."""
    wanted = workspace.strip().casefold()
    matches = []
    for item in tag_instances.discover(installation_root):
        if not item["valid"] or not Path(str(item["home"])).exists():
            continue
        home = Path(str(item["home"]))
        path = home / "config/settings.json"
        team = read_config(path).get("SLACK_TEAM_ID", "") if path.is_file() else ""
        name = tag_instances.workspace_name(home) or ""
        if wanted and wanted in {team.casefold(), name.casefold()}:
            matches.append(str(item["id"]))
    return matches


def _workspace_lifecycle(installation_root: Path, workspace: str, action: str, json_output: bool) -> int:
    """Start, stop, or restart every Tag in one Slack workspace, one at a time."""
    tags = _workspace_tags(installation_root, workspace)
    if not tags:
        raise RuntimeError(f"No Tags are connected to the Slack workspace '{workspace}'. See tag list.")
    results = []
    for tag_id in tags:
        path = tag_instances.resolve(installation_root, tag_id).home / "config/settings.json"
        if action != "stop" and not (path.is_file() and read_config(path).get("SLACK_APP_ID")):
            # Unfinished setup isn't a failure; it just can't start yet.
            results.append({"tag": tag_id, "exit_code": None, "skipped": "setup_incomplete"})
            continue
        command = [sys.executable, str(ROOT / "scripts/tag_cli.py"), tag_id, action]
        # Each Tag runs its own lifecycle; one failure doesn't stop the others.
        code = subprocess.call(command, stdout=subprocess.DEVNULL if json_output else None)
        results.append({"tag": tag_id, "exit_code": code})
    failed = [item["tag"] for item in results if item["exit_code"]]
    skipped = [item["tag"] for item in results if item.get("skipped")]
    if json_output:
        print(json.dumps({"schema_version": 1, "workspace": workspace, "action": action,
                          "ok": not failed, "tags": results}, indent=2))
    else:
        attempted = len(tags) - len(skipped)
        display.info_row(action.title(), f"{attempted - len(failed)} of {attempted} Tags in {workspace}",
                         good=not failed)
        for tag_id in failed:
            display.info_row(tag_id, f"needs attention · tag {tag_id} status", good=False)
        for tag_id in skipped:
            display.info_row(tag_id, f"setup not finished · tag {tag_id} setup", good=False)
    return 1 if failed else 0


def _autostart_command(installation_root: Path, args, parser) -> int:
    """Keep chosen Tags running after login, and restart them if they stop."""
    action = args.arguments[0] if args.arguments else "status"
    if action not in {"status", "on", "off", "run", "keep"} or (len(args.arguments) > 1 and action != "keep"):
        parser.error("autostart accepts status, on, off, run, or keep TAG...")
    if action == "run":
        return autostart.run(installation_root, sys.modules[__name__], ROOT)
    seeded: list[str] = []
    if action == "keep":
        # Record that these Tags should keep running, without starting them now.
        if len(args.arguments) < 2:
            parser.error("autostart keep needs one or more Tags")
        for reference in args.arguments[1:]:
            tag = tag_instances.resolve_reference(installation_root, reference)
            autostart.set_wanted(tag_instances.resolve(installation_root, tag).home, True)
        result = autostart.status(installation_root)
    elif action == "on":
        seeded = autostart.seed_from_running(installation_root, sys.modules[__name__])
        result = autostart.enable(installation_root, ROOT)
    elif action == "off":
        result = autostart.disable(installation_root)
    else:
        result = autostart.status(installation_root)
    tags = []
    for item in tag_instances.discover(installation_root):
        if item.get("valid") and Path(str(item["home"])).exists():
            home = tag_instances.resolve(installation_root, str(item["id"])).home
            tags.append({"tag": str(item["id"]), "keep_running": autostart.wanted(home) is True})
    result = {"schema_version": 1, **result, "tags": tags}
    if args.json_output:
        print(json.dumps(result, indent=2))
        return 0
    display.header("Autostart", "Start your Tags after you log in, and restart them if they stop.")
    display.info_row("Login service", "On" if result["enabled"] else "Off", good=result["enabled"])
    display.info_row("Mechanism", result["mechanism"])
    for tag in tags:
        display.info_row(tag["tag"], "kept running" if tag["keep_running"] else "left off",
                         good=tag["keep_running"])
    if seeded:
        display.info_row("Kept running", "Tags already running: " + ", ".join(seeded), good=True)
    display.next_action("Choose which Tags run", "tag NAME start  ·  tag NAME stop",
                        detail="Starting a Tag keeps it running; stopping it leaves it off.")
    if not result["enabled"]:
        display.next_action("Turn on", "tag autostart on")
    return 0


def _rename_command(context: tag_instances.InstanceContext, args) -> int:
    """Rename a Tag in Slack and give it a nickname for commands."""
    try:
        import slack_manifest_migrations
    except ImportError:
        from scripts import slack_manifest_migrations
    try:
        import tag_config as settings
    except ImportError:
        from scripts import tag_config as settings
    if len(args.arguments) != 1 or not args.arguments[0].strip():
        raise ValueError('rename needs the new Slack name, for example: tag rename "Research Tag"')
    name = args.arguments[0].strip()
    if error := settings.validation_error("OPENTAG_BOT_NAME", name):
        raise ValueError(error)
    alias = args.nickname or tag_instances.slugify(name)
    root = context.installation_root
    # Check the nickname before changing anything in Slack.
    tag_instances.validate_name(alias, allow_default=False)
    for other in tag_instances.discover(root):
        if other["valid"] and other["id"] != context.tag_id and Path(str(other["home"])).exists():
            if alias in {other["id"], tag_instances.nickname(Path(str(other["home"])))}:
                raise ValueError(f"Another Tag already uses '{alias}'; choose one with --nickname")
    config_path = context.home / "config/settings.json"
    values = read_config(config_path) if config_path.is_file() else {}
    retry = f'tag {context.tag_id} rename "{name}"'
    changed = slack_manifest_migrations.set_display_name(context.home, values, name, retry=retry)
    settings.update_config(config_path, {"OPENTAG_BOT_NAME": name})
    tag_instances.set_nickname(root, context.tag_id, alias)
    if args.json_output:
        print(json.dumps({"schema_version": 1, "tag": context.tag_id, "slack_name": name,
                          "nickname": alias, "slack_changed": changed}, indent=2))
    else:
        display.header("Rename", f"Tag '{context.tag_id}'")
        display.info_row("Slack", f"Now called {name}" if changed else f"Already called {name}", good=True)
        display.info_row("Command", f"tag {alias} start", good=True)
        if process_for(context.home / "state/slack.json") is not None:
            display.next_action("Use the new name in Tag's own messages", f"tag {alias} restart")
    return 0


def _describe_command(context: tag_instances.InstanceContext, args) -> int:
    """Change a Tag's one-line description in Slack; an empty one clears it."""
    try:
        import slack_manifest_migrations
    except ImportError:
        from scripts import slack_manifest_migrations
    try:
        import tag_config as settings
    except ImportError:
        from scripts import tag_config as settings
    if len(args.arguments) != 1:
        raise ValueError('describe needs the new description, for example: tag describe "Answers launch questions"')
    description = args.arguments[0].strip()
    if error := settings.validation_error("OPENTAG_BOT_DESCRIPTION", description):
        raise ValueError(error)
    config_path = context.home / "config/settings.json"
    values = read_config(config_path) if config_path.is_file() else {}
    retry = f"tag {context.tag_id} describe {json.dumps(description, ensure_ascii=False)}"
    changed = slack_manifest_migrations.set_description(context.home, values, description, retry=retry)
    settings.update_config(config_path, {"OPENTAG_BOT_DESCRIPTION": description})
    if args.json_output:
        print(json.dumps({"schema_version": 1, "tag": context.tag_id, "description": description or None,
                          "slack_changed": changed}, indent=2))
    else:
        display.header("Describe", f"Tag '{context.tag_id}'")
        display.info_row("Slack", ("Description saved" if description else "Description cleared") if changed
                         else "Already up to date", good=True)
    return 0


def _abandon_command(context: tag_instances.InstanceContext, args) -> int:
    """Set aside a Tag whose setup never reached Slack. It is moved, never deleted."""
    import shutil
    from datetime import datetime
    values = read_config(context.home / "config/settings.json") if (context.home / "config/settings.json").is_file() else {}
    # Setup records the workspace (SLACK_TEAM_ID) before it creates the app, so
    # only an installed bot token means the Tag reached Slack. A running Tag is
    # never touched.
    if (values.get("SLACK_BOT_TOKEN") or values.get("SLACK_APP_ID")
            or (context.home / "integrations/slack-cli/tag-create.json").exists()
            or process_for(context.home / "state/slack.json") is not None):
        raise ValueError(f"Tag '{context.tag_id}' has a Slack app, so it can't be abandoned. Use remove.")
    backup = context.installation_root / "abandoned" / f"{context.tag_id}-{datetime.now():%Y%m%d-%H%M%S}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(context.home), str(backup))
    if args.json_output:
        print(json.dumps({"schema_version": 1, "tag": context.tag_id, "backup": str(backup)}, indent=2))
    else:
        display.header("Abandon", f"Tag '{context.tag_id}'")
        display.info_row("Backup", display.short_path(backup), good=True)
    return 0


def _remove_command(context: tag_instances.InstanceContext, args) -> int:
    """Stop a Tag and set its local files aside; optionally delete its Slack app.

    The Slack app is deleted only with --delete-app and --confirm-app naming the
    saved App ID. The local files are moved, never deleted.
    """
    import shutil
    from datetime import datetime
    try:
        import tag_reset
    except ImportError:
        from scripts import tag_reset
    home = context.home
    app = tag_reset.selected_app(home)
    executable = None
    if args.delete_app:
        if not app:
            raise ValueError("No reliable saved App ID and Team ID were found. Nothing was removed or deleted.")
        if args.confirm_app != app["app_id"]:
            raise ValueError(f"--confirm-app must be this Tag's App ID ({app['app_id']}). Nothing was removed or deleted.")
        tag_reset.check_app_link(home / "integrations/slack-cli", app)
        search_path = str(home / "integrations/bin") + os.pathsep + os.environ.get("PATH", "")
        executable = shutil.which("slack", path=search_path)
        if not executable:
            raise ValueError("Slack CLI is unavailable. Nothing was removed or deleted. Remove without deleting the app, or install Slack CLI.")
    config_path = home / "config/settings.json"
    values = read_config(config_path) if config_path.is_file() else {}
    stop_process(home, "slack")
    memory = tag_reset.unregister_connector(home, values)
    backup = context.installation_root / "abandoned" / f"{context.tag_id}-{datetime.now():%Y%m%d-%H%M%S}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(home), str(backup))
    deleted = False
    if args.delete_app:
        (backup / "tmp").mkdir(exist_ok=True)
        deleted = tag_reset.delete_slack_app(backup, backup, app, executable, source=backup / "integrations/slack-cli")
        if not deleted:
            raise RuntimeError(f"The Tag was removed but Slack didn't confirm deleting app {app['app_id']}. "
                               f"Check it in Slack. Local files: {backup}")
    if args.json_output:
        print(json.dumps({"schema_version": 1, "ok": True, "tag": context.tag_id, "backup": str(backup),
                          "app_deleted": deleted, "app_id": app["app_id"] if app else None}, indent=2))
    else:
        display.header("Remove", f"Tag '{context.tag_id}'")
        display.info_row("Backup", display.short_path(backup), good=True)
        display.info_row("Slack app", f"{app['app_id']} deleted" if deleted else "Kept", good=True)
        display.info_row("Memory", "Slack history removed" if memory == "removed"
                         else "Slack history is removed the next time memory starts", good=True)
    return 0


def _avatar(home: Path, values: dict[str, str] | None = None) -> str | None:
    """Slack's cached profile first, with the setup upload as an offline fallback."""
    try:
        from . import slack_profile_icon
    except ImportError:
        import slack_profile_icon
    values = values or {}
    icon = slack_profile_icon.path(home, team_id=values.get("SLACK_TEAM_ID", ""),
                                   app_id=values.get("SLACK_APP_ID", ""))
    if icon:
        return str(icon)
    icons = sorted((home / "integrations/slack-cli/assets").glob("tag-profile.*"))
    return str(icons[0]) if icons else None


def _refresh_avatar(home: Path, values: dict[str, str]) -> str:
    try:
        from . import slack_profile_icon
    except ImportError:
        import slack_profile_icon
    return slack_profile_icon.refresh_safely(home, values)


def _workspace_icon(home: Path) -> str | None:
    """The saved Slack workspace icon, for apps that show it beside the workspace name."""
    try:
        import slack_workspace_icon
    except ImportError:
        from scripts import slack_workspace_icon
    icon = slack_workspace_icon.path(home)
    return str(icon) if icon else None


def _refresh_workspace_icon(home: Path, values: dict[str, str]) -> str:
    """Best effort: a missing icon must never stop setup or a start."""
    try:
        import slack_workspace_icon
    except ImportError:
        from scripts import slack_workspace_icon
    token, team = values.get("SLACK_BOT_TOKEN", ""), values.get("SLACK_TEAM_ID", "")
    if not token:
        return "skipped"
    try:
        return slack_workspace_icon.refresh(home, token, team)
    except (OSError, RuntimeError) as exc:
        return f"unavailable: {exc}"


def _refresh_workspace_name(home: Path, values: dict[str, str], *, api=None) -> str | None:
    """Record the Slack workspace's name for Tags set up before Tag saved it.

    Without it, Tag.app and tag list show the bare Team ID. Runs on each start
    until a name is saved; auth.test needs no extra permission, and nothing
    here may stop a start.
    """
    if tag_instances.workspace_name(home) or not values.get("SLACK_BOT_TOKEN"):
        return None
    try:
        import slack_channels
    except ImportError:
        from scripts import slack_channels
    try:
        payload = (api or slack_channels.slack_api)(values["SLACK_BOT_TOKEN"], "auth.test", {})
        name = payload.get("team") if isinstance(payload, dict) else None
        if not isinstance(name, str) or not name.strip():
            return None
        tag_instances.record_workspace_name(home, name.strip())
        return name.strip()
    except (OSError, ValueError, RuntimeError):
        return None


def _channels(values: dict[str, str], home: Path | None = None) -> list[dict[str, str | None]]:
    """The channels a Tag answers in, named from local metadata without network IO."""
    try:
        import tag_activity, slack_channel_names
    except ImportError:
        from scripts import tag_activity, slack_channel_names
    names = tag_activity.channel_names(values.get("MFS_ALLOWED_SCOPES", ""))
    if home is not None:
        names.update(slack_channel_names.read(home, values.get("SLACK_TEAM_ID", "")))
    ids = [part.strip() for part in values.get("SLACK_CHANNEL_IDS", "").split(",") if part.strip()]
    rows = [{"id": channel, "name": names.get(channel)} for channel in dict.fromkeys(ids)]
    return sorted(rows, key=lambda row: (row["name"] is None, (row["name"] or row["id"]).casefold()))


def _slack_name(home: Path) -> str | None:
    """The Tag's display name in Slack, for lists that show people names, not IDs."""
    path = home / "config/settings.json"
    name = read_config(path).get("OPENTAG_BOT_NAME", "") if path.is_file() else ""
    return name or None


def _rename(installation_root: Path, tag_id: str) -> str | None:
    """Name a provisionally named Tag after its Slack IDs once its app exists."""
    try:
        import tag_rename
    except ImportError:
        from scripts import tag_rename
    return tag_rename.migrate(installation_root, sys.modules[__name__], tag_id)


def _setup_ui():
    try:
        import setup_ui as ui
    except ImportError:
        from scripts import setup_ui as ui
    return ui


def _setup_ready(home: Path, values: dict[str, str]) -> dict:
    """What a finished setup made, for the client's Ready screen and its Slack links."""
    try:
        import tag_ai
    except ImportError:
        from scripts import tag_ai
    try:
        import slack_setup_icons
    except ImportError:
        from scripts import slack_setup_icons
    choice = tag_ai.default_choice(home, values)
    try:
        progress = json.loads((home / "config/setup-progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        progress = {}
    owner = values.get("SLACK_ALLOWED_USER_IDS", "").split(",")[0] or None
    return {"name": values.get("OPENTAG_BOT_NAME") or None,
            "owner": {"id": owner, "name": (progress.get("owner_name") if isinstance(progress, dict) else None) or None,
                      "icon": slack_setup_icons.pictures(tag_home(), home, values)["owner"]},
            "team": values.get("SLACK_TEAM_ID") or None, "app_id": values.get("SLACK_APP_ID") or None,
            "channels": _channels(values, home),
            "ai": {key: choice[key] for key in ("backend", "backend_name", "label")}}


def _setup_result(code: int, tag_id: str, protocol: bool) -> int:
    """Close a JSON-lines setup session with the outcome and selected Tag."""
    if code == 0:
        try:
            context = tag_instances.resolve(tag_home(), tag_id)
            values = read_config(context.home / "config/settings.json")
            _refresh_avatar(context.home, values)
        except (OSError, ValueError, RuntimeError):
            pass
    if protocol:
        configured = False
        try:
            try:
                import tag_config as settings
            except ImportError:
                from scripts import tag_config as settings
            context = tag_instances.resolve(tag_home(), tag_id)
            configured = not settings.config_errors(read_config(context.home / "config/settings.json"))
        except (OSError, ValueError, RuntimeError):
            pass
        status = "complete" if code == 0 and configured else "paused" if code == 0 else "failed"
        ready = None
        if status == "complete":
            try:
                context = tag_instances.resolve(tag_home(), tag_id)
                values = read_config(context.home / "config/settings.json")
                _refresh_workspace_name(context.home, values)
                _refresh_workspace_icon(context.home, values)
                ready = _setup_ready(context.home, values)
            except (OSError, ValueError, RuntimeError):
                pass
        _setup_ui().emit({"type": "result", "status": status, "tag": tag_id, "exit_code": code,
                          **({"ready": ready} if ready else {})})
    return code


def _setup_step(args: argparse.Namespace, installation_root: Path, *, raw_tag: str | None) -> int:
    """Drive JSON-lines setup one question per command, for agents and scripts."""
    try:
        import setup_session
    except ImportError:
        from scripts import setup_session
    command = [sys.executable, str(ROOT / "scripts/tag_cli.py"), *([raw_tag] if raw_tag else []), args.command]
    command += [flag for flag, on in (("--review", args.review), ("--no-start", args.no_start)) if on]
    command.append("--json")
    try:
        if args.step:
            reply = setup_session.step(installation_root, command)
        elif args.step_stop:
            reply = setup_session.stop(installation_root)
        elif args.step_back:
            reply = setup_session.back(installation_root)
        else:
            try:
                value = json.loads(args.step_answer)
            except ValueError:
                print(json.dumps({"schema_version": 1, "error": "--answer must be a JSON value, such as 0, true, or \"text\""}))
                return 2
            reply = setup_session.answer(installation_root, value, args.step_question)
    except setup_session.SessionError as error:
        print(json.dumps({"schema_version": 1, "error": str(error)}))
        return 1
    print(json.dumps({"schema_version": 1, **reply}, ensure_ascii=False))
    result = reply.get("result") or {}
    return 1 if reply.get("state") == "ended" and result.get("status") == "failed" else 0


def _ai_target(context):
    """The Tag that AI & models acts on, with a quiet restart for JSON clients."""
    try:
        import tag_ai
    except ImportError:
        from scripts import tag_ai

    def lifecycle(action: str) -> int:
        command = [sys.executable, str(ROOT / "scripts/tag_cli.py"), *context.command_arguments(action)]
        environment = os.environ.copy()
        environment["TAG_RESTART_FLOW"] = "1" if action == "restart" else environment.get("TAG_RESTART_FLOW", "")
        # Keep this command's own output, such as JSON lines, clean.
        return subprocess.call(command, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return tag_ai.Target(
        home=context.home, tag_id=context.tag_id,
        running=lambda: process_for(context.home / "state/slack.json") is not None,
        restart=lifecycle, name=_slack_name(context.home) or "Tag",
    )


def _global_ai_target(installation_root):
    """Connection changes restart exactly the Tags that were running beforehand."""
    try:
        import tag_ai
    except ImportError:
        from scripts import tag_ai
    def targets():
        # Discover after the connection guard is acquired, so newly started Tags count.
        return [_ai_target(tag_instances.resolve(installation_root, str(row["id"])))
                for row in tag_instances.discover(installation_root)
                if row["valid"] and Path(str(row["home"])).exists()]
    resume = []

    def lifecycle(action):
        if action == "stop":
            for target in targets():
                if target.running():
                    resume.append(target)
                    if target.restart("stop"):
                        return 1
            return 0
        failed = False
        for target in resume:
            if target.restart("start"):
                failed = True
        return int(failed)

    return tag_ai.Target(installation_root, "", lambda: any(t.running() for t in targets()),
                         lifecycle, "Your Tags")


@contextlib.contextmanager
def _waiting_lock(path):
    """Hold one exclusive lifecycle lock, waiting while another start, stop or setup holds it."""
    (lock,), _ = tag_locks.acquire_all([path], wait=START_LOCK_WAIT_SECONDS)
    try:
        yield lock
    finally:
        lock.release()


def _migrate_shared_ai(installation_root, current_home, *, defer_current=True):
    """Pause existing bridges around v1 credential migration and recover interrupted restarts."""
    try:
        import tag_chatgpt
    except ImportError:
        from scripts import tag_chatgpt
    store = tag_chatgpt.Store(current_home)
    checkpoint = installation_root / "shared/ai/migration-v1.json"
    if os.getenv("TAG_AI_MIGRATION_RESTART") == "1":
        return
    pending = []
    # Other Tags may be starting: wait for their migration check and shared AI locks.
    with _waiting_lock(installation_root / "state/ai-migration.lock"):
        record = tag_chatgpt.read_object(checkpoint)
        pending = record.get("restart", [])
        with _waiting_lock(installation_root / "state/ai-connection.lock"):
            if not store.path.exists():
                # Validate before interrupting any service.
                legacy = tag_chatgpt.legacy_accounts(current_home)
                running = [str(row["id"]) for row in tag_instances.discover(installation_root)
                           if legacy["mode"] == "chatgpt" and row["valid"]
                           and process_for(Path(str(row["home"])) / "state/slack.json")]
                pending = sorted(set(pending + running))
                tag_chatgpt.atomic_write(checkpoint, {"version": 1, "restart": pending})
                for tag_id in pending:
                    target = _ai_target(tag_instances.resolve(installation_root, tag_id))
                    if target.running() and target.restart("stop"):
                        raise RuntimeError("Couldn't stop all Tags for the shared AI migration. Retry tag start.")
            tag_chatgpt.migrate_shared_accounts(current_home)
            if store.enabled() and not store.read().get("active"):
                raise RuntimeError("Choose one shared ChatGPT account in Settings → AI connections, "
                                   "or run tag chatgpt status then tag chatgpt use ACCOUNT. "
                                   "Saved accounts were preserved; retry tag start afterwards.")
        for tag_id in list(pending):
            target = _ai_target(tag_instances.resolve(installation_root, tag_id))
            # The current start will finish below; other Tags resume now.
            if defer_current and target.home == current_home:
                continue
            if not target.running():
                command = [sys.executable, str(ROOT / "scripts/tag_cli.py"), tag_id, "start"]
                env = {**os.environ, "TAG_AI_MIGRATION_RESTART": "1"}
                if subprocess.call(command, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL):
                    raise RuntimeError(f"Shared AI accounts migrated, but {tag_id} could not restart. Retry tag start.")
            pending.remove(tag_id)
            tag_chatgpt.atomic_write(checkpoint, {"version": 1, "restart": pending})


def _settings_ai(context, args) -> int:
    try:
        import tag_ai
    except ImportError:
        from scripts import tag_ai
    target = _ai_target(context)
    return tag_ai.cli(args.arguments[1:], target, json_output=args.json_output, restart=args.restart,
                      method=args.method, account=args.account, effort=args.effort)


def _run_cli() -> int:
    parser = argparse.ArgumentParser(description="Tag: set up, inspect, and manage your Slack teammate.",
                                     usage="tag [TAG] [COMMAND] [OPTIONS]",
                                     epilog=(
                                         "Use tag status for your main Tag, or tag NAME status for another Tag. "
                                         "Start with tag setup; change configuration with tag settings.\n\n"
                                         "Telemetry: Tag can collect minimal anonymous CLI usage without prompts, "
                                         "Slack messages, agent output, paths, logs, credentials, or configuration "
                                         "values. Use 'tag telemetry status|on|off', or set TAG_TELEMETRY=off. "
                                         "Privacy notice: "
                                         f"{tag_telemetry.build_config.PRIVACY_NOTICE_URL or 'not configured in this build'}"
                                     ),
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", choices=COMMANDS)
    parser.add_argument("arguments", nargs="*", help="memory: start | status | stop; config: init | show | keys | set KEY VALUE; chatgpt: status | login [ACCOUNT] | use ACCOUNT | logout [ACCOUNT] | use-codex; settings: ai [status | models | sign-in codex|claude | resume | model VALUE | effort LEVEL|default]")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output", help="structured output for inspect, status, doctor, config, paths, upgrade, usage, and chatgpt")
    parser.add_argument("--consent", action="store_true", help="chatgpt login: request plan permission again")
    parser.add_argument("--method", choices=("chatgpt", "codex"), help="settings ai sign-in codex: a shared ChatGPT account, or the Codex sign-in on this computer")
    parser.add_argument("--account", help="settings ai sign-in codex --method chatgpt: renew this saved account")
    parser.add_argument("--effort", metavar="LEVEL", help="settings ai model VALUE: also save this thinking level, or default for the model's own")
    parser.add_argument("--restart", action="store_true", help="settings ai: restart a running Tag to apply the change")
    parser.add_argument("--stdin", action="store_true", help="read a config value from stdin")
    parser.add_argument("--from", dest="source", type=Path)
    parser.add_argument("--no-start", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--test", action="store_true", help="setup: use a separate test home; implies --no-start")
    parser.add_argument("--review", action="store_true", help="setup: review choices even when already configured")
    parser.add_argument("--follow", action="store_true", help="logs: continue streaming new service output")
    parser.add_argument("--limit", type=int, help="logs: recent lines (default: 50); chatgpt status: accounts (default: 10)")
    parser.add_argument("--activity", metavar="RUN_ID", help="logs: read one request's saved steps and error report (also supports --json)")
    parser.add_argument("--activity-channel", metavar="CHANNEL_ID", help="logs --json: show activity only in this Slack channel")
    parser.add_argument("--activity-limit", type=int, help="logs --json: number of recent activity records (default: 50, maximum: 10000)")
    parser.add_argument("--hide-errors", action="store_true", help="logs --json: omit failed requests from activity")
    upgrade_selector = parser.add_mutually_exclusive_group()
    upgrade_selector.add_argument("--channel", choices=UPGRADE_CHANNELS, help="upgrade: switch to this release channel")
    upgrade_selector.add_argument("--version", dest="target_version", help="upgrade: install and pin this exact version")
    parser.add_argument("--dry-run", action="store_true", help="upgrade or chatgpt: report the action without changing state")
    parser.add_argument("--no-restart", action="store_true", help="upgrade: leave running services on the previous code")
    parser.add_argument("--allow-downgrade", action="store_true", help="upgrade: explicitly permit installing an older release")
    parser.add_argument("--step", action="store_true", help="setup, add: start or continue setup in the background and print the next question as JSON")
    parser.add_argument("--answer", dest="step_answer", metavar="JSON", help="setup, add: answer the current --step question with a JSON value")
    parser.add_argument("--question", dest="step_question", metavar="ID", help="setup, add: with --answer, only answer if this question is being asked")
    parser.add_argument("--stop", action="store_true", dest="step_stop", help="setup, add: pause the background setup, saving progress")
    parser.add_argument("--back", action="store_true", dest="step_back", help="setup, add: return to the previous --step question when it says can_go_back")
    parser.add_argument("--nickname", help="rename: short name for commands (default: derived from the new name)")
    parser.add_argument("--delete-app", action="store_true", help="remove: also permanently delete the Tag's Slack app")
    parser.add_argument("--confirm-app", help="remove --delete-app: the App ID to delete, typed to confirm")
    parser.add_argument("--workspace", help="start, stop, restart: every Tag in this Slack workspace (team ID or name)")
    raw_arguments = sys.argv[1:]
    explicit_tag = bool(
        raw_arguments
        and not raw_arguments[0].startswith("-")
        and (raw_arguments[0] not in COMMANDS or (
            raw_arguments[0] == "usage" and len(raw_arguments) > 1 and raw_arguments[1] in COMMANDS
        ))
    )
    tag_id = raw_arguments.pop(0) if explicit_tag else "default"
    args = parser.parse_args(raw_arguments)
    if args.activity and (args.command != "logs" or args.follow):
        parser.error("--activity requires logs and cannot be combined with --follow")
    if (args.activity_channel or args.hide_errors or args.activity_limit is not None) and (args.command != "logs" or not args.json_output or args.follow or args.activity):
        parser.error("Activity filters and --activity-limit require logs --json without --activity or --follow")
    if args.activity_limit is not None and not 1 <= args.activity_limit <= 10000:
        parser.error("--activity-limit must be between 1 and 10000")
    if (args.no_start or args.test or args.review) and args.command != "setup":
        parser.error("--no-start, --test and --review are only for setup")
    if args.arguments and args.command not in {"add", "memory", "config", "telemetry", "rename", "describe", "autostart", "chatgpt", "settings"}:
        parser.error("Only add, memory, config, telemetry, rename, describe, autostart, chatgpt, and settings accept additional positional arguments")
    settings_ai = args.command == "settings" and args.arguments[:1] == ["ai"]
    if args.command == "settings" and args.arguments and not settings_ai:
        parser.error("settings accepts only ai, for example tag settings ai --json")
    if (args.method or args.account or args.restart or args.effort is not None) and not settings_ai:
        parser.error("--method, --account, --effort and --restart are only for tag settings ai")
    if args.json_output and args.command not in {"list", "memory", "inspect", "status", "doctor", "config", "paths", "upgrade", "telemetry", "setup", "add", "rename", "describe", "abandon", "remove", "start", "stop", "restart", "autostart", "version", "logs", "chatgpt", "usage"} and not settings_ai:
        parser.error("--json supports list, memory, inspect, status, doctor, config, paths, upgrade, telemetry, usage, chatgpt, settings ai, autostart, version, logs, setup, add, rename, describe, start, and stop/restart with --workspace")
    if args.json_output and args.follow:
        parser.error("--json cannot be combined with --follow")
    if args.json_output and args.command in {"stop", "restart"} and not args.workspace:
        parser.error("--json for stop and restart requires --workspace")
    if (args.delete_app or args.confirm_app) and args.command != "remove":
        parser.error("--delete-app and --confirm-app are only for remove")
    if args.nickname and args.command != "rename":
        parser.error("--nickname is only for rename")
    if args.workspace and args.command not in {"start", "stop", "restart"}:
        parser.error("--workspace is only for start, stop, and restart")
    if args.workspace and explicit_tag:
        parser.error("--workspace selects Tags itself; don't also name a Tag")
    if args.json_output and args.command in {"setup", "add"}:
        if args.test:
            parser.error("--json cannot be combined with --test")
        # A graphical client drives the same setup flow over JSON lines.
        os.environ[SETUP_PROTOCOL_ENV] = "jsonl"
    if os.getenv(SETUP_PROTOCOL_ENV) == "jsonl" and args.command in {"setup", "add"}:
        _setup_ui().enter_protocol()
    if args.stdin and args.command != "config":
        parser.error("--stdin is only for config set")
    if args.offline and args.command not in {"inspect", "doctor"}:
        parser.error("--offline supports inspect and doctor")
    if args.follow and args.command != "logs":
        parser.error("--follow is only for logs")
    if (args.channel or args.target_version or args.no_restart or args.allow_downgrade) and args.command != "upgrade":
        parser.error("--channel, --version, --no-restart, and --allow-downgrade are only for upgrade")
    if args.dry_run and args.command not in {"upgrade", "chatgpt"}:
        parser.error("--dry-run supports upgrade and chatgpt")
    if args.consent and args.command != "chatgpt":
        parser.error("--consent is only for chatgpt login")
    if args.limit is not None and args.command not in {"logs", "chatgpt"}:
        parser.error("--limit supports logs and chatgpt status")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    installation_root = tag_home()
    global_ai = settings_ai and args.arguments[1:2] in (["connections"], ["sign-in"], ["resume"])
    if global_ai:
        if explicit_tag:
            parser.error("AI connections are shared by all Tags. Use tag settings ai " + " ".join(args.arguments[1:]))
        try:
            import tag_ai
        except ImportError:
            from scripts import tag_ai
        return tag_ai.cli(args.arguments[1:], _global_ai_target(installation_root),
                          json_output=args.json_output, restart=args.restart,
                          method=args.method, account=args.account, effort=args.effort)
    if args.command == "chatgpt":
        if explicit_tag:
            parser.error("ChatGPT accounts are shared by all Tags. Use tag chatgpt without a Tag name.")
        try:
            from . import tag_chatgpt
        except ImportError:
            import tag_chatgpt
        read_only = args.dry_run or not args.arguments or args.arguments[0] == "status"
        with nullcontext() if read_only else LifecycleLock(installation_root / "state/ai-connection.lock"):
            return tag_chatgpt.cli(args.arguments, json_output=args.json_output,
                                   dry_run=args.dry_run, consent=args.consent, limit=args.limit or 10)
    stepping = args.step or args.step_answer is not None or args.step_stop or args.step_back
    if stepping or args.step_question:
        if args.command not in {"setup", "add"}:
            parser.error("--step, --answer, --question, --back and --stop are only for setup and add")
        if sum(map(bool, (args.step, args.step_answer is not None, args.step_stop, args.step_back))) != 1:
            parser.error("Use exactly one of --step, --answer, --back, or --stop")
        if args.step_question and args.step_answer is None:
            parser.error("--question is only used with --answer")
        if args.test:
            parser.error("--step cannot be combined with --test")
        return _setup_step(args, installation_root, raw_tag=sys.argv[1] if explicit_tag else None)
    if explicit_tag:
        # A nickname from `tag rename` works anywhere a Tag's ID does.
        tag_id = tag_instances.resolve_reference(installation_root, tag_id)
    tag_instances.validate_name(tag_id, existing=True)
    if tag_id == tag_instances.DEFAULT_TAG:
        # Plain commands and the legacy `tag default …` both mean the main Tag.
        tag_id = tag_instances.select_unnamed(installation_root)
    if args.command in {"version", "upgrade", "rollback", "migrate", "list", "add", "memory", "telemetry", "autostart"} and explicit_tag:
        parser.error(f"Tag selection is not supported for installation-wide command '{args.command}'")
    try:
        import tag_control as control
        import tag_config as settings
    except ImportError:
        from scripts import tag_control as control, tag_config as settings
    if args.command == "telemetry":
        action = args.arguments[0] if args.arguments else ""
        if action == "record":
            # Tag.app's fixed events, recorded only when the saved preference allows it.
            fields = dict(item.partition("=")[::2] for item in args.arguments[2:])
            if len(args.arguments) < 2 or not tag_telemetry.record_app_event(
                installation_root, args.arguments[1], fields
            ):
                parser.error("telemetry record requires a known app event and its fields")
            if args.json_output:
                print(json.dumps({"schema_version": 1, "ok": True}))
            return 0
        if len(args.arguments) != 1 or action not in {"status", "on", "off"}:
            parser.error("telemetry requires status, on, or off")
        if action == "on":
            if tag_telemetry.hard_disabled():
                raise RuntimeError(
                    "TAG_TELEMETRY=off is active for this process; unset it to save telemetry on"
                )
            if not tag_telemetry.collection_available():
                raise RuntimeError(
                    "This Tag build has no approved telemetry destination; no preference was changed"
                )
            # Apps show the same notice themselves before turning telemetry on.
            if not args.json_output:
                _show_telemetry_scope(installation_root)
            tag_telemetry.enable(installation_root)
        elif action == "off":
            if not tag_telemetry.disable(installation_root):
                raise RuntimeError(
                    "TAG_TELEMETRY=off is active for this process; telemetry is already stopped "
                    "for this command, but the saved preference was not changed"
                )
        result = tag_telemetry.status(installation_root)
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            display.header("Telemetry", "Installation-wide privacy control")
            display.info_row(
                "Collection",
                "On" if result["enabled"] else "Off",
                good=bool(result["enabled"]),
            )
            display.info_row("Saved", str(result["saved_preference"]).replace("_", " "))
            if result["process_override"]:
                display.info_row("Override", "TAG_TELEMETRY=off")
            display.info_row(
                "Privacy",
                str(result["privacy_notice"] or "Not configured in this build"),
            )
        return 0
    if args.command == "autostart":
        return _autostart_command(installation_root, args, parser)
    if args.command == "version" and args.json_output:
        print(json.dumps({"schema_version": 1,
                          "version": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
                          "app_protocol": APP_PROTOCOL, "capabilities": list(CAPABILITIES),
                          "platform": sys.platform,
                          "runtime": runtime_identity(installation_root)}, indent=2))
        return 0
    if args.command == "add":
        if args.arguments:
            parser.error("add does not accept a name; the workspace alias is chosen during onboarding")
        if not display.stdin_is_terminal() and not args.json_output:
            print(
                "Interactive setup requires a terminal. Use tag inspect --json and tag config set for automation.",
                file=sys.stderr,
            )
            return 2
        # Setup asks for the Tag's name and picture first, then the AI, then the
        # Slack workspace, and records the workspace name on this Tag. Setup
        # renames the Tag after its Slack IDs; nobody invents an alias.
        context = tag_instances.create(
            installation_root, tag_instances.suggest_name(installation_root, "new tag"), provisional=True
        )
        display.header("Add", "New Tag")
        display.info_row("Home", display.short_path(context.home), good=True)
        display.info_row("Command", context.command("setup"), good=True)
        command = [sys.executable, str(ROOT / "scripts/tag_cli.py"), *context.command_arguments("setup")]
        result = subprocess.call(command, env={**instance_environment(context), DEFER_RENAME_ENV: "1"})
        renamed = _rename(installation_root, context.tag_id) if result == 0 else None
        if renamed and not args.json_output:
            display.info_row("Tag", f"Named '{renamed}' after its Slack team and app IDs", good=True)
        return _setup_result(result, renamed or context.tag_id, args.json_output)
    if args.command == "list":
        if args.arguments:
            parser.error("list does not accept positional arguments")
        rows = []
        for item in tag_instances.discover(installation_root):
            record = dict(item)
            if item["valid"]:
                try:
                    context = tag_instances.resolve(installation_root, str(item["id"]))
                    report = control.inspect(context.home, sys.modules[__name__], tag_id=context.tag_id)
                    config_file = context.home / "config/settings.json"
                    values = read_config(config_file) if config_file.is_file() else {}
                    names = agent_models.load_model_names(agent_models.model_names_path(context.home))
                    default_backend = report["backend"]["selected"] or "codex"
                    record.update(state=report["state"], configuration=report["configuration"],
                                  services=report["services"], slack_workspace=report.get("slack_workspace"),
                                  workspace_name=tag_instances.workspace_name(context.home),
                                  slack_name=_slack_name(context.home),
                                  slack_app_id=values.get("SLACK_APP_ID") or None,
                                  has_app=bool(values.get("SLACK_APP_ID")) or (
                                      context.home / "integrations/slack-cli/tag-create.json").exists(),
                                  nickname=tag_instances.nickname(context.home),
                                  avatar=_avatar(context.home, values),
                                  workspace_icon=_workspace_icon(context.home),
                                  keep_running=autostart.wanted(context.home) is True,
                                  main=context.tag_id == tag_id,
                                  default_model=report["backend"]["default_model"],
                                  default_model_label=agent_models.describe_model_choice(
                                      report["backend"]["default_model"], default_backend, names=names,
                                  ),
                                  default_model_name=agent_models.model_choice_name(
                                      report["backend"]["default_model"], default_backend, names=names,
                                  ),
                                  default_effort=agent_models.effective_effort(context.home, values),
                                  description=values.get("OPENTAG_BOT_DESCRIPTION") or None,
                                  channels=_channels(values, context.home))
                except (OSError, ValueError, RuntimeError) as exc:
                    record.update(valid=False, state="invalid_configuration", error=str(exc))
            else:
                record["state"] = "invalid_tag"
            # Before first setup there is no Tag yet; don't list a placeholder.
            if record["id"] == tag_instances.DEFAULT_TAG and not Path(str(record["home"])).exists():
                continue
            rows.append(record)
        result = {"schema_version": 1, "installation_root": str(installation_root), "tags": rows}
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            display.header("Tags", "Grouped by Slack workspace. Several Tags can share one.")
            groups: dict[str, list[dict]] = {}
            for row in rows:
                label = row.get("workspace_name") or row.get("slack_workspace") or "Slack not connected yet"
                groups.setdefault(str(label), []).append(row)
            for label, members in groups.items():
                team = members[0].get("slack_workspace")
                display.section(f"{label} ({team})" if team and team != label else label)
                for row in members:
                    name = row.get("slack_name") or "New Tag"
                    alias = f" · tag {row['nickname']}" if row.get("nickname") else ""
                    main = " · main" if row.get("main") else ""
                    model = f" · {row['default_model_label']}" if row.get("default_model_label") else ""
                    if model and row.get("default_effort"):
                        model += f" · {agent_models.effort_label(row['default_effort'])} thinking"
                    detail = (f"{name} · {row['state']}{alias}{main}{model}" if row.get("valid")
                              else str(row.get("error")))
                    display.info_row(str(row["id"]), detail, good=bool(row.get("valid")))
            if len(groups) and any(row.get("slack_workspace") for row in rows):
                display.next_action("Start every Tag in a workspace", "tag start --workspace TEAM_ID")
            display.next_action("Add a Tag", "tag add")
        return 0
    if args.workspace:
        return _workspace_lifecycle(installation_root, args.workspace, args.command, args.json_output)
    initializes_default = tag_id == "default" and (
        args.command in {"start", "dev", "setup"}
        or (args.command == "config" and args.arguments and args.arguments[0] in {"init", "set"})
    )
    if initializes_default and not tag_instances.instance_path(installation_root, tag_id).exists():
        existing = [str(item["id"]) for item in tag_instances.discover(installation_root)
                    if item["valid"] and Path(str(item["home"])).exists()]
        if existing:
            # Several Tags and no main one: don't guess, and don't create another.
            raise RuntimeError("Several Tags exist. Name one, for example tag " + existing[0] + " "
                               + args.command + ", or run tag add for a new Tag.")
    context = (tag_instances.ensure_default(installation_root) if initializes_default
               else tag_instances.resolve(installation_root, tag_id))
    home = context.home
    if args.command in {"start", "dev", "setup"}:
        _migrate_shared_ai(installation_root, home, defer_current=args.command in {"start", "dev"})
        try:
            from tag_dependencies import migrate as migrate_dependencies
        except ImportError:
            from scripts.tag_dependencies import migrate as migrate_dependencies
        migrate_dependencies(installation_root, ROOT)
        try:
            from tag_layout import migrate as migrate_layout
        except ImportError:
            from scripts.tag_layout import migrate as migrate_layout
        migrate_layout(context, sys.modules[__name__])
        context = tag_instances.resolve(installation_root, tag_id)
        if args.command != "setup":
            # Setup renames once the Slack name is chosen; start renames first.
            tag_id = _rename(installation_root, tag_id) or tag_id
            context = tag_instances.resolve(installation_root, tag_id)
        home = context.home
    environment = instance_environment(context)
    startup_attempt_overrides = {
        key: environment[key] for key in STARTUP_ATTEMPT_ENV_KEYS if key in environment
    }
    os.environ.clear()
    os.environ.update(environment)
    if args.command == "usage":
        try:
            from . import agent_usage
        except ImportError:
            import agent_usage
        values = settings.load_config(settings.config_path(home))
        usage = agent_usage.report(home, values)
        if args.json_output:
            print(json.dumps(usage, indent=2))
        else:
            print(f"Usage · {usage['month_utc']} UTC · {usage['attempts']} attempts")
            print(f"Tokens: {usage['input_tokens']} input, {usage['output_tokens']} output, {usage['cached_input_tokens']} cached input")
            print(f"Recorded estimated cost: ${usage['estimated_cost_usd']:.4f}")
            if usage['monthly_budget_usd'] is not None:
                print(f"Monthly advisory budget: ${usage['monthly_budget_usd']:.2f}")
                if usage['recorded_cost_over_budget']:
                    print("Recorded estimated cost has reached the budget.")
            print(f"Missing usage: {usage['attempts_without_usage']} attempts; missing cost: {usage['attempts_without_cost']}; unfinished: {usage['unfinished_attempts']}")
            print(usage['coverage'])
        return 0
    if args.command == "memory":
        action = args.arguments[0] if len(args.arguments) == 1 else "status" if not args.arguments else ""
        if action not in {"start", "status", "stop"}:
            parser.error("memory accepts start, status, or stop")
        if action == "start":
            config_path = context.home / "config/settings.json"
            values = read_config(config_path) if config_path.is_file() else {}
            environment = instance_environment(context, values)
            ensure_shared_memory(context, environment)
        migrate_legacy_mfs_record(tag_instances.resolve(installation_root))
        context.shared_mfs_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        managed = process_for(context.shared_mfs_home / "mfs.json") is not None
        bridges = bridge_processes(installation_root)
        result = {"schema_version": 1, "managed": managed, "healthy": healthy("http://127.0.0.1:13619"),
                  "running_tags": bridges, "state_path": str(context.shared_mfs_home / "mfs.json")}
        if action == "stop":
            if bridges:
                raise RuntimeError("Stop every Tag bridge before stopping shared memory: " + ", ".join(bridges))
            if not managed:
                raise RuntimeError("The running memory service is not owned by this Tag installation")
            stop_process(home, "mfs", state_dir=context.shared_mfs_home)
            result.update(managed=False, healthy=False)
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            display.header("Memory", "Shared by every Tag in this installation.")
            display.info_row("Service", "Managed and running" if result["managed"] else "Not managed", good=result["managed"])
            display.info_row("Active Tags", ", ".join(bridges) if bridges else "None")
            detail = log_tail(home, "mfs", 20, state_dir=context.shared_mfs_home)
            if detail:
                display.section("Recent memory output")
                for line in detail.splitlines():
                    print("    " + line)
        return 0
    if args.command in {"start", "dev"}:
        missing = missing_runtime_dependencies()
        if missing:
            raise RuntimeError(runtime_dependency_message(missing))
        if legacy_slack_ready(home):
            raise RuntimeError(
                "Another Tag installation is already connected to Slack. "
                f"Stop it using {legacy_stop_command(home)}, then retry this start."
            )
    if args.command is None or args.command == "status":
        report = control.status_report(home, sys.modules[__name__], tag_id=context.tag_id)
        if args.json_output:
            print(json.dumps(report, indent=2))
        else:
            control.show_status(report)
            show_upgrade_reminder(installation_root)
        return int(args.command == "status" and report["state"] != "running")
    if args.command in {"setup", "settings", "reset"} and not display.stdin_is_terminal() and not (
        args.command == "setup" and os.getenv(SETUP_PROTOCOL_ENV) == "jsonl"
    ) and not (settings_ai and (args.json_output or len(args.arguments) > 1)):
        print("Interactive setup requires a terminal. Use tag inspect --json and tag config set for automation.", file=sys.stderr)
        return 2
    if args.command == "reset":
        try:
            from tag_reset import reset_and_setup
        except ImportError:
            from scripts.tag_reset import reset_and_setup
        return reset_and_setup(home, sys.modules[__name__])
    if settings_ai:
        if args.arguments[1:2] not in ([], ["status"], ["models"]):
            initialize_instance(home)  # Only changes need the private home; checks don't create one.
        return _settings_ai(context, args)
    if args.command == "settings":
        initialize_instance(home)
        control.settings_menu(home, ai=_ai_target(context))
        return 0
    if args.command == "rename":
        return _rename_command(context, args)
    if args.command == "describe":
        return _describe_command(context, args)
    if args.command == "abandon":
        return _abandon_command(context, args)
    if args.command == "remove":
        return _remove_command(context, args)
    if args.command == "restart":
        display.header("Restart", selected_target(home, context.tag_id))
        command = [sys.executable, str(ROOT / "scripts/tag_cli.py")]
        environment = os.environ.copy()
        environment["TAG_RESTART_FLOW"] = "1"
        result = subprocess.call(command + context.command_arguments("stop"), env=environment)
        return result if result else subprocess.call(command + context.command_arguments("start"), env=environment)
    if args.command == "config":
        # Initialize the private home before writing, including Windows ACLs.
        if args.arguments and args.arguments[0] in {"init", "set"}:
            initialize_instance(home)
        return control.config_command(home, args.arguments, json_output=args.json_output,
                                      stdin=args.stdin, tag_id=context.tag_id)
    if args.command == "inspect" or (args.command == "status" and args.json_output):
        report = control.inspect(home, sys.modules[__name__], offline=args.offline, tag_id=context.tag_id)
        if args.json_output:
            print(json.dumps(report, indent=2))
        else:
            control.show_inspection(report)
            show_upgrade_reminder(installation_root)
        return int(args.command == "status" and not all(report["services"].values()))
    if args.command == "version":
        print("Tag v" + (ROOT / "VERSION").read_text(encoding="utf-8").strip())
        return 0
    if args.command == "paths":
        paths = {key: str(home / key) for key in ("config", "integrations", "state", "tmp")}
        paths["workspace"] = str(context.workspace)
        paths.update(tag=context.tag_id, installation_root=str(installation_root),
                     instance_home=str(home), releases=str(installation_root / "releases"),
                     shared_mfs=str(context.shared_mfs_home))
        paths.update(management_guide=str(ROOT / "docs/tag-management.md"))
        paths["runtime"] = runtime_identity(installation_root)
        if args.json_output:
            print(json.dumps(paths, indent=2))
            return 0
        runtime = paths["runtime"]
        display.header("Paths", selected_target(home, context.tag_id))
        display.section("Installation")
        display.info_row("Installation", display.short_path(installation_root))
        display.info_row(
            "Runtime",
            f"Tag v{runtime['version']} · {runtime['mode']}",
            good=bool(runtime["active_release"]) or runtime["mode"] == "source",
        )
        display.section("Data")
        display.info_row("Settings", display.short_path(paths["config"]))
        display.info_row("Workspace", display.short_path(paths["workspace"]))
        display.info_row("State", display.short_path(paths["state"]))
        display.section("Agent")
        display.info_row("Guide", display.short_path(paths["management_guide"]))
        display.next_action("Machine-readable paths", "tag paths --json")
        return 0
    if args.command == "upgrade":
        return upgrade_command(
            installation_root,
            channel=args.channel,
            version=args.target_version,
            dry_run=args.dry_run,
            no_restart=args.no_restart,
            allow_downgrade=args.allow_downgrade,
            json_output=args.json_output,
        )
    initialize_instance(home)
    initialize_workspace(context.workspace)
    if args.command == "rollback":
        named = [str(item["id"]) for item in tag_instances.discover(installation_root)
                 if item.get("valid") and item["id"] != "default"]
        if named:
            raise RuntimeError(
                "Rollback is unavailable while named Tags exist because the previous CLI may not "
                "understand their lifecycle: " + ", ".join(named)
            )
        running = bridge_processes(installation_root)
        if (running or process_for(context.shared_mfs_home / "mfs.json")
                or process_for(installation_root / "state/mfs.json")):
            raise RuntimeError("Stop every Tag and run tag memory stop before rolling back")
        try:
            from tag_install import atomic_text
        except ImportError:
            from scripts.tag_install import atomic_text
        previous = installation_root / "previous.json"
        if not previous.exists():
            raise RuntimeError("No previous release is available")
        current = installation_root / "current.json"
        old, target = current.read_text(encoding="utf-8"), previous.read_text(encoding="utf-8")
        if home.name == ".tag" and json.loads(target).get("instance_layout", 1) < 2:
            raise RuntimeError(
                "The previous release cannot read this Tag's .tag folder. "
                "Rollback was refused to preserve the current settings and history."
            )
        target_record = json.loads(target)
        verification = subprocess.run([target_record["python"], "-c",
            "import sys; assert sys.version_info >= (3, 10)"], capture_output=True, check=False)
        if verification.returncode:
            raise RuntimeError("The previous release runtime is unavailable; current release retained")
        atomic_text(current, target)
        atomic_text(previous, old)
        display.header("Rollback", "Selecting the previously installed Tag release.")
        display.section("Release")
        display.info_row("Previous", "Selected", good=True)
        display.completion(
            "Rollback is ready",
            "Configuration and workspace data were left unchanged.",
            next_label="Start the selected release",
            next_command=context.command("start"),
        )
        return 0
    if args.command == "migrate":
        if args.source is None:
            parser.error("migrate requires --from /path/to/old-checkout")
        try:
            from tag_migrate import migrate
        except ImportError:
            from scripts.tag_migrate import migrate
        migrate(args.source, home, context.workspace)
        return 0
    config_path = Path(os.getenv("OPENTAG_ENV_FILE", str(home / "config/settings.json")))
    if args.command == "setup":
        environment = dict(os.environ)
        if args.test:
            test_home = home / "testing/onboarding"
            initialize_instance(test_home)
            environment = {key: value for key, value in environment.items()
                           if not key.startswith(("SLACK_", "MFS_", "OPENTAG_"))}
            # Test onboarding is an explicit disposable staging installation.
            environment.update(runtime_environment(
                test_home, installation_root=installation_root
            ))
            config_path = test_home / "config/settings.json"
            print(f"TEST MODE: {test_home}", flush=True)
            print("Local settings are isolated. No services or indexing.", flush=True)
            print("WARNING: Slack actions are real and create or install real apps.", flush=True)
        command = [sys.executable, str(ROOT / "scripts/opentag_setup.py"), "--config", str(config_path)]
        if args.no_start or args.test:
            command.append("--no-start")
        if args.test:
            command.append("--test-mode")
        if args.review:
            command.append("--review")
        result = subprocess.call(command, env=environment)
        tag_id = context.tag_id
        if result == 0 and not args.test and not os.getenv(DEFER_RENAME_ENV):
            was_running = process_for(home / "state/slack.json") is not None
            renamed = _rename(installation_root, tag_id)
            if renamed:
                tag_id = renamed
                if not args.json_output:
                    display.info_row("Tag", f"Named '{renamed}' after its Slack team and app IDs", good=True)
                if was_running:
                    result = subprocess.call(
                        # Name the renamed Tag: it isn't necessarily the main one.
                        [sys.executable, str(ROOT / "scripts/tag_cli.py"), tag_id, "start"],
                        env={key: value for key, value in os.environ.items()
                             # Drop the pre-rename instance's paths; start rebuilds them.
                             if not key.startswith(("TAG_INSTANCE_HOME", "TAG_ID", "OPENTAG_"))
                             and key not in {"TMPDIR", "TEMP", "TMP"}},
                    )
        if result == 0 and not args.test and not args.json_output:
            show_upgrade_reminder(installation_root)
        return _setup_result(result, tag_id, args.json_output)
    if args.command == "doctor" and args.json_output:
        report = control.inspect(home, sys.modules[__name__], offline=True, tag_id=context.tag_id)
        if not report["configuration"]["complete"]:
            print(json.dumps({"schema_version": 1, "tag": context.tag_id,
                              "slack_workspace": report.get("slack_workspace"),
                              "ok": False, "configuration": report["configuration"],
                              "next_command": report["next_command"]}, indent=2))
            return 1
    if args.command in ("start", "dev", "doctor", "status") and config_path.is_file() and config_path.stat().st_size:
        if args.command in {"start", "dev"}:
            settings.migrate_file_delivery(home, config_path)
        values = read_config(config_path)
        if args.command in {"start", "dev"} and settings.config_errors(values):
            raise RuntimeError("Configuration is incomplete or invalid. Run tag inspect or tag setup.")
        if args.command in {"start", "dev"}:
            assert_unique_slack_app(context, values)
        os.environ.update(values)
        os.environ.update(startup_attempt_overrides)
    elif args.command in {"start", "dev"} or (args.command == "doctor" and not args.offline):
        raise RuntimeError(f"Missing configuration: {config_path}. Run tag setup.")
    if args.command in {"start", "dev"} and os.getenv("OPENTAG_BACKEND", "codex") == "codex":
        try:
            from . import tag_chatgpt
        except ImportError:
            import tag_chatgpt
        if tag_chatgpt.enabled() and not agent_models.agent_connection.active("codex"):
            if os.getenv("OPENTAG_CODEX_TRANSPORT", "app-server") != "app-server":
                raise RuntimeError("ChatGPT plan usage requires app-server. Run tag config set OPENTAG_CODEX_TRANSPORT app-server.")
            tag_chatgpt.Store().access()  # Refresh before starting dependent services.
    if args.command in {"start", "dev"}:
        for backend in agent_models.allowed_backends(os.getenv("OPENTAG_BACKEND", "codex")):
            agent_models.agent_connection.validate(backend)
    # A TAG installation always has one stable integration workspace.
    os.environ["OPENTAG_WORKDIR"] = str(context.workspace)
    if args.command == "doctor":
        result = doctor(home, args.offline, args.json_output, tag_id=context.tag_id)
        if not args.offline and not args.json_output and display.stdin_is_terminal():
            try:
                import tag_diagnose
            except ImportError:
                from scripts import tag_diagnose
            report = control.inspect(home, sys.modules[__name__], tag_id=context.tag_id)
            tag_diagnose.offer(tag_diagnose.report(result, report["services"]["slack"],
                report["services"]["mfs"], (home / "state").glob("*.log")))
        return result
    if args.command == "logs":
        try:
            import tag_activity, slack_channel_names
        except ImportError:
            from scripts import tag_activity, slack_channel_names
        if args.activity:
            details = tag_activity.activity_details(home / "state/activity", args.activity,
                report_directory=Path(os.getenv("OPENTAG_ERROR_REPORTS_DIR", str(home / "state/error-reports"))).expanduser())
            if details is None:
                error = "Activity is unavailable or expired. Run " + context.command("logs") + " --json for retained run IDs."
                if args.json_output:
                    print(json.dumps({"schema_version": 1, "ok": False, "error": error}))
                else:
                    print(error, file=sys.stderr)
                return 1
            if args.json_output:
                print(json.dumps({"schema_version": 1, "ok": True, "activity": details}, ensure_ascii=False))
            else:
                print(tag_activity.activity_details_text(details))
            return 0
        logs = sorted((home / "state").glob("*.log"))
        if args.json_output:
            try:
                values = read_config(home / "config/settings.json")
            except (OSError, ValueError):
                values = {}
            scopes = values.get("MFS_ALLOWED_SCOPES", "")
            team = values.get("SLACK_TEAM_ID", "")
            activity_limit = args.activity_limit or tag_activity.MAX_RECENT
            activity = tag_activity.recent_activity(home / "state/activity", scopes, limit=activity_limit + 1,
                channel=args.activity_channel, hide_errors=args.hide_errors,
                cached_names={team: slack_channel_names.read(home, team)})
            print(json.dumps({"schema_version": 1, "tag": context.tag_id, "services": {
                log.stem: log_tail(home, log.stem, args.limit or 200).splitlines() for log in logs
            }, "activity": activity[:activity_limit], "activity_has_more": len(activity) > activity_limit},
                indent=2, ensure_ascii=False))
            return 0
        display.header("Logs", selected_target(home, context.tag_id))
        if not logs:
            display.section("Services")
            display.info_row("Logs", "No service logs found yet")
        for log in logs:
            display.section(log.stem)
            content = log_tail(home, log.stem, args.limit or 50)
            if content:
                for line in content.splitlines():
                    print("    " + line)
            else:
                display.info_row("Output", "No recent entries")
        if args.follow:
            follow_logs(home, logs)
        else:
            display.next_action("Follow new entries", context.command("logs") + " --follow",
                                detail=f"For deeper checks, run {context.command('doctor')}.")
        return 0
    if args.command == "dev":
        return development_loop(home)
    if args.command == "stop":
        restart_flow = os.getenv("TAG_RESTART_FLOW") == "1"
        if not restart_flow:
            # Stopping on purpose means the login service leaves this Tag off.
            autostart.set_wanted(home, False)
        if restart_flow:
            display.section("Stopping")
        else:
            display.header(
                "Stop",
                selected_target(home, context.tag_id),
            )
            display.section("Services")
        stop_process(home, "slack")
        display.info_row("Slack", "Stopped or already offline", good=True)
        memory_running = process_for(context.shared_mfs_home / "mfs.json") is not None
        remaining_tags = bridge_processes(installation_root)
        if memory_running and remaining_tags:
            memory_status = "Still running for: " + ", ".join(remaining_tags)
            memory_detail = "Stop those Tags before running tag memory stop."
        elif memory_running:
            memory_status = "Still running · no active Tags"
            memory_detail = "Memory runs separately and stays available for your next start."
        else:
            memory_status = "No Tag-managed process running"
            if remaining_tags:
                memory_status += " · active Tags: " + ", ".join(remaining_tags)
            memory_detail = "Externally managed memory, if configured, is unchanged."
        display.info_row("Memory", memory_status)
        if not restart_flow:
            display.completion(
                "Tag is stopped",
                memory_detail,
                next_label="Start again",
                next_command=context.command("start"),
            )
            if memory_running and not remaining_tags:
                display.next_action("Stop memory too", "tag memory stop")
        return 0
    if args.command == "start":
        if args.json_output:
            # One JSON line per readiness step as it happens, then the outcome.
            display.progress_events()
        restart_flow = os.getenv("TAG_RESTART_FLOW") == "1"
        supervised = os.getenv(autostart.SUPERVISED_ENV) == "1"
        if not supervised:
            # Recorded even if this start fails: the login service retries with backoff.
            autostart.set_wanted(home, True)
        if restart_flow:
            display.section("Starting")
        else:
            display.header("Start", selected_target(home, context.tag_id))
            display.section("Readiness")
        display.info_row("Runtime", "Dependencies available", good=True)
        # Serialize starts of this Tag so concurrent invocations cannot create orphan services.
        # Other Tags may start in parallel: the shared AI locks only exclude account changes.
        # Wait briefly for a setup, start or stop that already holds them rather than failing at once.
        shared_locks = (installation_root / "state/ai-connection.lock", installation_root / "state/ai-start.lock")
        (connection_lock, ai_lock, lock), waited = tag_locks.acquire_all(
            [*shared_locks, home / "state/start.lock"],
            shared=shared_locks,
            wait=START_LOCK_WAIT_SECONDS,
            waiting=lambda: display.pending_row("Start", "Waiting for another start, stop or setup to finish…"))
        if waited and slack_ready(home):
            # The operation we waited for started this Tag; starting it again would only restart it.
            for held in (lock, ai_lock, connection_lock):
                held.release()
            display.info_row("Slack", "Already connected", good=True)
            if not restart_flow:
                display.completion("Tag is running", "Another start finished first.")
            return 0
        started = []
        try:
            if supervised and autostart.wanted(home) is not True:
                # Someone stopped this Tag while the login service was about to start it.
                display.info_row("Start", "Skipped · this Tag was switched off")
                return 0
            try:
                import slack_manifest_migrations
            except ImportError:
                from scripts import slack_manifest_migrations
            try:
                from tag_error_migrations import migrate as migrate_error_reports
            except ImportError:
                from scripts.tag_error_migrations import migrate as migrate_error_reports
            diagnostics_migrated = migrate_error_reports(home)
            display.info_row(
                "Diagnostics",
                "Private report storage migrated" if diagnostics_migrated else "Private report storage ready",
                good=True,
            )
            manifest_changed = slack_manifest_migrations.reconcile(home, config_path, values)
            if manifest_changed:
                # A migration may rotate credentials; preflight and the bridge
                # must use the saved replacement during this same start.
                values = read_config(config_path)
                os.environ.update(values)
            display.info_row(
                "Slack app",
                "Permissions migrated" if manifest_changed else "Permissions current",
                good=True,
            )
            avatar = _refresh_avatar(home, values)
            if avatar != "skipped":
                display.info_row("Picture", {
                    "saved": "Slack picture updated", "current": "Slack picture current",
                    "needs_permission": "Picture waits for users:read · approve Tag's app update in Slack, then retry tag start",
                    "unavailable": "Slack picture unavailable · keeping the saved picture; Tag will retry",
                }[avatar], good=avatar in {"saved", "current"})
            _refresh_workspace_name(home, values)
            try:
                import slack_channel_names
            except ImportError:
                from scripts import slack_channel_names
            try:
                slack_channel_names.migrate(home, values)
            except (OSError, ValueError):
                pass  # Display metadata retries on the next start; it cannot block service readiness.
            icon = _refresh_workspace_icon(home, values)
            if icon != "skipped":
                # team:read is optional: without it the workspace shows as a letter, and the start continues.
                pending = slack_manifest_migrations.optional_pending(home)
                display.info_row("Workspace", {
                    "saved": "Icon updated", "current": "Icon current", "default": "Slack's default icon",
                    "needs_permission": "Icon waits for team:read · approve Tag's app update in Slack"
                    + ("; Tag asks again within a day" if pending else ""),
                }.get(icon, "Icon not updated · " + icon.removeprefix("unavailable: ")),
                    good=icon in {"saved", "current", "default"})
            ensure_connector_credential(home, values)
            display.pending_row("Memory", "Waiting for the service to become healthy…")
            ensure_shared_memory(context, os.environ.copy())
            display.info_row("Memory", "Healthy", good=True)
            display.pending_row("Channel memory", "Checking which channels are imported…")
            if os.getenv("SLACK_CHANNEL_POLICY") == "invited":
                reconcile_invitation_memory(home)
            else:
                sync_configured_slack_memory()
            # Don't wait for the first import: Tag answers now, and history search covers each
            # channel once memory has imported it. One check still picks up renamed channels.
            try:
                importing = wait_for_configured_mfs_scopes(attempts=1)
                paused = False
            except RuntimeError:
                importing, paused = [], True
            if importing or paused:
                names = [urllib.parse.unquote(scope.rstrip("/").rsplit("/", 1)[-1]).rpartition("__")[0] for scope in importing]
                where = ", ".join(f"#{name}" for name in names if name) or "its channels"
                display.info_row("Channel memory", (f"Importing {where} in the background"
                                 if not paused else "Import paused by a Slack rate limit; it resumes in the background")
                                 + " · history search covers it once done")
            else:
                display.info_row("Channel memory", "Ready", good=True)
            preflight_result, preflight = doctor_report(False)
            if preflight_result:
                failed = [item for item in preflight.get("checks", []) if not item.get("ok")]
                if failed:
                    first = failed[0]
                    detail = str(first.get("check", "unknown check"))
                    action = first.get("next_action")
                    raise RuntimeError(
                        f"Preflight failed: {detail}"
                        + (f". {action}" if action else "")
                    )
                raise RuntimeError("Preflight failed; run tag doctor")
            display.info_row("Checks", "Configuration and access verified", good=True)
            display.pending_row("Slack", "Waiting for the connection to become ready…")
            if not slack_ready(home):
                # Replace a live but disconnected TAG-managed bridge rather than
                # accepting a PID as proof that Socket Mode is operational.
                stop_process(home, "slack")
                instance_id = uuid.uuid4().hex
                ready_file = home / "state/slack.ready"
                slack_environment = os.environ.copy()
                slack_environment["OPENTAG_PROCESS_ID"] = instance_id
                slack_command = [
                    sys.executable,
                    str(ROOT / "scripts/slack_socket_agent.py"),
                    "--backend",
                    os.environ["OPENTAG_BACKEND"],
                    "--ready-file",
                    str(ready_file),
                    "--process-id",
                    instance_id,
                ]
                if start_process(
                    home,
                    "slack",
                    slack_command,
                    environment=slack_environment,
                    metadata={"instance_id": instance_id},
                ):
                    started.append("slack")
            attempts = int(os.getenv("OPENTAG_STARTUP_ATTEMPTS", "30"))
            for _ in range(attempts):
                if slack_ready(home):
                    break
                if process_for(home / "state/slack.json") is None:
                    detail = log_tail(home, "slack")
                    raise RuntimeError(
                        "Slack bridge exited before becoming ready"
                        + (f":\n{detail}" if detail else "; run tag logs")
                    )
                time.sleep(1)
            else:
                detail = log_tail(home, "slack")
                raise RuntimeError(
                    "Slack bridge did not become ready"
                    + (f":\n{detail}" if detail else "; run tag logs")
                )
            display.info_row("Slack", "Connected", good=True)
            try:
                welcome_status = tag_welcome.send_once(home, values)
                if welcome_status:
                    display.info_row("Welcome DM", welcome_status)
            except Exception:
                # Optional onboarding must never stop a healthy bridge. Do not
                # print provider exceptions, which may contain credentials.
                display.info_row("Welcome DM", f"Could not confirm delivery; Tag is running. Retry with {context.command('start')}.")
            bot_name = os.getenv("OPENTAG_BOT_NAME", "Tag")
            display.completion(
                "Tag restarted" if restart_flow else "Tag is connected",
                "Running in the background · first reply not verified yet.",
                next_label="Try it in an allowed Slack channel",
                next_command=f"@{bot_name} say hello",
            )
        except Exception:
            for name in reversed(started):
                stop_process(home, name)
            raise
        finally:
            lock.release()
            ai_lock.release()
            connection_lock.release()
        _clear_pending_restart(installation_root, context.tag_id)
        show_upgrade_reminder(installation_root)
    return 0


def _clear_pending_restart(installation_root, tag_id):
    """Drop a running Tag from the shared AI migration's restart list."""
    # The migration that started this Tag holds the lock and owns the list.
    if os.getenv("TAG_AI_MIGRATION_RESTART") == "1":
        return
    try:
        import tag_chatgpt
    except ImportError:
        from scripts import tag_chatgpt
    checkpoint = installation_root / "shared/ai/migration-v1.json"
    try:
        # Another Tag may be starting at the same time: wait for its migration check.
        with _waiting_lock(installation_root / "state/ai-migration.lock"):
            record = tag_chatgpt.read_object(checkpoint)
            if tag_id in record.get("restart", []):
                record["restart"].remove(tag_id)
                tag_chatgpt.atomic_write(checkpoint, record)
    except tag_locks.LockBusy:
        # This Tag is already running. The next migration check skips running
        # Tags and clears the entry, so this bookkeeping must not fail the start.
        pass


def _show_telemetry_scope(installation_root: Path) -> None:
    """Show the complete collection boundary before an operator enables it."""
    display.header("Telemetry", "Installation-wide privacy control")
    display.paragraph(
        "Tag collects minimal anonymous usage telemetry to improve setup and reliability. "
        "We never send prompts, Slack messages, agent output, workspace paths, logs, "
        "credentials, or configuration values."
    )
    display.paragraph(
        "Telemetry is on by default after this notice. You can turn it off at any time "
        "with 'tag telemetry off', or for one process with TAG_TELEMETRY=off."
    )
    display.paragraph(f"Privacy notice: {tag_telemetry.build_config.PRIVACY_NOTICE_URL}")


def _offer_first_run_telemetry(installation_root: Path) -> None:
    """Turn usage data on with a one-line notice on the first interactive run."""
    if (
        tag_telemetry.hard_disabled()
        or not tag_telemetry.collection_available()
        or tag_telemetry.saved_preference(installation_root) is not None
        or not display.stdin_is_terminal()
        or not sys.stdout.isatty()
    ):
        return
    # Non-interactive runs never reach here, so nobody is opted in unseen.
    if tag_telemetry.enable(installation_root):
        display.paragraph(
            "Tag shares anonymous usage data to improve setup and reliability. "
            "Turn it off with 'tag telemetry off'."
        )


def _command_name(arguments: list[str]) -> str:
    return next((item for item in arguments if item in COMMANDS), "status")


def _command_group(command: str) -> str:
    if command in {"start", "stop", "restart", "dev", "logs"}:
        return "lifecycle"
    if command in {"setup", "add", "reset", "settings"}:
        return "setup"
    if command in {"config", "paths"}:
        return "configuration"
    if command in {"inspect", "status", "doctor", "version"}:
        return "diagnostics"
    if command == "memory":
        return "memory"
    if command in {"upgrade", "rollback", "migrate"}:
        return "update"
    return "tag_management"


def _error_category(error: BaseException) -> str:
    if isinstance(error, KeyboardInterrupt):
        return "interrupted"
    if isinstance(error, PermissionError):
        return "permission"
    if isinstance(error, (ImportError, FileNotFoundError)):
        return "dependency"
    if isinstance(error, ValueError):
        return "validation"
    if isinstance(error, OSError):
        return "runtime"
    return "unknown"


def main() -> int:
    """Apply telemetry policy around the existing CLI without changing outcomes."""
    arguments = sys.argv[1:]
    command = _command_name(arguments)
    installation_root = tag_home()
    telemetry_command = command == "telemetry"
    help_command = any(item in {"-h", "--help"} for item in arguments)
    if not telemetry_command and not help_command:
        _offer_first_run_telemetry(installation_root)
    enabled = (
        not telemetry_command
        and tag_telemetry.saved_preference(installation_root) is True
        and not tag_telemetry.hard_disabled()
    )
    started = time.monotonic()
    group = _command_group(command)
    if enabled:
        invocation = (
            "interactive"
            if display.stdin_is_terminal() and sys.stdout.isatty()
            else "non_interactive"
        )
        tag_telemetry.tui_started(installation_root, invocation)
    try:
        result = _run_cli()
    except SystemExit as exc:
        if enabled:
            succeeded = exc.code in (None, 0)
            if not succeeded:
                tag_telemetry.command_failed(
                    installation_root,
                    group,
                    "validation" if exc.code == 2 else "unknown",
                )
            tag_telemetry.command_completed(
                installation_root,
                group,
                "succeeded" if succeeded else "failed",
                time.monotonic() - started,
            )
        raise
    except BaseException as exc:
        if enabled:
            tag_telemetry.command_failed(
                installation_root, group, _error_category(exc)
            )
            tag_telemetry.command_completed(
                installation_root,
                group,
                "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                time.monotonic() - started,
            )
        raise
    if enabled:
        tag_telemetry.command_completed(
            installation_root,
            group,
            "succeeded" if result == 0 else "failed",
            time.monotonic() - started,
        )
    return result


if __name__ == "__main__":
    try:
        code = main()
        if display.progress_active():
            display.progress_result(code)
        raise SystemExit(code)
    except KeyboardInterrupt:
        if len(sys.argv) > 1 and sys.argv[1] == "logs":
            print("\nStopped following logs.", file=sys.stderr)
        elif len(sys.argv) > 1 and sys.argv[1] == "dev":
            print("\nDevelopment services stopped.", file=sys.stderr)
        else:
            print("\nInterrupted. Run tag setup to resume saved setup.", file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        if os.getenv(SETUP_PROTOCOL_ENV) == "jsonl":
            _setup_ui().emit({"type": "result", "status": "failed", "error": str(exc), "exit_code": 1})
        elif display.progress_active():
            display.progress_result(1, str(exc))
        elif "--json" in sys.argv:
            print(json.dumps({"schema_version": 1, "ok": False, "error": str(exc)}))
        elif len(sys.argv) > 1 and sys.argv[1] in {"start", "restart", "dev"} and os.getenv(PLAIN_ERRORS_ENV) != "1":
            title = "Dev" if sys.argv[1] == "dev" else (
                "Restart" if os.getenv("TAG_RESTART_FLOW") == "1" or sys.argv[1] == "restart" else "Start"
            )
            display.failure(title, str(exc))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
