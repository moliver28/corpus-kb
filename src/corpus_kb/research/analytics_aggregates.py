"""Aggregate analytics reads plus the engine-dispatched kNN run legs.

The aggregate (non-ANN) half — code centroids, assigned-unit vectors,
source types — split here from ``analytics_sql.py`` at authoring; the
engine-dispatched runner half (Postgres planner flags, DuckDB exact-scan
twins, and the public ``residual_scan`` family) moved here from
``analytics_sql.py`` under the same split-don't-grow rule.

Split from ``analytics_sql.py`` (storage portability program §13: split,
don't grow). ``analytics_sql.py`` owns the SQL constants (the file the
determinism suite scans); this module owns the runners: the Postgres
planner-flag path, the DuckDB exact-scan twins, and the public
``residual_scan`` / ``residual_scan_exchanges`` / ``uncoded_units_ranking``
/ ``explain_plan`` entries (re-exported from ``analytics_sql`` for the
callers).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from corpus_kb.projections.research._embed import _parse_pg_vector
from corpus_kb.research.conn import SqlConn

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


async def code_centroids_256(conn: SqlConn, codebook_version_id: UUID) -> dict[str, list[float]]:
    """Per-code answer-space centroids: SQL avg() over embedding_256."""
    rows = await conn.fetch(CENTROID_SQL, str(codebook_version_id))
    out: dict[str, list[float]] = {}
    for row in rows:
        if row["centroid"]:
            out[str(row["code_id"])] = [float(x) for x in _parse_pg_vector(row["centroid"])]
    return out


async def coded_unit_vectors_256(conn: SqlConn, codebook_version_id: UUID) -> list[dict[str, Any]]:
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


async def source_types_for_units(conn: SqlConn, unit_ids: list[int]) -> dict[int, str]:
    """documents.source_type per unit id (PK-joined lookup, not a scan)."""
    if not unit_ids:
        return {}
    rows = await conn.fetch(SOURCE_TYPE_SQL, unit_ids)
    return {int(row["unit_id"]): str(row["source_type"]) for row in rows}
