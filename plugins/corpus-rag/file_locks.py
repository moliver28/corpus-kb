"""Cross-process advisory file locks: msvcrt on Windows, fcntl on POSIX.

The hook state files and the ingest queue are shared by several runs
(hooks, CLI commands) on one machine. Locks live on a ``.lock`` sidecar
and use one locked byte at position 0. A holder blocks other holders
until release; a caller that cannot take the lock inside its wait window
gets ``LockBusy`` (issue #352).

Stdlib only.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl

WAIT_SECONDS_DEFAULT = 5.0
RETRY_SLEEP = 0.02


class LockBusy(Exception):
    """Another run holds the lock and kept it past the wait window."""


def _try_lock(handle: object) -> bool:
    """One non-blocking attempt on byte 0. True when taken."""
    if os.name == "nt":
        handle.seek(0)  # type: ignore[attr-defined]
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        except OSError:
            return False
        return True
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
    except OSError:
        return False
    return True


def _unlock(handle: object) -> None:
    with contextlib.suppress(OSError):
        if os.name == "nt":
            handle.seek(0)  # type: ignore[attr-defined]
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


@contextlib.contextmanager
def exclusive(path: Path, wait: float = WAIT_SECONDS_DEFAULT) -> Iterator[None]:
    """Hold an exclusive lock on ``path``'s sidecar, or raise ``LockBusy``.

    The sidecar file is created when missing. Holders must keep the
    critical section short: hooks wait at most a few seconds.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait
    with open(path, "a+b") as handle:
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                raise LockBusy(f"Another run holds the lock at {path}.")
            time.sleep(RETRY_SLEEP)
        try:
            yield
        finally:
            _unlock(handle)
