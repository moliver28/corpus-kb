"""Typed readers for the shared ingest receipt (the #414 failure class).

The pipeline result (``tools/pipeline/result.py``) is a plain dict by
surface contract; its emitted keys are parity-locked and stay untouched.
Consumers that read it key by key guess. The demo loader guessed among
``pg_chunk_total`` and ``pg_chunk_count``, and an earlier reader took
only ``message`` — that read hid the real cause of every degraded demo
run (issue #414).

These helpers own the reading rules in one place. They only read: they
never write an envelope and never add a key.
"""

from __future__ import annotations

from collections.abc import Mapping

# The last resort when a receipt carries no failure words at all.
_UNKNOWN_FAILURE = "unknown ingest failure"


def _count(value: object) -> int:
    """A receipt count as an int (0 for a missing or non-integer value)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def stored_chunk_count(result: Mapping[str, object]) -> int:
    """Chunks the store holds for this document (0 when no write landed).

    ``chunk_count`` is the in-memory count, so it cannot prove a write.
    ``pg_chunk_total`` is the stored count of the whole document
    (issue #222). A sink without that count answers 0, so this run's
    write count (``pg_chunk_count``) is the fallback.
    """
    total = _count(result.get("pg_chunk_total"))
    return total if total > 0 else _count(result.get("pg_chunk_count"))


def failure_text(result: Mapping[str, object]) -> str:
    """The receipt's own failure words: its ``errors``, else its ``message``.

    A ``partial`` receipt carries ``errors`` and no ``message``; an
    ``error`` receipt carries ``message``. Reading only ``message`` hid
    the real cause of every degraded run (issue #414). A ``message`` of
    ``None`` counts as absent — the last resort beats the word ``None``.
    """
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        return "; ".join(str(item) for item in errors)
    message = result.get("message")
    return str(message) if message is not None else _UNKNOWN_FAILURE


__all__ = ["failure_text", "stored_chunk_count"]
