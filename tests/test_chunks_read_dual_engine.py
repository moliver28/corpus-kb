"""The Phase-2 Wave 5 chunks-list read on BOTH engines (D2 parity).

One flow — seed one document with five chunks (one tombstoned, one
superseded), read the window through the SAME shared tool function
every surface routes through, page it, and check both dead rows stay
out — runs against the DuckDB file gateway AND the Postgres pool. The
seed rides
``tenant_connection`` (the same scope the reads use), so the Postgres
leg sits inside the RLS tenant config and the DuckDB leg inside the
gateway's transaction scope. Postgres skips cleanly when the database
is not reachable (the conftest pg_pool contract), so CI's no-Postgres
legs still prove the DuckDB arm.

Chunk texts carry a run-unique suffix: the Postgres test database keeps
its rows between runs, so every run seeds a fresh document and reads
back only its own rows (deterministic order, rule 9).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from corpus_kb._setup.migrate import run_duckdb_migrations
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
from corpus_kb.storage.gateway import is_gateway
from corpus_kb.storage.tenant_conn import tenant_connection

TENANT = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
async def duckdb_pool(tmp_path: Path):
    from corpus_kb.storage.gateway import DuckDbGateway

    path = tmp_path / "chunks.duckdb"
    run_duckdb_migrations(duckdb_path_to_conn_string(str(path)))
    gateway = await DuckDbGateway.connect(duckdb_path_to_conn_string(str(path)))
    try:
        yield gateway
    finally:
        await gateway.close()


@pytest.fixture
async def postgres_pool(pg_pool):  # pg_pool skips when Postgres is down
    yield pg_pool


@pytest.fixture(params=["duckdb", "postgres"])
def pool(request: pytest.FixtureRequest):
    return request.getfixturevalue(f"{request.param}_pool")


async def _seed_document_with_chunks(pool, doc_id: UUID, run: str) -> list[UUID]:
    """One documents row plus five chunks (index 2 tombstoned, 4 superseded)."""
    if is_gateway(pool):
        doc_sql = (
            "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
            " VALUES (?, ?, ?, ?)"
        )
        chunk_sql = (
            "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text,"
            " tombstoned_at, superseded_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
    else:
        doc_sql = (
            "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
            " VALUES ($1, $2, $3, $4)"
        )
        chunk_sql = (
            "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text,"
            " tombstoned_at, superseded_at) VALUES ($1, $2, $3, $4, $5, $6, $7)"
        )
    async with tenant_connection(pool, TENANT) as conn:
        await conn.execute(doc_sql, str(doc_id), str(TENANT), f"wave5-{run}.md", "markdown")
        ids = []
        for index in range(5):
            chunk_id = uuid4()
            ids.append(chunk_id)
            # Chunk 2 stands in for removed source content; chunk 4 stands
            # in for dedup-replaced text. The active-chunk rule (migration
            # 007) is both NULL, so the read must leave both out of every
            # window.
            tombstoned = datetime(2026, 1, 1, tzinfo=UTC) if index == 2 else None
            superseded = datetime(2026, 1, 2, tzinfo=UTC) if index == 4 else None
            await conn.execute(
                chunk_sql,
                str(chunk_id),
                str(TENANT),
                str(doc_id),
                index,
                f"chunk {index} for {run}",
                tombstoned,
                superseded,
            )
    return ids


async def test_chunks_list_window_order_and_tombstone_on_both_engines(pool) -> None:
    from corpus_kb.tools.chunk_read_tools import chunks_list

    run = uuid4().hex[:8]
    doc_id = uuid4()
    ids = await _seed_document_with_chunks(pool, doc_id, run)

    # The full window answers in ingest order; the tombstoned chunk
    # (index 2) and the superseded chunk (index 4) never answer.
    listed = await chunks_list(pool, str(doc_id), limit=100, offset=0)
    assert listed["status"] == "success"
    assert [row["chunk_index"] for row in listed["result"]] == [0, 1, 3]
    assert [row["chunk_id"] for row in listed["result"]] == [
        str(c) for c in (ids[0], ids[1], ids[3])
    ]
    assert listed["result"][0]["text"] == f"chunk 0 for {run}"
    assert listed["result"][0]["doc_id"] == str(doc_id)

    # The window pages honestly: limit 2 from offset 1 answers indexes
    # 1 and 3 in the same total order.
    page = await chunks_list(pool, str(doc_id), limit=2, offset=1)
    assert [row["chunk_index"] for row in page["result"]] == [1, 3]

    # An offset past the window answers an empty result, still success.
    empty = await chunks_list(pool, str(doc_id), limit=2, offset=9)
    assert empty == {"status": "success", "result": []}

    # Another document's chunks never leak into this window.
    other = await chunks_list(pool, str(uuid4()), limit=100, offset=0)
    assert other == {"status": "success", "result": []}
