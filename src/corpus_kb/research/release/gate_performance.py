"""Accuracy release gates (U11 held-out per-code F1, U15 stratum disparity)
plus the overall reliability-alpha gate.

Pure functions over ``(GateInputs, ResolvedProfile)``; evidence blocks are
assembled by ``gate_reads.py``.

U11 heldout_per_code_f1 — held-out per-code precision/recall/F1 (computed
from tp/fp/fn when the block carries counts only) with the per-stratum
breakdown reported in the value; the gate compares per-code F1 against
``profile.per_code_f1_floor``. Codes with no usable held-out counts are
listed as not-measured in the value and the reason — never silently dropped.

U15 stratum_disparity — max pairwise per-stratum F1 disparity per code,
restricted to strata at or above ``profile.min_stratum_size``; smaller strata
are excluded as not_evaluable (v6 U15) and counted in the value. A breach
fails in EVERY profile that carries ``max_stratum_disparity``: the
warn-vs-fail distinction of v6 U15 falls out of ``required_gates`` (only
high-assurance requires this gate), so the evidence bundle stays honest
while only the requiring profile blocks on it.
"""

from __future__ import annotations

from corpus_kb.research.release.gate_support import (
    as_dict,
    as_list,
    as_str,
    confusion_prf,
    stratum_confusion,
)
from corpus_kb.research.release.gate_types import (
    GATE_HELDOUT_F1,
    GATE_STRATUM_DISPARITY,
    GateInputs,
    GateResult,
)
from corpus_kb.research.release.profiles import ResolvedProfile


def _code_counts(raw: dict[str, object]) -> tuple[int, int, int, int] | None:
    """(tp, fp, fn, n) for one held-out code block, or None when unusable."""
    counts = stratum_confusion(raw)
    return counts


def gate_heldout_per_code_f1(inputs: GateInputs, profile: ResolvedProfile) -> GateResult:
    """U11: held-out per-code P/R/F1 vs ``profile.per_code_f1_floor``."""
    block = as_dict(inputs.heldout)
    codes = [as_dict(c) for c in as_list(block.get("codes")) if isinstance(c, dict)]
    if not block or not codes:
        return GateResult(
            gate_id=GATE_HELDOUT_F1,
            status="not_evaluable",
            reason="no held-out evaluation recorded (inputs.heldout empty)",
        )
    floor = profile.per_code_f1_floor
    per_code: list[dict[str, object]] = []
    failing: list[str] = []
    unmeasured: list[str] = []
    measured_f1s: list[float] = []
    for raw in codes:
        code_id = as_str(raw.get("code_id")) or "<unnamed>"
        counts = _code_counts(raw)
        if counts is None:
            unmeasured.append(code_id)
            continue
        tp, fp, fn, n = counts
        precision, recall, f1 = confusion_prf(tp, fp, fn)
        entry: dict[str, object] = {
            "code_id": code_id,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "n": n,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "strata": as_list(raw.get("strata")),
        }
        per_code.append(entry)
        if f1 is None:
            unmeasured.append(code_id)
        else:
            measured_f1s.append(f1)
            if floor is not None and f1 < floor:
                failing.append(code_id)
    value: dict[str, object] = {
        "per_code": per_code,
        "failing_codes": failing,
        "unmeasured_codes": unmeasured,
        "per_code_f1_floor": floor,
    }
    if not per_code:
        return GateResult(
            gate_id=GATE_HELDOUT_F1,
            status="not_evaluable",
            reason="insufficient_data: no code carries usable held-out confusion counts",
            value=value,
        )
    if floor is None:
        return GateResult(
            gate_id=GATE_HELDOUT_F1,
            status="pass",
            reason=(
                "not_enforced (no per_code_f1_floor in profile; recorded-only): "
                f"held-out F1 measured for {len(measured_f1s)} code(s)"
            ),
            value=value,
            threshold=None,
        )
    if failing:
        return GateResult(
            gate_id=GATE_HELDOUT_F1,
            status="fail",
            reason=f"{len(failing)} code(s) below the per-code F1 floor {floor}: {failing}",
            value=value,
            threshold=floor,
        )
    reason = f"all {len(per_code)} measured code(s) meet the per-code F1 floor {floor}"
    if unmeasured:
        reason += f"; unmeasured (no usable held-out counts): {unmeasured}"
    return GateResult(
        gate_id=GATE_HELDOUT_F1,
        status="pass",
        reason=reason,
        value=value,
        threshold=floor,
    )


