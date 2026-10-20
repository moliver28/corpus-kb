"""Per-LF analysis: the LFAnalysis analog plus the F6 correlation guard.

Numbers computed from the scored matrix, in the spirit of Snorkel's
LFAnalysis:

- ``coverage``  — fraction of items the LF labels.
- ``overlap``   — fraction of fired items where another LF also fired.
- ``conflicts`` — fraction of fired items where another LF voted a
  different class.
- ``polarity``  — the class distribution among the LF's fired items.

Pairwise LF agreement over co-fired items guards F6: the Dawid-Skene
model assumes conditional independence, and two near-identical LFs
overcount one signal. Pairs with agreement at or above
``AGREEMENT_WARNING_THRESHOLD`` become plain-English warnings — warn-only
this wave; pruning is a later decision. A pair needs at least
``MIN_COFIRES_FOR_AGREEMENT`` shared items before one agreement number
means anything. All outputs are deterministically ordered.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from corpus_kb.coding.aggregation import MISSING

from .lfs import LFSpec

__all__ = [
    "AGREEMENT_WARNING_THRESHOLD",
    "MIN_COFIRES_FOR_AGREEMENT",
    "AgreementPair",
    "LFStat",
    "agreement_warnings",
    "lf_stats",
    "pairwise_agreements",
]

AGREEMENT_WARNING_THRESHOLD = 0.9
MIN_COFIRES_FOR_AGREEMENT = 2


@dataclass(frozen=True)
class LFStat:
    """One LF's scorecard over the scored corpus."""

    name: str
    kind: str
    label: str
    coverage: float
    overlap: float
    conflicts: float
    fired: int
    polarity: dict[str, float]  # class name -> fraction of fired items

    def to_dict(self) -> dict[str, object]:
        """JSON-safe dict with polarity keys in sorted (stable) order."""
        return {
            "name": self.name,
            "kind": self.kind,
            "label": self.label,
            "coverage": self.coverage,
            "overlap": self.overlap,
            "conflicts": self.conflicts,
            "fired": self.fired,
            "polarity": dict(sorted(self.polarity.items())),
        }


@dataclass(frozen=True)
class AgreementPair:
    """One LF pair's agreement over the items both fired on."""

    name_a: str
    name_b: str
    agreement: float
    co_fires: int


def lf_stats(matrix: np.ndarray, spec: LFSpec) -> list[LFStat]:
    """Per-LF scorecards, in the spec's LF order (deterministic)."""
    n_items = matrix.shape[0]
    fired_all = matrix != MISSING
    others_firing = fired_all.sum(axis=1, keepdims=True) - fired_all  # (items, LFs)
    stats: list[LFStat] = []
    for column, lf in enumerate(spec.lfs):
        fired = fired_all[:, column]
        fired_count = int(fired.sum())
        if fired_count and n_items:
            overlap = float(np.mean(others_firing[fired, column] > 0))
            different = (matrix != matrix[:, [column]]) & fired_all
            conflicts = float(np.mean(different[fired].any(axis=1)))
        else:
            overlap = 0.0
            conflicts = 0.0
        stats.append(
            LFStat(
                name=lf.name,
                kind=lf.kind,
                label=lf.label,
                coverage=float(fired.mean()) if n_items else 0.0,
                overlap=overlap,
                conflicts=conflicts,
                fired=fired_count,
                polarity=_polarity(matrix, column, spec, fired_count),
            )
        )
    return stats


def _polarity(matrix: np.ndarray, column: int, spec: LFSpec, fired_count: int) -> dict[str, float]:
    """Class-name distribution among one LF's fired items (sorted keys)."""
    if fired_count == 0:
        return {}
    votes = matrix[matrix[:, column] != MISSING, column]
    counts = np.bincount(votes, minlength=len(spec.classes))
    return {
        spec.classes[index]: float(count) / fired_count
        for index, count in enumerate(counts)
        if count > 0
    }


def pairwise_agreements(matrix: np.ndarray, spec: LFSpec) -> list[AgreementPair]:
    """Every LF pair's agreement over co-fired items, sorted by names."""
    pairs: list[AgreementPair] = []
    fired_all = matrix != MISSING
    order = sorted(range(len(spec.lfs)), key=lambda i: spec.lfs[i].name)
    for a_pos, a in enumerate(order):
        for b in order[a_pos + 1 :]:
            both = fired_all[:, a] & fired_all[:, b]
            co_fires = int(both.sum())
            agreement = 0.0
            if co_fires:
                same = matrix[both, a] == matrix[both, b]
                agreement = float(same.mean())
            names = sorted((spec.lfs[a].name, spec.lfs[b].name))
            pairs.append(AgreementPair(names[0], names[1], agreement, co_fires))
    return sorted(pairs, key=lambda pair: (pair.name_a, pair.name_b))


def agreement_warnings(pairs: list[AgreementPair]) -> list[str]:
    """Plain-English F6 warnings for highly agreeing LF pairs (sorted)."""
    warnings: list[str] = []
    for pair in sorted(pairs, key=lambda pair: (pair.name_a, pair.name_b)):
        if pair.agreement < AGREEMENT_WARNING_THRESHOLD:
            continue
        if pair.co_fires < MIN_COFIRES_FOR_AGREEMENT:
            continue
        percent = round(pair.agreement * 100)
        warnings.append(
            f'LFs "{pair.name_a}" and "{pair.name_b}" agree on {percent} percent of '
            f"{pair.co_fires} shared items. Correlated LFs overcount evidence; "
            "keep one or make them differ."
        )
    return warnings
