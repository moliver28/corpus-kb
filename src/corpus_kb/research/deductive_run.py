"""Deductive coding-run pipeline (todo 14, v5 §8) — the `coding run` surface.

Layering contract (r11), mirrored in docs/research/research-math.md:
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

Tier-3 wiring (issue #132): every run computes the combined review priority
and closed-enum routing reasons into the checkpoint's ``review_priority``
block. With ``coding.uncertainty.tier3_enabled`` the flagged minority is
escalated: resampled rationales and pairwise mutual-entailment verdicts are
measured through the degrading clients in ``coding/uncertainty/``, and the
escalated units' semantic entropy rides their assignment events into
research_signals. Escalation is OFF by default — the engine stays
deterministic and LLM-free unless the owner turns it on.
"""

from __future__ import annotations

from collections import Counter
from typing import Any
from uuid import UUID

import asyncpg
import numpy as np

from corpus_kb.coding.uncertainty.cascade import CascadeResult, run_cascade, select_escalation
from corpus_kb.coding.uncertainty.nli_client import NLI_MODEL, nli_mutual_entailment
from corpus_kb.coding.uncertainty.resample_client import (
    RESAMPLE_PROMPT_ID,
    resample_rationales,
)
from corpus_kb.coding.uncertainty.review_priority import (
    ReviewSignal,
    priority_scores,
    route_reasons,
    top2_margins,
)
from corpus_kb.coding.uncertainty.tier2_hedging import hedging_score
from corpus_kb.coding.uncertainty.tier3_consistency import N_SAMPLES
from corpus_kb.domain.application import dsn_from_config
from corpus_kb.handlers.research_handler import ResearchHandler
from corpus_kb.research.calibration import CONFORMAL_ALPHA, conformal_sets
from corpus_kb.research.deduction_guard import guard_deductive_run
from corpus_kb.research.deductive import DeductiveDecision, decide_unit
from corpus_kb.research.exact_cache import CacheSettings, load_cache_settings
from corpus_kb.research.exhaustiveness import exhaustiveness_block
from corpus_kb.research.notebook_evidence import DEFAULT_DELTA_AMB
from corpus_kb.research.prototypes import build_view_prototypes
from corpus_kb.research.run_inputs import UnitViews, load_codes, load_gold_views, load_unit_views
from corpus_kb.research.scoring import SCORING_BATCH_ROWS, stream_scores
from corpus_kb.storage.gateway import SqlConn
from corpus_kb.storage.tenant_conn import tenant_connection
from corpus_kb.utils.config_sections import mapping_section


def _thresholds_from_theory(theory: dict[str, Any]) -> dict[str, Any]:
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


async def _load_gold_by_code(
    tconn: SqlConn,
    tenant_id: UUID,
    codes_all: list[dict[str, Any]],
) -> dict[str, list[tuple[list[float], list[float], list[float], str]]]:
    """Gold view vectors (answer, qa, question, basis) per code id."""
    gold_by_code: dict[str, list[tuple[list[float], list[float], list[float], str]]] = {}
    for code in codes_all:
        refs = list(code["theory"].get("exemplar_text_sha256") or [])
        gold_by_code[str(code["code_id"])] = await load_gold_views(tconn, tenant_id, refs)
    return gold_by_code


def _score_codes(
    codes: list[dict[str, Any]],
    units: list[Any],
    gold_by_code: dict[str, list[tuple[list[float], list[float], list[float], str]]],
    skipped: dict[str, str],
) -> dict[str, dict[str, list[float]]]:
    """Per-code prototype scoring over every unit view.

    Codes with no gold exemplars are skipped (recorded in ``skipped``) — they
    cannot produce prototypes.
    """
    scored: dict[str, dict[str, list[float]]] = {}
    for code in codes:
        code_id = str(code["code_id"])
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
    return scored


def _record_assignments(
    handler: ResearchHandler,
    tenant_id: UUID,
    run_id: UUID,
    codebook_version_id: UUID,
    units: list[Any],
    decisions_by_unit: dict[int, list[DeductiveDecision]],
    scored: dict[str, dict[str, list[float]]],
    signals_by_unit: dict[int, list[dict[str, object]]] | None = None,
) -> tuple[int, int, int]:
    """Persist one assignment row per (unit, decision); return basis counts.

    Tier-3 signals (issue #132 wiring) ride the SAME assignment event via
    the ``signals`` kwarg; the analytics projector lands them in
    research_signals. Returns (explicit, question_dependent, review).
    """
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
                status=d.status,
                signals=(signals_by_unit or {}).get(unit.unit_id),
            )
            if d.evidence_basis == "explicit_in_answer":
                explicit += 1
            elif d.evidence_basis == "question_dependent":
                qdep += 1
            if d.status == "review":
                review += 1
    return explicit, qdep, review


