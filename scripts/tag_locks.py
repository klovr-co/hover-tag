"""Lifecycle locks released by the OS even when a CLI process is killed."""
from __future__ import annotations

import os
from pathlib import Path


class LifecycleLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Keep the guard inode: unlinking it allows two concurrent owners.
        guard = self.path.with_name(self.path.name + ".guard")
        self.handle = os.fdopen(os.open(guard, os.O_RDWR | os.O_CREAT, 0o600), "r+b")
        try:
            if os.name == "nt":
                import msvcrt
                if guard.stat().st_size == 0:
                    self.handle.write(b"0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise RuntimeError("Another lifecycle operation is in progress; retry when it finishes") from None
        try:
            self.handle.seek(0)
            managed = self.handle.read() == b"tag-lifecycle-lock-v1"
            if self.path.exists():
                if not managed:
                    self._check_legacy_owner()
                # Only empty legacy directory locks can be recovered.
                self.path.rmdir()
            self.handle.seek(0)
            self.handle.write(b"tag-lifecycle-lock-v1")
            self.handle.truncate()
            self.handle.flush()
            self.path.mkdir(mode=0o700)
        except Exception:
            self._unlock()
            raise
        return self

    def _check_legacy_owner(self):
        import psutil
        ancestors = {p.pid for p in psutil.Process().parents()} | {os.getpid()}
        username = psutil.Process().username()
        for process in psutil.process_iter(["pid", "cmdline", "username"]):
            if process.pid in ancestors:
                continue
            command = process.info.get("cmdline")
            if command is None and process.info.get("username") != username:
                continue
            if command is None:
                raise RuntimeError("Cannot verify an interrupted lifecycle lock's owner; retry with process visibility")
            if any(Path(part).name in {"tag_cli.py", "tag-launch.py", "tag_install.py"} for part in command):
                raise RuntimeError("Another lifecycle operation may own the legacy lock; retry when it finishes")

    def _unlock(self):
        if self.handle is not None:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            self.handle.close()
            self.handle = None

    def release(self):
        try:
            self.path.rmdir()
        finally:
            self._unlock()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
