"""Cluster-content + audit-design release gates (U16, U17; v6 section 7).

Pure functions over ``(GateInputs, ResolvedProfile)``; evidence blocks are
assembled by ``gate_reads.py``.

U16 content_stability — top-term Jaccard across seed/bootstrap re-runs
(``research.cluster_stability.top_term_jaccard``) gated against
``profile.content_stability_floor``; the membership ARI is REPORTED
SEPARATELY in the value (v6 U16: gate content, report membership) and never
contributes to the pass decision.

U17 audit_sample_design — the ``audit_partitions`` sample must exist with
stratum, inclusion_probability in (0, 1] and seed recorded per row
(the wave-1 ``AuditSampleRow`` shape); the row count is compared against
``profile.min_audit_sample`` (0 = no minimum). A recorded-but-incomplete
design is a design DEFECT (``fail``): U14's inverse-probability weighting is
exact only over rows that carry the design.
"""

from __future__ import annotations

from corpus_kb.research.cluster_stability import top_term_jaccard
from corpus_kb.research.release.gate_support import as_dict, as_float, as_int, as_list, as_str
from corpus_kb.research.release.gate_types import (
    GATE_AUDIT_SAMPLE,
    GATE_CONTENT_STABILITY,
    GateInputs,
    GateResult,
)
from corpus_kb.research.release.profiles import ResolvedProfile


def gate_content_stability(inputs: GateInputs, profile: ResolvedProfile) -> GateResult:
    """U16: top-term Jaccard vs ``content_stability_floor``; ARI beside it."""
    block = as_dict(inputs.stability)
    runs_raw = as_list(block.get("runs"))
    runs: list[list[str]] = []
    for entry in runs_raw:
        terms = [as_str(t) for t in as_list(entry)]
        runs.append([t for t in terms if t])
    if not block or len(runs) < 2:
        return GateResult(
            gate_id=GATE_CONTENT_STABILITY,
            status="not_evaluable",
            reason=(
                "insufficient_data: fewer than two clustering runs recorded "
                "(inputs.stability needs >= 2 top-terms lists)"
            ),
        )
    k = as_int(block.get("k")) or 10
    floor = profile.content_stability_floor
    if floor is None:
        floor = 0.7  # top_term_jaccard's documented default convention
    stability = top_term_jaccard(runs, k=k, threshold=floor)
    value = {
        "mean_top_term_jaccard": stability.mean_jaccard,
        "n_runs": stability.n_runs,
        "k": stability.k,
        "membership_ari": as_float(block.get("membership_ari")),
        "membership_note": "reported separately; NOT part of this gate's decision (v6 U16)",
    }
    if stability.mean_jaccard is None:
        return GateResult(
            gate_id=GATE_CONTENT_STABILITY,
            status="not_evaluable",
            reason="insufficient_data: top-term Jaccard undefined below two runs",
            value=value,
            threshold=floor,
        )
    passed = stability.mean_jaccard >= floor
    return GateResult(
        gate_id=GATE_CONTENT_STABILITY,
        status="pass" if passed else "fail",
        reason=(
            f"mean top-term Jaccard {stability.mean_jaccard:.3f} (k={k}, "
            f"{stability.n_runs} runs) vs content_stability_floor {floor}; membership "
            f"ARI {value['membership_ari']} reported separately"
        ),
        value=value,
        threshold=floor,
    )


def gate_audit_sample_design(inputs: GateInputs, profile: ResolvedProfile) -> GateResult:
    """U17: audit_partitions rows exist with stratum/probability/seed recorded."""
    block = as_dict(inputs.audit_sample)
    rows = [as_dict(r) for r in as_list(block.get("rows")) if isinstance(r, dict)]
    if not block or not rows:
        return GateResult(
            gate_id=GATE_AUDIT_SAMPLE,
            status="not_evaluable",
            reason="no audit_partitions sample recorded (inputs.audit_sample empty)",
        )
    defective: list[dict[str, object]] = []
    for row in rows:
        problems: list[str] = []
        if as_str(row.get("stratum")) is None:
            problems.append("missing stratum")
        probability = as_float(row.get("inclusion_probability"))
        if probability is None or not 0.0 < probability <= 1.0:
            problems.append(
                f"inclusion_probability out of (0, 1]: {row.get('inclusion_probability')}"
            )
        if as_int(row.get("seed")) is None:
            problems.append("missing seed")
        if as_str(row.get("unit_id")) is None:
            problems.append("missing unit_id")
        if problems:
            defective.append({"unit_id": row.get("unit_id"), "problems": problems})
    seeds = sorted({seed for seed in (as_int(r.get("seed")) for r in rows) if seed is not None})
    probabilities = [
        p for p in (as_float(r.get("inclusion_probability")) for r in rows) if p is not None
    ]
    value: dict[str, object] = {
        "n_rows": len(rows),
        "n_strata": len({as_str(r.get("stratum")) for r in rows if as_str(r.get("stratum"))}),
        "seeds": seeds,
        "inclusion_probability_range": (
            [min(probabilities), max(probabilities)] if probabilities else None
        ),
        "defective_rows": defective,
        "min_audit_sample": profile.min_audit_sample,
    }
    if defective:
        return GateResult(
            gate_id=GATE_AUDIT_SAMPLE,
            status="fail",
            reason=(
                f"invalid_output: {len(defective)} audit row(s) miss the recorded-design "
                "fields (stratum, inclusion_probability, seed) — U14 weighting would be "
                "inexact over them"
            ),
            value=value,
            threshold={"min_audit_sample": profile.min_audit_sample},
        )
    if len(rows) < profile.min_audit_sample:
        return GateResult(
            gate_id=GATE_AUDIT_SAMPLE,
            status="fail",
            reason=(
                f"insufficient_data: {len(rows)} audited unit(s) below the profile "
                f"minimum {profile.min_audit_sample}"
            ),
            value=value,
            threshold=profile.min_audit_sample,
        )
    return GateResult(
        gate_id=GATE_AUDIT_SAMPLE,
        status="pass",
        reason=(
            f"{len(rows)} audited unit(s) over {value['n_strata']} stratum(a), seeds "
            f"{seeds}, inclusion probabilities recorded (design-based reweighting exact)"
        ),
        value=value,
        threshold={"min_audit_sample": profile.min_audit_sample},
    )


__all__ = [
    "gate_audit_sample_design",
    "gate_content_stability",
]
