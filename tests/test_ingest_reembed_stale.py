"""Stale-row repair coverage for ``ingest-reembed`` (config v2 / migration 025).

A model switch (migration 025) leaves ``chunks_vectors.embedding`` NULL on
every pre-switch row: the old model's vectors cannot convert. The reembed
handler must target those STALE rows in addition to the degraded-flagged
rows, report the per-class counts, and write ``halfvec``-cast vectors.
"""

from __future__ import annotations

import re
from typing import cast
from uuid import uuid4

import pytest

from corpus_kb.tools import ingest_common, ingest_reembed


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


class _FakeConn:
    """Minimal asyncpg conn for reembed_degraded: scripted fetchval/fetch."""

    def __init__(
        self,
        degraded_columns_present: int,
        candidate_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.sql: list[str] = []
        self._column_count = degraded_columns_present
        self.candidate_rows = candidate_rows or []

    async def execute(self, sql: str, *args: object) -> str:
        self.sql.append(sql)
        return "OK"

    async def fetchval(self, sql: str, *args: object) -> object:
        self.sql.append(sql)
        if "information_schema" in sql:
            return self._column_count
        return None

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        self.sql.append(sql)
        return self.candidate_rows

    def transaction(self):
        outer = self

        class _Tx:
            async def __aenter__(self) -> _FakeConn:
                return outer

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Tx()


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self):
        outer = self

        class _Acquire:
            async def __aenter__(self) -> _FakeConn:
                return outer._conn

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Acquire()


def _candidate_row(*, was_stale: bool) -> dict[str, object]:
    return {
        "chunk_id": str(uuid4()),
        "doc_id": str(uuid4()),
        "text": "body text",
        "context_blurb": "a blurb",
        "was_stale": was_stale,
    }


def _ok_embed(dims: int = 4):
    async def fake_embed_chunks(
        chunks_in: list,
        config: dict,
        pool: object = None,
        text_override: dict | None = None,
        document_text: str | None = None,
        meta: dict | None = None,
    ):
        for chunk in chunks_in:
            chunk.embedding = [0.5] * dims
        return False, None

    return fake_embed_chunks


@pytest.mark.asyncio
async def test_scan_targets_stale_rows_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scan predicate includes ``cv.embedding IS NULL`` (migration 025)."""
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    conn = _FakeConn(degraded_columns_present=2)
    await ingest_common.reembed_degraded(
        cast("object", _FakePool(conn)),
        {"embedding": {"provider": "ollama"}},
    )
    scan = next(s for s in (_norm(x) for x in conn.sql) if "chunks_vectors cv" in s and "JOIN" in s)
    assert "cv.embedding IS NULL" in scan
    assert "cv.degraded = TRUE OR c.degraded = TRUE OR cv.embedding IS NULL" in scan


@pytest.mark.asyncio
async def test_stale_row_repaired_and_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale row is rebuilt, counted per class, and written as halfvec."""
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    row = _candidate_row(was_stale=True)
    conn = _FakeConn(degraded_columns_present=2, candidate_rows=[row])
    monkeypatch.setattr(ingest_reembed, "embed_chunks", _ok_embed())

    summary = await ingest_common.reembed_degraded(
        cast("object", _FakePool(conn)),
        {"embedding": {"provider": "ollama", "dimensions": 4}},
    )
    assert summary["status"] == "success"
    assert summary["scanned"] == 1
    assert summary["fixed"] == 1
    assert summary["failed"] == 0
    assert summary["stale"] == 1  # per-class honesty (conditional key)
    vector_update = next(
        s for s in (_norm(x) for x in conn.sql) if s.startswith("UPDATE chunks_vectors")
    )
    assert "::halfvec" in vector_update
    assert "embedding = $2::halfvec" in vector_update


@pytest.mark.asyncio
async def test_mixed_classes_counted_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    """Degraded and stale rows both repair; the stale count covers only stale."""
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    rows = [_candidate_row(was_stale=True), _candidate_row(was_stale=False)]
    conn = _FakeConn(degraded_columns_present=2, candidate_rows=rows)
    monkeypatch.setattr(ingest_reembed, "embed_chunks", _ok_embed())

    summary = await ingest_common.reembed_degraded(
        cast("object", _FakePool(conn)),
        {"embedding": {"provider": "ollama", "dimensions": 4}},
    )
    assert summary["scanned"] == 2
    assert summary["fixed"] == 2
    assert summary["stale"] == 1


@pytest.mark.asyncio
async def test_degraded_only_repair_has_no_stale_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The envelope stays byte-compatible: no stale key when none applied."""
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    conn = _FakeConn(degraded_columns_present=2, candidate_rows=[_candidate_row(was_stale=False)])
    monkeypatch.setattr(ingest_reembed, "embed_chunks", _ok_embed())

    summary = await ingest_common.reembed_degraded(
        cast("object", _FakePool(conn)),
        {"embedding": {"provider": "ollama", "dimensions": 4}},
    )
    assert summary == {"status": "success", "scanned": 1, "fixed": 1, "failed": 0}
    assert "stale" not in summary
