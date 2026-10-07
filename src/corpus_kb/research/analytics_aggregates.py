"""Aggregate (non-ANN) analytics reads over the research read models (todo 17).

Split from analytics_sql.py (250-line soft limit): THIS module owns bounded
SQL GROUP BY / PK lookups (code centroids over embedding_256, assigned-unit
vectors, source types); analytics_sql.py owns the HNSW kNN access patterns.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.projections.research._embed import _parse_pg_vector

CENTROID_SQL = """
SELECT ra.code_id, avg(ru.embedding_256) AS centroid, COUNT(*) AS n_units
FROM research_assignments ra
JOIN research_units ru
  ON ru.unit_id = ra.unit_id AND ru.tenant_id = ra.tenant_id
WHERE ra.cb_version_id = $1 AND ru.embedding_256 IS NOT NULL
GROUP BY ra.code_id
"""

CODED_VECTORS_SQL = """
SELECT ra.unit_id, ra.code_id, ra.status, ru.embedding_256 AS vec
FROM research_assignments ra
JOIN research_units ru
  ON ru.unit_id = ra.unit_id AND ru.tenant_id = ra.tenant_id
WHERE ra.cb_version_id = $1 AND ru.embedding_256 IS NOT NULL
ORDER BY ra.unit_id
"""

SOURCE_TYPE_SQL = """
SELECT u.unit_id, d.source_type
FROM research_units u
JOIN documents d ON d.doc_id = u.doc_id
WHERE u.unit_id = ANY($1::bigint[])
"""


async def code_centroids_256(
    conn: asyncpg.Connection, codebook_version_id: UUID
) -> dict[str, list[float]]:
    """Per-code answer-space centroids: SQL avg() over embedding_256."""
    rows = await conn.fetch(CENTROID_SQL, str(codebook_version_id))
    out: dict[str, list[float]] = {}
    for row in rows:
        if row["centroid"]:
            out[str(row["code_id"])] = [float(x) for x in _parse_pg_vector(row["centroid"])]
    return out


async def coded_unit_vectors_256(
    conn: asyncpg.Connection, codebook_version_id: UUID
) -> list[dict[str, Any]]:
    """Assigned units' 256-d vectors + code ids (overlap/silhouette inputs)."""
    rows = await conn.fetch(CODED_VECTORS_SQL, str(codebook_version_id))
    return [
        {
            "unit_id": int(row["unit_id"]),
            "code_id": str(row["code_id"]),
            "status": str(row["status"]),
            "vec": [float(x) for x in _parse_pg_vector(row["vec"])],
        }
        for row in rows
    ]


async def source_types_for_units(conn: asyncpg.Connection, unit_ids: list[int]) -> dict[int, str]:
    """documents.source_type per unit id (PK-joined lookup, not a scan)."""
    if not unit_ids:
        return {}
    rows = await conn.fetch(SOURCE_TYPE_SQL, unit_ids)
    return {int(row["unit_id"]): str(row["source_type"]) for row in rows}
