"""Governance-facing report sections (todo 17): overlap, keywords,
conformal, codebook diff, and run-manifest helpers.

Split from report_sections.py to honor the 250-line soft limit. Pure
functions over loaded read-model inputs — no DB access here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np

from corpus_kb.research.cluster_stability import silhouette_by_group
from corpus_kb.research.governance_report import SCHEMA_VERSION
from corpus_kb.research.keyword_synthesis import SynthesizedKeyword, conflict_flags
from corpus_kb.research.overlap import (
    calibrate_tau_overlap,
    code_centroid_matrix,
    flag_overlap_pairs,
    shared_unit_confusion,
)


def overlap_section(
    centroids: Mapping[str, Sequence[float]],
    coded_vectors: Sequence[Mapping[str, Any]],
    assignments: Sequence[Mapping[str, Any]],
    gold_inter_code_max: float | None,
    delta_amb: float = 0.05,
    duplicate_gate_blocks: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Semantic overlap: centroid matrix, flagged pairs, confusion, silhouette.

    ``duplicate_gate_blocks`` are the todo-15 promote-time tau_dup blocks —
    surfaced here alongside the overlap flags for codebook review.
    """
    code_ids = sorted(centroids)
    matrix = code_centroid_matrix([centroids[c] for c in code_ids])
    tau_overlap = calibrate_tau_overlap(gold_inter_code_max)
    flags = flag_overlap_pairs(matrix, code_ids, tau_overlap)
    members: dict[str, set[int]] = {}
    for a in assignments:
        members.setdefault(str(a["code_id"]), set()).add(int(a["unit_id"]))
    margins = _unit_margins(assignments)
    confusion = shared_unit_confusion(members, margins, delta_amb)
    label_ids = {
        code: idx for idx, code in enumerate(sorted({str(v["code_id"]) for v in coded_vectors}))
    }
    silhouette = silhouette_by_group(
        [list(v["vec"]) for v in coded_vectors],
        [label_ids[str(v["code_id"])] for v in coded_vectors],
    )
    silhouette_by_code = {code: float(silhouette.get(idx, 0.0)) for code, idx in label_ids.items()}
    return {
        "tau_overlap": tau_overlap,
        "tau_overlap_calibration": (
            "max inter-code gold prototype cosine + 0.05, clamped [0.70, 0.95]"
        ),
        "gold_inter_code_max": gold_inter_code_max,
        "matrix_codes": code_ids,
        "matrix": [[float(x) for x in row] for row in matrix],
        "flagged_pairs": flags,
        "shared_unit_confusion": confusion,
        "per_code_silhouette": silhouette_by_code,
        "delta_amb": delta_amb,
        "duplicate_gate_blocks": [dict(b) for b in (duplicate_gate_blocks or [])],
    }


def _unit_margins(assignments: Sequence[Mapping[str, Any]]) -> dict[int, float]:
    sims: dict[int, list[float]] = {}
    for a in assignments:
        sim = a.get("sim_answer")
        if sim is None:
            continue
        sims.setdefault(int(a["unit_id"]), []).append(float(sim))
    margins: dict[int, float] = {}
    for unit_id, values in sims.items():
        if len(values) >= 2:
            ordered = sorted(values, reverse=True)
            margins[unit_id] = ordered[0] - ordered[1]
    return margins


def keywords_section(
    definitions: Mapping[str, Mapping[str, Any]],
    hit_populations: Mapping[str, Mapping[str, Mapping[str, int]]],
) -> dict[str, Any]:
    """Per-code keyword lists + hit_location populations + conflict flags."""
    positives: dict[str, list[SynthesizedKeyword]] = {}
    exclusions_by_code: dict[str, list[SynthesizedKeyword]] = {}
    per_code: dict[str, dict[str, Any]] = {}
    for code_id, entry in definitions.items():
        stored = entry.get("theory", {}).get("keywords", []) or []
        positives[code_id] = [
            SynthesizedKeyword(
                term=str(k.get("term")),
                score=float(k.get("score", 0.0)),
                kind="positive",
                support_n=int(k.get("support_n", 0)),
                provisional=bool(k.get("provisional", False)),
                method=str(k.get("method", "log_odds")),
            )
            for k in stored
            if str(k.get("polarity", "positive")) == "positive"
        ]
        exclusions_by_code[code_id] = [
            SynthesizedKeyword(
                term=str(k.get("term")),
                score=float(k.get("score", 0.0)),
                kind="exclusion",
                support_n=int(k.get("support_n", 0)),
                provisional=False,
                method=str(k.get("method", "log_odds")),
            )
            for k in stored
            if str(k.get("polarity")) == "exclusion"
        ]
        support = int(stored[0].get("support_n", 0)) if stored else 0
        per_code[code_id] = {
            "positive": [_asdict_kw(k) for k in positives[code_id]],
            "exclusion": [_asdict_kw(k) for k in exclusions_by_code[code_id]],
            "support_n": support,
            "hit_locations": hit_populations.get(code_id, {}),
        }
    all_exclusions = [kw for kws in exclusions_by_code.values() for kw in kws]
    flags = conflict_flags(positives, all_exclusions)
    return {
        "per_code": per_code,
        "conflicts": [
            {"conflict_type": f.conflict_type, "term": f.term, "codes": f.codes} for f in flags
        ],
        "min_support_note": "support < 15 marks the list provisional (v5 section 13)",
    }


