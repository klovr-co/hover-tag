# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""Keep chosen Tags running: each Tag remembers whether it should be on, and a
small per-user login service starts those Tags and restarts them if they stop.

`tag start` and `tag stop` record the choice, so the CLI, Tag.app and the
service always agree. The service is the operating system's own mechanism:
a launchd agent on macOS, a systemd user service on Linux (or an XDG
autostart entry without systemd), and the per-user Run key on Windows.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import tag_instances
    from tag_locks import LifecycleLock
except ImportError:
    from scripts import tag_instances
    from scripts.tag_locks import LifecycleLock

INTENT = "state/keep-running.json"
LABEL = "team.hover.tag.supervisor"
WINDOWS_VALUE = "Tag"
CHECK_SECONDS = 60
MAX_BACKOFF_SECONDS = 3600
# Exit status the service manager treats as "start me again with new code".
RELOAD_EXIT = 75


def wanted(home: Path) -> bool | None:
    """Whether the person last asked this Tag to run; None if never recorded."""
    try:
        record = json.loads((home / INTENT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = record.get("running") if isinstance(record, dict) else None
    return value if isinstance(value, bool) else None


def set_wanted(home: Path, running: bool) -> None:
    path = home / INTENT
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"version": 1, "running": running}) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def service_command(installation_root: Path, source_root: Path) -> list[str]:
    """The command the login service runs, stable across upgrades.

    Managed installs go through the fixed launcher, which always selects the
    current release. A source checkout runs its own CLI directly.
    """
    launcher = installation_root / "bin/tag-launch.py"
    current = installation_root / "current.json"
    if launcher.is_file() and current.is_file():
        if os.name == "nt":
            # The launcher only needs Python 3; pythonw keeps it windowless.
            base = Path(getattr(sys, "_base_executable", sys.executable))
            windowless = base.with_name("pythonw.exe")
            python = windowless if windowless.is_file() else base
            return [str(python), str(launcher), "autostart", "run"]
        bin_dir = json.loads(current.read_text(encoding="utf-8")).get("bin_dir")
        command = Path(bin_dir) / "tag" if bin_dir else None
        if command and command.is_file():
            return ["/bin/sh", str(command), "autostart", "run"]
        return [sys.executable, str(launcher), "autostart", "run"]
    return [sys.executable, str(source_root / "scripts/tag_cli.py"), "autostart", "run"]


def mechanism() -> str:
    if sys.platform == "darwin":
        return "launchd"
    if os.name == "nt":
        return "windows-run"
    return "systemd" if _systemd_available() else "xdg-autostart"


def _systemd_available() -> bool:
    if not shutil.which("systemctl"):
        return False
    probe = subprocess.run(["systemctl", "--user", "show-environment"],
                           capture_output=True, text=True, check=False)
    return probe.returncode == 0


def _suffix(installation_root: Path) -> str:
    """Empty for the normal installation; a short stable ID for any other TAG_HOME,
    so a development or test home never replaces the real service."""
    try:
        from tag_paths import native_installation
    except ImportError:
        from scripts.tag_paths import native_installation
    if native_installation(installation_root):
        return ""
    import hashlib
    return "-" + hashlib.sha256(str(installation_root.resolve()).encode()).hexdigest()[:8]


def label(installation_root: Path) -> str:
    return LABEL + _suffix(installation_root).replace("-", ".")


def _launchd_plist(installation_root: Path) -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{label(installation_root)}.plist"


def _systemd_name(installation_root: Path) -> str:
    return f"tag-supervisor{_suffix(installation_root)}.service"


def _systemd_unit(installation_root: Path) -> Path:
    config = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config / "systemd/user" / _systemd_name(installation_root)


def _xdg_entry(installation_root: Path) -> Path:
    config = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config / f"autostart/tag-supervisor{_suffix(installation_root)}.desktop"


def _windows_value(installation_root: Path) -> str:
    return WINDOWS_VALUE + _suffix(installation_root)


def _environment(installation_root: Path) -> dict[str, str]:
    return {"TAG_HOME": str(installation_root)}


def launchd_plist(command: list[str], installation_root: Path, log: Path) -> str:
    from xml.sax.saxutils import escape
    arguments = "".join(f"<string>{escape(part)}</string>" for part in command)
    environment = "".join(f"<key>{escape(k)}</key><string>{escape(v)}</string>"
                          for k, v in _environment(installation_root).items())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f"<key>Label</key><string>{label(installation_root)}</string>\n"
        f"<key>ProgramArguments</key><array>{arguments}</array>\n"
        f"<key>EnvironmentVariables</key><dict>{environment}</dict>\n"
        "<key>RunAtLoad</key><true/>\n"
        "<key>KeepAlive</key><true/>\n"
        "<key>ThrottleInterval</key><integer>30</integer>\n"
        "<key>ProcessType</key><string>Background</string>\n"
        f"<key>StandardOutPath</key><string>{escape(str(log))}</string>\n"
        f"<key>StandardErrorPath</key><string>{escape(str(log))}</string>\n"
        "</dict></plist>\n"
    )


