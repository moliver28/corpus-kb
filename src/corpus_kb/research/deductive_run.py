"""Deductive coding-run pipeline (todo 14, v5 §8) — the `coding run` surface.

Layering contract (r11), mirrored in docs/research-math.md:
  * tau (per-code, calibrated) is the DECISION rule on raw cosine;
  * temperature/ECE calibrate the REPORTED confidence only — never move tau;
  * conformal prediction sets are the GUARANTEE layer — routing only;
  * gray-zone band, margin, and conformal set-size are ROUTING triggers.

Orchestration: read models in (units, exchanges, code_registry) ->
CodingRun / CodingAssignment events out via ResearchHandler. Prototypes are
rebuilt at run time from exemplar text_sha256 refs (vectors are derived data,
never event-carried); gold units scored for the conformal calibration split
use leave-one-out prototype rebuilds — a gold exemplar is never scored
against a medoid chosen from it.
"""

from __future__ import annotations

from collections import Counter
from typing import Any
from uuid import UUID

import asyncpg
import numpy as np

from corpus_kb.handlers.research_handler import ResearchHandler
from corpus_kb.research.calibration import CONFORMAL_ALPHA, conformal_sets
from corpus_kb.research.deductive import DeductiveDecision, decide_unit
from corpus_kb.research.exhaustiveness import exhaustiveness_block
from corpus_kb.research.prototypes import build_view_prototypes
from corpus_kb.research.run_inputs import UnitViews, load_codes, load_gold_views, load_unit_views
from corpus_kb.research.scoring import SCORING_BATCH_ROWS, stream_scores
from corpus_kb.storage.tenant_conn import tenant_connection


def _thresholds_from_theory(theory: dict[str, Any]) -> dict[str, object]:
    block = theory.get("thresholds") or {}
    cv_range = block.get("cv_range") or {}
    return {
        "tau_a": float(block.get("tau_a") or 0.0),
        "tau_qa": float(block.get("tau_qa") or 0.0),
        "delta": float(block.get("delta") or 0.0),
        "m_gz_a": float(cv_range.get("m_gz_a") or 0.0),
        "m_gz_qa": float(cv_range.get("m_gz_qa") or 0.0),
        "m_gz_delta": float(cv_range.get("m_gz_delta") or 0.0),
        "unreliable": bool(block.get("threshold_unreliable", True)),
    }


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _score_view(units: list[UnitViews], view: str, prototypes: list[list[float]]) -> list[float]:
    return list(stream_scores([getattr(u, view) for u in units], prototypes, SCORING_BATCH_ROWS))


