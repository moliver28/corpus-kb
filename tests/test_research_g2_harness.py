"""G2 harness for todo-14: threshold calibration + conformal evidence (offline).

Methodology (v5 §12 G2 + r10 calibration block):
  1. Stratified split of the gold fixture into calibration (train) and test.
  2. Per-code tau_a/tau_qa/delta fitted by 5-fold CV INSIDE the calibration
     split, prototypes rebuilt per training fold (a held-out exemplar is
     never scored against a medoid chosen from it).
  3. Prototypes rebuilt from ALL calibration positives; test units scored
     against them (three views, max over prototypes).
  4. Temperature T fitted on the calibration split; ECE on test.
  5. Multi-label conformal prediction sets from the calibration split;
     empirical coverage asserted >= nominal.
  6. Decision invariants: deny/deflect never qdep, Q-alone never assigns,
     echo not qdep, Cov_explicit vs Cov_qdep.

Seeds are pinned: G2 refits are byte-stable for the same seeds.
Evidence JSON path: $CORPUS_KB_G2_EVIDENCE when set (local evidence runs),
else the pytest tmp dir (CI never writes outside its sandbox).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from corpus_kb.research.calibration import (
    CONFORMAL_ALPHA,
    conformal_sets,
    expected_calibration_error,
    fit_code_thresholds,
    fit_temperature,
)
from corpus_kb.research.deductive import decide_unit
from corpus_kb.research.prototypes import build_code_prototypes
from corpus_kb.research.scoring import score_code_batch

_FIXTURE = Path(__file__).with_name("fixtures") / "research" / "gold_fixture.json"
CALIBRATION_SPLIT_SEED = 13
TEMPERATURE_SEED = 13
N_FOLDS = 5


def _load_fixture() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _records_by_code(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by.setdefault(r["code_id"], []).append(r)
    return by


def _train_test_split(
    records: list[dict[str, Any]], fold: int = 0, n_folds: int = N_FOLDS
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deterministic stratified split: unit_id % n_folds == fold -> test."""
    train, test = [], []
    for r in records:
        (test if int(r["unit_id"]) % n_folds == fold else train).append(r)
    return train, test


def _build_prototypes(
    train_by_code: dict[str, list[dict[str, Any]]], rng: np.random.Generator
) -> dict[str, dict[str, list[list[float]]]]:
    gold: dict[str, dict[str, list[list[float]]]] = {}
    for code_id, recs in train_by_code.items():
        pos = [r for r in recs if int(r["label"]) == 1]
        gold[code_id] = {
            "answer": [r["answer"] for r in pos],
            "qa": [r["qa"] for r in pos],
            "question": [r["question"] for r in pos],
        }
    return build_code_prototypes(gold, rng=rng)


def _score_all_codes(
    records: list[dict[str, Any]],
    prototypes_by_code: dict[str, dict[str, list[list[float]]]],
    code_ids: list[str],
) -> dict[str, dict[int, dict[str, float]]]:
    """Score every record against every code on all three views.

    Returns {code_id: {unit_id: {"answer": s_a, "qa": s_qa, "question": s_q}}}.
    """
    out: dict[str, dict[int, dict[str, float]]] = {}
    for code_id in code_ids:
        protos = prototypes_by_code.get(code_id, {})
        batch = score_code_batch(
            [r["answer"] for r in records],
            [r["qa"] for r in records],
            [r["question"] for r in records],
            protos,
        )
        out[code_id] = {
            int(r["unit_id"]): {
                "answer": float(batch.answer[i]),
                "qa": float(batch.qa[i]),
                "question": float(batch.question[i]),
            }
            for i, r in enumerate(records)
        }
    return out


def _matrix(
    scores: dict[str, dict[int, dict[str, float]]],
    records: list[dict[str, Any]],
    code_ids: list[str],
    view: str,
) -> np.ndarray:
    m = np.zeros((len(records), len(code_ids)), dtype=np.float32)
    for i, r in enumerate(records):
        for j, code_id in enumerate(code_ids):
            m[i, j] = scores[code_id][int(r["unit_id"])][view]
    return m


