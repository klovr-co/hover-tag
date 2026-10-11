"""Progress of the built-in search model download, kept where every Tag surface can read it.

The installer starts the download in the background (preload_mfs_model.py --progress) so
onboarding does not wait on 560 MB. The script writes `model-download.json` next to Tag's
memory files; `tag memory status`, Tag.app and the terminal read it from there.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

NAME = "model-download.json"
MB = 1_048_576  # what disk tools and the docs call a MB


def path(shared: Path) -> Path:
    return Path(shared) / NAME


def write(shared: Path, state: str, *, model: str = "", done: int = 0, total: int = 0,
          error: str = "", pid: int | None = None) -> None:
    shared = Path(shared)
    shared.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = {"state": state, "model": model, "done": done, "total": total, "error": error, "pid": pid}
    handle, name = tempfile.mkstemp(dir=shared, prefix=".model-download-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(record, stream)
        os.replace(name, path(shared))
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def read(shared: Path) -> dict | None:
    try:
        record = json.loads(path(shared).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True


def _megabytes(size: int) -> int:
    return round(size / MB)


def report(shared: Path) -> dict | None:
    """The stored progress with a percentage and a sentence to show, or None before any download."""
    record = read(shared)
    if record is None:
        return None
    state, model = str(record.get("state", "")), str(record.get("model", ""))
    done, total = int(record.get("done") or 0), int(record.get("total") or 0)
    error = str(record.get("error") or "")
    if state == "downloading" and not pid_alive(record.get("pid")):
        state, error = "failed", "the download was interrupted"
    percent = 100 if state == "ready" else (min(99, done * 100 // total) if total else 0)
    if state == "downloading":
        size = f": {_megabytes(done)} of {_megabytes(total)} MB" if total else ""
        message = (f"Downloading the search model{f' ({model})' if model else ''}{size}. "
                   "Memory search isn't available until it finishes.")
    elif state == "failed":
        message = (f"The search model{f' ({model})' if model else ''} didn't finish downloading: {error}. "
                   "Memory search isn't available; run tag memory download to try again.")
    else:
        message = ""
    return {"state": state, "model": model, "done": done, "total": total, "percent": percent,
            "error": error if state == "failed" else "", "message": message}


def active(shared: Path) -> bool:
    current = report(shared)
    return bool(current and current["state"] == "downloading")


class _Bar:
    def __init__(self, recorder: "Recorder", total: int):
        self.recorder, self.total, self.done = recorder, total, 0

    def advance(self, amount: int) -> None:
        self.done += amount
        self.recorder.publish(final=bool(self.total) and self.done >= self.total)


class Recorder:
    """Adds up the files of one model download and writes the running total, at most twice a second."""

    def __init__(self, shared: Path, model: str, *, clock: Callable[[], float] = time.monotonic):
        self.shared, self.model, self.clock = Path(shared), model, clock
        self.bars: list[_Bar] = []
        self.last: float | None = None

    def bar(self, total: int) -> _Bar:
        bar = _Bar(self, int(total or 0))
        self.bars.append(bar)
        return bar

    def publish(self, *, final: bool = False) -> None:
        now = self.clock()
        if not final and self.last is not None and now - self.last < 0.5:
            return
        self.last = now
        write(self.shared, "downloading", model=self.model, pid=os.getpid(),
              done=sum(bar.done for bar in self.bars), total=sum(bar.total for bar in self.bars))

    def hook(self) -> None:
        """Make every Hugging Face file download in this process report here."""
        import huggingface_hub
        from huggingface_hub.utils import tqdm as Base

        recorder = self

        class Progress(Base):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                # Hugging Face's Xet downloads draw two bars per file: the transfer, and a second one that
                # reassembles the file from it. Only the transfer counts, or every size would be doubled.
                reassembling = str(kwargs.get("name") or "").endswith("xet_get")
                self._tag = None if reassembling else recorder.bar(kwargs.get("total") or 0)

            def update(self, n=1):
                result = super().update(n)
                if self._tag is not None:
                    self._tag.advance(int(n or 0))
                return result

        original = huggingface_hub.hf_hub_download

        def download(*args, **kwargs):
            kwargs.setdefault("tqdm_class", Progress)
            return original(*args, **kwargs)

        huggingface_hub.hf_hub_download = download


def start_background(python: str | Path, script: str | Path, home: Path, environment: dict[str, str]) -> None:
    """Run the preload script on its own, so it keeps going after the caller exits."""
    shared = Path(home) / "shared/mfs"
    shared.mkdir(parents=True, exist_ok=True, mode=0o700)
    options: dict = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    # Marked first, so a status read right after this call already shows the download.
    with open(shared / "model-download.log", "ab") as log:
        process = subprocess.Popen(
            [str(python), str(script), "--progress"], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            env=dict(environment, TAG_HOME=str(home)), close_fds=True, **options)
    current = read(shared)
    if not current or current.get("state") != "downloading" or not pid_alive(current.get("pid")):
        write(shared, "downloading", model=str((current or {}).get("model") or ""), pid=process.pid)
