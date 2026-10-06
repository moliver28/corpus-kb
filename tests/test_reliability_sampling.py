from __future__ import annotations

from corpus_kb.coding.reliability_sampling import insufficient_codes, stratified_sample


def test_stratified_all_dispositions() -> None:
    rows = [
        {"chunk_id": str(i), "code_id": "FW-01", "disposition": d}
        for i, d in enumerate(["accepted", "rejected", "no_code_applies"] * 20)
    ]
    sample = stratified_sample(rows, per_code_min=25, seed=0)
    dispos = {r["disposition"] for r in sample}
    assert dispos == {"accepted", "rejected", "no_code_applies"}  # spans all three
    assert insufficient_codes({"FW-01": 10, "FW-02": 30}, per_code_min=25) == ["FW-01"]