async def run_deductive(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    codebook_version_id: UUID,
    project_id: UUID | None = None,
    cal_alpha: float = CONFORMAL_ALPHA,
    cfg: dict[str, Any] | None = None,
    allow_draft: bool = False,
) -> dict[str, Any]:
    """Launch one deductive run: score codable units, record assignments.

    Returns a summary with per-basis counts, coverage split, and any skipped
    codes (missing gold or non-UUID code_id). Assignment rows land after the
    caller advances the research projection.

    U24 guard: ``research.release.enforcement`` (from ``cfg``, or the loaded
    config when None) resolves the version's active release first — ``warn``
    records the hash or an ``unreleased_draft`` warning and continues;
    ``enforce`` blocks unreleased versions unless ``allow_draft`` (bypass
    runs are labeled ``sandbox``). The U42 cache gate is evaluated per run
    from the same config and recorded on the checkpoint.
    """
    from corpus_kb.config import load_config

    config = cfg if cfg is not None else load_config()
    guard = await guard_deductive_run(
        pool, tenant_id, codebook_version_id, config, allow_draft=allow_draft
    )
    if guard.action == "block":
        return {
            "status": "blocked_unreleased_draft",
            "enforcement": guard.enforcement,
            "codebook_version_id": str(codebook_version_id),
            "reason": guard.reason,
            "fix": "release the codebook version, or re-run with --allow-draft "
            "(the run is then labeled sandbox)",
        }

    cache = load_cache_settings(config)
    handler = ResearchHandler(pool, dsn=dsn_from_config(config))
    run_params: dict[str, Any] = {
        "cal_alpha": cal_alpha,
        "scoring_batch_rows": SCORING_BATCH_ROWS,
    }
    if guard.label:
        run_params["release_guard"] = guard.summary_block()
    run_info = handler.start_coding_run(tenant_id, params=run_params)
    run_id = UUID(str(run_info["run_id"]))

    async with tenant_connection(pool, tenant_id) as tconn:
        codes_all = await load_codes(tconn, tenant_id, codebook_version_id)
        units = await load_unit_views(tconn, tenant_id, project_id)
        gold_by_code = await _load_gold_by_code(tconn, tenant_id, codes_all)

    codes = [c for c in codes_all if _is_uuid(c["code_id"])]
    skipped: dict[str, str] = {
        c["code_id"]: "non_uuid_code_id" for c in codes_all if not _is_uuid(c["code_id"])
    }
    scored = _score_codes(codes, units, gold_by_code, skipped)

    decisions_by_unit, conformal_sets_by_unit, conformal_coverage = _decide_units(
        codes, units, scored, gold_by_code, cal_alpha
    )
    cascade, signals_by_unit, n_skipped, tier3_on = await _tier3_review_pass(
        cfg, codes, units, scored, decisions_by_unit
    )
    explicit, qdep, review = _record_assignments(
        handler,
        tenant_id,
        run_id,
        codebook_version_id,
        units,
        decisions_by_unit,
        scored,
        signals_by_unit,
    )

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
    if guard.label:
        summary["release"] = guard.summary_block()
    payload = {
        **{k: v for k, v in summary.items() if k != "status"},
        "cache": _cache_block(cache),
        "conformal": _conformal_block(conformal_sets_by_unit, cal_alpha, conformal_coverage),
        "review_priority": _review_priority_block(cascade, tier3_on, units, n_skipped),
        "exhaustiveness": exhaustiveness_block(units, scored, gold_by_code),
    }
    handler.checkpoint_coding_run(tenant_id, run_id, payload)
    handler.stop_coding_run(tenant_id, run_id)
    return summary


