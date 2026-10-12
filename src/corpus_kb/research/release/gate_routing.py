"""Agreement-signal release gates (U18 calibration, U19 routing; v6 §7 U18/U19).

Pure functions over ``(GateInputs, ResolvedProfile)``; evidence blocks are
assembled by ``gate_reads.py`` (which computes the per-unit cross-run
agreement scores with ``coding.uncertainty.cross_run_agreement``).

U18 agreement_calibration — the cross-run agreement score is the PRIMARY
uncertainty signal, but it is ORDINAL, not a probability: before it may gate
routing it must be validated against human-validated correctness on
calibration labels (AUROC + ECE via ``coding.confidence_calibration``).
Conventions when the profile sets none: AUROC floor reuses
``g3_audit.AUROC_DROP_FLOOR`` (the repo's existing signal floor); the ECE
ceiling is this module's documented 0.10. Either class missing ->
``not_evaluable`` (never a pass).

U19 agreement_routing — ROUTING BEHAVIOR (B2 wires the gray-zone queue):
the gate exposes the low-agreement unit list below
``profile.agreement_routing_threshold`` and fails only when below-threshold
units are NOT yet routed (``routed: false``). ``not_enforced`` when the
profile carries no threshold. Kept out of ``required_gates`` by the shipped
profiles; the halt-side twin lives in ``research.cycle_gates``.
"""

from __future__ import annotations

import numpy as np

from corpus_kb.research.g3_audit import AUROC_DROP_FLOOR
from corpus_kb.research.release.gate_support import as_dict, as_float, as_list, as_str
from corpus_kb.research.release.gate_types import (
    GATE_AGREEMENT_CALIBRATION,
    GATE_AGREEMENT_ROUTING,
    GateInputs,
    GateResult,
)
from corpus_kb.research.release.profiles import ResolvedProfile

# CONVENTION (documented, not a standard): calibrated ECE ceiling for the
# agreement score before routing may trust it as a probability proxy.
AGREEMENT_ECE_CEILING = 0.10


def gate_agreement_calibration(inputs: GateInputs, _profile: ResolvedProfile) -> GateResult:
    """U18: the cross-run agreement score AUROC/ECE-validated on human labels."""
    from corpus_kb.coding.confidence_calibration import evaluate_signal

    block = as_dict(inputs.agreement_calibration)
    scores = as_list(block.get("scores"))
    outcomes = as_list(block.get("outcomes"))
    if not block or not scores or not outcomes:
        return GateResult(
            gate_id=GATE_AGREEMENT_CALIBRATION,
            status="not_evaluable",
            reason="no agreement-calibration evidence recorded (inputs.agreement_calibration)",
        )
    try:
        score_arr = np.asarray(scores, dtype=float)
        outcome_arr = np.asarray(outcomes, dtype=float)
    except (ValueError, TypeError) as exc:
        return GateResult(
            gate_id=GATE_AGREEMENT_CALIBRATION,
            status="not_evaluable",
            reason=f"invalid_output: agreement-calibration inputs unusable ({exc})",
        )
    evaluation = evaluate_signal("cross_run_agreement", score_arr, outcome_arr)
    value = {
        "signal": evaluation.signal,
        "auroc": evaluation.auroc,
        "ece": evaluation.ece,
        "brier": evaluation.brier,
        "n": evaluation.n,
    }
    if evaluation.auroc is None:
        return GateResult(
            gate_id=GATE_AGREEMENT_CALIBRATION,
            status="not_evaluable",
            reason=(
                "insufficient_data: calibration outcomes carry a single class — the "
                "agreement score cannot be validated and may gate nothing"
            ),
            value=value,
            threshold={"auroc_floor": AUROC_DROP_FLOOR, "ece_ceiling": AGREEMENT_ECE_CEILING},
        )
    failed = evaluation.auroc < AUROC_DROP_FLOOR or evaluation.ece > AGREEMENT_ECE_CEILING
    if failed:
        return GateResult(
            gate_id=GATE_AGREEMENT_CALIBRATION,
            status="fail",
            reason=(
                f"agreement score not validated (AUROC {evaluation.auroc:.3f} vs floor "
                f"{AUROC_DROP_FLOOR}, ECE {evaluation.ece:.3f} vs ceiling "
                f"{AGREEMENT_ECE_CEILING}) — it may not gate routing"
            ),
            value=value,
            threshold={"auroc_floor": AUROC_DROP_FLOOR, "ece_ceiling": AGREEMENT_ECE_CEILING},
        )
    return GateResult(
        gate_id=GATE_AGREEMENT_CALIBRATION,
        status="pass",
        reason=(
            f"agreement score validated on calibration labels (AUROC "
            f"{evaluation.auroc:.3f} >= {AUROC_DROP_FLOOR}, ECE {evaluation.ece:.3f} "
            f"<= {AGREEMENT_ECE_CEILING})"
        ),
        value=value,
        threshold={"auroc_floor": AUROC_DROP_FLOOR, "ece_ceiling": AGREEMENT_ECE_CEILING},
    )


