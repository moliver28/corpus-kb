"""Annotator-evidence release gates (U12, U13, U14; v6 section 7).

Thin PURE adapters over the wave-1 math modules — the gate functions map the
module results onto the release ``GateResult`` vocabulary and pull thresholds
from the resolved profile (falling back to the modules' documented
conventions when the profile sets none). No queries here; ``gate_reads.py``
assembles the evidence blocks.

U12 prompt_stability — K STORED prompt-variant label-sets over the same
calibration units (variants are versioned/hashed upstream, never generated
at evaluation time); pooled alpha from ``coding.uncertainty.prompt_stability``
vs ``profile.prompt_stability_alpha``.

U13 alternative_annotator_test — Calderon et al. alt test
(``coding.alt_test``) with ``profile.alt_test_margin`` /
``profile.alt_test_passing_fraction``; ``insufficient_data`` without
multiply-annotated units.

U14 prediction_powered_estimates — ``coding.ppi`` over the audited sample's
design weights; the gate verifies the audit design exists and reports BOTH
the raw and the debiased estimate. The raw estimate is never labeled
validated: without an audit sample the gate is not_evaluable (blocking any
profile that requires it) with the ``uncorrected`` label carried in value.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from corpus_kb.coding.alt_test import AltTestResult, alt_test
from corpus_kb.coding.ppi import PPIResult, ppi_estimate
from corpus_kb.coding.uncertainty.prompt_stability import (
    PromptStabilityResult,
    prompt_stability,
)
from corpus_kb.research.release.gate_support import as_dict, as_list, as_str
from corpus_kb.research.release.gate_types import (
    GATE_ALT_TEST,
    GATE_PREDICTION_POWERED,
    GATE_PROMPT_STABILITY,
    GateInputs,
    GateResult,
)
from corpus_kb.research.release.profiles import ResolvedProfile

# Alt-test conventions when the profile sets none (AltTestSettings defaults).
_ALT_TEST_DEFAULT_MARGIN = 0.1
_ALT_TEST_DEFAULT_PASSING_FRACTION = 0.5
_ALT_TEST_SEED = 0  # fixed so the (bootstrap) alt test stays deterministic


def gate_prompt_stability(inputs: GateInputs, profile: ResolvedProfile) -> GateResult:
    """U12: agreement across stored prompt variants vs the profile alpha."""
    block = as_dict(inputs.prompt_variants)
    raw_variants = as_list(block.get("variant_label_sets"))
    label_sets: list[Mapping[str, str]] = []
    for entry in raw_variants:
        labels = as_dict(entry).get("labels")
        if isinstance(labels, dict):
            label_sets.append({str(k): str(v) for k, v in labels.items()})
    threshold = profile.prompt_stability_alpha
    if threshold is None:
        threshold = 0.667  # prompt_stability's documented default convention
    result: PromptStabilityResult = prompt_stability(label_sets, alpha_threshold=threshold)
    status = result.status if result.status in ("pass", "fail") else "not_evaluable"
    reason = result.reason
    if result.status == "not_evaluable":
        reason = f"insufficient_data: {result.reason}"
    return GateResult(
        gate_id=GATE_PROMPT_STABILITY,
        status=status,
        reason=reason,
        value={
            "alpha": result.alpha,
            "n_variants": result.n_variants,
            "n_units": result.n_units,
            "variant_names": [as_str(as_dict(v).get("variant")) for v in raw_variants],
        },
        threshold=threshold,
    )


def _alt_result_value(result: AltTestResult) -> dict[str, object]:
    return {
        "winning_rate": result.winning_rate,
        "advantage_probability": result.advantage_probability,
        "n_annotators": result.n_annotators,
        "n_instances": result.n_instances,
        "verdicts": [
            {
                "annotator": v.annotator,
                "rho_f": v.rho_f,
                "rho_h": v.rho_h,
                "p_value": v.p_value,
                "rejected": v.rejected,
            }
            for v in result.verdicts
        ],
    }


def gate_alt_test(inputs: GateInputs, profile: ResolvedProfile) -> GateResult:
    """U13: Alternative Annotator Test vs the profile margin/pass fraction."""
    block = as_dict(inputs.alt_test_data)
    annotations = block.get("annotations")
    model_preds = block.get("model_preds")
    if not block or not isinstance(annotations, dict) or not isinstance(model_preds, dict):
        return GateResult(
            gate_id=GATE_ALT_TEST,
            status="not_evaluable",
            reason="no multiply-annotated calibration evidence recorded (inputs.alt_test_data)",
        )
    margin = profile.alt_test_margin
    passing = profile.alt_test_passing_fraction
    if margin is None:
        margin = _ALT_TEST_DEFAULT_MARGIN
    if passing is None:
        passing = _ALT_TEST_DEFAULT_PASSING_FRACTION
    result = alt_test(
        {str(k): {str(a): str(label) for a, label in v.items()} for k, v in annotations.items()},
        {str(k): str(v) for k, v in model_preds.items()},
        margin=margin,
        passing_fraction=passing,
        seed=_ALT_TEST_SEED,
    )
    threshold: dict[str, object] = {"margin": margin, "passing_fraction": passing}
    if result.status == "insufficient_data":
        return GateResult(
            gate_id=GATE_ALT_TEST,
            status="not_evaluable",
            reason=f"insufficient_data: {result.reason}",
            value=_alt_result_value(result),
            threshold=threshold,
        )
    return GateResult(
        gate_id=GATE_ALT_TEST,
        status="pass" if result.status == "pass" else "fail",
        reason=result.reason,
        value=_alt_result_value(result),
        threshold=threshold,
    )


def _ppi_value(result: PPIResult) -> dict[str, object]:
    return {
        "label": result.label,
        "raw_estimate": result.raw_estimate,
        "debiased_estimate": result.estimate if result.label == "corrected" else None,
        "lower": result.lower,
        "upper": result.upper,
        "n_population": result.n_population,
        "n_audit": result.n_audit,
        "effective_audit_n": result.effective_audit_n,
        "raw_is_validated": False,
    }


def _float_array(raw: object) -> np.ndarray:
    """Float ndarray view of a JSON list (raises ValueError on junk)."""
    return np.asarray(as_list(raw), dtype=float)


def gate_prediction_powered(inputs: GateInputs, _profile: ResolvedProfile) -> GateResult:
    """U14: raw + debiased prevalence from PPI; raw never passes as validated.

    The gate verifies the audit DESIGN exists (audited rows with inclusion
    probabilities, U17's shape): no audit sample -> ``not_evaluable`` with
    the ``uncorrected`` raw estimate reported honestly in the value.
    """
    block = as_dict(inputs.ppi)
    population = as_list(block.get("model_preds_all"))
    if not block or not population:
        return GateResult(
            gate_id=GATE_PREDICTION_POWERED,
            status="not_evaluable",
            reason="no prediction-powered inputs recorded (inputs.ppi empty)",
        )
    try:
        result = ppi_estimate(
            _float_array(population),
            _float_array(block.get("human_labels_sample")),
            _float_array(block.get("model_preds_audited")),
            _float_array(block.get("inclusion_probabilities")),
        )
    except (ValueError, TypeError) as exc:
        return GateResult(
            gate_id=GATE_PREDICTION_POWERED,
            status="not_evaluable",
            reason=f"invalid_output: PPI inputs unusable ({exc})",
            value={"raw_estimate": None, "raw_is_validated": False},
        )
    if result.status == "corrected":
        return GateResult(
            gate_id=GATE_PREDICTION_POWERED,
            status="pass",
            reason=(
                "audit design present: raw estimate "
                f"{result.raw_estimate:.4f} reported beside the debiased "
                f"{result.estimate:.4f} [{result.lower:.4f}, {result.upper:.4f}] "
                "(raw never labeled validated)"
            ),
            value=_ppi_value(result),
        )
    label = "insufficient_data" if result.status == "insufficient_data" else "uncorrected"
    return GateResult(
        gate_id=GATE_PREDICTION_POWERED,
        status="not_evaluable",
        reason=(
            f"{label}: no usable audit sample (n_audit={result.n_audit}); the raw "
            f"estimate {result.raw_estimate:.4f} stays labeled uncorrected"
        ),
        value=_ppi_value(result),
    )


__all__ = [
    "gate_alt_test",
    "gate_prediction_powered",
    "gate_prompt_stability",
]
