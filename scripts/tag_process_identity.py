"""Match the PID a managed service reports to the process Tag recorded when starting it."""
from __future__ import annotations

from pathlib import Path


def runs_script(process, script: str) -> bool:
    return any(Path(arg).name == script for arg in process.cmdline())


def reported_by(process, pid, script: str) -> bool:
    """Whether ``pid``, reported by a service running ``script``, belongs to the recorded ``process``.

    A Windows venv's python.exe is a launcher that runs the base interpreter as a
    child, so there the service's own PID is a direct child of the recorded one.
    """
    if pid == process.pid:
        return True
    try:
        # psutil errors (the child exited, access denied) are not OSErrors.
        return any(child.pid == pid and runs_script(child, script) for child in process.children())
    except Exception:
        return False
