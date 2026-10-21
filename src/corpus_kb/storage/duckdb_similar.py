"""DuckDB search_similar arm: similar chunks over the mirror schema (§13).

Serves ``QueryHandler.handle_search_similar`` on a DuckDB deployment with
FULL parity to the pgvector shape (handlers/query_crud.py keeps the
dual-path dispatch and the row mapping; storage must not import handlers,
program rule 5). One exact cosine scan over ``chunks_vectors`` — DuckDB
ships no HNSW in this regime — with the same contract pins as the query
arm in ``duckdb_search.py``:

- The seed embedding is fetched first and bound as the compact varchar
  behind the SQL's explicit ``FLOAT[k]`` cast — never a bound Python list
  (2.7 s per 2560-float statement vs ~10 ms; the canonical formatter is
  ``duckdb_support.vector_literal``).
- Zero-vector candidate rows (a degraded store row the write path missed)
  take the ``sqrt(array_inner_product(v,v)) > 0`` guard as an explicit
  sentinel flag and sort LAST; pgvector would order them by NaN.
- Deterministic total order (program rule 9): sentinel ASC, similarity
  DESC, then the ``chunk_id`` tiebreaker.
- A chunk with no stored embedding seeds nothing and returns no rows —
  the same observable result as the PG arm's empty seed subquery.

The degraded filter stays owned by the caller (the mirrored
``handlers.query_integrity`` fragment rides in as ``degraded_pred``, the
same shape ``SearchRequest`` uses), so the seed lookup stays unfiltered
and a degraded seed chunk remains discoverable, like Postgres.
"""

from __future__ import annotations

from typing import Any

from corpus_kb.storage.dialect import DUCKDB
from corpus_kb.storage.duckdb_support import vector_literal
from corpus_kb.storage.gateway import SqlGateway

_SEED_SQL = "SELECT embedding FROM chunks_vectors WHERE tenant_id = $1 AND chunk_id = $2"

_DISTANCE = DUCKDB.vector_distance("cv.embedding", "$3")
_ZERO_FLAG = f"CASE WHEN NOT ({DUCKDB.zero_vector_guard('cv.embedding')}) THEN 1 ELSE 0 END"

_SIMILAR_SQL = f"""
WITH candidate AS MATERIALIZED (
    SELECT cv.chunk_id AS candidate_chunk_id,
           {_DISTANCE} AS dist,
           {_ZERO_FLAG} AS zero_flag
    FROM chunks_vectors cv
    WHERE cv.tenant_id = $1 AND cv.chunk_id != $2 {{degraded_pred}}
    ORDER BY {_DISTANCE}
    LIMIT $4
)
SELECT c.chunk_id, c.text, c.doc_id, d.source,
       c.file_path, c.start_line, c.end_line, c.chunk_index, c.heading_path,
       (1 - candidate.dist) AS score
FROM candidate
JOIN chunks c ON candidate.candidate_chunk_id = c.chunk_id
JOIN documents d ON c.doc_id = d.doc_id
WHERE c.tombstoned_at IS NULL AND c.superseded_at IS NULL
ORDER BY candidate.zero_flag ASC, (1 - candidate.dist) DESC, c.chunk_id ASC
LIMIT $4
"""


async def similar_rows(
    reader: SqlGateway,
    *,
    chunk_id: str,
    tenant_id: str,
    k: int,
    degraded_pred: str,
) -> list[Any]:
    """Rows for the k nearest chunks to ``chunk_id``'s stored vector.

    ``reader`` is the gateway inside the caller's tenant transaction
    scope. The seed varchar and the three scalar binds ride one statement;
    the repeated ``$4`` LIMIT is one prepared parameter used twice.
    """
    seed = await reader.fetchval(_SEED_SQL, tenant_id, chunk_id)
    if seed is None:
        return []
    rows = await reader.fetch(
        _SIMILAR_SQL.format(degraded_pred=degraded_pred),
        tenant_id,
        chunk_id,
        vector_literal(list(seed)),
        k,
    )
    return list(rows)