def systemd_unit(command: list[str], installation_root: Path) -> str:
    environment = " ".join(f'"{k}={v}"' for k, v in _environment(installation_root).items())
    return (
        "[Unit]\nDescription=Tag: keep chosen Tags running\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        f"ExecStart={shlex.join(command)}\n"
        f"Environment={environment}\n"
        "Restart=always\nRestartSec=30\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def xdg_entry(command: list[str], installation_root: Path) -> str:
    exec_line = shlex.join(["env", *(f"{k}={v}" for k, v in _environment(installation_root).items()), *command])
    return ("[Desktop Entry]\nType=Application\nName=Tag\n"
            "Comment=Keep chosen Tags running\n"
            f"Exec={exec_line}\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n")


def _windows_line(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def status(installation_root: Path) -> dict:
    kind = mechanism()
    if kind == "windows-run":
        path, enabled = None, _windows_get(_windows_value(installation_root)) is not None
    else:
        path = {"launchd": _launchd_plist, "systemd": _systemd_unit,
                "xdg-autostart": _xdg_entry}[kind](installation_root)
        enabled = path.is_file()
    return {"enabled": enabled, "mechanism": kind,
            "path": str(path) if path else None,
            "log": str(installation_root / "state/supervisor.log")}


def enable(installation_root: Path, source_root: Path) -> dict:
    """Register the login service and start it now. Safe to repeat."""
    command = service_command(installation_root, source_root)
    log = installation_root / "state/supervisor.log"
    log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    kind = mechanism()
    if kind == "launchd":
        plist = _launchd_plist(installation_root)
        plist.parent.mkdir(parents=True, exist_ok=True)
        _write(plist, launchd_plist(command, installation_root, log))
        domain = f"gui/{os.getuid()}"
        # Reload so a changed command takes effect; bootout fails harmlessly when absent.
        subprocess.run(["launchctl", "bootout", f"{domain}/{label(installation_root)}"],
                       capture_output=True, check=False)
        _check(subprocess.run(["launchctl", "bootstrap", domain, str(plist)],
                              capture_output=True, text=True, check=False), "launchctl bootstrap")
    elif kind == "systemd":
        unit, name = _systemd_unit(installation_root), _systemd_name(installation_root)
        unit.parent.mkdir(parents=True, exist_ok=True)
        _write(unit, systemd_unit(command, installation_root))
        _check(subprocess.run(["systemctl", "--user", "daemon-reload"],
                              capture_output=True, text=True, check=False), "systemctl daemon-reload")
        _check(subprocess.run(["systemctl", "--user", "enable", "--now", name],
                              capture_output=True, text=True, check=False), "systemctl enable")
        subprocess.run(["systemctl", "--user", "restart", name], capture_output=True, check=False)
    elif kind == "xdg-autostart":
        entry = _xdg_entry(installation_root)
        entry.parent.mkdir(parents=True, exist_ok=True)
        _write(entry, xdg_entry(command, installation_root))
        _spawn(command, installation_root, log)
    else:
        _windows_set(_windows_value(installation_root), _windows_line(command))
        _spawn(command, installation_root, log)
    return status(installation_root)


def disable(installation_root: Path) -> dict:
    """Remove the login service. Running Tags are left as they are."""
    kind = mechanism()
    if kind == "launchd":
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label(installation_root)}"],
                       capture_output=True, check=False)
        _launchd_plist(installation_root).unlink(missing_ok=True)
    elif kind == "systemd":
        subprocess.run(["systemctl", "--user", "disable", "--now", _systemd_name(installation_root)],
                       capture_output=True, check=False)
        _systemd_unit(installation_root).unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, check=False)
    elif kind == "xdg-autostart":
        _xdg_entry(installation_root).unlink(missing_ok=True)
    else:
        _windows_delete(_windows_value(installation_root))
    _stop_supervisor(installation_root)
    return status(installation_root)


def _write(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _check(result: subprocess.CompletedProcess, action: str) -> None:
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{action} failed: {detail or 'exit ' + str(result.returncode)}")


def _spawn(command: list[str], installation_root: Path, log: Path) -> None:
    options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
               if os.name == "nt" else {"start_new_session": True})
    with log.open("ab") as output:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                         env={**os.environ, **_environment(installation_root)}, **options)


def _windows_key():
    import winreg
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                          r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                          winreg.KEY_READ | winreg.KEY_SET_VALUE)


