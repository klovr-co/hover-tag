"""Cross-platform TAG lifecycle. Installed launchers use the release's Python."""
from __future__ import annotations

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
    from tag_config import read_config
    import tag_credentials
    import tag_display as display
except ImportError:
    from scripts.tag_paths import initialize_instance, initialize_workspace, runtime_environment, tag_home
    from scripts import tag_instances, tag_telemetry
    from scripts.tag_locks import LifecycleLock
    from scripts.tag_config import read_config
    from scripts import tag_credentials
    from scripts import tag_display as display

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DEPENDENCIES = ("mfs_server", "psutil", "slack_bolt")
UPGRADE_CHANNELS = ("stable", "beta", "alpha", "edge")
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


def mfs_client_executable() -> str | None:
    """Prefer Tag's bundled MFS client, but support an independent install."""
    name = "mfs.exe" if os.name == "nt" else "mfs"
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
    """Start the configured shared MFS if needed and wait until it is healthy."""
    url = environment.get("MFS_URL", "http://127.0.0.1:13619")
    local_mfs = local_mfs_endpoint(url)
    if local_mfs:
        migrate_legacy_mfs_record(context)
        replace_unmanaged_local_mfs(
            context.home, url, state_dir=context.shared_mfs_home
        )
    if healthy(url):
        return
    if not local_mfs:
        raise RuntimeError(
            "Configured external MFS endpoint is unavailable; start that server first"
        )
    executable = mfs_server_executable()
    if not executable:
        raise RuntimeError(
            "MFS server is unavailable; run ./install.sh --dependencies-only"
        )
    shared = context.shared_mfs_home
    shared.mkdir(parents=True, exist_ok=True, mode=0o700)
    mfs_lock = LifecycleLock(shared / "start.lock").acquire()
    try:
        # Recheck after acquiring the installation-wide lock. A different Tag
        # may have completed startup while this process waited.
        if not healthy(url):
            start_process(
                context.home,
                "mfs",
                [executable, "run"],
                environment=environment,
                state_dir=shared,
                cwd=context.workspace,
            )
        attempts = int(environment.get("OPENTAG_MFS_STARTUP_ATTEMPTS", "90"))
        for _ in range(attempts):
            if healthy(url):
                break
            if process_for(shared / "mfs.json") is None:
                detail = log_tail(context.home, "mfs", state_dir=shared)
                raise RuntimeError(
                    "Shared MFS exited before becoming healthy"
                    + (f":\n{detail}" if detail else "; run tag memory status")
                )
            time.sleep(1)
        else:
            detail = log_tail(context.home, "mfs", state_dir=shared)
            raise RuntimeError(
                f"Shared MFS did not become healthy within {attempts} seconds"
                + (f":\n{detail}" if detail else "; run tag memory status")
            )
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
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError(
            f"Another Tag is activating a Slack app. If interrupted, remove {lock} and retry."
        ) from None
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


def sync_configured_slack_memory(environment: dict[str, str] | None = None) -> None:
    """Register and incrementally sync the connector approved during setup."""
    source = environment if environment is not None else os.environ
    uri = source.get("MFS_SLACK_CONNECTOR_URI", "").strip()
    config = Path(source.get("MFS_SLACK_CONNECTOR_CONFIG", "")).expanduser()
    if not uri or not config.is_file():
        return
    executable = mfs_client_executable()
    if not executable:
        raise RuntimeError("MFS client is unavailable; install it before indexing Slack history")
    completed = subprocess.run(
        [executable, "add", uri, "--config", str(config), "--yes"],
        check=False,
        text=True,
        capture_output=True,
        env=environment,
        timeout=120,
    )
    if completed.returncode:
        detail = completed.stdout + completed.stderr
        # The managed connector survives MFS restarts. Re-adding it reports a
        # conflict, so update that same registered connector instead of
        # pretending that its old configuration describes new channel consent.
        if "connector_already_registered" in detail or "connector already registered" in detail.lower():
            completed = subprocess.run(
                [executable, "connector", "update", uri, "--config", str(config)],
                check=False,
                text=True,
                capture_output=True,
                env=environment,
                timeout=120,
            )
            if not completed.returncode:
                return
            detail = completed.stdout + completed.stderr
        if "sync_already_running" in detail:
            return
        if "environment variable MFS_SLACK_TOKEN is not set" in detail:
            raise MfsHistoryCredentialUnavailable(MFS_HISTORY_CREDENTIAL_MESSAGE)
        if "no plugin for slack" in detail.lower():
            raise MfsSlackConnectorUnavailable(MFS_SLACK_CONNECTOR_MESSAGE)
        raise RuntimeError("Slack history indexing could not start; run tag logs and mfs status")


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
        status.get("state") != "sync_requested"
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


