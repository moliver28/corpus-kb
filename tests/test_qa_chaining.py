from __future__ import annotations

from corpus_kb.coding.qa_chaining import chain_context, pool_score


def test_pool_max_and_chain() -> None:
    assert pool_score(plain_sim=0.4, ctx_sim=0.7) == 0.7
    assert chain_context(
        role="participant", moderator_q="how often?", answer="every quarter"
    ).startswith("how often?")
