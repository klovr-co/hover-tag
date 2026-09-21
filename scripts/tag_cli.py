"""Cross-platform TAG lifecycle. Installed launchers use the release's Python."""
from __future__ import annotations

import argparse
import contextlib
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
    from tag_paths import initialize, runtime_environment, tag_home
    from tag_config import read_config
    import tag_display as display
except ImportError:
    from scripts.tag_paths import initialize, runtime_environment, tag_home
    from scripts.tag_config import read_config
    from scripts import tag_display as display

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DEPENDENCIES = ("mfs_server", "psutil", "slack_bolt")
UPGRADE_CHANNELS = ("stable", "beta", "alpha", "edge")
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
        if process.create_time() == record["created"] and process.status() != psutil.STATUS_ZOMBIE:
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
) -> bool:
    import psutil
    record = home / "state" / f"{name}.json"
    if process_for(record):
        return False
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS} if os.name == "nt" else {"start_new_session": True}
    with (home / "state" / f"{name}.log").open("ab") as log:
        child = subprocess.Popen(command, cwd=home / "workspace", stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, env=environment, **options)
    process = psutil.Process(child.pid)
    identity = {"pid": child.pid, "created": process.create_time(), **(metadata or {})}
    record.write_text(json.dumps(identity), encoding="utf-8")
    time.sleep(0.3)
    if child.poll() is not None:
        record.unlink(missing_ok=True)
        detail = log_tail(home, name, 20)
        raise RuntimeError(
            f"{name} exited during startup"
            + (f":\n{detail}" if detail else "; run tag logs")
        )
    # This command intentionally leaves a detached service alive on return.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        del child
    return True


def stop_process(home: Path, name: str) -> None:
    import psutil
    record = home / "state" / f"{name}.json"
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


def replace_unmanaged_local_mfs(home: Path, url: str) -> bool:
    """Replace an untracked loopback MFS so Tag owns runtime and cleanup."""
    record = home / "state/mfs.json"
    if not local_mfs_endpoint(url) or process_for(record) is not None or not healthy(url):
        return False
    process = local_mfs_listener(url)
    if process is None:
        raise RuntimeError(
            "The local MFS endpoint is healthy but its process is not an identifiable "
            "mfs-server. Stop that service or configure a separate MFS_URL before starting Tag."
        )
    record.write_text(
        json.dumps({"pid": process.pid, "created": process.create_time(), "adopted": True}),
        encoding="utf-8",
    )
    stop_process(home, "mfs")
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


