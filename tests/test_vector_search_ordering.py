"""U40 regression tests: filtered vector KNN must use the relaxed-ordering pattern.

pgvector >= 0.8 ``hnsw.iterative_scan`` (relaxed/strict) can return results
that are not strictly distance-ordered. The fix pattern (pgvector README,
"Iterative scans"): wrap the ANN scan in a ``WITH candidate AS MATERIALIZED``
CTE (scan + filters + distance ORDER BY + LIMIT inside), then apply the
exact ordering OUTSIDE the CTE over the materialized set, defeating planner
elision with ``score + 0`` (the docs' recommended offset trick).

These tests are OFFLINE: a recording fake connection captures the SQL and
asserts its shape, so no Postgres is needed (the DB-backed monotonic-distance
check lives with the other requires_postgres suites).
"""

from __future__ import annotations

import re
from typing import cast

import pytest

from corpus_kb.domain.models import DEFAULT_TENANT_ID, SearchQuery, SearchSimilarQuery
from corpus_kb.handlers.query_handler import QueryHandler
from corpus_kb.rag.embedder import instruct
from corpus_kb.rag.fake_embedder import FakeEmbedder


class _RecordingConn:
    """Asyncpg-connection stand-in: records SQL, returns no rows."""

    def __init__(self, extversion: str | None = "0.8.2") -> None:
        self.sql: list[str] = []
        self.params: list[tuple[object, ...]] = []
        self.extversion = extversion

    async def execute(self, sql: str, *args: object) -> str:
        self.sql.append(sql)
        self.params.append(args)
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[object]:
        self.sql.append(sql)
        self.params.append(args)
        return []

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        self.sql.append(sql)
        self.params.append(args)
        if "pg_extension" in sql:
            if self.extversion is None:
                return None
            return {"extversion": self.extversion}
        return None

    def transaction(self):
        outer = self

        class _Tx:
            async def __aenter__(self) -> _RecordingConn:
                return outer

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Tx()


class _RecordingPool:
    def __init__(self, conn: _RecordingConn) -> None:
        self._conn = conn

    def acquire(self):
        outer = self

        class _Acquire:
            async def __aenter__(self) -> _RecordingConn:
                return outer._conn

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Acquire()


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def _handler(
    pool: _RecordingPool,
    config: dict[str, object],
    embedder: FakeEmbedder | None = None,
) -> QueryHandler:
    return QueryHandler(cast("object", pool), embedder=embedder, reranker=None, config=config)


_PGML_CONFIG: dict[str, object] = {
    "embedding": {"provider": "pgml", "model": "nomic-embed-text"},
    "search": {"reranker": "none"},
}

_OLLAMA_CONFIG: dict[str, object] = {
    "embedding": {"provider": "ollama", "model": "qwen3-embedding:8b-q8_0", "dimensions": 1024},
    "search": {"reranker": "none"},
}


def _vector_sql(conn: _RecordingConn, needle: str) -> list[str]:
    return [_norm(s) for s in conn.sql if needle in s]


@pytest.mark.asyncio
async def test_pgml_vector_arm_uses_materialized_candidate_cte() -> None:
    conn = _RecordingConn()
    handler = _handler(_RecordingPool(conn), dict(_PGML_CONFIG))
    await handler.handle_search(SearchQuery(query="housing policy", k=5))

    sqls = _vector_sql(conn, "pgml.embed")
    assert sqls, "pgml vector arm did not run"
    sql = sqls[0]
    assert "WITH candidate AS MATERIALIZED" in sql
    # ANN scan + LIMIT stay INSIDE the CTE...
    cte_body = sql.split("AS MATERIALIZED (", 1)[1].split(") SELECT", 1)[0]
    assert "ORDER BY cv.vector <=>" in cte_body
    assert "LIMIT $4" in cte_body
    assert "cv.tenant_id = $3" in cte_body
    # ...and the exact ordering is applied OUTSIDE over the materialized set.
    outer = sql.split(") SELECT", 1)[1]
    assert "ORDER BY candidate.score + 0 DESC" in outer
    assert outer.index("ORDER BY candidate.score + 0 DESC") < outer.index("LIMIT")


