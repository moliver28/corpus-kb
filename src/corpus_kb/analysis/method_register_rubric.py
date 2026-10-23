"""The selector rubric for the analysis method register (the ONE rubric).

``rank_methods`` is the single selector implementation. The wave-2
handler and the UI both call it; nobody writes a second rubric. Fixed
order: output family match, input coverage, complexity tier ascending,
calibration effort ascending, selection priority, then id. Same input,
same output, always.
"""

from __future__ import annotations

from corpus_kb.analysis.method_register import (
    CALIBRATION_EFFORTS,
    COMPLEXITY_TIERS,
    OUTPUT_FAMILIES,
    SELECTION_PRIORITIES,
    MethodRecommendation,
    MethodRecord,
    MethodRegister,
)

_TIER_ORDER = {name: index for index, name in enumerate(COMPLEXITY_TIERS)}
_CALIBRATION_ORDER = {name: index for index, name in enumerate(CALIBRATION_EFFORTS)}
_PRIORITY_ORDER = {name: index for index, name in enumerate(SELECTION_PRIORITIES)}

_ScoreKey = tuple[bool, bool, int, int, int, str]


def rank_methods(
    register: MethodRegister,
    output_family: str | None = None,
    inputs_available: list[str] | None = None,
    max_complexity_tier: str | None = None,
) -> list[MethodRecommendation]:
    """Rank register rows for a stated task. Deterministic; no LLM.

    ``None`` constraints are inactive: every row passes them. A tier cap
    drops rows above it. The sort order is fixed: family match, input
    coverage, complexity tier, calibration effort, selection priority, id.
    Uncovered rows still rank, with ``missing_inputs`` filled and sorted.
    """
    if output_family is not None and output_family not in OUTPUT_FAMILIES:
        raise ValueError(
            f"output_family: {output_family!r} is not supported. "
            f"Use one of: {', '.join(OUTPUT_FAMILIES)}."
        )
    if max_complexity_tier is not None and max_complexity_tier not in COMPLEXITY_TIERS:
        raise ValueError(
            f"max_complexity_tier: {max_complexity_tier!r} is not supported. "
            f"Use one of: {', '.join(COMPLEXITY_TIERS)}."
        )
    available = set(inputs_available) if inputs_available is not None else None
    cap = _TIER_ORDER[max_complexity_tier] if max_complexity_tier is not None else None

    scored: list[tuple[_ScoreKey, MethodRecord, list[str]]] = []
    for record in register.methods:
        if cap is not None and _TIER_ORDER[record.complexity_tier] > cap:
            continue
        missing = sorted(set(record.inputs_required) - available) if available is not None else []
        key: _ScoreKey = (
            output_family is not None and record.output_family != output_family,
            available is not None and bool(missing),
            _TIER_ORDER[record.complexity_tier],
            _CALIBRATION_ORDER[record.calibration_effort],
            _PRIORITY_ORDER[record.selection_priority],
            record.id,
        )
        scored.append((key, record, missing))
    scored.sort(key=lambda entry: entry[0])

    ranked: list[MethodRecommendation] = []
    previous: _ScoreKey | None = None
    for index, (key, record, missing) in enumerate(scored):
        reason = (
            _lead_reason(key, output_family, available)
            if previous is None
            else _step_reason(key, previous, missing)
        )
        ranked.append(
            MethodRecommendation.model_validate(
                {
                    **record.model_dump(),
                    "rank": index + 1,
                    "rank_reason": reason,
                    "missing_inputs": missing,
                }
            )
        )
        previous = key
    return ranked


def _lead_reason(key: _ScoreKey, output_family: str | None, available: set[str] | None) -> str:
    """Why the top row leads: the first active constraint it satisfies."""
    if output_family is not None:
        if key[0]:
            return "No method matches the requested output family."
        return "Output family matches the request."
    if available is not None:
        if key[1]:
            return "No method has every required input."
        return "All required inputs are available."
    return "Best row under the rubric order."


def _step_reason(key: _ScoreKey, previous: _ScoreKey, missing: list[str]) -> str:
    """The first axis at which this row falls below the row above it."""
    if key[0] != previous[0]:
        return "Output family does not match the request."
    if key[1] != previous[1]:
        return f"Required inputs are missing: {', '.join(missing)}."
    if key[2] != previous[2]:
        return "Complexity tier is higher than the record above."
    if key[3] != previous[3]:
        return "Calibration effort is higher than the record above."
    if key[4] != previous[4]:
        return "Selection priority ranks it lower."
    return "All axes are equal. The id order decides."
