"""Tests for corpus.rrf_fusion (migration 006) — RRF fusion in pure SQL.

Live tests run against PG16 on localhost:5432 via the explicit-DSN pattern
from tests/test_migrations.py (service-gated skip when unreachable). They
prove the SQL function produces byte-identical chunk_id ordering and
float-equal fused scores versus the Python RRF loop it replaced.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import pytest

from corpus_kb.domain.models import SearchQuery
from corpus_kb.handlers.query_handler import QueryHandler

DSN = "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"
CONNECT_TIMEOUT = 3
MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "corpus_kb"
    / "migrations"
    / "006_rrf_fusion.sql"
)
SCORE_TOLERANCE = 1e-12


def _python_rrf_reference(
    vector_rows: list[dict[str, Any]],
    fts_rows: list[dict[str, Any]],
    k: int,
    rrf_k: int = 60,
) -> list[tuple[str, str, str, str, float]]:
    """Verbatim port of the pre-refactor Python RRF loop in handle_search.

    Source: git show 21ea364:corpus-kb/src/handlers/query_handler.py, the RRF
    block (lines 97-130). chunk_id/doc_id are uuid strings here. Returns
    (chunk_id, text, source, doc_id, score) tuples in fused order.
    """
    scores: dict[str, float] = {}
    texts: dict[str, str] = {}
    sources: dict[str, str] = {}
    doc_ids: dict[str, str] = {}

    for rank, row in enumerate(vector_rows):
        cid = row["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)
        texts[cid] = row["text"]
        sources[cid] = row["source"]
        doc_ids[cid] = row["doc_id"]

    for rank, row in enumerate(fts_rows):
        cid = row["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)
        texts[cid] = row["text"]
        sources[cid] = row["source"]
        doc_ids[cid] = row["doc_id"]

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]
    return [(cid, texts[cid], sources[cid], doc_ids[cid], score) for cid, score in ranked]


def _side_rows(prefix: str, n: int, chunk_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Build one synthetic result side: {chunk_id, text, source, doc_id, score}."""
    rows = []
    for i in range(n):
        rows.append(
            {
                "chunk_id": chunk_ids[i] if chunk_ids else str(uuid4()),
                "text": f"{prefix} chunk {i}",
                "source": f"{prefix}_source",
                "doc_id": str(uuid4()),
                "score": 1.0 - i * 0.01,
            }
        )
    return rows


async def _sql_rrf(
    conn: asyncpg.Connection,
    vector_rows: list[dict[str, Any]] | None,
    fts_rows: list[dict[str, Any]] | None,
    k: int,
    rrf_k: int | None = 60,
) -> list[tuple[str, str, str, str, float]]:
    """Call corpus.rrf_fusion. None side => SQL NULL; rrf_k=None => DEFAULT."""
    vector_json = json.dumps(vector_rows) if vector_rows is not None else None
    fts_json = json.dumps(fts_rows) if fts_rows is not None else None
    if rrf_k is None:
        rows = await conn.fetch(
            "SELECT chunk_id::text AS cid, text, source, doc_id::text AS did, score "
            "FROM corpus.rrf_fusion($1::jsonb, $2::jsonb, $3)",
            vector_json,
            fts_json,
            k,
        )
    else:
        rows = await conn.fetch(
            "SELECT chunk_id::text AS cid, text, source, doc_id::text AS did, score "
            "FROM corpus.rrf_fusion($1::jsonb, $2::jsonb, $3, $4)",
            vector_json,
            fts_json,
            k,
            rrf_k,
        )
    return [(r["cid"], r["text"], r["source"], r["did"], r["score"]) for r in rows]


def _assert_identical(
    sql_rows: list[tuple[str, str, str, str, float]],
    py_rows: list[tuple[str, str, str, str, float]],
) -> None:
    """Byte-identical chunk_id ordering, identical metadata, float-equal scores."""
    assert [r[0] for r in sql_rows] == [r[0] for r in py_rows]
    for sql_row, py_row in zip(sql_rows, py_rows, strict=True):
        assert sql_row[1:4] == py_row[1:4]
        assert abs(sql_row[4] - py_row[4]) < SCORE_TOLERANCE


