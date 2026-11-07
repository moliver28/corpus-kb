"""Atomic registry IO: one lock file, one rename, patient reads.

The registry file is shared by every corpus-rag run on the machine, so
writes take an exclusive lock file (``O_CREAT | O_EXCL``, with a stale
lock taken over) and land through ``os.replace``. On Windows a
concurrent reader's open handle can deny the rename or the open for a
moment; both sides retry before giving up.

Stdlib only.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

STALE_LOCK_SECONDS = 30.0
LOCK_WAIT_SECONDS = 10.0
RETRY_ATTEMPTS = 5


class LockBusyError(Exception):
    """Another run holds the registry lock and will not let go."""


def acquire_lock(lock: Path, wait: float = LOCK_WAIT_SECONDS) -> None:
    """Take the exclusive lock file, or raise ``LockBusyError``."""
    deadline = time.monotonic() + wait
    while True:
        try:
            handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _stale_lock(lock):
                release_lock(lock)
                continue
            if time.monotonic() > deadline:
                raise LockBusyError(
                    "Another corpus-rag run holds the registry lock. Try again in a moment."
                ) from None
            time.sleep(0.02)
            continue
        os.write(handle, str(os.getpid()).encode("utf-8"))
        os.close(handle)
        return


def release_lock(lock: Path) -> None:
    with contextlib.suppress(OSError):
        lock.unlink()


def replace_with_retry(tmp: Path, path: Path) -> None:
    """Atomic rename, retried while a concurrent reader holds the target."""
    for attempt in range(RETRY_ATTEMPTS):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == RETRY_ATTEMPTS - 1:
                raise
            time.sleep(0.02 * (attempt + 1))


def read_with_retry(path: Path) -> str:
    """Read a registry file, retried while a rename denies the open."""
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            if attempt == RETRY_ATTEMPTS - 1:
                raise
            time.sleep(0.02 * (attempt + 1))
    raise OSError(f"cannot read {path}")  # pragma: no cover - unreachable


def _stale_lock(lock: Path) -> bool:
    """A lock older than the stale window belongs to a dead run."""
    try:
        return (time.time() - lock.stat().st_mtime) > STALE_LOCK_SECONDS
    except OSError:
        return False
