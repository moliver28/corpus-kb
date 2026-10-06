from __future__ import annotations

import math

from corpus_kb.coding.uncertainty.tier0_similarity import (
    kmedoids_prototypes,
    max_prototype_sim,
)
from corpus_kb.coding.uncertainty.tier1_logprob import logprob_entropy
from corpus_kb.coding.uncertainty.tier2_hedging import hedging_score


def test_tiers() -> None:
    protos = kmedoids_prototypes([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]], k=2)
    assert len(protos) == 2
    assert max_prototype_sim([1.0, 0.0], protos) > 0.9
    # near-uniform top logprobs -> high entropy
    lp = [{"top_logprobs": [{"logprob": math.log(0.5)}, {"logprob": math.log(0.5)}]}]
    assert logprob_entropy(lp) > 0.6
    assert hedging_score("it possibly seems like maybe this applies") >= 3
    assert hedging_score("this is the control being tested") == 0