@pytest.fixture
async def live_conn():
    """Explicit-DSN PG16 connection with migration 006 applied (idempotent)."""
    try:
        conn = await asyncio.wait_for(asyncpg.connect(DSN), timeout=CONNECT_TIMEOUT)
    except Exception:
        pytest.skip("Postgres not available on localhost:5432")
    await conn.execute(MIGRATION.read_text(encoding="utf-8"))
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_migration_006_double_apply_is_noop() -> None:
    """CREATE OR REPLACE makes re-applying 006 clean on a live server."""
    try:
        conn = await asyncio.wait_for(asyncpg.connect(DSN), timeout=CONNECT_TIMEOUT)
    except Exception:
        pytest.skip("Postgres not available on localhost:5432")
    try:
        sql = MIGRATION.read_text(encoding="utf-8")
        await conn.execute(sql)
        # Second apply must be a no-op success, not an error.
        await conn.execute(sql)
        present = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid = p.pronamespace WHERE n.nspname = 'corpus' "
            "AND p.proname = 'rrf_fusion')"
        )
        assert present
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_rrf_fusion_matches_python_on_overlapping_sides(live_conn) -> None:
    """Chunks in both sides accumulate both rank contributions; FTS metadata wins."""
    shared = [str(uuid4()) for _ in range(2)]
    vector_rows = _side_rows("vec", 4, chunk_ids=[shared[0], str(uuid4()), shared[1], str(uuid4())])
    fts_rows = _side_rows("fts", 3, chunk_ids=[shared[1], shared[0], str(uuid4())])

    sql_rows = await _sql_rrf(live_conn, vector_rows, fts_rows, k=10)
    py_rows = _python_rrf_reference(vector_rows, fts_rows, k=10)

    _assert_identical(sql_rows, py_rows)
    # shared[0] at vec rank 0 + fts rank 1 (1/61 + 1/62) outranks
    # shared[1] at vec rank 2 + fts rank 0 (1/63 + 1/61).
    assert sql_rows[0][0] == shared[0]
    assert sql_rows[1][0] == shared[1]
    # Python dict-overwrite order means the FTS side's text wins for overlaps.
    assert sql_rows[0][1] == "fts chunk 1"
    assert sql_rows[1][1] == "fts chunk 0"


@pytest.mark.asyncio
async def test_rrf_fusion_matches_python_on_disjoint_sides(live_conn) -> None:
    """Equal ranks across disjoint sides tie; ties keep Python's stable order."""
    vector_rows = _side_rows("vec", 3)
    fts_rows = _side_rows("fts", 3)

    sql_rows = await _sql_rrf(live_conn, vector_rows, fts_rows, k=10)
    py_rows = _python_rrf_reference(vector_rows, fts_rows, k=10)

    _assert_identical(sql_rows, py_rows)
    expected_order = [
        vector_rows[0]["chunk_id"],
        fts_rows[0]["chunk_id"],
        vector_rows[1]["chunk_id"],
        fts_rows[1]["chunk_id"],
        vector_rows[2]["chunk_id"],
        fts_rows[2]["chunk_id"],
    ]
    assert [r[0] for r in sql_rows] == expected_order


@pytest.mark.asyncio
async def test_rrf_fusion_matches_python_with_empty_vector_side(live_conn) -> None:
    """An empty vector side yields the FTS side in rank order."""
    fts_rows = _side_rows("fts", 3)
    sql_rows = await _sql_rrf(live_conn, [], fts_rows, k=10)
    py_rows = _python_rrf_reference([], fts_rows, k=10)
    _assert_identical(sql_rows, py_rows)


@pytest.mark.asyncio
async def test_rrf_fusion_matches_python_with_empty_fts_side(live_conn) -> None:
    """An empty FTS side yields the vector side in rank order."""
    vector_rows = _side_rows("vec", 3)
    sql_rows = await _sql_rrf(live_conn, vector_rows, [], k=10)
    py_rows = _python_rrf_reference(vector_rows, [], k=10)
    _assert_identical(sql_rows, py_rows)


@pytest.mark.asyncio
async def test_rrf_fusion_null_side_behaves_like_empty(live_conn) -> None:
    """A NULL JSONB side contributes nothing, same as an empty array."""
    fts_rows = _side_rows("fts", 2)
    sql_rows = await _sql_rrf(live_conn, None, fts_rows, k=10)
    py_rows = _python_rrf_reference([], fts_rows, k=10)
    _assert_identical(sql_rows, py_rows)


