"""Per-LF analysis stats and the F6 pairwise-agreement guard."""

from __future__ import annotations

import numpy as np

from corpus_kb.coding.aggregation import MISSING
from corpus_kb.labeling.analysis import (
    AGREEMENT_WARNING_THRESHOLD,
    agreement_warnings,
    lf_stats,
    pairwise_agreements,
)
from corpus_kb.labeling.lfs import LFSpec
from corpus_kb.labeling.protocol import LabelingFunction

# classes: a=0, b=1. Matrix rows are items, columns are LFs.
#         lf1   lf2   lf3
# item0    0     0     1     lf1/lf2 agree, lf3 conflicts
# item1    0    MISSING 0     lf2 abstains
# item2    MISSING 1   MISSING
# item3    1     1     MISSING
_MATRIX = np.array(
    [
        [0, 0, 1],
        [0, MISSING, 0],
        [MISSING, 1, MISSING],
        [1, 1, MISSING],
    ]
)


def _spec(lfs: int, names: list[str] | None = None) -> LFSpec:
    kinds = ["regex", "lexicon", "metadata"]
    names = names or [f"lf{i + 1}" for i in range(lfs)]
    return LFSpec(
        classes=("a", "b"),
        lfs=tuple(
            LabelingFunction(name=names[i], kind=kinds[i % len(kinds)], label="a", label_index=0)
            for i in range(lfs)
        ),
    )


def test_coverage_overlap_conflicts_and_polarity() -> None:
    stats = lf_stats(_MATRIX, _spec(3))
    by_name = {stat.name: stat for stat in stats}

    lf1 = by_name["lf1"]
    assert lf1.fired == 3
    assert lf1.coverage == 0.75
    # Another LF fired on every item lf1 fired on.
    assert lf1.overlap == 1.0
    # lf3 fired with a different class on 1 of lf1's 3 fired items.
    assert lf1.conflicts == 1 / 3
    # lf1 voted a twice, b once.
    assert lf1.polarity == {"a": 2 / 3, "b": 1 / 3}

    lf2 = by_name["lf2"]
    assert lf2.fired == 3
    assert lf2.overlap == 2 / 3  # alone on item2
    assert lf2.conflicts == 1 / 3  # item0 meets lf3's differing vote
    assert lf2.polarity == {"a": 1 / 3, "b": 2 / 3}
    lf3 = by_name["lf3"]
    assert lf3.fired == 2
    assert lf3.overlap == 1.0  # both fired items share a fire with lf1
    assert lf3.conflicts == 0.5  # ...and item0's votes differ
    assert lf3.polarity == {"a": 0.5, "b": 0.5}


def test_stats_order_follows_the_spec_and_is_deterministic() -> None:
    first = lf_stats(_MATRIX, _spec(3))
    second = lf_stats(_MATRIX, _spec(3))
    assert [stat.name for stat in first] == ["lf1", "lf2", "lf3"]
    assert [stat.to_dict() for stat in first] == [stat.to_dict() for stat in second]
    assert list(first[0].to_dict()) == [
        "name",
        "kind",
        "label",
        "coverage",
        "overlap",
        "conflicts",
        "fired",
        "polarity",
    ]


def test_empty_corpus_answers_zeroed_stats() -> None:
    stats = lf_stats(np.empty((0, 2), dtype=int), _spec(2))
    assert all(stat.coverage == 0.0 for stat in stats)
    assert all(stat.polarity == {} for stat in stats)


def test_pairwise_agreements_sorted_and_correct() -> None:
    pairs = pairwise_agreements(_MATRIX, _spec(3, ["zeta", "alpha", "mid"]))
    assert [(p.name_a, p.name_b) for p in pairs] == [
        ("alpha", "mid"),
        ("alpha", "zeta"),
        ("mid", "zeta"),
    ]
    by_pair = {(p.name_a, p.name_b): p for p in pairs}
    # alpha(lf2) and mid(lf3) co-fire on item0 only, and disagree there.
    assert by_pair[("alpha", "mid")].co_fires == 1
    assert by_pair[("alpha", "mid")].agreement == 0.0
    # alpha and zeta(lf1) co-fire on items 0 and 3, same label both times.
    assert by_pair[("alpha", "zeta")].co_fires == 2
    assert by_pair[("alpha", "zeta")].agreement == 1.0
    # mid and zeta co-fire on items 0 and 1: disagree, then agree.
    assert by_pair[("mid", "zeta")].co_fires == 2
    assert by_pair[("mid", "zeta")].agreement == 0.5


def test_warnings_fire_only_above_threshold_and_floor() -> None:
    pairs = pairwise_agreements(_MATRIX, _spec(3, ["zeta", "alpha", "mid"]))
    # alpha/zeta agree 1.0 on 2 co-fires -> warned; the 1-co-fire pair is not.
    warnings = agreement_warnings(pairs)
    assert len(warnings) == 1
    assert '"alpha" and "zeta"' in warnings[0]
    assert "100 percent" in warnings[0]

    # A near-miss below the threshold stays silent.
    below = pairwise_agreements(np.array([[0, 0], [0, 1], [0, 0], [0, 0]]), _spec(2, ["a1", "b2"]))
    assert below[0].agreement == 3 / 4
    assert below[0].agreement < AGREEMENT_WARNING_THRESHOLD
    assert agreement_warnings(below) == []

    # A single co-fire carries no correlation evidence even at 1.0.
    single = pairwise_agreements(np.array([[0, MISSING], [0, 0]]), _spec(2, ["a1", "b2"]))
    assert single[0].agreement == 1.0
    assert single[0].co_fires == 1
    assert agreement_warnings(single) == []