def gate_stratum_disparity(inputs: GateInputs, profile: ResolvedProfile) -> GateResult:
    """U15: max pairwise per-stratum F1 disparity vs ``max_stratum_disparity``."""
    block = as_dict(inputs.heldout)
    codes = [as_dict(c) for c in as_list(block.get("codes")) if isinstance(c, dict)]
    if not block or not codes:
        return GateResult(
            gate_id=GATE_STRATUM_DISPARITY,
            status="not_evaluable",
            reason="no held-out evaluation recorded (inputs.heldout empty)",
        )
    limit = profile.max_stratum_disparity
    min_size = profile.min_stratum_size
    per_code: list[dict[str, object]] = []
    excluded_strata = 0
    worst: tuple[float, str] | None = None
    for raw in codes:
        code_id = as_str(raw.get("code_id")) or "<unnamed>"
        f1_by_stratum: dict[str, float] = {}
        for stratum_raw in as_list(raw.get("strata")):
            stratum = as_dict(stratum_raw)
            counts = stratum_confusion(stratum)
            if counts is None:
                continue
            tp, fp, fn, n = counts
            if n < min_size:
                excluded_strata += 1
                continue
            _precision, _recall, f1 = confusion_prf(tp, fp, fn)
            name = as_str(stratum.get("stratum")) or "<unnamed>"
            if f1 is not None:
                f1_by_stratum[name] = f1
        entry: dict[str, object] = {
            "code_id": code_id,
            "evaluated_strata": sorted(f1_by_stratum),
            "f1_by_stratum": f1_by_stratum,
            "max_pairwise_disparity": None,
        }
        if len(f1_by_stratum) >= 2:
            names = sorted(f1_by_stratum)
            disparities = [
                abs(f1_by_stratum[a] - f1_by_stratum[b])
                for i, a in enumerate(names)
                for b in names[i + 1 :]
            ]
            disparity = max(disparities)
            entry["max_pairwise_disparity"] = disparity
            if worst is None or disparity > worst[0]:
                worst = (disparity, code_id)
        per_code.append(entry)
    value: dict[str, object] = {
        "per_code": per_code,
        "excluded_small_strata": excluded_strata,
        "min_stratum_size": min_size,
        "max_stratum_disparity": limit,
    }
    if worst is None:
        return GateResult(
            gate_id=GATE_STRATUM_DISPARITY,
            status="not_evaluable",
            reason=(
                "insufficient_data: no code has >= 2 strata at or above the minimum "
                f"size {min_size} (small strata are not_evaluable, v6 U15)"
            ),
            value=value,
        )
    if limit is None:
        return GateResult(
            gate_id=GATE_STRATUM_DISPARITY,
            status="pass",
            reason=(
                "not_enforced (no max_stratum_disparity in profile; recorded-only): "
                f"max disparity {worst[0]:.3f} on {worst[1]}"
            ),
            value=value,
            threshold=None,
        )
    if worst[0] > limit:
        return GateResult(
            gate_id=GATE_STRATUM_DISPARITY,
            status="fail",
            reason=(
                f"max per-stratum F1 disparity {worst[0]:.3f} ({worst[1]}) exceeds "
                f"max_stratum_disparity {limit}"
            ),
            value=value,
            threshold=limit,
        )
    return GateResult(
        gate_id=GATE_STRATUM_DISPARITY,
        status="pass",
        reason=(
            f"max per-stratum F1 disparity {worst[0]:.3f} within max_stratum_disparity {limit}; "
            f"{excluded_strata} small stratum(a) excluded as not_evaluable"
        ),
        value=value,
        threshold=limit,
    )


__all__ = [
    "gate_heldout_per_code_f1",
    "gate_stratum_disparity",
]
