"""Read-model inputs for the governance report (todo 17).

Every loader runs inside a tenant connection (FORCE RLS scopes each row) and
returns plain dicts the report runner assembles into the pinned schema.
Analytics that are kNN-shaped live in analytics_sql (HNSW probes); these are
the bounded aggregate/lookup reads around them.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.projections.research._embed import _parse_pg_vector

ASSIGNMENT_SUMMARY_SQL = """
SELECT ra.unit_id, ra.code_id, ra.status, ra.evidence_basis, ra.rationale,
       ra.sim_answer, ra.sim_qa, d.source_type
FROM research_assignments ra
JOIN research_units ru ON ru.unit_id = ra.unit_id AND ru.tenant_id = ra.tenant_id
JOIN documents d ON d.doc_id = ru.doc_id
WHERE ra.tenant_id = $1 AND ra.cb_version_id = $2
ORDER BY ra.assignment_id
"""

CODABLE_VECTORS_SQL = """
SELECT u.unit_id, u.embedding_256 AS vec, d.source_type, u.text
FROM research_units u
JOIN documents d ON d.doc_id = u.doc_id
WHERE u.is_codable AND u.role_in_exchange = 'answer' AND u.embedding_256 IS NOT NULL
ORDER BY u.unit_id
"""

GOLD_VIEWS_TYPED_SQL = """
SELECT u.text_sha256::text AS sha, u.embedding AS a_vec, x.embedding AS qa_vec,
       d.source_type
FROM research_units u
JOIN documents d ON d.doc_id = u.doc_id
JOIN research_exchanges x
  ON x.exchange_id = u.exchange_id AND x.tenant_id = u.tenant_id
WHERE u.tenant_id = $1 AND u.text_sha256::text = ANY($2::text[])
  AND u.embedding IS NOT NULL AND x.embedding IS NOT NULL
"""

RUNS_SQL = """
SELECT run_id, params, checkpoint, state, started_at FROM research_runs
WHERE tenant_id = $1
ORDER BY started_at
"""

LINK_STATS_SQL = """
SELECT COUNT(*) AS n_exchanges,
       COUNT(link_score) AS n_scored,
       AVG(link_score) AS mean_link_score,
       COUNT(*) FILTER (WHERE reviewed) AS n_reviewed
FROM research_exchanges
"""

SIGNAL_STATS_SQL = """
SELECT tier, COUNT(*) AS n,
       COUNT(*) FILTER (WHERE tier = 3 AND COALESCE(semantic_entropy, 0) > 0) AS n_disagree,
       unit_id, soft_cluster_entropy
FROM research_signals
GROUP BY tier, unit_id, soft_cluster_entropy
"""

KEYWORD_HITS_SQL = """
SELECT code_id, kind, hit_location, COUNT(*) AS n
FROM research_keyword_hits
WHERE cb_version_id = $1
GROUP BY code_id, kind, hit_location
"""

VERSIONS_SQL = """
SELECT v.version_id, v.label, v.paradigm, v.created_at,
       COUNT(c.code_id) AS n_codes
FROM codebook_versions v
LEFT JOIN code_registry c
  ON c.codebook_version_id = v.version_id AND c.tenant_id = v.tenant_id
