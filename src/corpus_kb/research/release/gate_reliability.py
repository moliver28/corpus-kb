"""Reliability release gate (pooled Krippendorff alpha).

Pure function over ``(GateInputs, ResolvedProfile)``; the reliability report
is a ``coding.reliability.computeReliability`` result fetched by
``gate_reads.py``.

reliability_alpha — pooled overall alpha vs ``profile.alpha_tentative`` (the
documented tentative convention is the pass floor); the reliable-level
attainment and per-code governance bands ride in the value so a halt-band
code is visible without re-deriving the dual-coefficient verdict here.
"""

from __future__ import annotations

from corpus_kb.research.irr_governance import classify_band
from corpus_kb.research.release.gate_support import as_dict, as_float, as_str
from corpus_kb.research.release.gate_types import (
    GATE_RELIABILITY_ALPHA,
    GateInputs,
    GateResult,
)
from corpus_kb.research.release.profiles import ResolvedProfile


def gate_reliability_alpha(inputs: GateInputs, profile: ResolvedProfile) -> GateResult:
    """Pooled overall alpha vs ``profile.alpha_tentative`` (pass floor).

    The reliable convention (``profile.alpha_reliable``) is NOT the gate:
    reaching it is recorded in the value. Per-code governance bands
    (irr_governance.classify_band) ride in the value so a halt-band code is
    visible without re-deriving the dual-coefficient verdict here.
    """
    report = as_dict(inputs.reliability)
    overall = as_dict(report.get("overall"))
    alpha = as_float(overall.get("alpha"))
    if alpha is None:
        return GateResult(
            gate_id=GATE_RELIABILITY_ALPHA,
            status="not_evaluable",
            reason="no reliability report recorded (inputs.reliability overall.alpha missing)",
        )
    per_code_raw = as_dict(report.get("per_code"))
    per_code = {
        as_str(code_id) or "<unnamed>": {
            "alpha": as_float(as_dict(block).get("alpha")),
            "band": classify_band(as_float(as_dict(block).get("alpha"))),
        }
        for code_id, block in per_code_raw.items()
        if isinstance(block, dict)
    }
    halt_band = sorted(c for c, b in per_code.items() if b["band"] == "halt")
    meets_reliable = alpha >= profile.alpha_reliable
    value: dict[str, object] = {
        "alpha": alpha,
        "alpha_tentative": profile.alpha_tentative,
        "alpha_reliable": profile.alpha_reliable,
        "meets_reliable_convention": meets_reliable,
        "per_code": per_code,
        "halt_band_codes": halt_band,
    }
    if alpha < profile.alpha_tentative:
        return GateResult(
            gate_id=GATE_RELIABILITY_ALPHA,
            status="fail",
            reason=f"pooled alpha {alpha:.3f} below the tentative convention "
            f"{profile.alpha_tentative}",
            value=value,
            threshold=profile.alpha_tentative,
        )
    level = "reliable" if meets_reliable else "tentative"
    reason = f"pooled alpha {alpha:.3f} meets the {level} convention"
    if halt_band:
        reason += f"; warn: {len(halt_band)} per-code alpha(s) in the halt band: {halt_band}"
    return GateResult(
        gate_id=GATE_RELIABILITY_ALPHA,
        status="pass",
        reason=reason,
        value=value,
        threshold=profile.alpha_tentative,
    )


__all__ = ["gate_reliability_alpha"]
