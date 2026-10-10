"""SQL-side kNN analytics on the 016 embedding_256 column (todo 17, r7/r10).

ALL kNN analytics run inside the database via the pinned HNSW probe
``embedding_256 <=> $1::vector(256)`` (tenant-scoped through the tenant GUC
+ FORCE RLS) — never Python-side O(n·m) full-vector scans. Per the MRL
contract (Kusupati et al. 2022; r10): the 256-d HNSW scan RETRIEVES the
candidate top-k id sets (one ordered scan per prototype), then surfaced
candidates are RE-RANKED at the FULL 1024-d vectors by exact cosine
(trivial reduction over the retrieved rows — ids fetched by PK, never a
table scan). Final residual/ranking values are therefore full-dim exact,
consistent with the 1024-d tau_res calibration. Recall@k delta of the
256-d slice vs full-dim is 0.0 on the todo-13 G1 artifact (MRL report).

The retrieval leg uses the halfvec(1024) cast index with the pinned probe
``embedding::halfvec(1024) <=> $1::halfvec(1024)`` — one primary index per
access pattern (r11); HALFVEC_PROBE_SQL exists for the EXPLAIN proof.

U40 (P3): every analytics KNN read follows the relaxed-ordering pattern —
the ANN scan (filters + distance ORDER BY + LIMIT) stays INSIDE a
``WITH candidate AS MATERIALIZED`` CTE and the exact ordering is applied
OUTSIDE over the retrieved set (``dist + 0`` defeats planner sort-elision,
per the pgvector docs). These reads pull fixed-shaped candidate sets (one
ordered scan per prototype), so the materialization costs nothing extra.

``explain_plan`` captures the EXPLAIN text so the fixture can assert index
usage. ``force_index=True`` emits ``SET LOCAL enable_seqscan = off`` inside
an explicit transaction (autocommit would drop the SET LOCAL) — on
fixture-sized tables the btree+sort plan is otherwise cheaper; production
leaves the planner free.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import asyncpg
import numpy as np

from corpus_kb.research.exhaustiveness import max_cos_by_id

RETRIEVAL_UNITS_SQL = """
WITH candidate AS MATERIALIZED (
    SELECT ru.unit_id,
           ru.embedding_256 <=> $1::vector(256) AS dist
    FROM research_units ru
    WHERE ru.is_codable AND ru.role_in_exchange = 'answer'
      AND ru.embedding_256 IS NOT NULL
    ORDER BY ru.embedding_256 <=> $1::vector(256)
    LIMIT $2
)
SELECT candidate.unit_id
FROM candidate
ORDER BY candidate.dist + 0 ASC
"""

RETRIEVAL_UNITS_TYPED_SQL = """
WITH candidate AS MATERIALIZED (
    SELECT ru.unit_id,
           ru.embedding_256 <=> $1::vector(256) AS dist
    FROM research_units ru
    JOIN documents d ON d.doc_id = ru.doc_id
    WHERE ru.is_codable AND ru.role_in_exchange = 'answer'
      AND ru.embedding_256 IS NOT NULL
      AND d.source_type = $2::text
    ORDER BY ru.embedding_256 <=> $1::vector(256)
    LIMIT $3
)
SELECT candidate.unit_id
FROM candidate
ORDER BY candidate.dist + 0 ASC
"""

RETRIEVAL_EXCHANGES_SQL = """
WITH candidate AS MATERIALIZED (
    SELECT e.exchange_id,
           e.embedding_256 <=> $1::vector(256) AS dist
    FROM research_exchanges e
    WHERE e.embedding_256 IS NOT NULL
    ORDER BY e.embedding_256 <=> $1::vector(256)
    LIMIT $2
)
SELECT candidate.exchange_id
FROM candidate
ORDER BY candidate.dist + 0 ASC
"""

RETRIEVAL_UNCODED_SQL = """
WITH candidate AS MATERIALIZED (
    SELECT ru.unit_id,
           ru.embedding_256 <=> $1::vector(256) AS dist
    FROM research_units ru
    WHERE ru.is_codable AND ru.role_in_exchange = 'answer'
      AND ru.embedding_256 IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM research_assignments ra
          WHERE ra.tenant_id = ru.tenant_id AND ra.unit_id = ru.unit_id
      )
    ORDER BY ru.embedding_256 <=> $1::vector(256)
    LIMIT $2
)
SELECT candidate.unit_id
FROM candidate
ORDER BY candidate.dist + 0 ASC
"""

HALFVEC_PROBE_SQL = """
WITH candidate AS MATERIALIZED (
    SELECT ru.unit_id,
           ru.embedding::halfvec(1024) <=> $1::halfvec(1024) AS dist
    FROM research_units ru
    WHERE ru.is_codable AND ru.role_in_exchange = 'answer' AND ru.embedding IS NOT NULL
    ORDER BY ru.embedding::halfvec(1024) <=> $1::halfvec(1024)
    LIMIT $2
)
SELECT candidate.unit_id
FROM candidate
ORDER BY candidate.dist + 0 ASC
"""

UNIT_VECTORS_SQL = """
SELECT unit_id, embedding FROM research_units WHERE unit_id = ANY($1::bigint[])
"""

UNIT_TEXT_VECTORS_SQL = """
SELECT unit_id, text, embedding FROM research_units WHERE unit_id = ANY($1::bigint[])
"""

EXCHANGE_VECTORS_SQL = """
SELECT exchange_id, embedding FROM research_exchanges
WHERE exchange_id = ANY($1::bigint[])
"""

EXCHANGE_ANSWER_UNITS_SQL = """
SELECT unit_id, exchange_id FROM research_units
WHERE exchange_id = ANY($1::bigint[]) AND role_in_exchange = 'answer' AND is_codable
"""

_FETCH_CHUNK = 500


def mrl_256(vector: Sequence[float]) -> list[float]:
    """256-d MRL slice of a 1024-d vector, L2-renormalized."""
    arr = np.asarray(vector[:256], dtype=np.float32)
    if len(arr) < 256:
        raise ValueError(f"prototype has {len(arr)} dims; need >= 256 for the MRL slice")
    norm = float(np.linalg.norm(arr))
    if norm == 0.0:
        raise ValueError("zero prototype has no MRL slice")
    return (arr / norm).tolist()


def probe_256(vector: Sequence[float]) -> str:
    """256-d MRL probe literal for the analytics HNSW scan."""
    return "[" + ",".join(f"{x:.9g}" for x in mrl_256(vector)) + "]"


async def _retrieve_ids(
    conn: asyncpg.Connection, sql: str, params: list[object], force_index: bool
) -> list[int]:
    if force_index:
        async with conn.transaction():
            await conn.execute("SET LOCAL enable_seqscan = off")
            await conn.execute("SET LOCAL enable_sort = off")
            rows = await conn.fetch(sql, *params)
    else:
        rows = await conn.fetch(sql, *params)
    return [int(row[0]) for row in rows]


async def _fetch_in_chunks(conn: asyncpg.Connection, sql: str, ids: list[int]) -> list[Any]:
    rows: list[Any] = []
    for start in range(0, len(ids), _FETCH_CHUNK):
        rows.extend(await conn.fetch(sql, ids[start : start + _FETCH_CHUNK]))
    return rows


async def residual_scan(
    conn: asyncpg.Connection,
    prototype_vectors: list[list[float]],
    k: int,
    source_type: str | None = None,
    force_index: bool = False,
) -> dict[int, float]:
    """Per-unit residual: max over prototypes of the FULL 1024-d cosine.

    Candidates are retrieved by the 256-d HNSW scan (one ordered read per
    prototype); the cross-prototype max is computed at full dimension over
    the retrieved rows (the MRL re-rank; k >= unit count gives exactness).
    """
    sql = RETRIEVAL_UNITS_TYPED_SQL if source_type is not None else RETRIEVAL_UNITS_SQL
    candidate_ids: set[int] = set()
    for proto in prototype_vectors:
        literal = probe_256(proto)
        params = [literal, source_type, k] if source_type is not None else [literal, k]
        candidate_ids.update(await _retrieve_ids(conn, sql, params, force_index))
    if not candidate_ids or not prototype_vectors:
        return {}
    rows = await _fetch_in_chunks(conn, UNIT_VECTORS_SQL, sorted(candidate_ids))
    vectors_by_id: dict[int, list[float]] = {
        int(row["unit_id"]): _parse_vector(row["embedding"]) for row in rows
    }
    return max_cos_by_id(vectors_by_id, prototype_vectors)


async def residual_scan_exchanges(
    conn: asyncpg.Connection,
    qa_prototypes: list[list[float]],
    k: int,
    force_index: bool = False,
) -> dict[int, float]:
    """QA-view residual leg: exchange-embedding probes mapped to answer units."""
    candidate_ids: set[int] = set()
    for proto in qa_prototypes:
        candidate_ids.update(
            await _retrieve_ids(conn, RETRIEVAL_EXCHANGES_SQL, [probe_256(proto), k], force_index)
        )
    if not candidate_ids or not qa_prototypes:
        return {}
    ordered = sorted(candidate_ids)
    xrows = await _fetch_in_chunks(conn, EXCHANGE_VECTORS_SQL, ordered)
    urows = await _fetch_in_chunks(conn, EXCHANGE_ANSWER_UNITS_SQL, ordered)
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
    conn: asyncpg.Connection,
    prototype_vectors: list[list[float]],
    k: int,
    force_index: bool = False,
) -> list[dict[str, Any]]:
    """Units with NO assignment, ascending residual (the missing-code radar).

    Same MRL pattern as residual_scan: 256-d HNSW candidate retrieval, full
    1024-d re-rank. k >= codable-unit count gives the exact ranking.
    """
    candidate_ids: set[int] = set()
    for proto in prototype_vectors:
        candidate_ids.update(
            await _retrieve_ids(conn, RETRIEVAL_UNCODED_SQL, [probe_256(proto), k], force_index)
        )
    if not candidate_ids:
        return []
    rows = await _fetch_in_chunks(conn, UNIT_TEXT_VECTORS_SQL, sorted(candidate_ids))
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
    conn: asyncpg.Connection,
    sql: str,
    params: list[object],
    force_index: bool = True,
) -> str:
    """EXPLAIN text for one analytics query (the fixture's index-usage proof)."""
    literal_params = [f"'{p}'" if isinstance(p, str) else str(p) for p in params]
    explained = sql
    for i, lit in enumerate(literal_params, start=1):
        explained = explained.replace(f"${i}", lit)
    async with conn.transaction():
        if force_index:
            await conn.execute("SET LOCAL enable_seqscan = off")
            await conn.execute("SET LOCAL enable_sort = off")
        rows = await conn.fetch(f"EXPLAIN (FORMAT TEXT) {explained}")
    return "\n".join(str(row["QUERY PLAN"]) for row in rows)


def _parse_vector(raw: Any) -> list[float]:
    text = str(raw).strip().strip("[]")
    return [float(x) for x in text.split(",")] if text and text != "None" else []
