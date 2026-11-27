"""Scaffold report reads: the tenant-scoped fetches and the small blocks.

Split out of scaffold_report.py (the T0-6 report text grew that file past
the 250-line gate). Every read is deterministic, tenant-scoped, and
model-free: embeddings come from stored vectors only (wave law).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from corpus_kb.coding._scope import CodingStore, is_gateway, tenant_scope
from corpus_kb.coding.mece_report import MAX_CHUNKS, share

__all__ = ["coverage_gaps_block", "fetch_inputs", "keyword_precision_block"]


async def fetch_inputs(
    pool: CodingStore, tenant_id: UUID, codebook_version_id: UUID
) -> dict[str, Any]:
    """One read pass over stored state; every query is tenant-scoped."""
    async with tenant_scope(pool, tenant_id) as conn:
        codes = await conn.fetch(
            "SELECT code_id, name, definition_vector FROM code_registry "
            "WHERE tenant_id=$1 AND codebook_version_id=$2 ORDER BY code_id",
            tenant_id,
            codebook_version_id,
        )
        vector_rows = await conn.fetch(
            "SELECT v.chunk_id, v.embedding, v.embedding_model, c.text "
            "FROM chunks_vectors v JOIN chunks c"
            "  ON c.chunk_id = v.chunk_id AND c.tenant_id = v.tenant_id "
            "WHERE v.tenant_id=$1 ORDER BY v.chunk_id LIMIT $2",
            tenant_id,
            MAX_CHUNKS,
        )
        coded = await conn.fetch(
            "SELECT chunk_id, code_id FROM chunk_codes "
            "WHERE tenant_id=$1 AND decision='assign' ORDER BY chunk_id, code_id",
            tenant_id,
        )
        keyword_hits = await conn.fetch(
            "SELECT DISTINCT chunk_id, code_id FROM chunk_keyword_hits "
            "WHERE tenant_id=$1 AND kind='inclusion' ORDER BY chunk_id, code_id",
            tenant_id,
        )
    return {
        "codes": codes,
        "vector_rows": [dict(r) for r in vector_rows],
        "coded": [dict(r) for r in coded],
        "keyword_hits": [dict(r) for r in keyword_hits],
    }


async def keyword_precision_block(pool: CodingStore, tenant_id: UUID) -> dict[str, Any]:
    """Per-keyword coverage from the materialized hits; precision waits for seeds.

    TD-10 point 5 asks for per-keyword precision on a seed set. No graded
    seed labels exist at the scaffold gate, so precision reports
    ``not_measured`` and coverage states its n. The counts aggregate the
    same chunk_keyword_hits rows keyword_governance_report reads.
    """
    async with tenant_scope(pool, tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT k.keyword, k.code_id, k.kind, k.source, "
            "COALESCE(h.hit_chunks, 0) AS hit_chunks "
            "FROM code_keywords k LEFT JOIN ("
            "  SELECT code_id, keyword, kind, COUNT(DISTINCT chunk_id) AS hit_chunks"
            "  FROM chunk_keyword_hits WHERE tenant_id=$1 GROUP BY code_id, keyword, kind"
            ") h ON h.code_id = k.code_id AND h.keyword = k.keyword AND h.kind = k.kind "
            "WHERE k.tenant_id=$1 AND k.status='active' "
            "ORDER BY k.code_id, k.kind, k.keyword",
            tenant_id,
        )
        corpus_chunks = await conn.fetchval(
            "SELECT COUNT(*) FROM chunks WHERE tenant_id=$1", tenant_id
        )
    total = int(corpus_chunks or 0)
    keywords = []
    for row in rows:
        hit_chunks = int(row["hit_chunks"])
        keywords.append(
            {
                "keyword": str(row["keyword"]),
                "code_id": str(row["code_id"]),
                "kind": str(row["kind"]),
                "source": str(row["source"]),
                "hit_chunks": hit_chunks,
                "coverage": share(hit_chunks, total),
                "precision": "not_measured",
            }
        )
    return {
        "status": "success",
        "keywords": keywords,
        "n_keywords": len(keywords),
        "precision_note": "precision names a seed set; the scaffold has none yet",
    }


async def coverage_gaps_block(
    pool: CodingStore, tenant_id: UUID, codebook_version_id: UUID
) -> dict[str, Any]:
    """Share of chunks missing a chunk_signals tier-0 row per active code (T0-3).

    Tier 0 is the similarity signal (``signal='vector'``); the fill runs on
    the next ``run_similarity_pooling`` call, so this block only measures.
    """
    async with tenant_scope(pool, tenant_id) as conn:
        codes = await conn.fetch(
            "SELECT code_id FROM code_registry WHERE tenant_id=$1 AND codebook_version_id=$2"
            " ORDER BY code_id",
            tenant_id,
            codebook_version_id,
        )
        corpus_chunks = await conn.fetchval(
            "SELECT COUNT(*) FROM chunks WHERE tenant_id=$1", tenant_id
        )
        # The gateway arm ignores parked rows (score NULL), like reconcile.
        parked = "AND score IS NOT NULL" if is_gateway(pool) else ""
        pooled = await conn.fetch(
            "SELECT code_id, COUNT(DISTINCT chunk_id) AS n FROM chunk_signals "
            f"WHERE tenant_id=$1 AND signal='vector' {parked} GROUP BY code_id",
            tenant_id,
        )
    pooled_by_code = {str(r["code_id"]): int(r["n"]) for r in pooled}
    total = int(corpus_chunks or 0)
    per_code = []
    for row in codes:
        code_id = str(row["code_id"])
        missing = total - min(pooled_by_code.get(code_id, 0), total)
        per_code.append(
            {
                "code_id": code_id,
                "n_chunks": total,
                "n_missing": missing,
                "missing_share": share(missing, total),
            }
        )
    return {
        "status": "success",
        "per_code": per_code,
        "note": "tier-0 fill runs on the next similarity-pooling call",
    }
