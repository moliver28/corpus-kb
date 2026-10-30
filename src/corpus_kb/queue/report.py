"""The drain report (textbook-ingestion program, Wave 2C).

One deterministic end-of-run shape for the queue worker: per-book
outcomes, per-status counts, and the run clock. Lines sort by
``queued_at, id`` (zero-drift rule 9), so a harness can snapshot a drain.
The CLI renders the human table here; ``--json`` prints ``as_dict``.
The row-to-outcome shaping helpers live here too, so the worker module
stays loop-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

#: The end states a worker can leave a row in, in report order.
REPORT_STATUSES = ("done", "failed", "pending")

#: last_error stays bounded; a crashed pipeline's message can be huge.
LAST_ERROR_CAP = 500


@dataclass(frozen=True)
class BookOutcome:
    """What the worker did to one queue row."""

    queue_id: str
    path: str
    queued_at: str = ""
    status: str = "pending"
    attempts: int | None = None
    last_error: str | None = None
    run_id: str | None = None
    detail: str | None = None
    elapsed_seconds: float = 0.0

    def sort_key(self) -> tuple[str, str]:
        """The deterministic order: queued_at, then id."""
        return (self.queued_at, self.queue_id)


def iso_or_empty(value: object) -> str:
    """One ISO timestamp, or an empty string for absent values."""
    return value.isoformat() if isinstance(value, datetime) else str(value or "")


def str_or_none(value: object) -> str | None:
    """The value when it is a string, else None."""
    return value if isinstance(value, str) else None


def int_or_none(value: object) -> int | None:
    """The value when it is an int, else None."""
    return value if isinstance(value, int) else None


def join_errors(raw: object) -> str | None:
    """The pipeline's stage errors as one bounded line, or None."""
    if not isinstance(raw, list) or not raw:
        return None
    return "; ".join(str(item) for item in raw)[:LAST_ERROR_CAP]


def report_status(raw: object) -> str:
    """Fold a row status onto a report status (a lost claim reads pending)."""
    status = str(raw or "")
    return status if status in REPORT_STATUSES else "pending"


def chunk_count_from(result: dict[str, object]) -> int | None:
    """The stored chunk count from one pipeline result, or None.

    The first POSITIVE value wins, read in the order ``pg_chunk_total``,
    ``pg_chunk_count``, ``chunk_count``. The envelope always carries
    ``pg_chunk_total``, and a stub sink answers 0, so a plain
    first-int read would let that 0 shadow a truthful
    ``pg_chunk_count``. When no value is positive, the first
    non-negative value present wins; None only when nothing is usable.
    """
    values = [result.get(key) for key in ("pg_chunk_total", "pg_chunk_count", "chunk_count")]
    for value in values:
        if isinstance(value, int) and value > 0:
            return value
    for value in values:
        if isinstance(value, int) and value >= 0:
            return value
    return None


@dataclass
class DrainReport:
    """One drain pass: outcomes in claim order plus the run clock."""

    outcomes: list[BookOutcome] = field(default_factory=list)
    stopped: bool = False
    stop_reason: str | None = None
    elapsed_seconds: float = 0.0

    def add(self, outcome: BookOutcome) -> None:
        self.outcomes.append(outcome)

    def sorted_outcomes(self) -> list[BookOutcome]:
        """Outcomes ordered by queued_at, id — stable for snapshots."""
        return sorted(self.outcomes, key=lambda o: o.sort_key())

    def counts(self) -> dict[str, int]:
        """Per-status counts; a status with zero rows still answers 0."""
        counts: dict[str, int] = dict.fromkeys(REPORT_STATUSES, 0)
        for outcome in self.outcomes:
            counts[outcome.status] = counts.get(outcome.status, 0) + 1
        return counts

    def as_dict(self) -> dict[str, object]:
        """The machine shape for ``--json``."""
        return {
            "status": "success",
            "stopped": self.stopped,
            "stop_reason": self.stop_reason,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "counts": self.counts(),
            "rows": [
                {
                    "queue_id": o.queue_id,
                    "path": o.path,
                    "queued_at": o.queued_at,
                    "status": o.status,
                    "attempts": o.attempts,
                    "last_error": o.last_error,
                    "run_id": o.run_id,
                    "detail": o.detail,
                    "elapsed_seconds": round(o.elapsed_seconds, 3),
                }
                for o in self.sorted_outcomes()
            ],
        }

    def render_lines(self) -> list[str]:
        """Plain-English report lines (ASD-STE100: short active sentences)."""
        lines = [f"queue: {len(self.outcomes)} rows handled"]
        counts = self.counts()
        summary = ", ".join(f"{status} {counts[status]}" for status in REPORT_STATUSES)
        lines.append(f"  {summary}")
        for outcome in self.sorted_outcomes():
            line = (
                f"  - {outcome.queue_id} {outcome.status} "
                f"attempts={outcome.attempts} {outcome.elapsed_seconds:.1f}s {outcome.path}"
            )
            lines.append(line)
            if outcome.last_error:
                lines.append(f"    error: {outcome.last_error}")
            if outcome.detail:
                lines.append(f"    note: {outcome.detail}")
        if self.stopped:
            lines.append(f"  stopped: {self.stop_reason or 'stop requested'}")
        return lines
