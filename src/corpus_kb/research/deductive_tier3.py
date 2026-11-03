"""Tier-3 cascade wiring for the deductive eval path (issue #132).

Pure move out of ``deductive_run.py`` for the 250-line rule. Every run
computes the combined review priority and the closed-enum routing reasons
into the checkpoint's ``review_priority`` block. With
``coding.uncertainty.tier3_enabled`` the flagged minority escalates:
resampled rationales and pairwise mutual-entailment verdicts are measured
through the degrading clients in ``coding/uncertainty/``, and the
escalated units' semantic entropy rides their assignment events into
research_signals. Escalation is OFF by default — the engine stays
deterministic and LLM-free unless the owner turns it on.
"""

from __future__ import annotations

from typing import Any

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
    top2_margins,
)
from corpus_kb.coding.uncertainty.tier2_hedging import hedging_score
from corpus_kb.coding.uncertainty.tier3_consistency import N_SAMPLES
from corpus_kb.research.deductive import DeductiveDecision
from corpus_kb.research.notebook_evidence import DEFAULT_DELTA_AMB
from corpus_kb.research.run_inputs import UnitViews
from corpus_kb.utils.config_sections import mapping_section


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
        top_interpretive = (
            code_ids[int(np.argmax(matrix[i]))] in interpretive if code_ids else False
        )
        signals.append(
            ReviewSignal(
                margin=float(margins[i]),
                hedge=hedging_score(unit.text or ""),
                interpretive=top_interpretive,
                question_dependent=any(d.evidence_basis == "question_dependent" for d in decisions),
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
