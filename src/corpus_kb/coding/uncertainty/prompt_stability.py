"""Prompt-stability computation (U12; v6 §7 U12).

K stored label-sets over the SAME calibration units -- one per prompt
variant -- are compared with the vendored krippendorff alpha in
``coding/reliability.py`` (nominal, pooled overall alpha). Variants are
hashed/versioned UPSTREAM in the prompt bundle; this module only measures.
Fewer than 2 variant sets -> ``not_evaluable`` (never a pass).

Identical label-sets are the degenerate zero-disagreement case the alpha
formula cannot divide through: reported as alpha 1.0 with the reason
stated, not fabricated through the matrix path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from corpus_kb.coding.reliability import computeReliability

NOT_EVALUABLE = "not_evaluable"
PASS = "pass"
FAIL = "fail"


@dataclass(frozen=True)
class PromptStabilityResult:
    """Gate-ready prompt-stability outcome (U12)."""

    status: str  # pass | fail | not_evaluable
    alpha: float | None
    threshold: float
    n_variants: int
    n_units: int
    reason: str


def _sheets_for(label_sets: Sequence[Mapping[str, str]]) -> list[dict[str, object]]:
    """Convert per-variant {unit_id -> label} maps to coder sheets.

    Each variant becomes one sheet with a binary 0/1 assignment per
    (segment, code) pair over the union label universe -- the encoding
    ``computeReliability`` consumes (its per-code alpha is the binary
    application agreement; the pooled overall alpha is the gate value).
    """
    universe = sorted({label for labels in label_sets for label in labels.values()})
    sheets: list[dict[str, object]] = []
    for labels in label_sets:
        entries: list[dict[str, object]] = []
        # Only units this variant actually labeled get an entry: a variant
        # missing a unit contributes MISSING DATA for it (no rating), which
        # is the alignment-recovery semantics computeReliability implements.
        for unit_id in sorted(labels):
            assignments = [{"code_id": code} for code in universe if labels.get(unit_id) == code]
            entries.append({"segment_id": unit_id, "assignments": assignments})
        sheets.append({"entries": entries})
    return sheets


def prompt_stability(
    variant_label_sets: Sequence[Mapping[str, str]],
    *,
    alpha_threshold: float = 0.667,
    min_variants: int = 2,
) -> PromptStabilityResult:
    """Agreement across K prompt variants over the same units (U12).

    Args:
        variant_label_sets: One {unit_id -> label} mapping per stored prompt
            variant, over the same calibration units.
        alpha_threshold: Gate threshold (profile-level; 0.667 = tentative
            alpha convention, 0.80 = reliable).
        min_variants: Below this the gate is honestly ``not_evaluable``.

    Returns:
        PromptStabilityResult with the pooled overall alpha vs threshold.
    """
    if len(variant_label_sets) < min_variants:
        return PromptStabilityResult(
            status=NOT_EVALUABLE,
            alpha=None,
            threshold=alpha_threshold,
            n_variants=len(variant_label_sets),
            n_units=0,
            reason=(
                f"prompt stability needs >= {min_variants} stored variant "
                f"label-sets; got {len(variant_label_sets)}"
            ),
        )
    units = {u for labels in variant_label_sets for u in labels}
    if not units:
        return PromptStabilityResult(
            status=NOT_EVALUABLE,
            alpha=None,
            threshold=alpha_threshold,
            n_variants=len(variant_label_sets),
            n_units=0,
            reason="no calibration units carry variant labels",
        )
    if all(labels == variant_label_sets[0] for labels in variant_label_sets):
        return PromptStabilityResult(
            status=PASS,
            alpha=1.0,
            threshold=alpha_threshold,
            n_variants=len(variant_label_sets),
            n_units=len(units),
            reason="all variants produced identical labels (zero disagreement)",
        )
    reliability = computeReliability(_sheets_for(variant_label_sets))
    overall = reliability["overall"]
    alpha = overall["alpha"]
    if alpha is None:
        return PromptStabilityResult(
            status=NOT_EVALUABLE,
            alpha=None,
            threshold=alpha_threshold,
            n_variants=len(variant_label_sets),
            n_units=len(units),
            reason="alpha undefined (no variance or too little data)",
        )
    passed = float(alpha) >= alpha_threshold
    return PromptStabilityResult(
        status=PASS if passed else FAIL,
        alpha=float(alpha),
        threshold=alpha_threshold,
        n_variants=len(variant_label_sets),
        n_units=len(units),
        reason=f"pooled alpha {float(alpha):.3f} vs threshold {alpha_threshold}",
    )