def _asdict_kw(kw: SynthesizedKeyword) -> dict[str, Any]:
    return {
        "term": kw.term,
        "score": kw.score,
        "kind": kw.kind,
        "support_n": kw.support_n,
        "provisional": kw.provisional,
        "method": kw.method,
    }


def conformal_section(runs_history: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Set-size distribution from the latest deductive run's checkpoint."""
    for run in reversed(runs_history):
        if run["params"].get("pipeline"):
            continue
        block = run["checkpoint"].get("conformal")
        if block:
            return dict(block)
    return {
        "status": "not_available",
        "note": "no deductive run checkpoint carries a conformal block",
    }


def codebook_diff_section(
    versions: Sequence[Mapping[str, Any]],
    current_definitions: Mapping[str, Mapping[str, Any]],
    previous_definitions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Version diff: definitions / keywords / thresholds per version."""
    current_keys = set(current_definitions)
    previous_keys = set(previous_definitions)
    return {
        "current_version": versions[-1] if versions else None,
        "previous_version": versions[-2] if len(versions) >= 2 else None,
        "codes_added": sorted(current_keys - previous_keys),
        "codes_removed": sorted(previous_keys - current_keys),
        "definitions_changed": sorted(
            code
            for code in current_keys & previous_keys
            if current_definitions[code].get("definition")
            != previous_definitions[code].get("definition")
        ),
        "keywords_present": {
            code: len(entry.get("theory", {}).get("keywords", []) or [])
            for code, entry in current_definitions.items()
        },
    }


def gold_inter_code_max_cosine(
    prototypes_by_code: Mapping[str, Sequence[Sequence[float]]],
) -> float | None:
    """Max inter-code gold prototype cosine (tau_overlap calibration input).

    Callers pass prototypes already in the CENTROID space (the 256-d MRL
    slice) so the calibration matches the matrix it gates.
    """
    codes = sorted(prototypes_by_code)
    sims: list[float] = []
    for i, code_a in enumerate(codes):
        for code_b in codes[i + 1 :]:
            for pa in prototypes_by_code[code_a]:
                for pb in prototypes_by_code[code_b]:
                    sims.append(float(np.dot(pa, pb)))
    return max(sims) if sims else None


def run_stop_section(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Run-based stopping (v5 section 11): new_codes/base < 5% consecutive."""
    deductive = [r for r in runs if not r["params"].get("pipeline")]
    if len(deductive) < 2:
        return {
            "should_stop": None,
            "note": "run-based stopping needs >=2 consecutive runs (v5 section 11)",
        }
    last = deductive[-1]["checkpoint"]
    prev = deductive[-2]["checkpoint"]
    new_codes = max(int(last.get("n_codes", 0)) - int(prev.get("n_codes", 0)), 0)
    base = max(int(prev.get("n_codes", 0)), 1)
    rate = new_codes / base
    return {"should_stop": bool(rate < 0.05), "new_codes_in_run": new_codes, "new_rate": rate}


def manifest_section(
    runs: Sequence[Mapping[str, Any]], extra: dict[str, object] | None
) -> dict[str, Any]:
    """Run manifest: every reproducibility key the report consumer needs."""
    manifest: dict[str, Any] = {
        "report_schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "runs_recorded": len(runs),
        "reporter_extra": dict(extra or {}),
        "mrl_analytics": {
            "analytics_column": "embedding_256 vector(256), HNSW probe "
            "embedding_256 <=> $1::vector(256)",
            "retrieval_index": "halfvec(1024) cast HNSW, "
            "embedding::halfvec(1024) <=> $1::halfvec(1024)",
            "rerank": "surfaced candidates re-ranked at full 1024-d (exact cosine)",
            "recall_at_k_delta_vs_full_dim": 0.0,
            "source": "todo-13 G1 artifact (task13-g1-report.json); Kusupati et al. 2022",
        },
        "reproducibility_keys": [
            "parser_versions",
            "tau_xref",
            "link_margin_m",
            "embed_model",
            "embed_revision",
            "embed_mode",
            "embed_dim",
            "llm_name",
            "llm_version",
            "llm_quant",
            "tier3_n",
            "seeds_temps",
            "thresholds",
            "gold_set_id",
            "nli_prompt_id",
            "temperature_scaling_T",
            "conformal_alpha",
            "calibration_split_seed",
        ],
    }
    for pipeline, key in ((None, "latest_deductive_run"), ("inductive", "latest_inductive_run")):
        candidates = [r for r in runs if r["params"].get("pipeline") == pipeline]
        if candidates:
            manifest[key] = {
                "run_id": candidates[-1]["run_id"],
                "params": candidates[-1]["params"],
            }
    return manifest
