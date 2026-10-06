"""Lifecycle locks released by the OS even when a CLI process is killed."""
from __future__ import annotations

import os
import time
from pathlib import Path


class LockBusy(RuntimeError):
    """Another live process holds the lock; waiting may help."""


class LifecycleLock:
    def __init__(self, path: Path, *, shared: bool = False):
        self.path = path
        # Shared holders exclude exclusive holders but not each other. Windows
        # has no shared byte-range lock in msvcrt, so it stays exclusive there.
        self.shared = shared and os.name != "nt"
        self.handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Keep the guard inode: unlinking it allows two concurrent owners.
        guard = self.path.with_name(self.path.name + ".guard")
        self.handle = os.fdopen(os.open(guard, os.O_RDWR | os.O_CREAT, 0o600), "r+b")
        try:
            if os.name == "nt":
                import msvcrt
                # Windows can lock a byte beyond EOF. Take the lock before
                # writing: another caller may already own even an empty guard.
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), (fcntl.LOCK_SH if self.shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise LockBusy("Another lifecycle operation is in progress; retry when it finishes") from None
        if self.shared:
            # The marker directory and legacy recovery belong to exclusive owners,
            # but an older CLI holds only the directory: never share with it.
            try:
                self.handle.seek(0)
                if self.path.exists() and self.handle.read() != b"tag-lifecycle-lock-v1":
                    self._check_legacy_owner()
            except Exception:
                self._unlock()
                raise
            return self
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
            if not self.shared:
                self.path.rmdir()
        finally:
            self._unlock()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


def acquire_all(paths, *, shared=(), wait: float = 0.0, waiting=None, sleep=time.sleep, clock=time.monotonic):
    """Take every lock in order, all or none, waiting up to `wait` seconds while another operation holds one.

    Paths in `shared` are taken in shared mode. Returns the held locks and whether this call had to wait. `waiting` is called once, when waiting starts.
    """
    deadline = clock() + wait
    waited = False
    while True:
        held = []
        try:
            for path in paths:
                held.append(LifecycleLock(path, shared=path in shared).acquire())
            return held, waited
        except LockBusy:
            for lock in reversed(held):
                lock.release()
            if clock() >= deadline:
                raise
            if not waited and waiting is not None:
                waiting()
            waited = True
            sleep(0.5)
        except BaseException:
            for lock in reversed(held):
                lock.release()
            raise
