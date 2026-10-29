"""Queue-row payload shaping (textbook-ingestion program, Wave 1).

One builder turns a raw ``ingest_queue`` row into the documented status
entry every surface serves: timestamps as ISO strings, elapsed seconds
from the run clock, and the nullable progress keys the Wave 2 worker
fills in. Keeping it separate holds both queue modules under the size
limit and gives the parity legs one pinned shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

#: The per-entry progress keys a worker populates; always present, null
#: until that wave lands. Declared once so the envelope stays pinned.
PROGRESS_KEYS = ("pages_done", "pages_total", "chunks")


def _iso(value: object) -> str | None:
    """One ISO timestamp, or None for absent / non-datetime values."""
    return value.isoformat() if isinstance(value, datetime) else None


def queue_entry_payload(row: dict[str, Any]) -> dict[str, object]:
    """The documented queue-status entry for one raw row.

    ``elapsed_seconds`` counts from ``started_at`` when a worker claimed
    the row, else from ``queued_at``; a finished row counts to
    ``finished_at``, a live row to now. Non-database shapes (the parity
    stubs) answer None elapsed rather than guessing.
    """
    queued_at = row.get("queued_at")
    started_at = row.get("started_at")
    finished_at = row.get("finished_at")
    reference = started_at if isinstance(started_at, datetime) else queued_at
    elapsed: float | None = None
    if isinstance(reference, datetime):
        end = finished_at if isinstance(finished_at, datetime) else datetime.now(UTC)
        elapsed = round((end - reference).total_seconds(), 3)
    payload: dict[str, object] = {
        "id": row.get("id"),
        "path": row.get("path"),
        "title": row.get("title"),
        "status": row.get("status"),
        "attempts": row.get("attempts"),
        "max_attempts": row.get("max_attempts"),
        "last_error": row.get("last_error"),
        "elapsed_seconds": elapsed,
        "queued_at": _iso(queued_at),
        "run_id": row.get("run_id"),
        "project_id": row.get("project_id"),
    }
    for key in PROGRESS_KEYS:
        payload[key] = None
    return payload
