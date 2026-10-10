from __future__ import annotations

import pytest

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


def test_audit_partitions_stratified_by_code_and_band() -> None:
    """U17: seeded stratified audit design rows in the audit_partitions shape."""
    from corpus_kb.coding.reliability_sampling import AuditSampleRow, audit_partitions

    units = []
    for i in range(100):
        units.append(
            {
                "unit_id": f"u{i}",
                "code_id": "FW-01" if i % 2 == 0 else "FW-02",
                # i % 3 crosses both parity classes, so BOTH codes get BOTH
                # agreement bands (i % 4 would align with i % 2 and starve
                # FW-02|high).
                "agreement": 0.9 if i % 3 == 0 else 0.2,
            }
        )
    rows = audit_partitions(units, sample_size=20, seed=42, scope="run-1")
    assert all(isinstance(r, AuditSampleRow) for r in rows)
    assert len(rows) == 20
    # Strata are code x band; every row carries scope, partition, seed.
    strata = {r.stratum for r in rows}
    assert strata == {"FW-01|high", "FW-01|low", "FW-02|high", "FW-02|low"}
    assert all(r.partition == "audit" and r.scope == "run-1" and r.seed == 42 for r in rows)
    # Inclusion probability is the design-based n_sampled / n_stratum.
    by_stratum: dict[str, int] = {}
    for r in rows:
        by_stratum[r.stratum] = by_stratum.get(r.stratum, 0) + 1
    for r in rows:
        stratum_size = sum(
            1
            for u in units
            if u["code_id"] == r.stratum.split("|")[0]
            and ((u["agreement"] >= 0.5) == (r.stratum.endswith("high")))
        )
        assert r.inclusion_probability == pytest.approx(by_stratum[r.stratum] / stratum_size)
    # No unit sampled twice.
    assert len({r.unit_id for r in rows}) == 20


def test_audit_partitions_deterministic_under_seed() -> None:
    from corpus_kb.coding.reliability_sampling import audit_partitions

    units = [
        {"unit_id": f"u{i}", "code_id": f"C{i % 3}", "agreement": 0.1 + (i % 3) * 0.4}
        for i in range(60)
    ]
    a = audit_partitions(units, sample_size=12, seed=7)
    b = audit_partitions(units, sample_size=12, seed=7)
    c = audit_partitions(units, sample_size=12, seed=8)
    assert a == b
    assert [r.unit_id for r in a] != [r.unit_id for r in c]


def test_audit_partitions_caps_at_pool_size() -> None:
    from corpus_kb.coding.reliability_sampling import audit_partitions

    units = [{"unit_id": "u0", "code_id": "C", "agreement": 0.9}]
    rows = audit_partitions(units, sample_size=10, seed=0)
    assert len(rows) == 1
    assert rows[0].inclusion_probability == 1.0


def test_audit_partitions_rejects_agreement_outside_bands() -> None:
    from corpus_kb.coding.reliability_sampling import audit_partitions

    units = [{"unit_id": "u0", "code_id": "C", "agreement": 1.5}]
    with pytest.raises(ValueError, match="band"):
        audit_partitions(units, sample_size=1, seed=0)
