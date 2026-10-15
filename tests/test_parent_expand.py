"""Offline tests for U48/P6 query-time parent expansion (small-to-big).

Proves the DEFAULT-OFF identity (same list object, zero pool traffic) and
the flag-on behavior (text replaced by shadow parent context, everything
else preserved, fail-open on probe errors). No DB, no Ollama, no markers.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from corpus_kb.domain.models import SearchResult
from corpus_kb.handlers.query_handler import QueryHandler
from corpus_kb.rag.parent_expand import expand_to_parent

TENANT = "00000000-0000-0000-0000-000000000001"


class _FakeConn:
    """Fake asyncpg connection answering the parent-text probe."""

    def __init__(self, rows: list[dict[str, str]] | None = None, *, fail: bool = False) -> None:
        self.rows = rows or []
        self.fail = fail
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, sql: str, *args: object) -> list[Mapping[str, object]]:
        self.fetch_calls.append((sql, args))
        if self.fail:
            raise RuntimeError("shadow probe exploded")
        return [dict(row) for row in self.rows]


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _FakePool:
    """Fake asyncpg.Pool that records every acquire() call."""

    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn
        self.acquire_calls = 0

    def acquire(self) -> _FakeAcquire:
        self.acquire_calls += 1
        return _FakeAcquire(self.conn)


def _hit(text: str = "hit text") -> SearchResult:
    return SearchResult(
        chunk_id=uuid4(),
        text=text,
        score=0.75,
        source="raw_text",
        doc_id=uuid4(),
    )


def _handler(pool: _FakePool, *, expand: bool) -> QueryHandler:
    config: dict[str, object] = {
        "retrieval": {"expand_to_parent": expand},
        "reranking": {"enabled": False},
    }
    return QueryHandler(pool, reranker=None, config=config)


async def test_default_off_returns_same_object_without_pool_traffic() -> None:
    """Flag off (the shipped default): byte-identical passthrough — the SAME
    list object comes back and the pool is never touched."""
    pool = _FakePool(_FakeConn())
    results = [_hit(), _hit("other")]
    handler = _handler(pool, expand=False)
    out = await handler._maybe_expand_to_parent(results, TENANT)
    assert out is results
    assert [r.text for r in out] == ["hit text", "other"]
    assert pool.acquire_calls == 0


async def test_expand_replaces_text_and_preserves_everything_else() -> None:
    parent_id = uuid4()
    hit_ids = [uuid4(), uuid4()]
    results = [
        SearchResult(
            chunk_id=hit_ids[0], text="small child", score=0.9, source="raw_text", doc_id=parent_id
        ),
        SearchResult(
            chunk_id=hit_ids[1],
            text="another child",
            score=0.5,
            source="raw_text",
            doc_id=parent_id,
        ),
    ]
    conn = _FakeConn(
        rows=[
            {"v1_chunk_id": str(hit_ids[0]), "parent_text": "PARENT ONE"},
            {"v1_chunk_id": str(hit_ids[1]), "parent_text": "PARENT TWO"},
        ]
    )
    out = await expand_to_parent(_FakePool(conn), TENANT, results)
    assert [r.text for r in out] == ["PARENT ONE", "PARENT TWO"]
    assert [r.chunk_id for r in out] == [r.chunk_id for r in results]
    assert [r.score for r in out] == [0.9, 0.5]
    assert [r.source for r in out] == ["raw_text", "raw_text"]
    assert [r.doc_id for r in out] == [parent_id, parent_id]
    sql = conn.fetch_calls[0][0]
    assert "DISTINCT ON" in sql and "shadow_chunks" in sql


async def test_expand_hits_without_shadow_link_pass_through() -> None:
    linked = uuid4()
    unlinked = uuid4()
    results = [_hit("linked"), _hit("unlinked")]
    results[0] = results[0].model_copy(update={"chunk_id": linked})
    results[1] = results[1].model_copy(update={"chunk_id": unlinked})
    conn = _FakeConn(rows=[{"v1_chunk_id": str(linked), "parent_text": "BIG"}])
    out = await expand_to_parent(_FakePool(conn), TENANT, results)
    assert out[0].text == "BIG"
    assert out[1].text == "unlinked"


async def test_expand_no_shadow_rows_returns_results_unchanged() -> None:
    results = [_hit()]
    out = await expand_to_parent(_FakePool(_FakeConn(rows=[])), TENANT, results)
    assert out == results
    assert out[0].text == "hit text"


async def test_expand_fail_open_on_probe_error() -> None:
    """A broken probe must never break search: results come back unchanged."""
    results = [_hit()]
    out = await expand_to_parent(_FakePool(_FakeConn(fail=True)), TENANT, results)
    assert out is results


async def test_expand_empty_results_skips_pool() -> None:
    pool = _FakePool(_FakeConn())
    out = await expand_to_parent(pool, TENANT, [])
    assert out == []
    assert pool.acquire_calls == 0


async def test_handler_flag_on_uses_shadow_probe() -> None:
    """With the flag on, the handler delegates to the expansion probe."""
    pool = _FakePool(_FakeConn(rows=[]))
    handler = _handler(pool, expand=True)
    results = [_hit()]
    out = await handler._maybe_expand_to_parent(results, TENANT)
    assert pool.acquire_calls == 1
    assert out == results
