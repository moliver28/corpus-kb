"""Data-split release gates (U8 boundary agreement, U9 partition independence).

Pure functions over ``(GateInputs, ResolvedProfile)``; evidence blocks are
assembled by ``gate_reads.py``.

U8 boundary_agreement — SEGMENT/boundary agreement between coders, scored
SEPARATELY from label agreement (labels belong to ``reliability_alpha``).
Matching reuses the quote-verification normalizer
(``coding.quote_verification.normalize_quote``): two segments match when
their (start, end) spans are identical OR their normalized quotes are equal.
Per coder pair: precision/recall of the match, pooled F1; the gate value is
the mean pairwise F1 against the CONVENTION floor below (v6 U8 fixes no
number; profiles carry no field — recorded as a convention, not a standard).

U9 partition_independence — discovery/calibration/test/audit partitions are
disjoint by unit and, when the identifiers exist, by document and by
participant/source. Overlap is a fact and fails regardless of the profile's
``partition_rule``; with no violations and ``partition_rule: none`` the gate
passes with an explicit not-enforced reason.
"""

from __future__ import annotations

from corpus_kb.coding.quote_verification import normalize_quote
from corpus_kb.research.release.gate_support import as_dict, as_int, as_list, as_str
from corpus_kb.research.release.gate_types import (
    GATE_BOUNDARY_AGREEMENT,
    GATE_PARTITION_INDEPENDENCE,
    GateInputs,
    GateResult,
)
from corpus_kb.research.release.profiles import ResolvedProfile

# CONVENTION (documented, not a standard): v6 U8 fixes no numeric floor and
# ResolvedProfile carries no boundary field, so the gate compares against 0.80
# (the "reliable" agreement convention profiles already use for alpha).
BOUNDARY_AGREEMENT_FLOOR = 0.80

PARTITIONS = ("discovery", "calibration", "test", "audit")


def _segment_key(raw: dict[str, object]) -> tuple[object, ...]:
    """Match key for one segment: span + normalized quote (quote optional)."""
    start = as_int(raw.get("start"))
    end = as_int(raw.get("end"))
    quote = normalize_quote(as_str(raw.get("quote")) or "")
    return (start, end, quote or None)


def _segments_match(a: tuple[object, ...], b: tuple[object, ...]) -> bool:
    """Spans identical, or normalized quotes equal when both present."""
    if a[0] is not None and a[1] is not None and (a[0], a[1]) == (b[0], b[1]):
        return True
    return a[2] is not None and b[2] is not None and a[2] == b[2]


def _pair_f1(
    segments_a: list[tuple[object, ...]], segments_b: list[tuple[object, ...]]
) -> tuple[float | None, int, int, int]:
    """Boundary F1 for one coder pair + (matched, n_a, n_b) counts."""
    n_a, n_b = len(segments_a), len(segments_b)
    if n_a == 0 or n_b == 0:
        return None, 0, n_a, n_b
    matched_a = sum(
        1 for seg in segments_a if any(_segments_match(seg, other) for other in segments_b)
    )
    matched_b = sum(
        1 for seg in segments_b if any(_segments_match(seg, other) for other in segments_a)
    )
    precision = matched_a / n_a
    recall = matched_b / n_b
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else None
    return f1, matched_a, n_a, n_b


def gate_boundary_agreement(inputs: GateInputs, _profile: ResolvedProfile) -> GateResult:
    """U8: segment/boundary agreement between coders (label agreement excluded)."""
    block = as_dict(inputs.boundary)
    documents = as_list(block.get("documents"))
    if not block or not documents:
        return GateResult(
            gate_id=GATE_BOUNDARY_AGREEMENT,
            status="not_evaluable",
            reason="no per-coder segmentation evidence recorded (inputs.boundary empty)",
        )
    pair_results: list[dict[str, object]] = []
    f1_values: list[float] = []
    tot_matched = tot_a = tot_b = 0
    for entry in documents:
        doc = as_dict(entry)
        by_coder = as_dict(doc.get("segments_by_coder"))
        coders = sorted(c for c in by_coder if as_str(c))
        for i, coder_a in enumerate(coders):
            for coder_b in coders[i + 1 :]:
                segs_a = [
                    _segment_key(s) for s in as_list(by_coder[coder_a]) if isinstance(s, dict)
                ]
                segs_b = [
                    _segment_key(s) for s in as_list(by_coder[coder_b]) if isinstance(s, dict)
                ]
                f1, matched, n_a, n_b = _pair_f1(segs_a, segs_b)
                tot_matched += matched
                tot_a += n_a
                tot_b += n_b
                if f1 is not None:
                    f1_values.append(f1)
                    pair_results.append(
                        {
                            "document_id": as_str(doc.get("document_id")),
                            "coder_a": coder_a,
                            "coder_b": coder_b,
                            "boundary_f1": f1,
                            "n_a": n_a,
                            "n_b": n_b,
                        }
                    )
    if not pair_results:
        return GateResult(
            gate_id=GATE_BOUNDARY_AGREEMENT,
            status="not_evaluable",
            reason=(
                "insufficient_data: fewer than two coders share a segmented document "
                "(boundary agreement needs overlapping segmentations)"
            ),
        )
    mean_f1 = sum(f1_values) / len(f1_values)
    passed = mean_f1 >= BOUNDARY_AGREEMENT_FLOOR
    return GateResult(
        gate_id=GATE_BOUNDARY_AGREEMENT,
        status="pass" if passed else "fail",
        reason=(
            f"mean boundary F1 {mean_f1:.3f} over {len(pair_results)} coder pair(s) vs "
            f"convention floor {BOUNDARY_AGREEMENT_FLOOR}; label agreement is scored "
            "separately by reliability_alpha"
        ),
        value={
            "mean_boundary_f1": mean_f1,
            "pairs": pair_results,
            "pooled": {"matched": tot_matched, "n_a": tot_a, "n_b": tot_b},
            "label_agreement": "out of scope here (see reliability_alpha)",
        },
        threshold=BOUNDARY_AGREEMENT_FLOOR,
    )


