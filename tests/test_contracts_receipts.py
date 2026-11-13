"""Typed ingest-receipt reader tests (the #414 class; momus F7/F8).

The readers own the key-reading rules the demo loader once guessed at.
These tests pin the rules directly, so a key-shape change fails here
before it can hide a degraded run's cause again.
"""

from __future__ import annotations

from corpus_kb.contracts.receipts import failure_text, stored_chunk_count


def test_stored_count_reads_the_document_total() -> None:
    receipt: dict[str, object] = {"pg_chunk_total": 9, "pg_chunk_count": 4}
    assert stored_chunk_count(receipt) == 9


def test_stored_count_falls_back_to_this_runs_write_count() -> None:
    """A sink without the document total still reports what this run wrote."""
    receipt: dict[str, object] = {"pg_chunk_total": 0, "pg_chunk_count": 7}
    assert stored_chunk_count(receipt) == 7


def test_stored_count_is_zero_without_count_keys() -> None:
    assert stored_chunk_count({}) == 0


def test_stored_count_ignores_non_integer_and_bool_cells() -> None:
    receipt: dict[str, object] = {"pg_chunk_total": "9", "pg_chunk_count": True}
    assert stored_chunk_count(receipt) == 0


def test_failure_text_reads_errors_before_message() -> None:
    receipt: dict[str, object] = {
        "errors": ["write failed", "no vectors"],
        "message": "wrong words",
    }
    assert failure_text(receipt) == "write failed; no vectors"


def test_failure_text_falls_back_to_message() -> None:
    """An ``error`` receipt carries ``message`` and no ``errors`` list."""
    receipt: dict[str, object] = {"errors": [], "message": "database unreachable"}
    assert failure_text(receipt) == "database unreachable"


def test_failure_text_keeps_a_last_resort_word() -> None:
    assert failure_text({}) == "unknown ingest failure"


def test_failure_text_ignores_a_non_list_errors_cell() -> None:
    receipt: dict[str, object] = {"errors": "write failed"}
    assert failure_text(receipt) == "unknown ingest failure"


def test_failure_text_treats_a_null_message_as_unknown() -> None:
    """A ``message`` of ``None`` counts as absent, never as the word None."""
    receipt: dict[str, object] = {"message": None}
    assert failure_text(receipt) == "unknown ingest failure"
