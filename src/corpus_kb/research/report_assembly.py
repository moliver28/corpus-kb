"""Report finalization (todo 17) — assembles the pinned ResearchReport.

Split from report_runner.py (250-line soft limit): the runner owns loading;
THIS module turns loaded read-model inputs into the ResearchReport instance.
Pure over its inputs except the timestamps it stamps.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.research import guide_copy
from corpus_kb.research.governance_report import SCHEMA_VERSION, ResearchReport
from corpus_kb.research.inductive_store import queue_noise
from corpus_kb.research.report_governance import (
    codebook_diff_section,
    conformal_section,
    keywords_section,
    manifest_section,
    overlap_section,
    run_stop_section,
)
from corpus_kb.research.report_sections import (
    checkpoint_section,
    coverage_curve_section,
    irr_section,
    stability_section,
)


async def queue_candidates(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    candidates: list[dict[str, Any]],
    runs: list[dict[str, Any]],
) -> int:
    """Surface residual candidates to the manual-review queue (noise queue,
    cluster labels offset above the HDBSCAN range)."""
    if not candidates or not runs:
        return 0
    run_id = UUID(runs[-1]["run_id"])
    queued = 0
    for cluster in candidates:
        for unit_id in cluster["unit_ids"]:
            await queue_noise(
                conn,
                tenant_id,
                int(unit_id),
                run_id,
                probability=None,
                entropy=cluster["mean_entropy"],
            )
            queued += 1
    return queued


def finalize_report(**ctx: Any) -> ResearchReport:
    assignments: list[dict[str, Any]] = ctx["assignments"]
    codable = ctx["codable"]
    runs: list[dict[str, Any]] = ctx["runs"]
    checkpoint = checkpoint_section(assignments, len(codable))
    tau_block: dict[str, Any] = ctx["residual_block"]
    prior_r = [
        float(run["checkpoint"]["exhaustiveness"]["pooled"]["R"])
        for run in runs
        if not run["params"].get("pipeline")
        and "exhaustiveness" in run["checkpoint"]
        and dict(run["checkpoint"]["exhaustiveness"].get("pooled", {})).get("R") is not None
    ]
    live_r = float(dict(tau_block["pooled"])["R"])
    curve = coverage_curve_section(prior_r, live_r, _meta_new_series(runs))
    stability = stability_section([list(r["vec"]) for r in codable])
    stability["dbcv_relative_validity"] = _latest_dbcv(runs)
    overlap = overlap_section(
        ctx["centroids"],
        ctx["coded_vectors"],
        assignments,
        ctx["gold_inter_code_max"],
        duplicate_gate_blocks=ctx["duplicate_gate_blocks"],
    )
    tier3_rows = [s for s in ctx["signals"] if s["tier"] == 3]
    disagreement = (
        sum(1 for s in tier3_rows if s["n_disagree"] > 0) / len(tier3_rows) if tier3_rows else 0.0
    )
    return ResearchReport(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(UTC).isoformat(),
        level=guide_copy.LEVEL_EXPERT,
        tenant_id=str(ctx["tenant_id"]),
        codebook_version_id=str(ctx["version_id"]),
        codebook_label=str(ctx["label"]),
        isr_pooled=float(checkpoint["isr_pooled"]),
        isr_by_source_type=dict(checkpoint["isr_by_source_type"]),
        run_stop=run_stop_section(runs),
        coverage_explicit=float(checkpoint["coverage_explicit"]),
        coverage_qdep=float(checkpoint["coverage_qdep"]),
        gray_zone_share=float(checkpoint["gray_zone_share"]),
        tier3_disagreement_rate=disagreement,
        link_accuracy=dict(ctx["link"]),
        review_backlog=dict(checkpoint["review_backlog"]),
        kappa_alpha=irr_section(ctx["irr_sheets"], ctx["units_by_code"]),
        residual=dict(tau_block),
        coverage_curve=dict(curve),
        deductive_coverage_note=str(checkpoint["deductive_coverage_note"]),
        missing_codes={
            "candidates": ctx["candidates"],
            "uncoded_ranking": ctx["uncoded_ranking"][:20],
            "surfaced_to_review_queue": int(ctx["queued"]),
        },
        cluster_stability=dict(stability),
        overlap=dict(overlap),
        keywords=keywords_section(ctx["definitions"], ctx["hits"]),
        g3_audit=dict(
            ctx["g3_report"] or {"status": "not_available", "note": "no audit rows supplied"}
        ),
        conformal=conformal_section(runs),
        run_manifest=manifest_section(runs, ctx["manifest_extra"]),
        codebook_diff=codebook_diff_section(
            ctx["versions"], ctx["definitions"], ctx["prev_definitions"]
        ),
    )


def _meta_new_series(runs: list[dict[str, Any]]) -> list[float]:
    for run in reversed(runs):
        if run["params"].get("pipeline") == "inductive":
            batches = run["checkpoint"].get("batches")
            if isinstance(batches, list):
                return [float(b.get("meta_new", 0) or 0) for b in batches]
    return []


def _latest_dbcv(runs: list[dict[str, Any]]) -> float | None:
    for run in reversed(runs):
        if run["params"].get("pipeline") == "inductive":
            value = run["checkpoint"].get("dbcv_relative_validity")
            if value is not None:
                return float(value)
    return None