def _windows_get(name: str) -> str | None:
    if os.name != "nt":
        return None
    import winreg
    with _windows_key() as key:
        try:
            return winreg.QueryValueEx(key, name)[0]
        except FileNotFoundError:
            return None


def _windows_set(name: str, line: str) -> None:
    import winreg
    with _windows_key() as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, line)


def _windows_delete(name: str) -> None:
    import winreg
    with _windows_key() as key:
        try:
            winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass


def _pid_path(installation_root: Path) -> Path:
    return installation_root / "state/supervisor.json"


def _stop_supervisor(installation_root: Path) -> None:
    """Stop a supervisor this installation started (Windows, XDG), by verified PID."""
    try:
        import psutil
        record = json.loads(_pid_path(installation_root).read_text(encoding="utf-8"))
        process = psutil.Process(record["pid"])
        if process.create_time() == record["created"]:
            process.terminate()
    except Exception:
        pass
    _pid_path(installation_root).unlink(missing_ok=True)


def seed_from_running(installation_root: Path, lifecycle) -> list[str]:
    """Before any choice is recorded, treat the Tags running now as wanted."""
    contexts = [tag_instances.resolve(installation_root, str(item["id"]))
                for item in tag_instances.discover(installation_root)
                if item.get("valid") and Path(str(item["home"])).exists()]
    if any(wanted(context.home) is not None for context in contexts):
        return []
    seeded = []
    for context in contexts:
        if lifecycle.process_for(context.home / "state/slack.json") is not None:
            set_wanted(context.home, True)
            seeded.append(context.tag_id)
    return seeded


def _release(installation_root: Path) -> str | None:
    try:
        return json.loads((installation_root / "current.json").read_text(encoding="utf-8")).get("release")
    except (OSError, ValueError):
        return None


class Supervisor:
    """Start wanted Tags that aren't running, backing off after failures."""

    def __init__(self, installation_root: Path, lifecycle, start, *, clock=time.monotonic):
        self.root = installation_root
        self.lifecycle = lifecycle
        self.start = start  # (tag_id) -> exit code
        self.clock = clock
        self.failures: dict[str, int] = {}
        self.retry_at: dict[str, float] = {}

    def check(self) -> list[str]:
        """One pass. Returns the Tags it tried to start."""
        attempted = []
        for item in tag_instances.discover(self.root):
            if not item.get("valid") or not Path(str(item["home"])).exists():
                continue
            context = tag_instances.resolve(self.root, str(item["id"]))
            tag_id = context.tag_id
            if wanted(context.home) is not True:
                self.failures.pop(tag_id, None)
                continue
            if self.lifecycle.process_for(context.home / "state/slack.json") is not None:
                self.failures.pop(tag_id, None)
                continue
            if self.clock() < self.retry_at.get(tag_id, 0):
                continue
            attempted.append(tag_id)
            if self.start(tag_id) == 0:
                self.failures.pop(tag_id, None)
            else:
                count = self.failures[tag_id] = self.failures.get(tag_id, 0) + 1
                self.retry_at[tag_id] = self.clock() + min(CHECK_SECONDS * 2 ** count, MAX_BACKOFF_SECONDS)
        return attempted


def run(installation_root: Path, lifecycle, source_root: Path) -> int:
    """The login service's main loop."""
    try:
        lock = LifecycleLock(installation_root / "state/supervisor.lock").acquire()
    except RuntimeError:
        print("Another Tag supervisor is already running.", flush=True)
        return 0
    try:
        import psutil
        me = psutil.Process()
        _write(_pid_path(installation_root), json.dumps({"pid": me.pid, "created": me.create_time()}))
        command = service_command(installation_root, source_root)[:-2]

        def start(tag_id: str) -> int:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} starting {tag_id}", flush=True)
            # The supervisor has no console; don't let its children open one on Windows.
            hidden = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            result = subprocess.run([*command, tag_id, "start"], stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, check=False, **hidden)
            if result.returncode:
                tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
                print(f"  {tag_id} did not start (exit {result.returncode}): {' / '.join(tail)}", flush=True)
            return result.returncode

        supervisor = Supervisor(installation_root, lifecycle, start)
        release = _release(installation_root)
        while True:
            supervisor.check()
            time.sleep(CHECK_SECONDS)
            if _release(installation_root) != release:
                # An upgrade changed the code; let the service manager start the new one.
                print("Tag was upgraded; restarting the supervisor.", flush=True)
                if mechanism() in {"windows-run", "xdg-autostart"}:
                    lock.release()
                    _spawn(service_command(installation_root, source_root), installation_root,
                           installation_root / "state/supervisor.log")
                    return 0
                return RELOAD_EXIT
    finally:
        lock.release()
