"""Offline tests for the semantic-overlap section (todo 17)."""

from __future__ import annotations

import numpy as np
import pytest

from corpus_kb.research.overlap import (
    calibrate_tau_overlap,
    code_centroid_matrix,
    flag_overlap_pairs,
    shared_unit_confusion,
)
from corpus_kb.research.report_governance import (
    gold_inter_code_max_cosine,
    overlap_section,
)


def test_centroid_matrix_is_cosine():
    c1 = [1.0, 0.0]
    c2 = [0.0, 1.0]
    c3 = [0.70710678, 0.70710678]
    m = code_centroid_matrix([c1, c2, c3])
    assert m[0, 0] == pytest.approx(1.0)
    assert m[0, 1] == pytest.approx(0.0, abs=1e-5)
    assert m[0, 2] == pytest.approx(0.7071, abs=1e-3)


def test_flag_overlap_pairs_above_threshold_only():
    m = code_centroid_matrix([[1.0, 0.0], [0.99, 0.1], [0.0, 1.0]])
    flags = flag_overlap_pairs(m, ["a", "b", "c"], 0.90)
    assert len(flags) == 1
    assert {flags[0]["a"], flags[0]["b"]} == {"a", "b"}
    assert float(flags[0]["cos"]) > 0.9


def test_calibrate_tau_overlap_gold_plus_margin_clamped():
    assert calibrate_tau_overlap(0.9) == pytest.approx(0.95)
    assert calibrate_tau_overlap(0.99) == pytest.approx(0.95)
    assert calibrate_tau_overlap(0.0) == pytest.approx(0.70)
    assert calibrate_tau_overlap(None) == pytest.approx(0.70)


def test_shared_unit_confusion_counts_both_and_margin():
    members = {"a": {1, 2, 3}, "b": {2, 3, 4}}
    margins = {2: 0.01, 3: 0.5}
    confusion = shared_unit_confusion(members, margins, delta_amb=0.05)
    assert len(confusion) == 1
    row = confusion[0]
    assert row["both_codes"] == 2
    assert row["ambiguous_margin"] == 1
    assert row["combined"] == 2


def test_gold_inter_code_max_cosine_picks_worst_pair():
    a = [[1.0, 0.0]]
    b = [[0.0, 1.0]]
    c = [[0.7071, 0.7071]]
    assert gold_inter_code_max_cosine({"a": a, "b": b, "c": c}) == pytest.approx(0.7071, abs=1e-3)
    assert gold_inter_code_max_cosine({"a": a}) is None


def test_overlap_section_flags_seeded_collision():
    rng = np.random.default_rng(3)
    c1 = rng.normal(size=8)
    c1 /= np.linalg.norm(c1)
    c2 = rng.normal(size=8)
    c2 -= c2 @ c1 * c1
    c2 /= np.linalg.norm(c2)
    mixed = c1 + c2
    mixed /= np.linalg.norm(mixed)
    blended = 0.6 * c1 + 0.4 * c2
    blended /= np.linalg.norm(blended)
    centroids = {"code_a": c1.tolist(), "code_b": blended.tolist()}
    gold = {"code_a": [c1.tolist()], "code_b": [mixed.tolist()]}
    section = overlap_section(
        centroids,
        coded_vectors=[],
        assignments=[{"unit_id": 1, "code_id": "code_a", "sim_answer": 0.9}],
        gold_inter_code_max=gold_inter_code_max_cosine(gold),
    )
    assert section["tau_overlap"] == pytest.approx(0.7571, abs=1e-3)
    assert isinstance(section["shared_unit_confusion"], list)
    assert isinstance(section["matrix"], list)