@pytest.mark.asyncio
async def test_ollama_plain_arm_uses_materialized_candidate_cte() -> None:
    conn = _RecordingConn()
    embedder = FakeEmbedder(dimensions=1024)
    handler = _handler(_RecordingPool(conn), dict(_OLLAMA_CONFIG), embedder=embedder)
    await handler.handle_search(SearchQuery(query="housing policy", k=5))

    sqls = _vector_sql(conn, "$1::vector")
    assert sqls, "ollama plain vector arm did not run"
    sql = sqls[0]
    assert "WITH candidate AS MATERIALIZED" in sql
    cte_body = sql.split("AS MATERIALIZED (", 1)[1].split(") SELECT", 1)[0]
    assert "ORDER BY cv.vector <=> $1::vector" in cte_body
    assert "cv.tenant_id = $2" in cte_body
    outer = sql.split(") SELECT", 1)[1]
    assert "ORDER BY candidate.score + 0 DESC" in outer


@pytest.mark.asyncio
async def test_matryoshka_arm_materializes_candidates_and_reorders_outside() -> None:
    conn = _RecordingConn()
    embedder = FakeEmbedder(dimensions=1024)
    config = dict(_OLLAMA_CONFIG)
    config["search"] = {"reranker": "none", "matryoshka_enabled": True, "matryoshka_dim": 1024}
    handler = _handler(_RecordingPool(conn), config, embedder=embedder)
    await handler.handle_search(SearchQuery(query="housing policy", k=5))

    sqls = _vector_sql(conn, "vector_1024")
    assert sqls, "matryoshka vector arm did not run"
    sql = sqls[0]
    assert "WITH cand AS MATERIALIZED" in sql
    outer = sql.split(") SELECT", 1)[1]
    # Exact full-dimension distance ordering OUTSIDE the materialized CTE.
    assert "ORDER BY (1 - (cand.vector <=> $1::vector)) + 0 DESC" in outer


@pytest.mark.asyncio
async def test_search_similar_uses_materialized_candidate_cte() -> None:
    from uuid import uuid4

    conn = _RecordingConn()
    handler = _handler(_RecordingPool(conn), dict(_PGML_CONFIG))
    results = await handler.handle_search_similar(
        SearchSimilarQuery(chunk_id=uuid4(), tenant_id=DEFAULT_TENANT_ID, k=5)
    )
    assert results == []

    sqls = [s for s in (_norm(x) for x in conn.sql) if "<=>" in s]
    assert sqls, "search_similar did not issue a vector query"
    sql = sqls[0]
    assert "WITH candidate AS MATERIALIZED" in sql
    outer = sql.split(") SELECT", 1)[1]
    assert "ORDER BY candidate.score + 0 DESC" in outer


@pytest.mark.asyncio
async def test_hnsw_settings_applied_as_set_local_inside_transaction() -> None:
    conn = _RecordingConn(extversion="0.8.2")
    config = dict(_PGML_CONFIG)
    config["search"] = {
        "reranker": "none",
        "hnsw": {"iterative_scan": "strict_order", "ef_search": 200, "max_scan_tuples": 20000},
    }
    handler = _handler(_RecordingPool(conn), config)
    await handler.handle_search(SearchQuery(query="housing policy", k=5))

    set_locals = [s for s in (_norm(x) for x in conn.sql) if s.startswith("SET LOCAL hnsw.")]
    assert set_locals == [
        "SET LOCAL hnsw.iterative_scan = 'strict_order'",
        "SET LOCAL hnsw.ef_search = 200",
        "SET LOCAL hnsw.max_scan_tuples = 20000",
    ]
    # Tenant GUC and settings must precede the search statements.
    first_search_index = next(
        i for i, s in enumerate(_norm(x) for x in conn.sql) if "<=>" in s or "ts_rank" in s
    )
    assert all(conn.sql.index(s) < first_search_index for s in set_locals)


@pytest.mark.asyncio
async def test_hnsw_off_mode_emits_no_set_local() -> None:
    conn = _RecordingConn(extversion="0.8.2")
    config = dict(_PGML_CONFIG)
    config["search"] = {"reranker": "none", "hnsw": {"iterative_scan": "off"}}
    handler = _handler(_RecordingPool(conn), config)
    await handler.handle_search(SearchQuery(query="housing policy", k=5))

    assert not [s for s in (_norm(x) for x in conn.sql) if "SET LOCAL hnsw." in s]


@pytest.mark.asyncio
async def test_hnsw_settings_skipped_on_old_pgvector() -> None:
    conn = _RecordingConn(extversion="0.7.4")
    handler = _handler(_RecordingPool(conn), dict(_PGML_CONFIG))
    await handler.handle_search(SearchQuery(query="housing policy", k=5))
    assert not [s for s in (_norm(x) for x in conn.sql) if "SET LOCAL hnsw." in s]


