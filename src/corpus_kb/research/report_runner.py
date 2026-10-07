"""Governance report runner (todo 17) — loads read models, builds the report.

Read models in, ONE report out (v5 §11/§13/§14 + r7/r9/r10/r12). Every
kNN-shaped read goes through analytics_sql (256-d HNSW retrieval + full
1024-d re-rank); everything else is a bounded aggregate read. Section math
lives in report_sections / report_governance (pure); finalization lives in
report_assembly.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.research import guide_copy
from corpus_kb.research.analytics_aggregates import (
    code_centroids_256,
    coded_unit_vectors_256,
)
from corpus_kb.research.analytics_sql import (
    mrl_256,
    residual_scan,
    residual_scan_exchanges,
    uncoded_units_ranking,
)
from corpus_kb.research.governance_report import ResearchReport, with_level
from corpus_kb.research.prototypes import build_view_prototypes
from corpus_kb.research.report_assembly import finalize_report, queue_candidates
from corpus_kb.research.report_governance import gold_inter_code_max_cosine
from corpus_kb.research.report_inputs import (
    load_assignment_rows,
    load_codable_vectors,
    load_code_definitions,
    load_duplicate_gate_blocks,
    load_gold_views_typed,
    load_keyword_hit_populations,
    load_link_stats,
    load_runs_history,
    load_signal_rows,
    load_versions,
)
from corpus_kb.research.report_irr_sheets import load_irr_sheets
from corpus_kb.research.report_sections import (
    candidate_missing_codes,
    residual_full_section,
)
from corpus_kb.storage.tenant_conn import tenant_connection


async def build_report(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    codebook_version_id: UUID | None = None,
    *,
    level: str = guide_copy.LEVEL_EXPERT,
    g3_report: dict[str, object] | None = None,
    run_manifest: dict[str, object] | None = None,
    force_index_proof: bool = False,
) -> ResearchReport:
    """Build the schema-pinned governance report from live read models."""
    async with tenant_connection(pool, tenant_id) as conn:
        versions = await load_versions(conn, tenant_id)
        if not versions:
            raise ValueError("no codebook versions for tenant")
        version_id = (
            UUID(str(codebook_version_id))
            if codebook_version_id
            else UUID(versions[-1]["version_id"])
        )
        label = next((v["label"] for v in versions if v["version_id"] == str(version_id)), "")
        codes = await _load_codes(conn, tenant_id, version_id)
        gold_by_code = {
            code["code_id"]: await load_gold_views_typed(
                conn, tenant_id, list(code["theory"].get("exemplar_text_sha256") or [])
            )
            for code in codes
        }
        codable = await load_codable_vectors(conn)
        prototypes = _prototypes(codes, gold_by_code)
        answer_protos = [p for c in prototypes.values() for p in c["answer"]]
        qa_protos = [p for c in prototypes.values() for p in c["qa"]]
        k = max(len(codable), 1)
        residual_answer = await residual_scan(conn, answer_protos, k, force_index=force_index_proof)
        residual_qa = await residual_scan_exchanges(
            conn, qa_protos, k, force_index=force_index_proof
        )
        residual_by_unit = _max_residuals(residual_answer, residual_qa)
        uncoded_ranking = await uncoded_units_ranking(conn, answer_protos, k)
        centroids = await code_centroids_256(conn, version_id)
        coded_vectors = await coded_unit_vectors_256(conn, version_id)
        assignments = await load_assignment_rows(conn, tenant_id, version_id)
        irr_sheets, units_by_code = await load_irr_sheets(
            conn, tenant_id, version_id, [c["code_id"] for c in codes]
        )
        link = await load_link_stats(conn)
        signals = await load_signal_rows(conn)
        hits = await load_keyword_hit_populations(conn, version_id)
        definitions = await load_code_definitions(conn, tenant_id, version_id)
        prev_definitions = (
            await load_code_definitions(conn, tenant_id, UUID(versions[-2]["version_id"]))
            if len(versions) >= 2
            else {}
        )
        runs = await load_runs_history(conn, tenant_id)
        duplicate_gate_blocks = await load_duplicate_gate_blocks(conn, tenant_id)

        unit_types = {int(r["unit_id"]): str(r["source_type"]) for r in codable}
        tau_block = residual_full_section(gold_by_code, residual_by_unit, unit_types)
        tau_by_type = {
            source_type: float(block["tau_res"])
            for source_type, block in dict(tau_block["by_source_type"]).items()
        }
        entropy_by_unit = {
            int(s["unit_id"]): (
                float(s["soft_cluster_entropy"]) if s["soft_cluster_entropy"] is not None else None
            )
            for s in signals
            if s["tier"] == 0
        }
        candidates = candidate_missing_codes(
            residual_by_unit,
            unit_types,
            tau_by_type,
            entropy_by_unit,
            {int(r["unit_id"]): str(r["text"]) for r in codable},
            {int(r["unit_id"]): r["vec"] for r in codable},
        )
        queued = await queue_candidates(conn, tenant_id, candidates, runs)
        gold_inter_code_max = gold_inter_code_max_cosine(
            {
                c: [mrl_256(p) for p in protos["answer"]]
                for c, protos in prototypes.items()
                if protos["answer"]
            }
        )

    report = finalize_report(
        tenant_id=tenant_id,
        versions=versions,
        version_id=version_id,
        label=label,
        gold_inter_code_max=gold_inter_code_max,
        codable=codable,
        residual_block=tau_block,
        uncoded_ranking=uncoded_ranking,
        centroids=centroids,
        coded_vectors=coded_vectors,
        assignments=assignments,
        irr_sheets=irr_sheets,
        units_by_code=units_by_code,
        link=link,
        signals=signals,
        hits=hits,
        definitions=definitions,
        prev_definitions=prev_definitions,
        runs=runs,
        candidates=candidates,
        queued=queued,
        duplicate_gate_blocks=duplicate_gate_blocks,
        g3_report=g3_report,
        manifest_extra=run_manifest,
    )
    return with_level(report, level)


async def _load_codes(
    conn: asyncpg.Connection, tenant_id: UUID, version_id: UUID
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT code_id, theory FROM code_registry "
        "WHERE tenant_id = $1 AND codebook_version_id = $2",
        str(tenant_id),
        str(version_id),
    )
    codes: list[dict[str, Any]] = []
    for row in rows:
        theory = row["theory"]
        if isinstance(theory, str):
            from json import loads

            theory = loads(theory or "{}")
        codes.append({"code_id": str(row["code_id"]), "theory": theory or {}})
    return codes


def _prototypes(
    codes: list[dict[str, Any]], gold_by_code: dict[str, list[dict[str, Any]]]
) -> dict[str, dict[str, list[list[float]]]]:
    prototypes: dict[str, dict[str, list[list[float]]]] = {}
    for code in codes:
        gold = gold_by_code.get(code["code_id"], [])
        prototypes[code["code_id"]] = {
            "answer": build_view_prototypes([g["answer"] for g in gold if g["answer"]]),
            "qa": build_view_prototypes([g["qa"] for g in gold if g["qa"]]),
        }
    return prototypes


def _max_residuals(a: dict[int, float], b: dict[int, float]) -> dict[int, float]:
    merged = dict(a)
    for unit_id, r in b.items():
        if r > merged.get(unit_id, -2.0):
            merged[unit_id] = r
    return merged
