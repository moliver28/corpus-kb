"""Behavior tests for the cross-platform file lock (issue #352).

Windows locks use ``msvcrt.locking``; POSIX uses ``fcntl.flock``. Each
platform tests its native path: the msvcrt tests skip off Windows and the
fcntl tests skip on Windows, so CI (ubuntu) and this Windows machine each
prove the path they run.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

from file_locks import LockBusy, exclusive  # noqa: E402

IS_WINDOWS = os.name == "nt"


def _second_lock_takes(path: Path, results: list[str], wait: float) -> None:
    try:
        with exclusive(path, wait=wait):
            results.append("acquired")
    except LockBusy:
        results.append("busy")


def test_lock_held_blocks_second_holder_until_release(tmp_path: Path) -> None:
    path = tmp_path / "state.lock"
    results: list[str] = []
    with exclusive(path, wait=5.0):
        worker = threading.Thread(target=_second_lock_takes, args=(path, results, 0.2))
        worker.start()
        worker.join(timeout=5.0)
    assert results == ["busy"]


def test_lock_free_after_release(tmp_path: Path) -> None:
    path = tmp_path / "state.lock"
    with exclusive(path, wait=5.0):
        pass
    results: list[str] = []
    _second_lock_takes(path, results, 5.0)
    assert results == ["acquired"]


def test_lock_creates_sidecar_and_parent(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "nest" / "state.lock"
    with exclusive(path, wait=1.0):
        assert path.is_file()


def test_lock_creates_parent_only_once(tmp_path: Path) -> None:
    path = tmp_path / "sidecar.lock"
    with exclusive(path):
        pass
    with exclusive(path):
        pass
    assert path.is_file()


@pytest.mark.skipif(not IS_WINDOWS, reason="msvcrt path is Windows-native")
def test_windows_msvcrt_lock_is_exclusive_across_handles(tmp_path: Path) -> None:
    """Native proof: msvcrt.locking refuses a second handle's lock attempt."""
    import msvcrt

    path = tmp_path / "win.lock"
    with exclusive(path, wait=1.0):
        second = open(path, "a+b")
        try:
            second.seek(0)
            with pytest.raises(OSError):
                msvcrt.locking(second.fileno(), msvcrt.LK_NBLCK, 1)
        finally:
            second.close()


@pytest.mark.skipif(IS_WINDOWS, reason="fcntl path is POSIX-native")
def test_posix_flock_is_exclusive_across_handles(tmp_path: Path) -> None:
    import fcntl

    path = tmp_path / "posix.lock"
    with exclusive(path, wait=1.0):
        second = open(path, "a+b")
        try:
            with pytest.raises(OSError):
                fcntl.flock(second.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            second.close()


def test_lock_busy_raises_inside_wait(tmp_path: Path) -> None:
    path = tmp_path / "busy.lock"
    stop = threading.Event()
    holder = threading.Thread(target=lambda: stop.wait(2.0) or None)

    def hold() -> None:
        with exclusive(path, wait=2.0):
            stop.wait(1.0)

    holder = threading.Thread(target=hold)
    holder.start()
    holder.join(timeout=0.2)
    with pytest.raises(LockBusy):
        with exclusive(path, wait=0.1):
            pass
    stop.set()
    holder.join(timeout=5.0)