@pytest.mark.asyncio
async def test_hnsw_settings_skipped_when_version_unknown() -> None:
    conn = _RecordingConn(extversion=None)
    handler = _handler(_RecordingPool(conn), dict(_PGML_CONFIG))
    await handler.handle_search(SearchQuery(query="housing policy", k=5))
    assert not [s for s in (_norm(x) for x in conn.sql) if "SET LOCAL hnsw." in s]


@pytest.mark.asyncio
async def test_ollama_probe_matches_raw_query_embedding() -> None:
    """Main-path vector probes embed the RAW query (no instruct prefix).

    The instruction prefix is the RESEARCH path contract (research/retrieval
    embeds ``instruct(q)``); the main search path sends the raw query text.
    Pin the distinction so neither path silently drifts.
    """
    conn = _RecordingConn()
    embedder = FakeEmbedder(dimensions=1024)
    handler = _handler(_RecordingPool(conn), dict(_OLLAMA_CONFIG), embedder=embedder)
    await handler.handle_search(SearchQuery(query="housing policy", k=5))

    expected = str(embedder.embed("housing policy"))
    all_params = [str(p) for call in conn.params for p in call]
    assert expected in all_params, "vector probe was not embedded from the raw query"
    instructed = str(embedder.embed(instruct("housing policy")))
    assert instructed != expected
    assert instructed not in all_params, (
        "main path must not silently switch to instructed queries without review"
    )


# ============================================================================
# U40 (P3): the analytics_sql KNN reads follow the same pattern. These are
# module-level SQL constants read over fixed-shaped candidate sets, so the
# assertions are offline string-shape checks on each exported statement.
# ============================================================================

from corpus_kb.research.analytics_sql import (  # noqa: E402
    HALFVEC_PROBE_SQL,
    RETRIEVAL_EXCHANGES_SQL,
    RETRIEVAL_UNCODED_SQL,
    RETRIEVAL_UNITS_SQL,
    RETRIEVAL_UNITS_TYPED_SQL,
)


def _assert_relaxed_ordering_shape(sql: str, id_col: str) -> None:
    norm = _norm(sql)
    assert "WITH candidate AS MATERIALIZED" in norm, sql
    cte_body, outer = norm.split("AS MATERIALIZED (", 1)[1].split(") SELECT", 1)
    assert f"{id_col}," in cte_body, sql
    assert "AS dist" in cte_body, sql
    # The ANN ordering + LIMIT stay inside the CTE...
    assert "<=>" in cte_body
    assert "ORDER BY" in cte_body and "LIMIT" in cte_body
    assert "ORDER BY" in outer, sql
    # ...and the outer ordering is EXACT over the materialized set.
    assert "ORDER BY candidate.dist + 0" in outer, sql


def test_analytics_units_sql_uses_relaxed_ordering_pattern() -> None:
    _assert_relaxed_ordering_shape(RETRIEVAL_UNITS_SQL, "ru.unit_id")


def test_analytics_units_typed_sql_uses_relaxed_ordering_pattern() -> None:
    _assert_relaxed_ordering_shape(RETRIEVAL_UNITS_TYPED_SQL, "ru.unit_id")


def test_analytics_exchanges_sql_uses_relaxed_ordering_pattern() -> None:
    _assert_relaxed_ordering_shape(RETRIEVAL_EXCHANGES_SQL, "e.exchange_id")


def test_analytics_uncoded_sql_uses_relaxed_ordering_pattern() -> None:
    _assert_relaxed_ordering_shape(RETRIEVAL_UNCODED_SQL, "ru.unit_id")


def test_analytics_halfvec_probe_sql_uses_relaxed_ordering_pattern() -> None:
    _assert_relaxed_ordering_shape(HALFVEC_PROBE_SQL, "ru.unit_id")
    assert "halfvec(1024)" in _norm(HALFVEC_PROBE_SQL)


def test_analytics_parameter_ordering_is_unchanged() -> None:
    """The sweep must not renumber parameters: callers pass [vector, limit]."""
    for sql in (RETRIEVAL_UNITS_SQL, RETRIEVAL_EXCHANGES_SQL, RETRIEVAL_UNCODED_SQL):
        norm = _norm(sql)
        assert norm.index("$1::vector(256)") < norm.index("LIMIT $2")
    typed = _norm(RETRIEVAL_UNITS_TYPED_SQL)
    assert typed.index("$1::vector(256)") < typed.index("LIMIT $3")
