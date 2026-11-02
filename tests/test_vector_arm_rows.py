"""Focused tests for the extracted vector-arm scan helpers.

Covers the two diff-coverage gaps from the arm split: the pgml EXACT
scan fetch (only the relaxed scan ran in the pipeline tests) and the
matryoshka arm's zero-query abstention.
"""

from __future__ import annotations

from typing import Any

import pytest

from corpus_kb.handlers.query_integrity import REASON_ZERO_QUERY
from corpus_kb.handlers.query_vector_arms import _matryoshka_rows, _pgml_exact_rows


class _RecordingConn:
    """Asyncpg fetch double that records SQL and positional args."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append((sql, args))
        return self.rows


class _ZeroEmbedder:
    """Embedder double whose query embedding is the all-zero marker."""

    def __init__(self) -> None:
        self.embedded = 0

    def embed(self, text: str) -> list[float]:
        self.embedded += 1
        return [0.0] * 8

    def embed_matryoshka(self, text: str, dim: int) -> list[float]:
        raise AssertionError("the zero-query guard must abstain before the slice")


@pytest.mark.asyncio
async def test_pgml_exact_rows_fetches_materialized_exact_scan() -> None:
    """The U40 exact scan materializes candidates and ranks them outside."""
    conn = _RecordingConn(rows=[{"chunk_id": "c1"}])

    rows = await _pgml_exact_rows(
        conn,
        "pgml-model",
        "find things",
        "tenant-1",
        10,
        "AND documents.source_type = $5",
        "AND chunks.degraded_at IS NULL",
        ["v5"],
    )

    assert rows == [{"chunk_id": "c1"}]
    assert len(conn.calls) == 1
    sql, args = conn.calls[0]
    assert "WITH candidate AS MATERIALIZED" in sql
    assert "pgml.embed($1, ARRAY[$2]::text[])" in sql
    # No inner ANN ordering or LIMIT: every matching row is scored exactly.
    assert "ORDER BY cv.embedding" not in sql
    assert "ORDER BY candidate.score + 0 DESC, chunks.chunk_id ASC" in sql
    assert "LIMIT $4" in sql
    assert args == ("pgml-model", "find things", "tenant-1", 10, "v5")


@pytest.mark.asyncio
async def test_matryoshka_rows_abstain_on_zero_query() -> None:
    """The zero-query guard abstains before any slice or SQL runs."""
    conn = _RecordingConn()
    embedder = _ZeroEmbedder()

    rows, reason = await _matryoshka_rows(
        conn,
        embedder,
        "anything",
        "tenant-1",
        10,
        20,
        [],
        "AND chunks.degraded_at IS NULL",
        4,
    )

    assert rows == []
    assert reason == REASON_ZERO_QUERY
    assert embedder.embedded == 1
    assert conn.calls == []