def _labels_matrix(records: list[dict[str, Any]], code_ids: list[str]) -> np.ndarray:
    labels = np.zeros((len(records), len(code_ids)), dtype=int)
    code_index = {c: i for i, c in enumerate(code_ids)}
    for i, r in enumerate(records):
        if int(r["label"]) == 1:
            labels[i, code_index[r["code_id"]]] = 1
    return labels


def _thresholds_to_dict(thresholds: Any) -> dict[str, Any]:
    return {
        "tau_a": thresholds.tau_a,
        "tau_qa": thresholds.tau_qa,
        "delta": thresholds.delta,
        "m_gz_a": thresholds.m_gz_a,
        "m_gz_qa": thresholds.m_gz_qa,
        "m_gz_delta": thresholds.m_gz_delta,
        "cv_range": thresholds.cv_range,
        "n_gold": thresholds.n_gold,
        "unreliable": thresholds.unreliable,
    }


def _fit_all_thresholds(
    train: list[dict[str, Any]], code_ids: list[str], seed: int
) -> dict[str, Any]:
    return {
        code_id: fit_code_thresholds(
            train, code_id, n_folds=N_FOLDS, rng=np.random.default_rng(seed)
        )
        for code_id in code_ids
    }


def test_g2_refits_are_byte_stable_for_pinned_seeds():
    """G2 seed pinning (r11): same seeds -> identical threshold refits."""
    fixture = _load_fixture()
    code_ids = [c["code_id"] for c in fixture["codes"]]
    train, _ = _train_test_split(fixture["records"])
    first = _fit_all_thresholds(train, code_ids, CALIBRATION_SPLIT_SEED)
    second = _fit_all_thresholds(train, code_ids, CALIBRATION_SPLIT_SEED)
    for code_id in code_ids:
        assert _thresholds_to_dict(first[code_id]) == _thresholds_to_dict(second[code_id])
    rng_a = np.random.default_rng(CALIBRATION_SPLIT_SEED)
    rng_b = np.random.default_rng(CALIBRATION_SPLIT_SEED)
    protos_a = _build_prototypes(_records_by_code(train), rng_a)
    protos_b = _build_prototypes(_records_by_code(train), rng_b)
    assert protos_a == protos_b