def low_agreement_units(block: dict[str, object], threshold: float) -> list[dict[str, object]]:
    """Units whose agreement is below the routing threshold (routed flag kept)."""
    entries: list[tuple[float, str, bool]] = []
    for entry in as_list(block.get("units")):
        unit = as_dict(entry)
        agreement = as_float(unit.get("agreement"))
        if agreement is None or agreement >= threshold:
            continue
        entries.append(
            (agreement, as_str(unit.get("unit_id")) or "<unnamed>", bool(unit.get("routed")))
        )
    entries.sort()
    return [
        {"unit_id": unit_id, "agreement": agreement, "routed": routed}
        for agreement, unit_id, routed in entries
    ]


def gate_agreement_routing(inputs: GateInputs, profile: ResolvedProfile) -> GateResult:
    """U19: expose the low-agreement queue; fail on unrouted below-threshold units."""
    block = as_dict(inputs.agreement_routing)
    if not block:
        return GateResult(
            gate_id=GATE_AGREEMENT_ROUTING,
            status="not_evaluable",
            reason="no per-unit agreement ledger recorded (inputs.agreement_routing empty)",
        )
    threshold = profile.agreement_routing_threshold
    if threshold is None:
        return GateResult(
            gate_id=GATE_AGREEMENT_ROUTING,
            status="pass",
            reason=(
                "not_enforced (no agreement_routing_threshold in profile; recorded-only): "
                f"{len(as_list(block.get('units')))} unit(s) with agreement scores on file"
            ),
            value={"n_units": len(as_list(block.get("units"))), "low_agreement_units": []},
            threshold=None,
        )
    low = low_agreement_units(block, threshold)
    unrouted = [u for u in low if not u["routed"]]
    value: dict[str, object] = {
        "threshold": threshold,
        "low_agreement_units": low,
        "n_below_threshold": len(low),
        "n_unrouted": len(unrouted),
        "unrouted_unit_ids": [u["unit_id"] for u in unrouted],
    }
    if unrouted:
        return GateResult(
            gate_id=GATE_AGREEMENT_ROUTING,
            status="fail",
            reason=(
                f"{len(unrouted)} low-agreement unit(s) (below {threshold}) are not yet "
                "routed to the gray-zone review queue"
            ),
            value=value,
            threshold=threshold,
        )
    return GateResult(
        gate_id=GATE_AGREEMENT_ROUTING,
        status="pass",
        reason=(
            f"all {len(low)} low-agreement unit(s) (below {threshold}) are routed to the "
            "gray-zone queue"
        ),
        value=value,
        threshold=threshold,
    )


__all__ = [
    "AGREEMENT_ECE_CEILING",
    "gate_agreement_calibration",
    "gate_agreement_routing",
    "low_agreement_units",
]