def authenticated_mfs_url() -> str:
    """Return an MFS base URL that is safe to receive a bearer token."""
    raw = os.getenv("MFS_URL", "http://127.0.0.1:13619").rstrip("/")
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


def mfs_request_json(path: str, parameters: dict[str, str]) -> dict[str, object] | None:
    """Call an authenticated MFS endpoint after enforcing its transport boundary."""
    base = authenticated_mfs_url()
    token = os.getenv("MFS_TOKEN", "").strip()
    if not token:
        try:
            token = (Path.home() / ".mfs/server.token").read_text(encoding="utf-8").strip()
        except OSError:
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
    for attempt in range(max(1, limit)):
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
            for tag_id in running_tags:
                arguments = [] if tag_id == "default" else [tag_id]
                command = [sys.executable, str(home / "bin/tag-launch.py"), *arguments, "restart"]
                completed = subprocess.run(command, capture_output=json_output, text=True, check=False)
                if completed.returncode:
                    detail = redact_log_text((completed.stderr or completed.stdout or "").strip())
                    failures.append(tag_id + (f": {detail}" if detail else ""))
            if failures:
                raise RuntimeError(
                    "Tag was upgraded, but these Tags need attention: " + "\n".join(failures)
                    + ". Run tag [alias] doctor, resolve the reported requirement, then retry start."
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


def _run_cli() -> int:
    parser = argparse.ArgumentParser(description="Tag: set up, inspect, and manage your Slack teammate.",
                                     usage="tag [TAG] [COMMAND] [OPTIONS]",
                                     epilog=(
                                         "Use tag for default status, or tag NAME status for a named Tag. "
                                         "Start with tag setup; change configuration with tag settings.\n\n"
                                         "Telemetry: Tag can collect minimal anonymous CLI usage without prompts, "
                                         "Slack messages, agent output, paths, logs, credentials, or configuration "
                                         "values. Use 'tag telemetry status|on|off', or set TAG_TELEMETRY=off. "
                                         "Privacy notice: "
                                         f"{tag_telemetry.build_config.PRIVACY_NOTICE_URL or 'not configured in this build'}"
                                     ),
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", choices=COMMANDS)
    parser.add_argument("arguments", nargs="*", help="memory: start | status | stop; config: init | show | keys | set KEY VALUE")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output", help="structured output for inspect, status, doctor, config, paths, and upgrade")
    parser.add_argument("--stdin", action="store_true", help="read a config value from stdin")
    parser.add_argument("--from", dest="source", type=Path)
    parser.add_argument("--no-start", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--test", action="store_true", help="setup: use a separate test home; implies --no-start")
    parser.add_argument("--review", action="store_true", help="setup: review choices even when already configured")
    parser.add_argument("--follow", action="store_true", help="logs: continue streaming new service output")
    parser.add_argument("--limit", type=int, help="logs: number of recent lines per service (default: 50)")
    upgrade_selector = parser.add_mutually_exclusive_group()
    upgrade_selector.add_argument("--channel", choices=UPGRADE_CHANNELS, help="upgrade: switch to this release channel")
    upgrade_selector.add_argument("--version", dest="target_version", help="upgrade: install and pin this exact version")
    parser.add_argument("--dry-run", action="store_true", help="upgrade: verify and report the target without installing")
    parser.add_argument("--no-restart", action="store_true", help="upgrade: leave running services on the previous code")
    parser.add_argument("--allow-downgrade", action="store_true", help="upgrade: explicitly permit installing an older release")
    raw_arguments = sys.argv[1:]
    explicit_tag = bool(
        raw_arguments
        and not raw_arguments[0].startswith("-")
        and raw_arguments[0] not in COMMANDS
    )
    tag_id = raw_arguments.pop(0) if explicit_tag else "default"
    args = parser.parse_args(raw_arguments)
    if (args.no_start or args.test or args.review) and args.command != "setup":
        parser.error("--no-start, --test and --review are only for setup")
    if args.arguments and args.command not in {"add", "memory", "config", "telemetry"}:
        parser.error("Only add, memory, config, and telemetry accept additional positional arguments")
    if args.json_output and args.command not in {"list", "memory", "inspect", "status", "doctor", "config", "paths", "upgrade", "telemetry"}:
        parser.error("--json supports list, memory, inspect, status, doctor, config, paths, upgrade, and telemetry")
    if args.stdin and args.command != "config":
        parser.error("--stdin is only for config set")
    if args.offline and args.command not in {"inspect", "doctor"}:
        parser.error("--offline supports inspect and doctor")
    if (args.follow or args.limit is not None) and args.command != "logs":
        parser.error("--follow and --limit are only for logs")
    if (args.channel or args.target_version or args.dry_run or args.no_restart or args.allow_downgrade) and args.command != "upgrade":
        parser.error("--channel, --version, --dry-run, --no-restart, and --allow-downgrade are only for upgrade")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    installation_root = tag_home()
    tag_instances.validate_name(tag_id)
    if args.command in {"version", "upgrade", "rollback", "migrate", "list", "add", "memory", "telemetry"} and explicit_tag:
        parser.error(f"Tag selection is not supported for installation-wide command '{args.command}'")
    try:
        import tag_control as control
        import tag_config as settings
    except ImportError:
        from scripts import tag_control as control, tag_config as settings
    if args.command == "telemetry":
        if len(args.arguments) != 1 or args.arguments[0] not in {"status", "on", "off"}:
            parser.error("telemetry requires status, on, or off")
        action = args.arguments[0]
        if action == "on":
            if tag_telemetry.hard_disabled():
                raise RuntimeError(
                    "TAG_TELEMETRY=off is active for this process; unset it to save telemetry on"
                )
            if not tag_telemetry.collection_available():
                raise RuntimeError(
                    "This Tag build has no approved telemetry destination; no preference was changed"
                )
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
    if args.command == "add":
        if args.arguments:
            parser.error("add does not accept a name; the workspace alias is chosen during onboarding")
        if not sys.stdin.isatty():
            print(
                "Interactive setup requires a terminal. Use tag inspect --json and tag config set for automation.",
                file=sys.stderr,
            )
            return 2
        try:
            import opentag_setup as setup
        except ImportError:
            from scripts import opentag_setup as setup
        selected = setup.connect_slack_workspace()
        if not selected:
            return 1
        team_id, workspace_name = selected
        suggestion = tag_instances.suggest_name(installation_root, workspace_name)
        display.header("Add", f"Slack workspace connected: {workspace_name}")
        display.paragraph("Choose a workspace alias. It is used in commands and does not change your assistant's Slack name.")
        while True:
            alias = setup.ask("Workspace alias", suggestion).strip()
            try:
                context = tag_instances.create(installation_root, alias)
                break
            except ValueError as exc:
                display.paragraph(str(exc), display.WARNING)
        settings.update_config(settings.config_path(context.home), {"SLACK_TEAM_ID": team_id})
        display.header("Add", f"Created workspace alias '{context.tag_id}'.")
        display.info_row("Home", display.short_path(context.home), good=True)
        display.info_row("Command", context.command("setup"), good=True)
        command = [sys.executable, str(ROOT / "scripts/tag_cli.py"), *context.command_arguments("setup")]
        return subprocess.call(command, env=instance_environment(context))
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
                    record.update(state=report["state"], configuration=report["configuration"],
                                  services=report["services"], slack_workspace=report.get("slack_workspace"))
                except (OSError, ValueError, RuntimeError) as exc:
                    record.update(valid=False, state="invalid_configuration", error=str(exc))
            else:
                record["state"] = "invalid_tag"
            rows.append(record)
        result = {"schema_version": 1, "installation_root": str(installation_root), "tags": rows}
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            display.header("Tags", "Independent Slack workspaces managed by this installation.")
            for row in rows:
                workspace = row.get("slack_workspace") or "Slack not configured"
                detail = f"{row['state']} · {workspace}" if row.get("valid") else str(row.get("error"))
                display.info_row(str(row["id"]), detail, good=bool(row.get("valid")))
            display.next_action("Connect another Slack workspace", "tag add")
        return 0
    context = tag_instances.resolve(installation_root, tag_id)
    home = context.home
    if args.command in {"start", "dev", "setup"}:
        tag_instances.ensure_default(installation_root)
        try:
            from tag_layout import migrate as migrate_layout
        except ImportError:
            from scripts.tag_layout import migrate as migrate_layout
        migrate_layout(context, sys.modules[__name__])
    environment = instance_environment(context)
    startup_attempt_overrides = {
        key: environment[key] for key in STARTUP_ATTEMPT_ENV_KEYS if key in environment
    }
    os.environ.clear()
    os.environ.update(environment)
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
    if args.command in {"setup", "settings", "reset"} and not sys.stdin.isatty():
        print("Interactive setup requires a terminal. Use tag inspect --json and tag config set for automation.", file=sys.stderr)
        return 2
    if args.command == "reset":
        try:
            from tag_reset import reset_and_setup
        except ImportError:
            from scripts.tag_reset import reset_and_setup
        return reset_and_setup(home, sys.modules[__name__])
    if args.command == "settings":
        initialize_instance(home)
        control.settings_menu(home)
        return 0
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
        print("Tag v" + (ROOT / "VERSION").read_text().strip())
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
        if result == 0 and not args.test:
            show_upgrade_reminder(installation_root)
        return result
    if args.command == "doctor" and args.json_output:
        report = control.inspect(home, sys.modules[__name__], offline=True, tag_id=context.tag_id)
        if not report["configuration"]["complete"]:
            print(json.dumps({"schema_version": 1, "tag": context.tag_id,
                              "slack_workspace": report.get("slack_workspace"),
                              "ok": False, "configuration": report["configuration"],
                              "next_command": report["next_command"]}, indent=2))
            return 1
    if args.command in ("start", "dev", "doctor", "status") and config_path.is_file() and config_path.stat().st_size:
        values = read_config(config_path)
        if args.command in {"start", "dev"} and settings.config_errors(values):
            raise RuntimeError("Configuration is incomplete or invalid. Run tag inspect or tag setup.")
        if args.command in {"start", "dev"}:
            assert_unique_slack_app(context, values)
        os.environ.update(values)
        os.environ.update(startup_attempt_overrides)
    elif args.command in {"start", "dev"} or (args.command == "doctor" and not args.offline):
        raise RuntimeError(f"Missing configuration: {config_path}. Run tag setup.")
    # A TAG installation always has one stable integration workspace.
    os.environ["OPENTAG_WORKDIR"] = str(context.workspace)
    if args.command == "doctor":
        result = doctor(home, args.offline, args.json_output, tag_id=context.tag_id)
        if not args.offline and not args.json_output and sys.stdin.isatty():
            try:
                import tag_diagnose
            except ImportError:
                from scripts import tag_diagnose
            report = control.inspect(home, sys.modules[__name__], tag_id=context.tag_id)
            tag_diagnose.offer(tag_diagnose.report(result, report["services"]["slack"],
                report["services"]["mfs"], (home / "state").glob("*.log")))
        return result
    if args.command == "logs":
        display.header("Logs", selected_target(home, context.tag_id))
        logs = sorted((home / "state").glob("*.log"))
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
        restart_flow = os.getenv("TAG_RESTART_FLOW") == "1"
        if restart_flow:
            display.section("Starting")
        else:
            display.header("Start", selected_target(home, context.tag_id))
            display.section("Readiness")
        display.info_row("Runtime", "Dependencies available", good=True)
        # Serialize starts so concurrent invocations cannot create orphan services.
        lock = LifecycleLock(home / "state/start.lock").acquire()
        started = []
        try:
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
            ensure_connector_credential(home, values)
            display.pending_row("Memory", "Waiting for the service to become healthy…")
            ensure_shared_memory(context, os.environ.copy())
            display.info_row("Memory", "Healthy", good=True)
            display.pending_row(
                "Channel memory", "Waiting for selected channels to become readable…"
            )
            if os.getenv("SLACK_CHANNEL_POLICY") == "invited":
                reconcile_invitation_memory(home)
            else:
                sync_configured_slack_memory()
            unavailable_scopes = wait_for_configured_mfs_scopes()
            if unavailable_scopes:
                raise RuntimeError(
                    "MFS scope did not become readable after indexing: "
                    + unavailable_scopes[0]
                    + ". Run mfs status and tag doctor, then retry tag start."
                )
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
        show_upgrade_reminder(installation_root)
    return 0


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
    """Persist a choice only after the notice is visible in an interactive TUI."""
    if (
        tag_telemetry.hard_disabled()
        or not tag_telemetry.collection_available()
        or tag_telemetry.saved_preference(installation_root) is not None
        or not sys.stdin.isatty()
        or not sys.stdout.isatty()
    ):
        return
    _show_telemetry_scope(installation_root)
    try:
        import setup_ui as ui
    except ImportError:
        from scripts import setup_ui as ui
    try:
        choice = ui.choose(
            "Help support Tag’s development",
            ["Continue", "Turn telemetry off"],
            default=0,
        )
    except ui.Paused:
        # An interrupted notice is not consent. Continue this command without
        # collection and offer the same notice on a later interactive run.
        return
    if choice == 0:
        tag_telemetry.enable(installation_root)
    else:
        tag_telemetry.disable(installation_root)


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
            if sys.stdin.isatty() and sys.stdout.isatty()
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
        raise SystemExit(main())
    except KeyboardInterrupt:
        if len(sys.argv) > 1 and sys.argv[1] == "logs":
            print("\nStopped following logs.", file=sys.stderr)
        elif len(sys.argv) > 1 and sys.argv[1] == "dev":
            print("\nDevelopment services stopped.", file=sys.stderr)
        else:
            print("\nInterrupted. Run tag setup to resume saved setup.", file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        if "--json" in sys.argv:
            print(json.dumps({"schema_version": 1, "ok": False, "error": str(exc)}))
        elif len(sys.argv) > 1 and sys.argv[1] in {"start", "restart", "dev"}:
            title = "Dev" if sys.argv[1] == "dev" else (
                "Restart" if os.getenv("TAG_RESTART_FLOW") == "1" or sys.argv[1] == "restart" else "Start"
            )
            display.failure(title, str(exc))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