@pytest.mark.asyncio
async def test_rrf_fusion_single_result(live_conn) -> None:
    """A single hit scores exactly 1/(rrf_k + 1)."""
    vector_rows = _side_rows("vec", 1)
    sql_rows = await _sql_rrf(live_conn, vector_rows, [], k=10)
    py_rows = _python_rrf_reference(vector_rows, [], k=10)
    _assert_identical(sql_rows, py_rows)
    assert len(sql_rows) == 1
    assert abs(sql_rows[0][4] - 1.0 / 61) < SCORE_TOLERANCE


@pytest.mark.asyncio
async def test_rrf_fusion_both_sides_empty_returns_no_rows(live_conn) -> None:
    """Two empty sides fuse to an empty result, like the Python loop."""
    assert await _sql_rrf(live_conn, [], [], k=10) == []
    assert _python_rrf_reference([], [], k=10) == []


@pytest.mark.asyncio
async def test_rrf_fusion_default_rrf_k_is_60(live_conn) -> None:
    """Omitting rrf_k uses the DEFAULT 60, matching the old Python constant."""
    vector_rows = _side_rows("vec", 2)
    fts_rows = _side_rows("fts", 2)
    sql_rows = await _sql_rrf(live_conn, vector_rows, fts_rows, k=10, rrf_k=None)
    py_rows = _python_rrf_reference(vector_rows, fts_rows, k=10, rrf_k=60)
    _assert_identical(sql_rows, py_rows)


@pytest.mark.asyncio
async def test_rrf_fusion_custom_rrf_k(live_conn) -> None:
    """A non-default rrf_k flows through to the same formula on both sides."""
    vector_rows = _side_rows("vec", 3)
    fts_rows = _side_rows("fts", 3)
    sql_rows = await _sql_rrf(live_conn, vector_rows, fts_rows, k=10, rrf_k=10)
    py_rows = _python_rrf_reference(vector_rows, fts_rows, k=10, rrf_k=10)
    _assert_identical(sql_rows, py_rows)


@pytest.mark.asyncio
async def test_rrf_fusion_respects_k_limit(live_conn) -> None:
    """LIMIT k trims the fused list exactly like the Python slice [:k]."""
    vector_rows = _side_rows("vec", 3)
    fts_rows = _side_rows("fts", 3)
    sql_rows = await _sql_rrf(live_conn, vector_rows, fts_rows, k=2)
    py_rows = _python_rrf_reference(vector_rows, fts_rows, k=2)
    _assert_identical(sql_rows, py_rows)
    assert len(sql_rows) == 2


@pytest.mark.asyncio
async def test_handle_search_calls_sql_rrf_function() -> None:
    """handle_search ships both sides as JSONB to corpus.rrf_fusion."""
    cid, did = uuid4(), uuid4()
    fts_row = {
        "chunk_id": cid,
        "text": "fts hit",
        "doc_id": did,
        "source": "s1",
        "score": 0.5,
    }
    fused_row = {
        "chunk_id": cid,
        "text": "fts hit",
        "source": "s1",
        "doc_id": did,
        "score": 1.0 / 61,
    }
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.fetch = AsyncMock(side_effect=[[fts_row], [fused_row]])

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    handler = QueryHandler(
        pool=mock_pool,
        config={"embedding": {"provider": "ollama", "model": "nomic-embed-text"}},
    )  # no embedder -> vector side empty
    results = await handler.handle_search(SearchQuery(query="test", k=5))

    fusion_call = mock_conn.fetch.call_args_list[1]
    assert "corpus.rrf_fusion" in fusion_call.args[0]
    assert json.loads(fusion_call.args[1]) == []  # empty vector side
    fts_payload = json.loads(fusion_call.args[2])
    assert fts_payload[0]["chunk_id"] == str(cid)
    assert fts_payload[0]["doc_id"] == str(did)
    assert fusion_call.args[3] == 5
    assert fusion_call.args[4] == 60
    assert len(results) == 1
    assert results[0].chunk_id == cid
    assert results[0].doc_id == did


@pytest.mark.asyncio
async def test_handle_search_missing_rrf_function_is_loud() -> None:
    """A missing corpus.rrf_fusion raises a clear error naming migration 006."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.fetch = AsyncMock(
        side_effect=[
            [],
            asyncpg.UndefinedFunctionError(
                "function corpus.rrf_fusion(jsonb, jsonb, integer, integer) does not exist"
            ),
        ]
    )

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    handler = QueryHandler(
        pool=mock_pool,
        config={"embedding": {"provider": "ollama", "model": "nomic-embed-text"}},
    )
    with pytest.raises(RuntimeError, match="006"):
        await handler.handle_search(SearchQuery(query="test", k=5))
