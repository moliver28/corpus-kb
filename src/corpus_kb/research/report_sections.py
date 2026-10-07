"""Pure section builders for the governance report (todo 17).

Each builder turns loaded read-model inputs into ONE pinned report section
dict. No DB access here — the runner loads, these compute (offline-testable
twins of the math modules they call).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from corpus_kb.research.cluster_stability import (
    BOOTSTRAP_RESAMPLES,
    bootstrap_stability,
    spherical_kmeans,
)
from corpus_kb.research.exhaustiveness import (
    calibrate_tau_res,
    coverage_curve_slope,
    gold_within_code_similarities,
    plateau_detected,
    residual_section,
)
from corpus_kb.research.irr_governance import govern_irr

DEDUCTIVE_COVERAGE_NOTE = (
    "v5 §8 deductive coverage is threshold-driven and is reported separately "
    "below; it is NEVER a completeness measure (r8) — the residual section is."
)
NEW_CODES_SLOPE_SOURCE = "meta_new per batch checkpoint (inductive run)"
GRAY_ZONE_REASONS = ("gray_zone", "conformal")


def checkpoint_section(assignments: Sequence[Mapping[str, Any]], n_codable: int) -> dict[str, Any]:
    """§14 checkpoint numbers: ISR, coverage split, gray-zone share, backlog."""
    coded_units = {int(a["unit_id"]) for a in assignments}
    isr_pooled = len({a["code_id"] for a in assignments}) / len(assignments) if assignments else 0.0
    by_type: dict[str, float] = {}
    for source_type in {str(a["source_type"]) for a in assignments}:
        rows = [a for a in assignments if str(a["source_type"]) == source_type]
        by_type[source_type] = len({a["code_id"] for a in rows}) / len(rows) if rows else 0.0
    explicit = {
        int(a["unit_id"]) for a in assignments if a["evidence_basis"] == "explicit_in_answer"
    }
    qdep = {int(a["unit_id"]) for a in assignments if a["evidence_basis"] == "question_dependent"}
    review = [a for a in assignments if str(a["status"]) == "review"]
    gray_zone_units = {
        int(a["unit_id"])
        for a in review
        if any(reason in str(a["rationale"]) for reason in GRAY_ZONE_REASONS)
    }
    multi_category = sum(
        1 for count in Counter(int(a["unit_id"]) for a in assignments).values() if count >= 2
    )
    return {
        "isr_pooled": isr_pooled,
        "isr_by_source_type": by_type,
        "coverage_explicit": len(explicit) / n_codable if n_codable else 0.0,
        "coverage_qdep": len(qdep) / n_codable if n_codable else 0.0,
        "gray_zone_share": len(gray_zone_units) / n_codable if n_codable else 0.0,
        "review_backlog": {
            "assignments": len(review),
            "coded_units": len(coded_units),
            "multi_category_units": multi_category,
        },
        "deductive_coverage_note": DEDUCTIVE_COVERAGE_NOTE,
    }


def irr_section(
    reliability_report: Mapping[str, Any],
    units_by_code: Mapping[str, Sequence[tuple[int, int, int]]],
) -> dict[str, Any]:
    """Dual-coefficient IRR: alpha + kappa + Gwet AC1 + governance bands."""
    return govern_irr(dict(reliability_report), dict(units_by_code))


def residual_full_section(
    gold_by_code: Mapping[str, Sequence[Mapping[str, Any]]],
    residual_by_unit: Mapping[int, float],
    unit_types: Mapping[int, str],
) -> dict[str, Any]:
    """tau_res calibration + R(tau_res) + P10/P50/P90 per source type."""
    by_type_sims: dict[str, list[float]] = {}
    for gold in gold_by_code.values():
        by_type_groups: dict[str, list[list[float]]] = {}
        for g in gold:
            if g["answer"]:
                by_type_groups.setdefault(str(g["source_type"]), []).append(list(g["answer"]))
        for source_type, vectors in by_type_groups.items():
            if len(vectors) >= 2:
                by_type_sims.setdefault(source_type, []).extend(
                    gold_within_code_similarities([vectors])
                )
    tau_res = calibrate_tau_res(by_type_sims)
    residuals_by_type: dict[str, list[float]] = {}
    for unit_id, r in residual_by_unit.items():
        residuals_by_type.setdefault(unit_types.get(unit_id, "unknown"), []).append(r)
    return residual_section(residuals_by_type, tau_res)


def coverage_curve_section(
    prior_residual_r: Sequence[float],
    live_residual_r: float,
    meta_new_per_batch: Sequence[float],
) -> dict[str, Any]:
    """Coverage-curve slope across consecutive checkpoints.

    Plateau is defined on R(tau_res) and/or new-codes-per-batch — NEVER on
    §8 deductive coverage (r8), which is only reported separately.
    """
    r_series = [*prior_residual_r, live_residual_r]
    r_slope = coverage_curve_slope(r_series)
    r_plateau = plateau_detected(r_slope)
    meta_slope = coverage_curve_slope(list(meta_new_per_batch))
    meta_plateau = plateau_detected(meta_slope) if meta_new_per_batch else False
    return {
        "R_series": r_series,
        "R_slope": r_slope,
        "R_plateau": r_plateau,
        "new_codes_per_batch": list(meta_new_per_batch),
        "new_codes_slope": meta_slope,
        "new_codes_plateau": meta_plateau,
        "plateau_detected": bool(r_plateau or meta_plateau),
        "basis": "R(tau_res) checkpoints + new-codes-per-batch (meta_new)",
        "deductive_coverage_excluded": True,
        "source_note": NEW_CODES_SLOPE_SOURCE,
    }


def stability_section(vectors: Sequence[Sequence[float]]) -> dict[str, Any]:
    """Bootstrap re-cluster stability: mean pairwise ARI >= 0.75 = stable."""
    return bootstrap_stability(vectors, n_resamples=BOOTSTRAP_RESAMPLES)


def candidate_missing_codes(
    residual_by_unit: Mapping[int, float],
    unit_types: Mapping[int, str],
    tau_res_by_type: Mapping[str, float],
    entropy_by_unit: Mapping[int, float | None],
    texts_by_unit: Mapping[int, str],
    vectors_by_unit: Mapping[int, Sequence[float]],
) -> list[dict[str, Any]]:
    """Cluster low-residual AND low-entropy units into candidate missing codes."""
    candidates = [
        unit_id
        for unit_id, r in residual_by_unit.items()
        if r < tau_res_by_type.get(unit_types.get(unit_id, "unknown"), 0.0)
        and _entropy_ok(entropy_by_unit.get(unit_id))
    ]
    if len(candidates) < 2:
        return []
    k = 2 if len(candidates) >= 4 else 1
    matrix = [list(vectors_by_unit[u]) for u in candidates]
    labels = spherical_kmeans(matrix, k).tolist()
    clusters: dict[int, list[int]] = {}
    for unit_id, label in zip(candidates, labels, strict=True):
        clusters.setdefault(label, []).append(unit_id)
    out: list[dict[str, Any]] = []
    for label, unit_ids in sorted(clusters.items()):
        out.append(
            {
                "cluster_label": f"residual_candidate:{label}",
                "n_units": len(unit_ids),
                "unit_ids": unit_ids,
                "mean_residual": float(np.mean([residual_by_unit[u] for u in unit_ids])),
                "mean_entropy": _mean_entropy(entropy_by_unit, unit_ids),
                "suggested_label": _label_from_texts([texts_by_unit.get(u, "") for u in unit_ids]),
            }
        )
    out.sort(key=lambda c: -int(c["n_units"]))
    return out


def _entropy_ok(entropy: float | None) -> bool:
    return entropy is None or entropy < 0.85


def _mean_entropy(entropy_by_unit: Mapping[int, float | None], unit_ids: list[int]) -> float | None:
    values: list[float] = []
    for u in unit_ids:
        entropy = entropy_by_unit.get(u)
        if entropy is not None:
            values.append(float(entropy))
    return float(np.mean(values)) if values else None


def _label_from_texts(texts: list[str]) -> str:
    counts: Counter[str] = Counter()
    for text in texts:
        for word in str(text).lower().split():
            word = "".join(ch for ch in word if ch.isalnum())
            if len(word) > 3:
                counts[word] += 1
    top = [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:2]]
    return "-".join(top) if top else "residual-cluster"