def _ids_by_partition(assignments: list[dict[str, object]], key: str) -> dict[str, set[str]]:
    """partition -> identifier sets for one column (unit_id/document_id/...)."""
    buckets: dict[str, set[str]] = {}
    for raw in assignments:
        partition = as_str(raw.get("partition"))
        identifier = as_str(raw.get(key))
        if partition is None or identifier is None:
            continue
        buckets.setdefault(partition, set()).add(identifier)
    return buckets


def _overlaps(buckets: dict[str, set[str]]) -> list[str]:
    """Identifiers appearing in more than one partition (deterministic order)."""
    seen: dict[str, set[str]] = {}
    for partition, ids in buckets.items():
        for identifier in ids:
            seen.setdefault(identifier, set()).add(partition)
    return sorted(
        f"{ident}:{'+'.join(sorted(parts))}" for ident, parts in seen.items() if len(parts) > 1
    )


def gate_partition_independence(inputs: GateInputs, profile: ResolvedProfile) -> GateResult:
    """U9: discovery/calibration/test/audit disjoint by unit/doc/participant."""
    block = as_dict(inputs.partitions)
    assignments = [as_dict(a) for a in as_list(block.get("assignments")) if isinstance(a, dict)]
    if not block or not assignments:
        return GateResult(
            gate_id=GATE_PARTITION_INDEPENDENCE,
            status="not_evaluable",
            reason="no partition assignments recorded (inputs.partitions empty)",
        )
    rule = as_str(block.get("rule")) or profile.partition_rule
    by_unit = _ids_by_partition(assignments, "unit_id")
    unknown = sorted(set(by_unit) - set(PARTITIONS))
    value: dict[str, object] = {
        "rule": rule,
        "n_assignments": len(assignments),
        "counts": {p: len(by_unit.get(p, set())) for p in PARTITIONS},
    }
    if unknown:
        return GateResult(
            gate_id=GATE_PARTITION_INDEPENDENCE,
            status="fail",
            reason=(
                f"invalid_output: unknown partition label(s) {unknown} "
                f"(allowed: {list(PARTITIONS)})"
            ),
            value={**value, "unknown_partitions": unknown},
        )
    overlaps: dict[str, list[str]] = {"unit": _overlaps(by_unit)}
    for key, kind in (("document_id", "document"), ("participant_id", "participant")):
        found = _overlaps(_ids_by_partition(assignments, key))
        if found:
            overlaps[kind] = found
    value["overlaps"] = overlaps
    total = sum(len(v) for v in overlaps.values())
    if total:
        detail = ", ".join(f"{kind}[{len(ids)}]" for kind, ids in overlaps.items())
        return GateResult(
            gate_id=GATE_PARTITION_INDEPENDENCE,
            status="fail",
            reason=f"{total} identifier(s) shared across partitions (leakage): {detail}",
            value=value,
        )
    reason = "partitions disjoint by unit"
    if not any(a.get("document_id") or a.get("participant_id") for a in assignments):
        reason += "; no document/participant identifiers recorded (unit-level check only)"
    if rule != "disjoint":
        reason += f"; not_enforced: partition_rule={rule} (disjointness still verified)"
    return GateResult(
        gate_id=GATE_PARTITION_INDEPENDENCE,
        status="pass",
        reason=reason,
        value=value,
        threshold={"rule": rule, "disjoint_over": ["unit_id", "document_id", "participant_id"]},
    )


__all__ = [
    "BOUNDARY_AGREEMENT_FLOOR",
    "PARTITIONS",
    "gate_boundary_agreement",
    "gate_partition_independence",
]
