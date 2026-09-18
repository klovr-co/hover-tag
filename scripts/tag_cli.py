"""Cross-platform TAG lifecycle. Installed launchers use the release's Python."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import warnings
from pathlib import Path

try:
    from tag_paths import codex_workspace_args, initialize, runtime_environment, tag_home
except ImportError:
    from scripts.tag_paths import codex_workspace_args, initialize, runtime_environment, tag_home

ROOT = Path(__file__).resolve().parents[1]


def read_config(path: Path) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
        raise ValueError("TAG configuration must be a JSON object of string values")
    if any(not (k.startswith("OPENTAG_") or k.startswith("SLACK_") or k.startswith("MFS_")) for k in value):
        raise ValueError("Configuration keys must start with OPENTAG_, SLACK_, or MFS_")
    return value


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


def start_process(home: Path, name: str, command: list[str]) -> bool:
    import psutil
    record = home / "state" / f"{name}.json"
    if process_for(record):
        return False
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS} if os.name == "nt" else {"start_new_session": True}
    with (home / "state" / f"{name}.log").open("ab") as log:
        child = subprocess.Popen(command, cwd=home / "workspace", stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, **options)
    process = psutil.Process(child.pid)
    record.write_text(json.dumps({"pid": child.pid, "created": process.create_time()}), encoding="utf-8")
    time.sleep(0.3)
    if child.poll() is not None:
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


def healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def doctor(home: Path, offline: bool) -> int:
    print(f"TAG home: {home}", flush=True)
    print(f"Agent workspace: {home / 'workspace'}", flush=True)
    print(f"Codex skills: {home / 'workspace/.agents/skills'}", flush=True)
    print(f"Codex MCP: {home / 'workspace/.codex/config.toml'}", flush=True)
    print(f"Claude skills / MCP: {home / 'workspace/.claude/skills'} / {home / 'workspace/.mcp.json'}", flush=True)
    definitions = codex_workspace_args(home / "workspace")
    print(f"TAG Codex MCP definitions: {len(definitions) // 2} (configuration parsed; connectivity checked by backend)", flush=True)
    cmd = [sys.executable, str(ROOT / "scripts/opentag_doctor.py")]
    if offline:
        cmd.append("--offline")
    elif os.getenv("SLACK_CHANNEL_ID"):
        cmd.extend(["--channel-id", os.environ["SLACK_CHANNEL_ID"]])
    return subprocess.call(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("setup", "migrate", "rollback", "version", "paths", "doctor", "start", "stop", "status", "logs"))
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--from", dest="source", type=Path)
    args = parser.parse_args()
    home = tag_home()
    if args.command == "version":
        print("Tag v" + (ROOT / "VERSION").read_text().strip())
        return 0
    if args.command == "paths":
        print(json.dumps({key: str(home / key) for key in ("config", "workspace", "integrations", "state", "tmp", "releases")}, indent=2))
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
        return subprocess.call([sys.executable, str(ROOT / "scripts/opentag_setup.py"), "--config", str(config_path)])
    if args.command in ("start", "doctor", "status") and config_path.is_file() and config_path.stat().st_size:
        os.environ.update(read_config(config_path))
    elif args.command == "start" or (args.command == "doctor" and not args.offline):
        raise RuntimeError(f"Missing configuration: {config_path}. Run tag setup.")
    # A TAG installation always has one stable integration workspace.
    os.environ["OPENTAG_WORKDIR"] = str(home / "workspace")
    if args.command == "doctor":
        return doctor(home, args.offline)
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
    if args.command == "status":
        for name in ("slack", "mfs"):
            print(f"{name}: {'running' if process_for(home / 'state' / (name + '.json')) else 'stopped (TAG-managed)'}")
        print(f"MFS endpoint: {'healthy' if healthy(os.getenv('MFS_URL', 'http://127.0.0.1:13619')) else 'unavailable'}")
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
                executable = Path(sys.executable).parent / ("mfs-server.exe" if os.name == "nt" else "mfs-server")
                if start_process(home, "mfs", [str(executable), "run"]):
                    started.append("mfs")
                for _ in range(30):
                    if healthy(url):
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError("MFS did not become healthy; run tag logs")
            if doctor(home, False):
                raise RuntimeError("Preflight failed")
            if start_process(home, "slack", [sys.executable, str(ROOT / "scripts/slack_socket_agent.py"), "--backend", os.environ["OPENTAG_BACKEND"]]):
                started.append("slack")
            print("TAG is running in the background. Use tag stop to stop it.")
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
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