def _cache_block(cache: CacheSettings) -> dict[str, Any]:
    """The U42 cache gate, evaluated and recorded per deductive run.

    The deductive v2 engine scores deterministically against prototypes and
    makes NO LLM calls, so nothing is consulted or written today; the gate
    decision (``cache.enabled`` x ``cache.deductive_enabled``, ``--no-cache``
    drops the key at the CLI layer) is still recorded honestly per run.
    """
    return {
        "enabled": cache.enabled,
        "deductive_allowed": cache.allows("deductive"),
        "consulted": False,
        "note": "deductive v2 scoring is deterministic (no LLM calls); "
        "the cache gate is evaluated per run for the assignment path",
    }


def _conformal_block(
    sets_by_unit: dict[int, set[int]], cal_alpha: float, empirical_coverage: float
) -> dict[str, Any]:
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


def _uncertainty_settings(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """The ``coding.uncertainty`` block with code defaults.

    Read from the loaded config with the same fallback pattern the conflict
    ops use; ``tier3_enabled`` defaults to False, so the deductive engine
    keeps its documented deterministic no-LLM contract until the owner
    turns escalation on.
    """
    section = mapping_section(mapping_section(cfg if cfg else {}, "coding"), "uncertainty")
    return {
        "tier3_enabled": bool(section.get("tier3_enabled", False)),
        "model": str(section.get("model") or "qwen3:4b"),
        "base_url": str(section.get("base_url") or "http://localhost:11434"),
    }


def _review_pass_inputs(
    codes: list[dict[str, Any]],
    units: list[UnitViews],
    scored: dict[str, dict[str, list[float]]],
    decisions_by_unit: dict[int, list[DeductiveDecision]],
) -> tuple[np.ndarray, list[ReviewSignal], list[str]]:
    """Deterministic tier-0..2 cascade inputs (issue #132 wiring).

    Margins come from the answer-view score matrix, hedge counts from the
    unit text, interpretive from the top-scored code's theory, and the raw
    decision reason rides to the routing boundary. Tier-1 logprobs, SE, and
    the exchange link are NOT MEASURED here: they stay None, so the
    priority never fabricates uncertainty.
    """
    code_ids = list(scored)
    matrix = (
        np.array(
            [[scored[c]["answer"][i] for c in code_ids] for i in range(len(units))],
            dtype=np.float32,
        )
        if code_ids and units
        else np.zeros((len(units), 0), dtype=np.float32)
    )
    margins = top2_margins(matrix)
    interpretive = {
        c["code_id"] for c in codes if bool((c["theory"] or {}).get("is_interpretive", False))
    }
    signals: list[ReviewSignal] = []
    reasons: list[str] = []
    for i, unit in enumerate(units):
        decisions = decisions_by_unit.get(unit.unit_id, [])
        top_interpretive = code_ids[int(np.argmax(matrix[i]))] in interpretive if code_ids else False
        signals.append(
            ReviewSignal(
                margin=float(margins[i]),
                hedge=hedging_score(unit.text or ""),
                interpretive=top_interpretive,
                question_dependent=any(
                    d.evidence_basis == "question_dependent" for d in decisions
                ),
            )
        )
        reasons.append(decisions[0].reason if decisions else "")
    return matrix, signals, reasons


async def _escalated_resamples(
    matrix: np.ndarray,
    code_ids: list[str],
    codes: list[dict[str, Any]],
    units: list[UnitViews],
    base_signals: list[ReviewSignal],
    settings: dict[str, Any],
) -> dict[int, list[str]]:
    """Eager resamples for the escalated minority (the tier-3 live path).

    The cascade is sync, so its model I/O happens here first, under the
    client degrade contracts, and replays into it through closures.
    """
    preliminary = priority_scores(base_signals)
    by_code = {str(c["code_id"]): c for c in codes}
    out: dict[int, list[str]] = {}
    for idx in select_escalation(preliminary):
        code = by_code.get(code_ids[int(np.argmax(matrix[idx]))], {}) if code_ids else {}
        out[idx] = await resample_rationales(
            units[idx].text or "",
            str(code.get("name") or ""),
            str(code.get("definition") or ""),
            N_SAMPLES,
            str(settings["model"]),
            str(settings["base_url"]),
        )
    return out


async def _pairwise_nli_verdicts(
    rationales_by_unit: dict[int, list[str]], settings: dict[str, Any]
) -> dict[tuple[str, str], bool]:
    """Mutual-entailment verdicts for every escalated rationale pair.

    A degraded pair (judge None) is non-entailing, per the nli_client
    contract; verdicts replay into the cascade through a closure.
    """
    verdicts: dict[tuple[str, str], bool] = {}
    for rationales in rationales_by_unit.values():
        for i in range(len(rationales)):
            for j in range(i + 1, len(rationales)):
                a, b = rationales[i], rationales[j]
                verdict = await nli_mutual_entailment(
                    a, b, model=NLI_MODEL, base_url=str(settings["base_url"])
                )
                verdicts[(a, b)] = bool(verdict)
    return verdicts


async def _tier3_review_pass(
    cfg: dict[str, Any] | None,
    codes: list[dict[str, Any]],
    units: list[UnitViews],
    scored: dict[str, dict[str, list[float]]],
    decisions_by_unit: dict[int, list[DeductiveDecision]],
) -> tuple[CascadeResult, dict[int, list[dict[str, object]]], int, bool]:
    """The tier-3 wiring point (issue #132): priority always, escalation opt-in.

    The pure priority and routing reasons run on EVERY run and land in the
    checkpoint block. With ``coding.uncertainty.tier3_enabled`` the flagged
    minority escalates: resamples and pairwise NLI verdicts are measured
    eagerly (async, degrading per the client contracts) and replayed into
    the sync cascade. An escalated unit with a decision carries its tier-3
    signal on the assignment event; a degraded unit carries SE=None so the
    disagreement flag stays honest; an escalated unit without a decision is
    counted, never silently dropped.
    """
    settings = _uncertainty_settings(cfg)
    tier3_on = bool(settings["tier3_enabled"])
    matrix, base_signals, decision_reasons = _review_pass_inputs(
        codes, units, scored, decisions_by_unit
    )
    rationales_by_unit: dict[int, list[str]] = {}
    if tier3_on and units:
        rationales_by_unit = await _escalated_resamples(
            matrix, list(scored), codes, units, base_signals, settings
        )
    verdicts: dict[tuple[str, str], bool] = {}
    if any(len(r) >= 2 for r in rationales_by_unit.values()):
        verdicts = await _pairwise_nli_verdicts(rationales_by_unit, settings)

    def resample_fn(i: int) -> list[str]:
        return rationales_by_unit.get(i, [])

    def nli_fn(a: str, b: str) -> bool:
        return verdicts.get((a, b), verdicts.get((b, a), False))

    cascade = run_cascade(
        matrix,
        base_signals,
        resample_fn,
        nli_fn,
        decision_reasons,
        DEFAULT_DELTA_AMB,
        escalate=tier3_on,
    )
    signals_by_unit: dict[int, list[dict[str, object]]] = {}
    n_skipped = 0
    for i in sorted(rationales_by_unit):
        unit_id = units[i].unit_id
        if not decisions_by_unit.get(unit_id):
            n_skipped += 1
            continue
        measured = bool(rationales_by_unit[i])
        signals_by_unit[unit_id] = [
            {
                "tier": 3,
                "semantic_entropy": cascade.routed[i].semantic_entropy if measured else None,
                "n_clusters": None,
                "extra": {
                    "escalated": True,
                    "measured": measured,
                    "resample_prompt_id": RESAMPLE_PROMPT_ID,
                },
            }
        ]
    return cascade, signals_by_unit, n_skipped, tier3_on


def _review_priority_block(
    cascade: CascadeResult,
    tier3_on: bool,
    units: list[UnitViews],
    n_skipped: int,
) -> dict[str, Any]:
    """The checkpoint block where review_priority ranks surface (issue #132).

    Top 100 candidates, descending priority, unit id as the stable tiebreak
    (deterministic output, program rule 9).
    """
    ranked = sorted(cascade.routed, key=lambda r: (-r.priority, r.unit_index))[:100]
    return {
        "delta_amb": DEFAULT_DELTA_AMB,
        "weights": "equal",
        "tier3_enabled": tier3_on,
        "n_escalated": cascade.n_escalated,
        "n_nli_calls": cascade.n_nli_calls,
        "n_signals_skipped": n_skipped,
        "units": [
            {
                "unit_id": units[r.unit_index].unit_id,
                "priority": float(r.priority),
                "reason": str(r.reason),
                "escalated": bool(r.escalated),
                "semantic_entropy": r.semantic_entropy,
            }
            for r in ranked
        ],
    }
