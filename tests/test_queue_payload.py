"""The queue-status payload progress keys (textbook program Wave 3).

Pins the mapping from the migration 038 progress columns onto the
documented entry: real ints pass, NULL and junk answer None, and the
elapsed and timestamp shaping stays untouched.
"""

from __future__ import annotations

from typing import Any

from corpus_kb.tools.queue_payload import PROGRESS_KEYS, queue_entry_payload
from tests.cli_fakes import EPOCH, QUEUE_ID, queue_row


def test_progress_keys_map_real_ints() -> None:
    entry = queue_entry_payload(queue_row(pages_done=3, pages_total=250, chunks=42))
    assert entry["pages_done"] == 3
    assert entry["pages_total"] == 250
    assert entry["chunks"] == 42


def test_progress_keys_stay_null_for_blank_rows() -> None:
    entry = queue_entry_payload(queue_row())
    assert entry["pages_done"] is None
    assert entry["pages_total"] is None
    assert entry["chunks"] is None


def test_progress_keys_survive_stub_shapes_without_the_columns() -> None:
    entry = queue_entry_payload({"id": QUEUE_ID, "queued_at": EPOCH})
    assert set(PROGRESS_KEYS) <= set(entry)
    assert all(entry[key] is None for key in PROGRESS_KEYS)


def test_progress_keys_tolerate_junk() -> None:
    junk: dict[str, Any] = {"pages_done": "7", "pages_total": True, "chunks": 9.5}
    entry = queue_entry_payload({**queue_row(), **junk})
    assert entry["pages_done"] is None
    assert entry["pages_total"] is None  # a bool is not a count
    assert entry["chunks"] is None


def test_progress_fill_leaves_the_elapsed_shaping_alone() -> None:
    from datetime import datetime, timedelta

    started = EPOCH
    finished = started + timedelta(seconds=5)
    entry = queue_entry_payload(
        queue_row(started_at=started, finished_at=finished, pages_total=250, chunks=42)
    )
    assert entry["elapsed_seconds"] == 5.0
    assert entry["queued_at"] == started.isoformat()
    live = queue_entry_payload(queue_row(started_at=started, chunks=1))
    assert isinstance(live["elapsed_seconds"], float) and live["elapsed_seconds"] >= 0.0
    assert datetime.fromisoformat(str(live["queued_at"])) == started
