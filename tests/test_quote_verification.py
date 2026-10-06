from __future__ import annotations

from corpus_kb.coding.quote_verification import classify_quote


def test_exact_and_normalized_and_missing() -> None:
    chunk = "we read it back to them to confirm the number"
    assert classify_quote("read it back to them", chunk)[0] == "exact"
    assert classify_quote("read it back to them…", chunk)[0] in {"exact", "normalized_match"}
    assert classify_quote("we never said this", chunk)[0] == "not_found"
