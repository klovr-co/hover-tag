"""Cross-platform TAG lifecycle. Installed launchers use the release's Python."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import warnings
from pathlib import Path

try:
    from tag_paths import codex_workspace_args, initialize, runtime_environment, tag_home
    from tag_config import read_config
except ImportError:
    from scripts.tag_paths import codex_workspace_args, initialize, runtime_environment, tag_home
    from scripts.tag_config import read_config

ROOT = Path(__file__).resolve().parents[1]
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
        raise RuntimeError(f"{name} exited during startup; run tag logs")
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
    return "\n".join(content.splitlines()[-lines:])


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


def doctor(home: Path, offline: bool, json_output: bool = False) -> int:
    definitions = codex_workspace_args(home / "workspace")
    if not json_output:
        print(f"TAG home: {home}", flush=True)
        print(f"Agent workspace: {home / 'workspace'}", flush=True)
        print(f"Codex skills: {home / 'workspace/.agents/skills'}", flush=True)
        print(f"Codex MCP: {home / 'workspace/.codex/config.toml'}", flush=True)
        print(f"Claude skills / MCP: {home / 'workspace/.claude/skills'} / {home / 'workspace/.mcp.json'}", flush=True)
        print(f"TAG Codex MCP definitions: {len(definitions) // 2} (configuration parsed; connectivity checked by backend)", flush=True)
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
    return subprocess.call(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Tag: set up, inspect, and manage your Slack teammate.",
                                     epilog="Use tag for status and the next step. Start with tag setup; change configuration with tag settings.")
    parser.add_argument("command", nargs="?", choices=("settings", "inspect", "config", "setup", "reset", "migrate", "rollback", "version", "paths", "doctor", "start", "stop", "restart", "status", "logs"))
    parser.add_argument("arguments", nargs="*", help="config: init | show | keys | set KEY VALUE")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output", help="structured output for inspect, status, doctor, and config")
    parser.add_argument("--stdin", action="store_true", help="read a config value from stdin")
    parser.add_argument("--from", dest="source", type=Path)
    parser.add_argument("--no-start", action="store_true", help="setup: save choices without starting services or indexing")
    parser.add_argument("--test", action="store_true", help="setup: use a separate test home; implies --no-start")
    parser.add_argument("--review", action="store_true", help="setup: review choices even when already configured")
    args = parser.parse_args()
    if (args.no_start or args.test or args.review) and args.command != "setup":
        parser.error("--no-start, --test and --review are only for setup")
    if args.arguments and args.command != "config":
        parser.error("Only config accepts additional positional arguments")
    if args.json_output and args.command not in {"inspect", "status", "doctor", "config"}:
        parser.error("--json supports inspect, status, doctor, and config")
    if args.stdin and args.command != "config":
        parser.error("--stdin is only for config set")
    if args.offline and args.command not in {"inspect", "doctor"}:
        parser.error("--offline supports inspect and doctor")
    home = tag_home()
    try:
        import tag_control as control
        import tag_config as settings
    except ImportError:
        from scripts import tag_control as control, tag_config as settings
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
        command = [sys.executable, str(ROOT / "scripts/tag_cli.py")]
        result = subprocess.call(command + ["stop"])
        return result if result else subprocess.call(command + ["start"])
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
        paths.update(admin_skill=str(ROOT / "SKILL.md"), management_guide=str(ROOT / "docs/tag-management.md"))
        print(json.dumps(paths, indent=2))
        return 0
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
        print("Previous release selected. Run tag start.")
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
            print(f"Test setup: {test_home}", flush=True)
            print("No services or indexing. Slack actions are real; existing CLI sign-ins are shared.", flush=True)
        command = [sys.executable, str(ROOT / "scripts/opentag_setup.py"), "--config", str(config_path)]
        if args.no_start or args.test:
            command.append("--no-start")
        if args.review:
            command.append("--review")
        return subprocess.call(command, env=environment)
    if args.command == "doctor" and args.json_output:
        report = control.inspect(home, sys.modules[__name__], offline=True)
        if not report["configuration"]["complete"]:
            print(json.dumps({"schema_version": 1, "ok": False, "configuration": report["configuration"],
                              "next_command": report["next_command"]}, indent=2))
            return 1
    if args.command in ("start", "doctor", "status") and config_path.is_file() and config_path.stat().st_size:
        values = read_config(config_path)
        if args.command == "start" and settings.config_errors(values):
            raise RuntimeError("Configuration is incomplete or invalid. Run tag inspect or tag setup.")
        os.environ.update(values)
    elif args.command == "start" or (args.command == "doctor" and not args.offline):
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
        for log in sorted((home / "state").glob("*.log")):
            print(f"==> {log.name} <==")
            print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-50:]))
        return 0
    if args.command == "stop":
        for name in ("slack", "mfs"):
            stop_process(home, name)
        print("TAG stopped. Independently managed MFS servers were left running.")
        return 0
    if args.command == "start":
        # Serialize starts so concurrent invocations cannot create orphan services.
        lock = home / "state/start.lock"
        try:
            lock.mkdir()
        except FileExistsError:
            raise RuntimeError(f"Another start is in progress. If interrupted, remove {lock} and retry.")
        started = []
        try:
            url = os.getenv("MFS_URL", "http://127.0.0.1:13619")
            if not healthy(url):
                if url.rstrip("/") not in ("http://localhost:13619", "http://127.0.0.1:13619"):
                    raise RuntimeError("Configured MFS endpoint is unavailable; start that server first")
                executable = mfs_server_executable()
                if not executable:
                    raise RuntimeError("MFS server is unavailable; run ./install.sh --dependencies-only")
                if start_process(home, "mfs", [executable, "run"]):
                    started.append("mfs")
                for _ in range(30):
                    if healthy(url):
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError("MFS did not become healthy; run tag logs")
            if os.getenv("SLACK_CHANNEL_POLICY") == "invited":
                reconcile_invitation_memory(home)
            else:
                sync_configured_slack_memory()
            if doctor(home, False):
                raise RuntimeError("Preflight failed")
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
            print("TAG is running in the background. Use tag stop to stop it.")
            print("Try a first mention in your Slack channel: @" + os.getenv("OPENTAG_BOT_NAME", "OpenMax") + " say hello")
            print("Service readiness passed. A successful Slack reply must still be verified in Slack.")
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
        print("\nInterrupted. Run tag setup to resume saved setup.", file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        if "--json" in sys.argv:
            print(json.dumps({"schema_version": 1, "ok": False, "error": str(exc)}))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
