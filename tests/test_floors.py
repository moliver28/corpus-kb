from __future__ import annotations

from corpus_kb.coding.floors import bounded_floor


def test_bounded_floor_is_max_of_recall_and_precision() -> None:
    # positive 2nd pctile 0.55, negative 94th pctile 0.62 -> bounded to 0.62
    assert bounded_floor(pos_pctile=0.55, neg_pctile=0.62) == 0.62
    assert bounded_floor(pos_pctile=0.71, neg_pctile=0.40) == 0.71