WHERE v.tenant_id = $1
GROUP BY v.version_id, v.label, v.paradigm, v.created_at
ORDER BY v.created_at
"""

DUPLICATE_BLOCKS_SQL = """
SELECT cluster_id, suggested_label, block_reason
FROM research_proposed_codes
WHERE tenant_id = $1 AND block_reason IS NOT NULL
ORDER BY proposed_id
"""


def _jsonb(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return dict(json.loads(value or "{}"))
    return dict(value or {})


async def load_assignment_rows(
    conn: asyncpg.Connection, tenant_id: UUID, version_id: UUID
) -> list[dict[str, Any]]:
    return [
        {
            "unit_id": int(row["unit_id"]),
            "code_id": str(row["code_id"]),
            "status": str(row["status"]),
            "evidence_basis": row["evidence_basis"],
            "rationale": str(row["rationale"] or ""),
            "sim_answer": row["sim_answer"],
            "sim_qa": row["sim_qa"],
            "source_type": str(row["source_type"]),
        }
        for row in await conn.fetch(ASSIGNMENT_SUMMARY_SQL, str(tenant_id), str(version_id))
    ]


async def load_codable_vectors(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch(CODABLE_VECTORS_SQL)
    return [
        {
            "unit_id": int(row["unit_id"]),
            "vec": _parse_pg_vector(row["vec"]) if row["vec"] else [],
            "source_type": str(row["source_type"]),
            "text": str(row["text"] or ""),
        }
        for row in rows
    ]


async def load_gold_views_typed(
    conn: asyncpg.Connection, tenant_id: UUID, refs: list[str]
) -> list[dict[str, Any]]:
    if not refs:
        return []
    rows = await conn.fetch(GOLD_VIEWS_TYPED_SQL, str(tenant_id), refs)
    return [
        {
            "sha": str(row["sha"]),
            "answer": _parse_pg_vector(row["a_vec"]) if row["a_vec"] else [],
            "qa": _parse_pg_vector(row["qa_vec"]) if row["qa_vec"] else [],
            "source_type": str(row["source_type"]),
        }
        for row in rows
    ]


async def load_runs_history(conn: asyncpg.Connection, tenant_id: UUID) -> list[dict[str, Any]]:
    return [
        {
            "run_id": str(row["run_id"]),
            "params": _jsonb(row["params"]),
            "checkpoint": _jsonb(row["checkpoint"]),
            "state": str(row["state"]),
        }
        for row in await conn.fetch(RUNS_SQL, str(tenant_id))
    ]


async def load_link_stats(conn: asyncpg.Connection) -> dict[str, Any]:
    row = await conn.fetchrow(LINK_STATS_SQL)
    assert row is not None
    return {
        "n_exchanges": int(row["n_exchanges"]),
        "n_scored": int(row["n_scored"]),
        "mean_link_score": float(row["mean_link_score"] or 0.0),
        "n_reviewed": int(row["n_reviewed"]),
    }


async def load_signal_rows(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    return [
        {
            "tier": int(row["tier"]),
            "unit_id": int(row["unit_id"]),
            "soft_cluster_entropy": row["soft_cluster_entropy"],
            "n_disagree": int(row["n_disagree"]),
        }
        for row in await conn.fetch(SIGNAL_STATS_SQL)
    ]


async def load_keyword_hit_populations(
    conn: asyncpg.Connection, version_id: UUID
) -> dict[str, dict[str, dict[str, int]]]:
    rows = await conn.fetch(KEYWORD_HITS_SQL, str(version_id))
    out: dict[str, dict[str, dict[str, int]]] = {}
    for row in rows:
        code = str(row["code_id"])
        kind = str(row["kind"])
        out.setdefault(code, {}).setdefault(kind, {})[str(row["hit_location"])] = int(row["n"])
    return out


async def load_versions(conn: asyncpg.Connection, tenant_id: UUID) -> list[dict[str, Any]]:
    return [
        {
            "version_id": str(row["version_id"]),
            "label": str(row["label"]),
            "paradigm": str(row["paradigm"]),
            "n_codes": int(row["n_codes"]),
        }
        for row in await conn.fetch(VERSIONS_SQL, str(tenant_id))
    ]


async def load_duplicate_gate_blocks(
    conn: asyncpg.Connection, tenant_id: UUID
) -> list[dict[str, Any]]:
    """Promote-time tau_dup blocks (todo-15) surfaced for codebook review."""
    return [
        {
            "cluster_id": str(row["cluster_id"]),
            "suggested_label": str(row["suggested_label"] or ""),
            "block_reason": str(row["block_reason"] or ""),
        }
        for row in await conn.fetch(DUPLICATE_BLOCKS_SQL, str(tenant_id))
    ]


async def load_code_definitions(
    conn: asyncpg.Connection, tenant_id: UUID, version_id: UUID
) -> dict[str, dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT code_id, name, brief_definition, inclusion_criteria, exclusion_criteria, theory
        FROM code_registry WHERE tenant_id = $1 AND codebook_version_id = $2
        """,
        str(tenant_id),
        str(version_id),
    )
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        theory = row["theory"]
        if isinstance(theory, str):
            theory = json.loads(theory or "{}")
        out[str(row["code_id"])] = {
            "name": str(row["name"]),
            "definition": str(row["brief_definition"] or ""),
            "inclusion": str(row["inclusion_criteria"] or ""),
            "exclusion": str(row["exclusion_criteria"] or ""),
            "theory": dict(theory or {}),
        }
    return out
