from __future__ import annotations

from corpus_kb.coding.keyness import g2, log_odds_dirichlet


def test_g2_signs_and_monroe() -> None:
    ll, lr = g2(a=40, b=2, target_total=500, ref_total=50000)
    assert ll > 0 and lr > 0  # over-represented in target
    z = log_odds_dirichlet(a=40, b=2, target_total=500, ref_total=50000, alpha=0.01)
    assert z > 1.96  # distinctive at ~95%
