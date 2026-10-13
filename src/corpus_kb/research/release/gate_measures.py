"""Coverage-measure release gates (exhaustiveness plateau, U36 saturation).

Pure functions over ``(GateInputs, ResolvedProfile)``; both read their
evidence from ``inputs.metrics`` — the checkpoint metric snapshots the
deductive/inductive runs already produced (fetched by ``gate_reads.py``).

Exhaustiveness — the coverage curve must have PLATEAUED
(``research.exhaustiveness.plateau_detected`` at the module's
``PLATEAU_EPSILON``): a run still absorbing structure (negative slope on
R(tau_res) / new-codes-per-batch) is not releasable evidence of
completeness. The pooled R(tau_res) share rides in the value.

Saturation (U36) — the run_stop rule from ``coding.saturation`` (ISR-based:
new-code rate at or below 0.05 once >= 50 unique codes exist) plus the
cumulative saturation CURVE — ``render_saturation_curve`` over the
``saturation_curve`` points — when ``unit_code_sets`` are on file, so the
review packet carries the whole unique-to-total trajectory beside the final
scalar ISR (the packet used to surface only that scalar). No profile
field exists for these; the module's own documented conventions are the
thresholds and are reported in ``threshold``.
"""

from __future__ import annotations

from corpus_kb.coding.saturation import (
    isr,
    render_saturation_curve,
    run_stop,
    saturation_curve,
)
from corpus_kb.research.exhaustiveness import PLATEAU_EPSILON, plateau_detected
from corpus_kb.research.release.gate_support import as_dict, as_float, as_int, as_list, as_str
from corpus_kb.research.release.gate_types import (
    GATE_EXHAUSTIVENESS,
    GATE_SATURATION,
    GateInputs,
    GateResult,
)
from corpus_kb.research.release.profiles import ResolvedProfile

_SATURATION_STOP_THRESHOLD = 0.05
_SATURATION_MIN_SAMPLES = 50


def gate_exhaustiveness(inputs: GateInputs, _profile: ResolvedProfile) -> GateResult:
    """Coverage-curve plateau gate over the exhaustiveness metric snapshot."""
    block = as_dict(as_dict(inputs.metrics).get("exhaustiveness"))
    pooled = as_dict(block.get("pooled"))
    slope = as_float(block.get("coverage_slope"))
    if not block or slope is None:
        return GateResult(
            gate_id=GATE_EXHAUSTIVENESS,
            status="not_evaluable",
            reason=(
                "no exhaustiveness snapshot recorded (needs a coverage curve over "
                ">= 2 checkpoints to measure a slope)"
            ),
        )
    value = {
        "coverage_slope": slope,
        "pooled_R": as_float(pooled.get("R")),
        "pooled_tau_res": as_float(pooled.get("tau_res")),
        "pooled_n": as_int(pooled.get("n")),
    }
    if plateau_detected(slope):
        return GateResult(
            gate_id=GATE_EXHAUSTIVENESS,
            status="pass",
            reason=(
                f"coverage curve plateaued (|slope| {abs(slope):.4f} <= "
                f"{PLATEAU_EPSILON}); pooled R(tau_res)={value['pooled_R']}"
            ),
            value=value,
            threshold=PLATEAU_EPSILON,
        )
    return GateResult(
        gate_id=GATE_EXHAUSTIVENESS,
        status="fail",
        reason=(
            f"coverage curve still moving (slope {slope:.4f} beyond +/-{PLATEAU_EPSILON}): "
            "structure is still being absorbed"
        ),
        value=value,
        threshold=PLATEAU_EPSILON,
    )


def gate_saturation(inputs: GateInputs, _profile: ResolvedProfile) -> GateResult:
    """U36 saturation gate: run_stop rule + the cumulative curve as evidence."""
    block = as_dict(as_dict(inputs.metrics).get("saturation"))
    new_codes = as_int(block.get("new_codes"))
    base_unique = as_int(block.get("base_unique"))
    if not block or new_codes is None or base_unique is None:
        return GateResult(
            gate_id=GATE_SATURATION,
            status="not_evaluable",
            reason="no saturation snapshot recorded (inputs.metrics.saturation empty)",
        )
    unit_sets: list[list[str]] = []
    for entry in as_list(block.get("unit_code_sets")):
        codes = [c for c in (as_str(t) for t in as_list(entry)) if c is not None]
        unit_sets.append(codes)
    value: dict[str, object] = {
        "new_codes": new_codes,
        "base_unique": base_unique,
        "stop_threshold": _SATURATION_STOP_THRESHOLD,
        "min_samples": _SATURATION_MIN_SAMPLES,
    }
    if unit_sets:
        curve = saturation_curve(unit_sets)
        value["curve_final_isr"] = curve.ratio[-1] if curve.ratio else None
        value["n_units"] = len(unit_sets)
        value["isr"] = isr(
            len({c for unit in unit_sets for c in unit}), sum(len(unit) for unit in unit_sets)
        )
        # U36: the cumulative unique-to-total curve, RENDERED (not just the
        # final scalar above) — the review packet's gate evidence carries the
        # whole trajectory so an approver can see WHERE saturation bent.
        value["curve"] = render_saturation_curve(curve)
    stopped = run_stop(
        new_codes,
        base_unique,
        threshold=_SATURATION_STOP_THRESHOLD,
        min_samples=_SATURATION_MIN_SAMPLES,
    )
    if stopped:
        return GateResult(
            gate_id=GATE_SATURATION,
            status="pass",
            reason=(
                f"saturation reached: {new_codes} new code(s) on {base_unique} unique "
                f"(rate <= {_SATURATION_STOP_THRESHOLD}, min samples "
                f"{_SATURATION_MIN_SAMPLES})"
            ),
            value=value,
            threshold={
                "threshold": _SATURATION_STOP_THRESHOLD,
                "min_samples": _SATURATION_MIN_SAMPLES,
            },
        )
    return GateResult(
        gate_id=GATE_SATURATION,
        status="fail",
        reason=(
            f"saturation not reached: {new_codes} new code(s) on {base_unique} unique "
            f"(run_stop rule at threshold {_SATURATION_STOP_THRESHOLD} / min "
            f"{_SATURATION_MIN_SAMPLES} not met)"
        ),
        value=value,
        threshold={"threshold": _SATURATION_STOP_THRESHOLD, "min_samples": _SATURATION_MIN_SAMPLES},
    )


__all__ = [
    "gate_exhaustiveness",
    "gate_saturation",
]