def _loo_calibration_rows(
    gold_by_code: dict[str, list[tuple[list[float], list[float], list[float], str]]],
    code_ids: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    """Answer-view scores for gold units, leave-one-out per code.

    Each gold unit is scored against prototypes rebuilt from the OTHER gold
    units of its code (G2 discipline: never score an exemplar against a
    medoid chosen from it). With a single gold exemplar the full prototype
    set is the only option (documented degenerate case).
    """
    full_answer_protos = {
        code_id: build_view_prototypes([g[0] for g in gold])
        for code_id, gold in gold_by_code.items()
    }
    rows: list[list[float]] = []
    labels: list[list[int]] = []
    for code_id in code_ids:
        gold = gold_by_code.get(code_id, [])
        for i in range(len(gold)):
            held_out = [g for j, g in enumerate(gold) if j != i]
            row: list[float] = []
            for other in code_ids:
                protos = (
                    build_view_prototypes([g[0] for g in held_out])
                    if other == code_id and held_out
                    else full_answer_protos[other]
                )
                scored = _score_view(
                    [UnitViews(0, gold[i][0], gold[i][1], gold[i][2], None, None)],
                    "answer",
                    protos,
                )
                row.append(scored[0])
            rows.append(row)
            labels.append([1 if c == code_id else 0 for c in code_ids])
    return np.array(rows, dtype=np.float32), np.array(labels, dtype=int)


def _coverage_of(
    decisions_by_unit: dict[int, list[DeductiveDecision]], evidence_basis: str
) -> float:
    if not decisions_by_unit:
        return 0.0
    covered = sum(
        1
        for ds in decisions_by_unit.values()
        if any(d.evidence_basis == evidence_basis for d in ds)
    )
    return covered / len(decisions_by_unit)


async def run_deductive(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    codebook_version_id: UUID,
    project_id: UUID | None = None,
    cal_alpha: float = CONFORMAL_ALPHA,
) -> dict[str, object]:
    """Launch one deductive run: score codable units, record assignments.

    Returns a summary with per-basis counts, coverage split, and any skipped
    codes (missing gold or non-UUID code_id). Assignment rows land after the
    caller advances the research projection.
    """
    handler = ResearchHandler(pool)
    run_info = handler.start_coding_run(
        tenant_id,
        params={"cal_alpha": cal_alpha, "scoring_batch_rows": SCORING_BATCH_ROWS},
    )
    run_id = UUID(str(run_info["run_id"]))

    async with tenant_connection(pool, tenant_id) as tconn:
        codes_all = await load_codes(tconn, tenant_id, codebook_version_id)
        units = await load_unit_views(tconn, tenant_id, project_id)
        gold_by_code: dict[str, list[tuple[list[float], list[float], list[float], str]]] = {}
        for code in codes_all:
            refs = list(code["theory"].get("exemplar_text_sha256") or [])
            gold_by_code[code["code_id"]] = await load_gold_views(tconn, tenant_id, refs)

    codes = [c for c in codes_all if _is_uuid(c["code_id"])]
    skipped: dict[str, str] = {
        c["code_id"]: "non_uuid_code_id" for c in codes_all if not _is_uuid(c["code_id"])
    }
    scored: dict[str, dict[str, list[float]]] = {}
    for code in codes:
        code_id = code["code_id"]
        gold = gold_by_code.get(code_id, [])
        if not gold:
            skipped[code_id] = "no_gold_prototypes"
            continue
        prototypes = {
            "answer": build_view_prototypes([g[0] for g in gold]),
            "qa": build_view_prototypes([g[1] for g in gold]),
            "question": build_view_prototypes([g[2] for g in gold]),
        }
        scored[code_id] = {
            view: _score_view(units, view, prototypes[view])
            for view in ("answer", "qa", "question")
        }

    decisions_by_unit, conformal_sets_by_unit, conformal_coverage = _decide_units(
        codes, units, scored, gold_by_code, cal_alpha
    )

    explicit = qdep = review = 0
    row_of_unit = {u.unit_id: i for i, u in enumerate(units)}
    for unit in units:
        for d in decisions_by_unit[unit.unit_id]:
            views = scored.get(d.code_id, {})
            handler.record_assignment(
                tenant_id,
                unit_id=unit.unit_id,
                code_id=UUID(d.code_id),
                run_id=run_id,
                cb_version_id=codebook_version_id,
                sim_answer=views["answer"][row_of_unit[unit.unit_id]] if views else None,
                sim_qa=views["qa"][row_of_unit[unit.unit_id]] if views else None,
                sim_q=views["question"][row_of_unit[unit.unit_id]] if views else None,
                evidence_basis=d.evidence_basis,
                stance=unit.stance,
                term_origin=unit.term_origin,
                rationale=f"deductive:v2:{d.reason}",
                confidence=d.confidence,
                tier_fired=d.tier_fired,
            )
            if d.evidence_basis == "explicit_in_answer":
                explicit += 1
            elif d.evidence_basis == "question_dependent":
                qdep += 1
            if d.status == "review":
                review += 1

    summary = {
        "status": "success",
        "run_id": str(run_id),
        "codebook_version_id": str(codebook_version_id),
        "n_units": len(units),
        "n_codes": len(scored),
        "explicit": explicit,
        "question_dependent": qdep,
        "review": review,
        "cov_explicit": _coverage_of(decisions_by_unit, "explicit_in_answer"),
        "cov_qdep": _coverage_of(decisions_by_unit, "question_dependent"),
        "skipped_codes": skipped,
        "conformal_alpha": cal_alpha,
    }
    payload = {
        **{k: v for k, v in summary.items() if k != "status"},
        "conformal": _conformal_block(conformal_sets_by_unit, cal_alpha, conformal_coverage),
        "exhaustiveness": exhaustiveness_block(units, scored, gold_by_code),
    }
    handler.checkpoint_coding_run(tenant_id, run_id, payload)
    handler.stop_coding_run(tenant_id, run_id)
    return summary


def _conformal_block(
    sets_by_unit: dict[int, set[int]], cal_alpha: float, empirical_coverage: float
) -> dict[str, object]:
    """Set-size distribution + coverage (the report reads this block).

    ``per_unit`` persists set sizes > 1 only (the routing-relevant minority;
    size 1 is the default the notebook evidence view assumes).
    """
    sizes = Counter(len(s) for s in sets_by_unit.values())
    return {
        "set_size_distribution": {str(size): count for size, count in sorted(sizes.items())},
        "nominal_coverage": 1.0 - cal_alpha,
        "empirical_coverage": empirical_coverage,
        "n_units": len(sets_by_unit),
        "per_unit": {
            str(unit_id): len(s) for unit_id, s in sorted(sets_by_unit.items()) if len(s) > 1
        },
    }


def _decide_units(
    codes: list[dict[str, Any]],
    units: list[UnitViews],
    scored: dict[str, dict[str, list[float]]],
    gold_by_code: dict[str, list[tuple[list[float], list[float], list[float], str]]],
    cal_alpha: float,
) -> tuple[dict[int, list[DeductiveDecision]], dict[int, set[int]], float]:
    code_ids = list(scored)
    decisions_by_unit: dict[int, list[DeductiveDecision]] = {u.unit_id: [] for u in units}
    sets_by_unit: dict[int, set[int]] = {u.unit_id: set() for u in units}
    if not code_ids or not units:
        return decisions_by_unit, sets_by_unit, 0.0
    unit_matrix = np.array(
        [[scored[c]["answer"][i] for c in code_ids] for i in range(len(units))],
        dtype=np.float32,
    )
    cal_scores, cal_labels = _loo_calibration_rows(gold_by_code, code_ids)
    sets, coverage = conformal_sets(cal_scores, cal_labels, unit_matrix, alpha=cal_alpha)
    sets_by_unit = {u.unit_id: sets[i] for i, u in enumerate(units)}
    interpretive = {
        c["code_id"] for c in codes if bool((c["theory"] or {}).get("is_interpretive", False))
    }
    thresholds = {c["code_id"]: _thresholds_from_theory(c["theory"]) for c in codes}
    for i, unit in enumerate(units):
        scores_by_code = {
            c: {
                "answer": scored[c]["answer"][i],
                "qa": scored[c]["qa"][i],
                "question": scored[c]["question"][i],
            }
            for c in code_ids
        }
        decisions_by_unit[unit.unit_id] = decide_unit(
            scores_by_code,
            thresholds,
            stance=unit.stance,
            interpretive_codes=interpretive,
            conformal_set_size=len(sets[i]),
        )
    return decisions_by_unit, sets_by_unit, coverage
