"""PPI validation of LF-derived labels against a small gold dev set (F6).

``coding.ppi.ppi_estimate`` corrects a full-population estimate with a
small audited sample. Here the "model" is the weak-supervision
aggregation: ``f = 1[aggregated label == class]`` over every built chunk,
``y = 1[gold label == class]`` over the gold dev set. The corrected
estimate is the debiased prevalence of the class; the raw gold-set
agreement rides beside it.

ASSUMPTION (documented, binding): the gold dev set is a random draw from
the corpus, so the inclusion probabilities are uniform (n_gold / N). A
hand-picked dev set breaks that assumption and the corrected interval
inherits the bias — report the raw agreement alongside it, never the
corrected number alone. With no gold overlap the function raises, with
the fix in the message; ``ppi_estimate`` itself answers the honest
``uncorrected`` / ``insufficient_data`` statuses.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from corpus_kb.coding.ppi import ppi_estimate

__all__ = ["GoldValidation", "ppi_validate"]


@dataclass(frozen=True)
class GoldValidation:
    """One class's validation outcome against the gold dev set."""

    class_name: str
    status: str  # corrected | uncorrected | insufficient_data (ppi statuses)
    label: str  # exactly "uncorrected" when no audit sample
    raw_accuracy: float  # agreement with gold over the matched items
    estimate: float  # debiased (or raw) prevalence estimate
    lower: float | None
    upper: float | None
    n_population: int
    n_gold: int
    effective_audit_n: float | None

    def to_dict(self) -> dict[str, object]:
        """JSON-safe dict (UUID-free, fixed key order)."""
        return {
            "class_name": self.class_name,
            "status": self.status,
            "label": self.label,
            "raw_accuracy": self.raw_accuracy,
            "estimate": self.estimate,
            "lower": self.lower,
            "upper": self.upper,
            "n_population": self.n_population,
            "n_gold": self.n_gold,
            "effective_audit_n": self.effective_audit_n,
        }


def ppi_validate(
    model_labels: np.ndarray,
    chunk_ids: list[str],
    gold: Mapping[str, int],
    class_index: int,
    class_name: str,
    *,
    confidence_level: float = 0.95,
) -> GoldValidation:
    """Validate one class's LF-derived labels against the gold dev set.

    Args:
        model_labels: (n_items,) aggregated class indices (the matrix
            world of ``coding.aggregation``).
        chunk_ids: The chunk ids aligned row-for-row with the labels.
        gold: ``{chunk_id: class index}`` for the dev set (values are
            non-negative ints; anything else raises). Ids that do not
            appear in ``chunk_ids`` are ignored.
        class_index: The class this validation scores.
        class_name: The same class's name (recorded in the result).
        confidence_level: Two-sided interval coverage for the CI.

    Returns:
        GoldValidation. Raises ``ValueError`` when no gold id matches a
        built chunk, or when a gold label is not a valid class index.
    """
    labels = np.asarray(model_labels, dtype=int).ravel()
    if labels.shape[0] != len(chunk_ids):
        raise ValueError("model_labels and chunk_ids must align row-for-row.")
    if chunk_ids and len(set(chunk_ids)) != len(chunk_ids):
        raise ValueError("chunk_ids must be unique (one row per chunk).")
    for chunk_id, label in gold.items():
        if isinstance(label, bool) or not isinstance(label, int) or label < 0:
            raise ValueError(
                f"gold[{chunk_id!r}]: label must be a class index (a non-negative int)."
            )

    positions = [position for position, cid in enumerate(chunk_ids) if cid in gold]
    if not positions:
        raise ValueError(
            "no gold chunk ids match the built chunks. Pass gold ids from the "
            "same tenant build (labeling_list shows the built chunk ids)."
        )
    matched_gold = [gold[chunk_ids[position]] for position in positions]

    f_all = (labels == class_index).astype(float)
    y = np.array([1.0 if label == class_index else 0.0 for label in matched_gold])
    f_aud = f_all[positions]
    inclusion = np.full(len(positions), len(positions) / len(chunk_ids))
    result = ppi_estimate(f_all, y, f_aud, inclusion, confidence_level=confidence_level)
    model_matched = labels[positions]
    raw_accuracy = float(np.mean(model_matched == np.array(matched_gold)))
    return GoldValidation(
        class_name=class_name,
        status=result.status,
        label=result.label,
        raw_accuracy=raw_accuracy,
        estimate=result.estimate,
        lower=result.lower,
        upper=result.upper,
        n_population=result.n_population,
        n_gold=result.n_audit,
        effective_audit_n=result.effective_audit_n,
    )