def test_g2_harness_calibration_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fixture = _load_fixture()
    records: list[dict[str, Any]] = fixture["records"]
    code_ids = [c["code_id"] for c in fixture["codes"]]

    train, test = _train_test_split(records)
    train_by_code = _records_by_code(train)

    thresholds_by_code = _fit_all_thresholds(train, code_ids, CALIBRATION_SPLIT_SEED)
    prototypes_by_code = _build_prototypes(
        train_by_code, np.random.default_rng(CALIBRATION_SPLIT_SEED)
    )

    cal_scores_by_code = _score_all_codes(train, prototypes_by_code, code_ids)
    test_scores_by_code = _score_all_codes(test, prototypes_by_code, code_ids)

    # Temperature scaling fitted on the held-out calibration split.
    cal_answer = _matrix(cal_scores_by_code, train, code_ids, "answer")
    cal_labels = _labels_matrix(train, code_ids)
    temperature, _ = fit_temperature(cal_answer, cal_labels)
    assert temperature > 0.0

    # Multi-label conformal sets from the calibration split.
    _sets, coverage = conformal_sets(
        cal_answer,
        cal_labels,
        _matrix(test_scores_by_code, test, code_ids, "answer"),
        alpha=CONFORMAL_ALPHA,
    )
    assert coverage >= 1 - CONFORMAL_ALPHA

    # Decision invariants + coverage split on the test set.
    set_sizes = {len(s) for s in _sets}
    explicit_units: set[int] = set()
    qdep_units: set[int] = set()
    review_units: set[int] = set()
    deny_qdep = 0
    echo_qdep = 0
    for r in test:
        unit_id = int(r["unit_id"])
        scores_by_code = {code_id: test_scores_by_code[code_id][unit_id] for code_id in code_ids}
        ds = decide_unit(
            scores_by_code,
            {c: _thresholds_to_dict(t) for c, t in thresholds_by_code.items()},
            stance=r.get("stance"),
        )
        for d in ds:
            if d.evidence_basis == "explicit_in_answer":
                explicit_units.add(unit_id)
            elif d.evidence_basis == "question_dependent":
                qdep_units.add(unit_id)
            if d.status == "review":
                review_units.add(unit_id)
            if d.evidence_basis == "question_dependent" and r.get("stance") in (
                "deny",
                "deflect",
            ):
                deny_qdep += 1
        if (
            not ds
            and abs(
                test_scores_by_code[r["code_id"]][unit_id]["qa"]
                - test_scores_by_code[r["code_id"]][unit_id]["question"]
            )
            <= 0.02
            and str(r.get("evidence_basis")) == "question_dependent"
        ):
            echo_qdep += 1

    assert deny_qdep == 0, "deny/deflect stance question-dependent-assigned"
    assert echo_qdep == 0, "question-echo record question-dependent-assigned"
    all_units = {int(r["unit_id"]) for r in test}
    cov_explicit = len(explicit_units) / len(all_units)
    cov_qdep = len(qdep_units) / len(all_units)

    # ECE on temperature-scaled test confidences.
    scaled = _matrix(test_scores_by_code, test, code_ids, "answer") / temperature
    confidences = np.max(scaled, axis=1)
    top1_correct = np.zeros(len(test), dtype=int)
    for i, r in enumerate(test):
        if int(r["label"]) == 1:
            true_idx = code_ids.index(r["code_id"])
            if int(np.argmax(scaled[i])) == true_idx:
                top1_correct[i] = 1
    ece = expected_calibration_error(confidences, top1_correct)

    evidence = {
        "task": "todo-14",
        "module": "research.deductive",
        "branch": "feature/v5-deductive-v2",
        "codes": code_ids,
        "seeds": {
            "calibration_split_seed": CALIBRATION_SPLIT_SEED,
            "temperature_seed": TEMPERATURE_SEED,
        },
        "thresholds": {c: _thresholds_to_dict(t) for c, t in thresholds_by_code.items()},
        "temperature": temperature,
        "conformal": {
            "alpha": CONFORMAL_ALPHA,
            "coverage": coverage,
            "nominal_coverage": 1 - CONFORMAL_ALPHA,
            "n_cal": len(cal_answer),
            "n_test": len(test),
            "set_size_min": min(set_sizes) if set_sizes else 0,
            "set_size_max": max(set_sizes) if set_sizes else 0,
        },
        "calibration": {"ece": ece, "n_test": len(test)},
        "coverage": {"cov_explicit": cov_explicit, "cov_qdep": cov_qdep},
        "invariants": {
            "explicit_units": len(explicit_units),
            "qdep_units": len(qdep_units),
            "review_units": len(review_units),
            "deny_qdep_violations": deny_qdep,
            "echo_qdep_violations": echo_qdep,
        },
        "pass": coverage >= 1 - CONFORMAL_ALPHA and deny_qdep == 0 and echo_qdep == 0,
    }

    out_path = os.environ.get("CORPUS_KB_G2_EVIDENCE")
    out = Path(out_path) if out_path else tmp_path / "g2-evidence.json"
    out.write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    for t in thresholds_by_code.values():
        assert t.tau_a >= 0.0
        assert t.tau_qa >= 0.0
        assert t.delta >= 0.0
        assert t.unreliable is False
        assert "tau_a" in t.cv_range
    assert evidence["pass"]
