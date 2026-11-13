"""Process checks and shutdown for detached study servers (issue #351).

Liveness: ``os.kill(pid, 0)`` on POSIX, ``OpenProcess`` +
``GetExitCodeProcess`` on Windows (``os.kill`` on Windows terminates the
process, so it must never probe). Shutdown: polite first, then forced
after the grace, ending the whole process tree on Windows.

Stdlib only.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import time


def as_pid(pid: object) -> int | None:
    """A usable pid int, or None for junk (None, 0, negatives, words)."""
    if isinstance(pid, bool) or pid is None:
        return None
    if isinstance(pid, int):
        value = pid
    elif isinstance(pid, str) and pid.isdigit():
        value = int(pid)
    else:
        return None
    return value if value > 0 else None


def pid_alive(pid: object) -> bool:
    """True when a live process holds this pid."""
    pid_int = as_pid(pid)
    if pid_int is None:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid_int)
    try:
        os.kill(pid_int, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:
    import ctypes

    process_query_limited = 0x1000
    still_active = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(process_query_limited, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def terminate_pid(pid: int, grace: float) -> None:
    """Stop one pid: polite first, then forced after the grace."""
    if os.name == "nt":
        _terminate_windows(pid, grace)
    else:
        _terminate_posix(pid, grace)


def _terminate_posix(pid: int, grace: float) -> None:
    import signal

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + max(0.0, grace)
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.2)
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)


def _terminate_windows(pid: int, grace: float) -> None:
    # Detached console processes ignore WM_CLOSE, so the polite pass rarely
    # lands; the forced pass ends the whole process tree.
    subprocess.run(
        ["taskkill", "/PID", str(pid)], capture_output=True, check=False, timeout=10
    )
    deadline = time.monotonic() + max(0.0, grace)
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.2)
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        check=False,
        timeout=10,
    )
