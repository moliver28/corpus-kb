"""Notebook surface — read views (todo 18, v5 §15 + r7/r11).

Project-scoped read views over the research read models:

  * ``evidence_for_code``  — ranked units + flags + conformal sizes (r11).
    Lives in :mod:`notebook_evidence` (a pure move for the 250-line rule)
    and is re-exported here so every consumer import keeps working.
  * ``uncoded_units``      — uncoded units ranked by nearest-code similarity
    margin (1 - residual): the no-missing-clusters radar (r7). kNN-served
    via the engine-dispatched analytics runners, never a Python-side scan.
  * ``overlap_view``       — flagged code pairs + shared-unit confusion +
    borderline top-2-margin units (r7), reusing the todo-17 overlap math.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.research.analytics_aggregates import (
    code_centroids_256,
    coded_unit_vectors_256,
)
from corpus_kb.research.analytics_sql import mrl_256, uncoded_units_ranking
from corpus_kb.research.notebook_evidence import (
    DEFAULT_DELTA_AMB,
    NO_VERSIONS_MESSAGE,
    _unit_details,
    _unit_margins,
    evidence_for_code,
)
from corpus_kb.research.prototypes import build_view_prototypes
from corpus_kb.research.report_governance import (
    gold_inter_code_max_cosine,
    overlap_section,
)
from corpus_kb.research.report_inputs import (
    load_assignment_rows,
    load_gold_views_typed,
    load_versions,
)
from corpus_kb.storage.gateway import SqlGateway, is_gateway
from corpus_kb.storage.tenant_conn import tenant_connection

__all__ = ["DEFAULT_DELTA_AMB", "evidence_for_code", "overlap_view", "uncoded_units"]


async def uncoded_units(
    pool: asyncpg.Pool | SqlGateway,
    embedder: ResearchEmbedder,
    tenant_id: UUID,
    project_id: UUID | None = None,
    version_id: UUID | None = None,
    k: int = 25,
) -> dict[str, Any]:
    """Uncoded units ranked by nearest-code margin (the missing-code radar)."""
    del embedder  # residuals are SQL-side; kept in the signature for surface parity
    async with tenant_connection(pool, tenant_id) as conn:
        versions = await load_versions(conn, tenant_id)
        if not versions:
            return {"status": "empty", "message": NO_VERSIONS_MESSAGE}
        resolved = (
            UUID(str(version_id)) if version_id is not None else UUID(versions[-1]["version_id"])
        )
        codes = await _codes_of(conn, tenant_id, resolved)
        gold_by_code = {
            code_id: await load_gold_views_typed(
                conn, tenant_id, list(theory.get("exemplar_text_sha256") or [])
            )
            for code_id, theory in codes
        }
        prototypes = [
            proto
            for code_id, _ in codes
            for proto in build_view_prototypes(
                [g["answer"] for g in gold_by_code.get(code_id, []) if g["answer"]]
            )
        ]
        if not prototypes:
            return {
                "status": "empty",
                "message": "no gold prototypes for this codebook version; cannot rank residuals",
            }
        n_codable = int(
            await conn.fetchval(
                """
                SELECT COUNT(*) FROM research_units ru
                JOIN documents d ON d.doc_id = ru.doc_id
                WHERE ru.tenant_id = $1 AND ru.is_codable
                  AND ru.role_in_exchange = 'answer' AND ru.embedding IS NOT NULL
                """,
                str(tenant_id),
            )
            or 0
        )
        n_uncoded = n_codable - int(
            await conn.fetchval(
                """
                SELECT COUNT(DISTINCT ra.unit_id) FROM research_assignments ra
                JOIN research_units ru ON ru.unit_id = ra.unit_id
                    AND ru.tenant_id = ra.tenant_id
                WHERE ra.tenant_id = $1 AND ru.is_codable
                  AND ru.role_in_exchange = 'answer' AND ru.embedding IS NOT NULL
                """,
                str(tenant_id),
            )
            or 0
        )
        if n_uncoded <= 0:
            return {"status": "empty", "message": "no uncoded units", "version_id": str(resolved)}
        # Scan depth = ALL codable units: a filtered kNN scan can return
        # fewer rows than LIMIT (filtered-search gotcha), so the radar must
        # over-fetch the full codable set before the NOT EXISTS filter.
        ranked = await uncoded_units_ranking(
            conn, prototypes, max(n_codable, k), duck=is_gateway(pool)
        )
        scoped = ranked
        if project_id is not None:
            project_ids = {
                int(row["unit_id"])
                for row in await conn.fetch(
                    """
                    SELECT ru.unit_id FROM research_units ru
                    JOIN documents d ON d.doc_id = ru.doc_id
                    WHERE ru.tenant_id = $1 AND d.project_id = $2
                      AND ru.unit_id = ANY($3::bigint[])
                    """,
                    str(tenant_id),
                    str(project_id),
                    [int(r["unit_id"]) for r in ranked] or [0],
                )
            }
            scoped = [r for r in ranked if int(r["unit_id"]) in project_ids]
    out = [
        {
            "unit_id": int(r["unit_id"]),
            "text": str(r["text"]),
            "residual": float(r["r"]),
            "margin": 1.0 - float(r["r"]),
        }
        for r in scoped[:k]
    ]
    return {"status": "ok", "version_id": str(resolved), "units": out, "n_uncoded": n_uncoded}


async def overlap_view(
    pool: asyncpg.Pool | SqlGateway,
    tenant_id: UUID,
    project_id: UUID | None = None,
    version_id: UUID | None = None,
    delta_amb: float = DEFAULT_DELTA_AMB,
) -> dict[str, Any]:
    """Code-overlap view: flagged pairs, confusion, borderline-margin units."""
    del project_id  # centroids/assignments are codebook-version-scoped by design
    async with tenant_connection(pool, tenant_id) as conn:
        versions = await load_versions(conn, tenant_id)
        if not versions:
            return {"status": "empty", "message": NO_VERSIONS_MESSAGE}
        resolved = (
            UUID(str(version_id)) if version_id is not None else UUID(versions[-1]["version_id"])
        )
        codes = await _codes_of(conn, tenant_id, resolved)
        gold_by_code = {
            code_id: await load_gold_views_typed(
                conn, tenant_id, list(theory.get("exemplar_text_sha256") or [])
            )
            for code_id, theory in codes
        }
        centroids = await code_centroids_256(conn, resolved, duck=is_gateway(pool))
        if not centroids:
            return {"status": "empty", "message": "no coded units for this codebook version"}
        coded_vectors = await coded_unit_vectors_256(conn, resolved)
        assignments = await load_assignment_rows(conn, tenant_id, resolved)
        gold_max = gold_inter_code_max_cosine(
            {
                code_id: [
                    mrl_256(p)
                    for p in build_view_prototypes(
                        [g["answer"] for g in gold_by_code.get(code_id, []) if g["answer"]]
                    )
                ]
                for code_id, _ in codes
                if gold_by_code.get(code_id)
            }
        )
    section = overlap_section(centroids, coded_vectors, assignments, gold_max, delta_amb)
    margins = _unit_margins(assignments)
    borderline_ids = sorted(uid for uid, margin in margins.items() if margin < delta_amb)
    async with tenant_connection(pool, tenant_id) as conn:
        borderline = await _unit_details(conn, tenant_id, borderline_ids)
    return {"status": "ok", "version_id": str(resolved), **section, "borderline_units": borderline}


async def _codes_of(
    conn: asyncpg.Connection | SqlGateway, tenant_id: UUID, version_id: UUID
) -> list[tuple[str, dict[str, Any]]]:
    rows = await conn.fetch(
        "SELECT code_id, theory FROM code_registry "
        "WHERE tenant_id = $1 AND codebook_version_id = $2",
        str(tenant_id),
        str(version_id),
    )
    out: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        theory = row["theory"]
        if isinstance(theory, str):
            theory = json.loads(theory or "{}")
        out.append((str(row["code_id"]), dict(theory or {})))
    return out
