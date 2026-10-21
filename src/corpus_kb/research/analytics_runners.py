"""Engine-dispatched kNN run legs over the pinned analytics SQL.

Split from ``analytics_aggregates.py`` (storage portability program §13:
split, don't grow). The Postgres path keeps its planner-flag transaction
and byte-identical pgvector probes; DuckDB runs the exact-scan twins from
``analytics_sql_duckdb.py``. The public names re-export from
``analytics_sql`` (PEP 562), so callers never import this module directly.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from corpus_kb.research.analytics_sql import (
    _FETCH_CHUNK,
    EXCHANGE_ANSWER_UNITS_SQL,
    EXCHANGE_VECTORS_SQL,
    RETRIEVAL_EXCHANGES_SQL,
    RETRIEVAL_UNCODED_SQL,
    RETRIEVAL_UNITS_SQL,
    RETRIEVAL_UNITS_TYPED_SQL,
    UNIT_TEXT_VECTORS_SQL,
    UNIT_VECTORS_SQL,
    _parse_vector,
    probe_256,
)
from corpus_kb.research.conn import SqlConn
from corpus_kb.research.exhaustiveness import max_cos_by_id


class _PlannerConn(Protocol):
    """The session surface the Postgres planner-flag path needs (fixture-only)."""

    async def fetch(self, sql: str, /, *args: object) -> list[Any]:
        """Run one read."""
        ...

    async def execute(self, sql: str, /, *args: object) -> object:
        """Run one statement."""
        ...

    def transaction(self) -> AbstractAsyncContextManager[Any]:
        """Open one transaction scope (SET LOCAL dies in autocommit)."""
        ...


async def _retrieve_ids(
    conn: _PlannerConn, sql: str, params: list[object], force_index: bool
) -> list[int]:
    if force_index:
        async with conn.transaction():
            await conn.execute("SET LOCAL enable_seqscan = off")
            await conn.execute("SET LOCAL enable_sort = off")
            rows = await conn.fetch(sql, *params)
    else:
        rows = await conn.fetch(sql, *params)
    return [int(row[0]) for row in rows]


async def _fetch_in_chunks(conn: SqlConn, sql: str, ids: list[int]) -> list[Any]:
    rows: list[Any] = []
    for start in range(0, len(ids), _FETCH_CHUNK):
        rows.extend(await conn.fetch(sql, ids[start : start + _FETCH_CHUNK]))
    return rows


def _legs(duck: bool) -> tuple[dict[str, str], Any]:
    """The engine's retrieval/id-read SQL map and its probe runner.

    The DuckDB constants live in ``analytics_sql_duckdb.py`` (this module
    is file-scanned for exactly the Postgres ORDER BY clauses).
    """
    if not duck:
        return {
            "units_typed": RETRIEVAL_UNITS_TYPED_SQL,
            "units": RETRIEVAL_UNITS_SQL,
            "exchanges": RETRIEVAL_EXCHANGES_SQL,
            "uncoded": RETRIEVAL_UNCODED_SQL,
            "unit_vectors": UNIT_VECTORS_SQL,
            "unit_text_vectors": UNIT_TEXT_VECTORS_SQL,
            "exchange_vectors": EXCHANGE_VECTORS_SQL,
            "exchange_answer_units": EXCHANGE_ANSWER_UNITS_SQL,
        }, _retrieve_ids
    from corpus_kb.research import analytics_sql_duckdb as twin

    async def _plain(conn: SqlConn, sql: str, params: list[object], _force: bool) -> list[int]:
        return await twin.retrieve_ids(conn, sql, params)

    return {
        "units_typed": twin.RETRIEVAL_UNITS_TYPED_SQL_DUCK,
        "units": twin.RETRIEVAL_UNITS_SQL_DUCK,
        "exchanges": twin.RETRIEVAL_EXCHANGES_SQL_DUCK,
        "uncoded": twin.RETRIEVAL_UNCODED_SQL_DUCK,
        "unit_vectors": twin.UNIT_VECTORS_SQL_DUCK,
        "unit_text_vectors": twin.UNIT_TEXT_VECTORS_SQL_DUCK,
        "exchange_vectors": twin.EXCHANGE_VECTORS_SQL_DUCK,
        "exchange_answer_units": twin.EXCHANGE_ANSWER_UNITS_SQL_DUCK,
    }, _plain


async def residual_scan(
    conn: SqlConn,
    prototype_vectors: list[list[float]],
    k: int,
    source_type: str | None = None,
    force_index: bool = False,
    duck: bool = False,
) -> dict[int, float]:
    """Per-unit residual: max over prototypes of the FULL 1024-d cosine.

    Candidates are retrieved by the 256-d probe (one ordered read per
    prototype — the pgvector HNSW scan on Postgres, the exact cosine scan
    on DuckDB); the cross-prototype max is computed at full dimension over
    the retrieved rows (the MRL re-rank; k >= unit count gives exactness).
    """
    sql_map, retrieve = _legs(duck)
    base_sql = sql_map["units_typed" if source_type is not None else "units"]
    candidate_ids: set[int] = set()
    for proto in prototype_vectors:
        literal = probe_256(proto)
        params = [literal, source_type, k] if source_type is not None else [literal, k]
        candidate_ids.update(await retrieve(conn, base_sql, params, force_index))
    if not candidate_ids or not prototype_vectors:
        return {}
    rows = await _fetch_in_chunks(conn, sql_map["unit_vectors"], sorted(candidate_ids))
    vectors_by_id: dict[int, list[float]] = {
        int(row["unit_id"]): _parse_vector(row["embedding"]) for row in rows
    }
    return max_cos_by_id(vectors_by_id, prototype_vectors)


async def residual_scan_exchanges(
    conn: SqlConn,
    qa_prototypes: list[list[float]],
    k: int,
    force_index: bool = False,
    duck: bool = False,
) -> dict[int, float]:
    """QA-view residual leg: exchange-embedding probes mapped to answer units."""
    sql_map, retrieve = _legs(duck)
    candidate_ids: set[int] = set()
    for proto in qa_prototypes:
        candidate_ids.update(
            await retrieve(conn, sql_map["exchanges"], [probe_256(proto), k], force_index)
        )
    if not candidate_ids or not qa_prototypes:
        return {}
    ordered = sorted(candidate_ids)
    xrows = await _fetch_in_chunks(conn, sql_map["exchange_vectors"], ordered)
    urows = await _fetch_in_chunks(conn, sql_map["exchange_answer_units"], ordered)
    vectors = {
        int(row["exchange_id"]): _parse_vector(row["embedding"])
        for row in xrows
        if row["embedding"]
    }
    sims = max_cos_by_id(vectors, qa_prototypes)
    best: dict[int, float] = {}
    for row in urows:
        sim = sims.get(int(row["exchange_id"]))
        if sim is not None:
            unit_id = int(row["unit_id"])
            best[unit_id] = max(best.get(unit_id, -2.0), sim)
    return best


async def uncoded_units_ranking(
    conn: SqlConn,
    prototype_vectors: list[list[float]],
    k: int,
    force_index: bool = False,
    duck: bool = False,
) -> list[dict[str, Any]]:
    """Units with NO assignment, ascending residual (the missing-code radar).

    Same MRL pattern as residual_scan: 256-d probe candidate retrieval, full
    1024-d re-rank. k >= codable-unit count gives the exact ranking.
    """
    sql_map, retrieve = _legs(duck)
    candidate_ids: set[int] = set()
    for proto in prototype_vectors:
        candidate_ids.update(
            await retrieve(conn, sql_map["uncoded"], [probe_256(proto), k], force_index)
        )
    if not candidate_ids:
        return []
    rows = await _fetch_in_chunks(conn, sql_map["unit_text_vectors"], sorted(candidate_ids))
    vectors_by_id = {
        int(row["unit_id"]): _parse_vector(row["embedding"]) for row in rows if row["embedding"]
    }
    residuals = max_cos_by_id(vectors_by_id, prototype_vectors)
    texts = {int(row["unit_id"]): str(row["text"] or "") for row in rows}
    ranked = sorted(
        ({"unit_id": uid, "text": texts.get(uid, ""), "r": r} for uid, r in residuals.items()),
        key=lambda r: float(r["r"]),
    )
    return ranked[:k]


async def explain_plan(
    conn: _PlannerConn,
    sql: str,
    params: list[object],
    force_index: bool = True,
    duck: bool = False,
) -> str:
    """EXPLAIN text for one analytics query (the fixture's index-usage proof)."""
    literal_params = [f"'{p}'" if isinstance(p, str) else str(p) for p in params]
    explained = sql
    for i, lit in enumerate(literal_params, start=1):
        explained = explained.replace(f"${i}", lit)
    if duck:
        # DuckDB EXPLAIN yields one row (explain_key, explain_value).
        rows = await conn.fetch(f"EXPLAIN {explained}")
        return str(rows[0]["explain_value"])
    async with conn.transaction():
        if force_index:
            await conn.execute("SET LOCAL enable_seqscan = off")
            await conn.execute("SET LOCAL enable_sort = off")
        rows = await conn.fetch(f"EXPLAIN (FORMAT TEXT) {explained}")
    return "\n".join(str(row["QUERY PLAN"]) for row in rows)