def log_tail(home: Path, name: str, lines: int = 50) -> str:
    try:
        content = (home / "state" / f"{name}.log").read_text(
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

    display.header("Dev", "Watching Python source and reloading the Slack bridge.")
    display.section("Bootstrap")
    command = [sys.executable, str(ROOT / "scripts/tag_cli.py"), "start"]
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


def sync_configured_slack_memory(environment: dict[str, str] | None = None) -> None:
    """Register and incrementally sync the connector approved during setup."""
    source = environment if environment is not None else os.environ
    uri = source.get("MFS_SLACK_CONNECTOR_URI", "").strip()
    config = Path(source.get("MFS_SLACK_CONNECTOR_CONFIG", "")).expanduser()
    if not uri or not config.is_file():
        return
    executable = shutil.which("mfs")
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
    if status.get("state") != "sync_requested":
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


def doctor(home: Path, offline: bool, json_output: bool = False) -> int:
    if json_output:
        return subprocess.call(doctor_command(offline, json_output=True))
    result, report = doctor_report(offline)
    display.doctor_summary(report)
    return result


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
        running = any(
            process_for(home / "state" / f"{name}.json")
            for name in ("slack", "mfs")
        )
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
            command = [sys.executable, str(home / "bin/tag-launch.py"), "restart"]
            completed = subprocess.run(
                command,
                capture_output=json_output,
                text=True,
                check=False,
            )
            if completed.returncode:
                detail = (completed.stderr or completed.stdout or "").strip()
                raise RuntimeError(
                    "Tag was upgraded, but its services did not restart. "
                    "Run tag stop, then tag rollback."
                    + (f"\n{detail}" if detail else "")
                )
            result["restarted"] = True

    if json_output:
        print(json.dumps(result, indent=2))
    else:
        display.completion(
            "Tag upgraded",
            f"Now using Tag v{result['target']['version']}. Configuration and workspace data were preserved.",
            next_label="Activate the new release" if result["restart_required"] else "",
            next_command="tag restart" if result["restart_required"] else "",
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Tag: set up, inspect, and manage your Slack teammate.",
                                     epilog="Use tag for status and the next step. Start with tag setup; change configuration with tag settings.")
    parser.add_argument("command", nargs="?", choices=("settings", "inspect", "config", "setup", "reset", "migrate", "upgrade", "rollback", "version", "paths", "doctor", "start", "stop", "restart", "status", "logs", "dev"))
    parser.add_argument("arguments", nargs="*", help="config: init | show | keys | set KEY VALUE")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output", help="structured output for inspect, status, doctor, config, paths, and upgrade")
    parser.add_argument("--stdin", action="store_true", help="read a config value from stdin")
    parser.add_argument("--from", dest="source", type=Path)
    parser.add_argument("--no-start", action="store_true", help="setup: save choices without starting services or indexing")
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
    args = parser.parse_args()
    if (args.no_start or args.test or args.review) and args.command != "setup":
        parser.error("--no-start, --test and --review are only for setup")
    if args.arguments and args.command != "config":
        parser.error("Only config accepts additional positional arguments")
    if args.json_output and args.command not in {"inspect", "status", "doctor", "config", "paths", "upgrade"}:
        parser.error("--json supports inspect, status, doctor, config, paths, and upgrade")
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
    home = tag_home()
    try:
        import tag_control as control
        import tag_config as settings
    except ImportError:
        from scripts import tag_control as control, tag_config as settings
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
        report = control.status_report(home, sys.modules[__name__])
        if args.json_output:
            print(json.dumps(report, indent=2))
        else:
            control.show_status(report)
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
        initialize(home)
        os.environ.update(runtime_environment(home))
        control.settings_menu(home)
        return 0
    if args.command == "restart":
        display.header("Restart", "Refreshing the services managed by this Tag installation.")
        command = [sys.executable, str(ROOT / "scripts/tag_cli.py")]
        environment = os.environ.copy()
        environment["TAG_RESTART_FLOW"] = "1"
        result = subprocess.call(command + ["stop"], env=environment)
        return result if result else subprocess.call(command + ["start"], env=environment)
    if args.command == "config":
        # Initialize the private home before writing, including Windows ACLs.
        if args.arguments and args.arguments[0] in {"init", "set"}:
            initialize(home)
        return control.config_command(home, args.arguments, json_output=args.json_output, stdin=args.stdin)
    if args.command == "inspect" or (args.command == "status" and args.json_output):
        report = control.inspect(home, sys.modules[__name__], offline=args.offline)
        if args.json_output:
            print(json.dumps(report, indent=2))
        else:
            control.show_inspection(report)
        return int(args.command == "status" and not all(report["services"].values()))
    if args.command == "version":
        print("Tag v" + (ROOT / "VERSION").read_text().strip())
        return 0
    if args.command == "paths":
        paths = {key: str(home / key) for key in ("config", "workspace", "integrations", "state", "tmp", "releases")}
        paths.update(management_guide=str(ROOT / "docs/tag-management.md"))
        paths["runtime"] = runtime_identity(home)
        if args.json_output:
            print(json.dumps(paths, indent=2))
            return 0
        runtime = paths["runtime"]
        display.header("Paths", "Where this Tag installation keeps its runtime and data.")
        display.section("Installation")
        display.info_row("Home", display.short_path(home))
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
            home,
            channel=args.channel,
            version=args.target_version,
            dry_run=args.dry_run,
            no_restart=args.no_restart,
            allow_downgrade=args.allow_downgrade,
            json_output=args.json_output,
        )
    initialize(home)
    os.environ.update(runtime_environment(home))
    if args.command == "rollback":
        if any(process_for(home / "state" / f"{name}.json") for name in ("slack", "mfs")):
            raise RuntimeError("Run tag stop before rolling back")
        try:
            from tag_install import atomic_text
        except ImportError:
            from scripts.tag_install import atomic_text
        previous = home / "previous.json"
        if not previous.exists():
            raise RuntimeError("No previous release is available")
        current = home / "current.json"
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
            next_command="tag start",
        )
        return 0
    if args.command == "migrate":
        if args.source is None:
            parser.error("migrate requires --from /path/to/old-checkout")
        try:
            from tag_migrate import migrate
        except ImportError:
            from scripts.tag_migrate import migrate
        migrate(args.source, home)
        return 0
    config_path = Path(os.getenv("OPENTAG_ENV_FILE", str(home / "config/settings.json")))
    if args.command == "setup":
        environment = dict(os.environ)
        if args.test:
            test_home = home / "testing/onboarding"
            initialize(test_home)
            environment = {key: value for key, value in environment.items()
                           if not key.startswith(("SLACK_", "MFS_", "OPENTAG_"))}
            environment.update(runtime_environment(test_home))
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
        return subprocess.call(command, env=environment)
    if args.command == "doctor" and args.json_output:
        report = control.inspect(home, sys.modules[__name__], offline=True)
        if not report["configuration"]["complete"]:
            print(json.dumps({"schema_version": 1, "ok": False, "configuration": report["configuration"],
                              "next_command": report["next_command"]}, indent=2))
            return 1
    if args.command in ("start", "dev", "doctor", "status") and config_path.is_file() and config_path.stat().st_size:
        values = read_config(config_path)
        if args.command in {"start", "dev"} and settings.config_errors(values):
            raise RuntimeError("Configuration is incomplete or invalid. Run tag inspect or tag setup.")
        os.environ.update(values)
    elif args.command in {"start", "dev"} or (args.command == "doctor" and not args.offline):
        raise RuntimeError(f"Missing configuration: {config_path}. Run tag setup.")
    # A TAG installation always has one stable integration workspace.
    os.environ["OPENTAG_WORKDIR"] = str(home / "workspace")
    if args.command == "doctor":
        result = doctor(home, args.offline, args.json_output)
        if not args.offline and not args.json_output and sys.stdin.isatty():
            try:
                import tag_diagnose
            except ImportError:
                from scripts import tag_diagnose
            report = control.inspect(home, sys.modules[__name__])
            tag_diagnose.offer(tag_diagnose.report(result, report["services"]["slack"],
                report["services"]["mfs"], (home / "state").glob("*.log")))
        return result
    if args.command == "logs":
        display.header("Logs", "Recent output from services managed by this Tag installation.")
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
            display.next_action("Follow new entries", "tag logs --follow", detail="For deeper checks, run tag doctor.")
        return 0
    if args.command == "dev":
        return development_loop(home)
    if args.command == "stop":
        restart_flow = os.getenv("TAG_RESTART_FLOW") == "1"
        if restart_flow:
            display.section("Stopping")
        else:
            display.header("Stop", "Disconnecting services managed by this Tag installation.")
            display.section("Services")
        for name in ("slack", "mfs"):
            stop_process(home, name)
            display.info_row(
                "Slack" if name == "slack" else "Memory",
                "Stopped or already offline",
                good=True,
            )
        if not restart_flow:
            display.completion(
                "Tag is stopped",
                "Independently managed memory servers were left running.",
                next_label="Start again",
                next_command="tag start",
            )
        return 0
    if args.command == "start":
        restart_flow = os.getenv("TAG_RESTART_FLOW") == "1"
        if restart_flow:
            display.section("Starting")
        else:
            display.header("Start", "Bringing memory and Slack online.")
            display.section("Readiness")
        display.info_row("Runtime", "Dependencies available", good=True)
        # Serialize starts so concurrent invocations cannot create orphan services.
        lock = home / "state/start.lock"
        try:
            lock.mkdir()
        except FileExistsError:
            raise RuntimeError(f"Another start is in progress. If interrupted, remove {lock} and retry.")
        started = []
        try:
            try:
                import slack_manifest_migrations
            except ImportError:
                from scripts import slack_manifest_migrations
            manifest_changed = slack_manifest_migrations.reconcile(home, config_path, values)
            display.info_row(
                "Slack app",
                "Permissions migrated" if manifest_changed else "Permissions current",
                good=True,
            )
            url = os.getenv("MFS_URL", "http://127.0.0.1:13619")
            replace_unmanaged_local_mfs(home, url)
            if not healthy(url):
                if url.rstrip("/") not in ("http://localhost:13619", "http://127.0.0.1:13619"):
                    raise RuntimeError("Configured MFS endpoint is unavailable; start that server first")
                executable = mfs_server_executable()
                if not executable:
                    raise RuntimeError("MFS server is unavailable; run ./install.sh --dependencies-only")
                if start_process(home, "mfs", [executable, "run"]):
                    started.append("mfs")
                attempts = int(os.getenv("OPENTAG_MFS_STARTUP_ATTEMPTS", "90"))
                for _ in range(attempts):
                    if healthy(url):
                        break
                    if process_for(home / "state/mfs.json") is None:
                        detail = log_tail(home, "mfs")
                        raise RuntimeError(
                            "MFS exited before becoming healthy"
                            + (f":\n{detail}" if detail else "; run tag logs")
                        )
                    time.sleep(1)
                else:
                    detail = log_tail(home, "mfs")
                    raise RuntimeError(
                        f"MFS did not become healthy within {attempts} seconds"
                        + (f":\n{detail}" if detail else "; run tag logs")
                    )
            display.info_row("Memory", "Healthy", good=True)
            if os.getenv("SLACK_CHANNEL_POLICY") == "invited":
                reconcile_invitation_memory(home)
            else:
                sync_configured_slack_memory()
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
            lock.rmdir()
    return 0


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
