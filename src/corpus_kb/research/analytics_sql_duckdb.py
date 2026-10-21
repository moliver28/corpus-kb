"""DuckDB analytics twins (storage portability program §13, sp-research-port).

``research/analytics_sql.py`` is file-scanned by the determinism suite
(exactly five ``ORDER BY candidate.dist + 0`` clauses), so the DuckDB
forms live here:

- kNN retrieval: the exact cosine scan replaces the pgvector probe —
  ``array_cosine_distance(embedding_256, $n::FLOAT[256])``; the U40 shape
  (candidate CTE, exact re-order outside) and the stable-id tiebreaker are
  preserved. DuckDB has no planner flags, so ``force_index`` is a no-op.
- id reads: ``= ANY($n::bigint[])`` becomes ``list_contains``.
- ``EXPLAIN``: no ``FORMAT TEXT``; the plan text rides the single row's
  ``explain_value`` column.
"""

from __future__ import annotations

from typing import Any

from corpus_kb.research.conn import ids_any

RETRIEVAL_UNITS_SQL_DUCK = """
WITH candidate AS MATERIALIZED (
    SELECT ru.unit_id,
           1 - array_cosine_distance(ru.embedding_256, $1::FLOAT[256]) AS dist
    FROM research_units ru
    WHERE ru.is_codable AND ru.role_in_exchange = 'answer'
      AND ru.embedding_256 IS NOT NULL
)
SELECT candidate.unit_id
FROM candidate
ORDER BY candidate.dist + 0, candidate.unit_id ASC
LIMIT $2
"""

RETRIEVAL_UNITS_TYPED_SQL_DUCK = """
WITH candidate AS MATERIALIZED (
    SELECT ru.unit_id,
           1 - array_cosine_distance(ru.embedding_256, $1::FLOAT[256]) AS dist
    FROM research_units ru
    JOIN documents d ON d.doc_id = ru.doc_id
    WHERE ru.is_codable AND ru.role_in_exchange = 'answer'
      AND ru.embedding_256 IS NOT NULL
      AND d.source_type = $2
)
SELECT candidate.unit_id
FROM candidate
ORDER BY candidate.dist + 0, candidate.unit_id ASC
LIMIT $3
"""

RETRIEVAL_EXCHANGES_SQL_DUCK = """
WITH candidate AS MATERIALIZED (
    SELECT e.exchange_id,
           1 - array_cosine_distance(e.embedding_256, $1::FLOAT[256]) AS dist
    FROM research_exchanges e
    WHERE e.embedding_256 IS NOT NULL
)
SELECT candidate.exchange_id
FROM candidate
ORDER BY candidate.dist + 0, candidate.exchange_id ASC
LIMIT $2
"""

RETRIEVAL_UNCODED_SQL_DUCK = """
WITH candidate AS MATERIALIZED (
    SELECT ru.unit_id,
           1 - array_cosine_distance(ru.embedding_256, $1::FLOAT[256]) AS dist
    FROM research_units ru
    WHERE ru.is_codable AND ru.role_in_exchange = 'answer'
      AND ru.embedding_256 IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM research_assignments ra
          WHERE ra.tenant_id = ru.tenant_id AND ra.unit_id = ru.unit_id
      )
)
SELECT candidate.unit_id
FROM candidate
ORDER BY candidate.dist + 0, candidate.unit_id ASC
LIMIT $2
"""

UNIT_VECTORS_SQL_DUCK = f"""
SELECT unit_id, embedding FROM research_units WHERE {ids_any("$1", "unit_id", True, "bigint")}
"""

UNIT_TEXT_VECTORS_SQL_DUCK = f"""
SELECT unit_id, text, embedding FROM research_units
WHERE {ids_any("$1", "unit_id", True, "bigint")}
"""

EXCHANGE_VECTORS_SQL_DUCK = f"""
SELECT exchange_id, embedding FROM research_exchanges
WHERE {ids_any("$1", "exchange_id", True, "bigint")}
"""

EXCHANGE_ANSWER_UNITS_SQL_DUCK = f"""
SELECT unit_id, exchange_id FROM research_units
WHERE {ids_any("$1", "exchange_id", True, "bigint")}
  AND role_in_exchange = 'answer' AND is_codable
"""


async def retrieve_ids(conn: Any, sql: str, params: list[object]) -> list[int]:
    """DuckDB ``_retrieve_ids``: no planner flags exist, so one plain fetch."""
    rows = await conn.fetch(sql, *params)
    return [int(row[0]) for row in rows]
