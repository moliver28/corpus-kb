"""Coding status + export read bodies (issue #350).

One operation, one handler: ``coding_status`` and ``coding_export`` are
backend-neutral reads over the coding tables through the SAME
``SqlGateway`` seam the projections use (tenant GUC inside the
transaction on Postgres, explicit ``tenant_id`` predicate on DuckDB).
Plugin 0.6.0 built the status from four hand-written SQL queries and the
export from hand-written SQL plus a JSON-to-CSV step; the engine owns
both now, on both engines.

``coding_status`` reuses :func:`coding.saturation_query.compute_saturation`
for its saturation ratio (the SAME implementation the saturation route
serves), so no second copy of the ISR math exists.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from corpus_kb.coding._scope import CodingStore
from corpus_kb.coding.saturation_query import compute_saturation
from corpus_kb.storage.gateway import as_gateway, gateway_backend

#: The full disposition vocabulary, zero-filled so a disposition with no
#: rows still answers (deterministic key set for harness snapshots).
_DISPOSITIONS = ("accepted", "rejected", "manual_review")

#: The export's stable column order (the CSV header on the CLI).
EXPORT_COLUMNS = (
    "chunk_id",
    "code_id",
    "confidence",
    "decided_at",
    "text",
    "document_source",
)

_EXPORT_SELECT = """
    SELECT fc.chunk_id, fc.code_id, fc.confidence, fc.decided_at,
           c.text AS text, d.source AS document_source
    FROM final_codes fc
    JOIN chunks c ON c.chunk_id = fc.chunk_id AND c.tenant_id = fc.tenant_id
    JOIN documents d ON d.doc_id = c.doc_id AND d.tenant_id = fc.tenant_id
    WHERE fc.tenant_id = {tenant} AND fc.status = 'accepted'
    ORDER BY fc.chunk_id, fc.code_id
    LIMIT {limit} OFFSET {offset}
"""


def _sql(is_duckdb: bool) -> dict[str, str]:
    """The backend statement variants (casts differ; text stays parallel)."""
    tenant = "$1" if is_duckdb else "$1::uuid"
    return {
        "documents_present": (
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'documents'"
            if is_duckdb
            else "SELECT to_regclass('public.documents')"
        ),
        "documents": f"SELECT count(*) FROM documents WHERE tenant_id = {tenant}",
        "chunks": (
            f"SELECT count(*) FROM chunks WHERE tenant_id = {tenant} AND tombstoned_at IS NULL"
        ),
        "coverage": (
            f"SELECT status, count(*) AS n FROM final_codes"
            f" WHERE tenant_id = {tenant} GROUP BY status ORDER BY status"
        ),
        "routes": (
            f"SELECT route, count(*) AS n FROM final_codes"
            f" WHERE tenant_id = {tenant} GROUP BY route ORDER BY route"
        ),
        "export": _EXPORT_SELECT.format(tenant=tenant, limit="$2", offset="$3"),
    }


async def coding_status(pool: CodingStore, tenant_id: str) -> dict[str, Any]:
    """Corpus counts, saturation ratio, coverage by disposition, route counts.

    Returns ``{status, documents, chunks, saturation, coverage, routes}``.
    ``saturation`` carries the ISR ratio plus its unique-code and
    application counts (the compute_saturation implementation). Missing
    tables answer ``status: "unavailable"`` with the setup remedy, never a
    crash.
    """
    gw = as_gateway(pool)
    sql = _sql(gateway_backend(gw) == "duckdb")
    async with gw.transaction(tenant_id) as tx:
        if not await tx.fetchval(sql["documents_present"]):
            return {
                "status": "unavailable",
                "reason": "documents table absent (run corpus-kb setup)",
                "documents": 0,
                "chunks": 0,
                "saturation": {},
                "coverage": {},
                "routes": {},
            }
        documents = await tx.fetchval(sql["documents"], tenant_id)
        chunks = await tx.fetchval(sql["chunks"], tenant_id)
        coverage_rows = await tx.fetch(sql["coverage"], tenant_id)
        route_rows = await tx.fetch(sql["routes"], tenant_id)
    coverage: dict[str, int] = dict.fromkeys(_DISPOSITIONS, 0)
    for row in coverage_rows:
        coverage[str(row["status"])] = int(row["n"])
    routes = {str(row["route"]): int(row["n"]) for row in route_rows}
    saturation = await compute_saturation(gw, UUID(tenant_id))
    slim = {
        key: saturation[key]
        for key in ("unique_codes", "total_applications", "isr")
        if key in saturation
    }
    return {
        "status": "success",
        "documents": int(documents),
        "chunks": int(chunks or 0),
        "saturation": slim,
        "coverage": coverage,
        "routes": routes,
    }


async def coding_export(
    pool: CodingStore,
    tenant_id: str,
    limit: int = 1000,
    offset: int = 0,
) -> dict[str, Any]:
    """Accepted codes joined with chunk text and document source, as rows.

    Returns ``{status, columns, rows, count}``; ``columns`` pins the stable
    field order (the CLI's CSV header). Rows order by (chunk_id, code_id)
    so snapshots stay deterministic. Missing tables answer
    ``status: "unavailable"`` with the setup remedy, never a crash.
    """
    gw = as_gateway(pool)
    sql = _sql(gateway_backend(gw) == "duckdb")
    bounded_limit = max(1, min(int(limit), 10000))
    bounded_offset = max(0, int(offset))
    async with gw.transaction(tenant_id) as tx:
        if not await tx.fetchval(sql["documents_present"]):
            return {
                "status": "unavailable",
                "reason": "documents table absent (run corpus-kb setup)",
                "columns": list(EXPORT_COLUMNS),
                "rows": [],
                "count": 0,
            }
        rows = await tx.fetch(sql["export"], tenant_id, bounded_limit, bounded_offset)
    serialized = [
        {
            "chunk_id": str(row["chunk_id"]),
            "code_id": str(row["code_id"]),
            "confidence": float(row["confidence"]),
            "decided_at": row["decided_at"].isoformat() if row["decided_at"] else None,
            "text": row["text"],
            "document_source": row["document_source"],
        }
        for row in rows
    ]
    return {
        "status": "success",
        "columns": list(EXPORT_COLUMNS),
        "rows": serialized,
        "count": len(serialized),
    }
