"""Notebook evidence view (pure move out of ``notebook_views.py``).

``evidence_for_code`` — units ranked by confidence with disagreement flags,
moderator_introduced flag, explicit vs question-dependent tabs, and
per-unit conformal set-size (r11) — plus its SQL constants, the conformal
reader, and the shared unit-row renderers. Re-exported from
``notebook_views`` so every consumer import keeps working.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.storage.gateway import SqlGateway
from corpus_kb.storage.tenant_conn import tenant_connection

CONFIDENCE_TIERS: dict[str, int] = {"high": 0, "medium": 1, "low": 2}
DEFAULT_DELTA_AMB = 0.05
NO_VERSIONS_MESSAGE = "no codebook versions for tenant; run corpus-kb codebook promote first"

_EVIDENCE_SQL = """
SELECT ra.assignment_id, ra.unit_id, ra.evidence_basis, ra.confidence, ra.status,
       ra.sim_answer, ra.term_origin, ra.rationale,
       ru.text, ru.seq AS unit_seq, ru.t_start, ru.doc_id,
       s.role AS speaker_role, s.pseudonym, d.title
FROM research_assignments ra
JOIN research_units ru ON ru.unit_id = ra.unit_id AND ru.tenant_id = ra.tenant_id
LEFT JOIN research_speakers s ON s.speaker_id = ru.speaker_id
JOIN documents d ON d.doc_id = ru.doc_id
WHERE ra.tenant_id = $1 AND ra.code_id = $2
"""

_DISAGREEMENT_SQL = """
SELECT DISTINCT unit_id FROM research_signals
WHERE tenant_id = $1 AND unit_id = ANY($2::bigint[]) AND tier = 3
  AND COALESCE(semantic_entropy, 0) > 0
"""

_LATEST_RUN_SQL = """
SELECT checkpoint FROM research_runs
WHERE tenant_id = $1 AND checkpoint IS NOT NULL
ORDER BY started_at DESC LIMIT 1
"""


async def evidence_for_code(
    pool: asyncpg.Pool | SqlGateway,
    tenant_id: UUID,
    code: str,
    project_id: UUID | None = None,
    version_id: UUID | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Evidence view for one code: ranked units + flags + conformal sizes."""
    from corpus_kb.research.notebook import resolve_code

    resolved = await resolve_code(pool, tenant_id, code)
    if resolved is None:
        return {
            "status": "empty",
            "message": f"unknown code {code!r}: not in any codebook version",
        }
    code_id = str(resolved["code_id"])
    resolved_version = UUID(str(resolved["version_id"]))
    if version_id is not None and version_id != resolved_version:
        resolved_version = version_id
    async with tenant_connection(pool, tenant_id) as conn:
        sql = _EVIDENCE_SQL
        params: list[object] = [str(tenant_id), code_id]
        if project_id is not None:
            sql += " AND d.project_id = $3"
            params.append(str(project_id))
        rows = await conn.fetch(sql, *params)
        if not rows:
            return {
                "status": "empty",
                "message": f"no assignments for code {code!r} yet",
                "code": code,
                "code_id": code_id,
            }
        unit_ids = [int(row["unit_id"]) for row in rows]
        disagreeing = {
            int(row["unit_id"])
            for row in await conn.fetch(_DISAGREEMENT_SQL, str(tenant_id), unit_ids)
        }
        conformal = await _conformal_per_unit(conn, tenant_id)
    tabs: dict[str, list[dict[str, Any]]] = {"explicit": [], "question_dependent": []}
    for row in rows:
        basis = str(row["evidence_basis"] or "explicit_in_answer")
        tab = "explicit" if basis == "explicit_in_answer" else "question_dependent"
        unit_id = int(row["unit_id"])
        entry: dict[str, Any] = {
            "unit_id": unit_id,
            "text": str(row["text"]),
            "confidence": row["confidence"],
            "status": str(row["status"]),
            "sim_answer": row["sim_answer"],
            "rationale": str(row["rationale"] or ""),
            "disagreement": unit_id in disagreeing,
            "moderator_introduced": str(row["speaker_role"] or "") == "moderator"
            or str(row["term_origin"] or "") in ("moderator", "both"),
            "conformal_set_size": conformal.get(unit_id, 1),
            "pseudonym": row["pseudonym"],
            "speaker_role": row["speaker_role"],
            "doc_id": str(row["doc_id"]),
            "doc_title": str(row["title"] or ""),
            "unit_seq": int(row["unit_seq"]),
            "t_start": row["t_start"],
        }
        tabs[tab].append(entry)
    for tab in tabs:
        tabs[tab] = sorted(
            tabs[tab],
            key=lambda e: (
                CONFIDENCE_TIERS.get(str(e["confidence"]), 3),
                -float(str(e["sim_answer"] or 0.0)),
                int(e["unit_id"]),
            ),
        )[:limit]
    return {
        "status": "ok",
        "code": str(resolved["name"]),
        "code_id": code_id,
        "definition": str(resolved["definition"]),
        "version_id": str(resolved_version),
        "n_units": len(rows),
        "tabs": tabs,
    }


async def _conformal_per_unit(
    conn: asyncpg.Connection | SqlGateway, tenant_id: UUID
) -> dict[int, int]:
    """Per-unit conformal set sizes (>1 only) from the latest run checkpoint."""
    row = await conn.fetchrow(_LATEST_RUN_SQL, str(tenant_id))
    if row is None:
        return {}
    checkpoint = row["checkpoint"]
    if isinstance(checkpoint, str):
        checkpoint = json.loads(checkpoint or "{}")
    per_unit = (dict(checkpoint or {}).get("conformal") or {}).get("per_unit") or {}
    return {int(unit_id): int(size) for unit_id, size in dict(per_unit).items()}


def _unit_margins(assignments: list[dict[str, Any]]) -> dict[int, float]:
    sims: dict[int, list[float]] = {}
    for a in assignments:
        if a.get("sim_answer") is None:
            continue
        sims.setdefault(int(a["unit_id"]), []).append(float(a["sim_answer"]))
    margins: dict[int, float] = {}
    for unit_id, values in sims.items():
        if len(values) >= 2:
            ordered = sorted(values, reverse=True)
            margins[unit_id] = ordered[0] - ordered[1]
    return margins


async def _unit_details(
    conn: asyncpg.Connection | SqlGateway, tenant_id: UUID, unit_ids: list[int]
) -> list[dict[str, Any]]:
    if not unit_ids:
        return []
    rows = await conn.fetch(
        """
        SELECT ru.unit_id, ru.text, ru.seq, ru.t_start, s.pseudonym, d.title
        FROM research_units ru
        LEFT JOIN research_speakers s ON s.speaker_id = ru.speaker_id
        JOIN documents d ON d.doc_id = ru.doc_id
        WHERE ru.tenant_id = $1 AND ru.unit_id = ANY($2::bigint[])
        ORDER BY ru.unit_id
        """,
        str(tenant_id),
        unit_ids,
    )
    return [
        {
            "unit_id": int(row["unit_id"]),
            "text": str(row["text"]),
            "unit_seq": int(row["seq"]),
            "t_start": row["t_start"],
            "pseudonym": row["pseudonym"],
            "doc_title": str(row["title"] or ""),
        }
        for row in rows
    ]
