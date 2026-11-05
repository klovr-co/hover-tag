"""Run a Slack CLI command in this process's windowless console on Windows.

Tag starts this host with CREATE_NO_WINDOW, so it owns a console that has no
window. The Slack CLI and the hooks it starts through PowerShell inherit that
console, so none of them opens a window.

At startup the Slack CLI asks the terminal for its background color and reads
the reply from CONIN$ with no working timeout. A windowless console never
replies, so the host puts a terminal's reply in the console input first. It also
keeps the command in a job that ends with the host, so a caller's timeout ends
the whole command and its output pipes close. Standard library only.
"""
from __future__ import annotations

import subprocess
import sys

# OSC 11 (background color, black) then DA1, which ends the CLI's query.
TERMINAL_REPLY = "\x1b]11;rgb:0000/0000/0000\x07\x1b[?62c"

KEY_EVENT = 0x0001
GENERIC_READ_WRITE = 0x80000000 | 0x40000000
FILE_SHARE_READ_WRITE = 0x1 | 0x2
OPEN_EXISTING = 3
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.GetConsoleWindow.restype = wintypes.HWND
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


def answer_terminal_query(kernel32) -> bool:
    """Queue the reply in this console's input; only in a console with no window."""
    import ctypes
    from ctypes import wintypes

    if kernel32.GetConsoleWindow():
        return False  # A real terminal answers for itself; never type into it.

    class KeyEventRecord(ctypes.Structure):
        _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD),
                    ("wVirtualKeyCode", wintypes.WORD), ("wVirtualScanCode", wintypes.WORD),
                    ("UnicodeChar", wintypes.WCHAR), ("dwControlKeyState", wintypes.DWORD)]

    class Event(ctypes.Union):
        _fields_ = [("KeyEvent", KeyEventRecord), ("padding", ctypes.c_byte * 16)]

    class InputRecord(ctypes.Structure):
        _fields_ = [("EventType", wintypes.WORD), ("Event", Event)]

    conin = kernel32.CreateFileW("CONIN$", GENERIC_READ_WRITE, FILE_SHARE_READ_WRITE, None, OPEN_EXISTING, 0, None)
    if not conin or conin == wintypes.HANDLE(-1).value:
        return False  # No console: the CLI can't open one either, so it doesn't wait.
    try:
        records = (InputRecord * len(TERMINAL_REPLY))()
        for record, character in zip(records, TERMINAL_REPLY):
            record.EventType = KEY_EVENT
            record.Event.KeyEvent.bKeyDown = True
            record.Event.KeyEvent.wRepeatCount = 1
            record.Event.KeyEvent.UnicodeChar = character
        written = wintypes.DWORD()
        return bool(kernel32.WriteConsoleInputW(conin, records, len(records), ctypes.byref(written)))
    finally:
        kernel32.CloseHandle(conin)


def _job_limits(kernel32, job, flags: int) -> bool:
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = flags
    return bool(kernel32.SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                                                 ctypes.byref(limits), ctypes.sizeof(limits)))


def end_with_host(kernel32):
    """Put this host, and so everything it starts, in a job that ends when the host does."""
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    if _job_limits(kernel32, job, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE) and \
            kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        return job
    kernel32.CloseHandle(job)
    return None


def main(command: list[str]) -> int:
    if not command:
        print("usage: slack_console_host.py COMMAND [ARGUMENT ...]", file=sys.stderr)
        return 2
    job = None
    if sys.platform == "win32":
        kernel32 = _kernel32()
        job = end_with_host(kernel32)
        answer_terminal_query(kernel32)
    try:
        code = subprocess.call(command)
    except OSError as error:
        print(f"could not start {command[0]}: {error.strerror or error}", file=sys.stderr)
        return 127
    if job is not None:
        # A normal finish leaves anything the CLI started on purpose, such as a browser.
        _job_limits(kernel32, job, 0)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
