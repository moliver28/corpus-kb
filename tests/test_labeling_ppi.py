"""The PPI validation hook: LF-derived labels vs a small gold dev set."""

from __future__ import annotations

import numpy as np
import pytest

from corpus_kb.labeling.ppi_check import ppi_validate

# Ten built chunks, all labeled class 1 by the aggregation.
_IDS = [f"10000000-0000-0000-0000-{i:012d}" for i in range(10)]
_LABELS = np.ones(10, dtype=int)


def test_perfect_gold_agreement_corrects_to_the_true_prevalence() -> None:
    # Model says class 1 everywhere; gold says class 0 on 4 of the 10.
    # All residuals are -1, so the debiased prevalence is exactly 0.0 with
    # a degenerate (zero-width) interval.
    gold = {_IDS[i]: 0 for i in range(4)}
    result = ppi_validate(_LABELS, _IDS, gold, class_index=1, class_name="relevant")
    assert result.status == "corrected"
    assert result.label == "corrected"
    assert result.raw_accuracy == 0.0  # the model misses every audited unit
    assert result.estimate == 0.0
    assert result.lower == 0.0
    assert result.upper == 0.0
    assert result.n_population == 10
    assert result.n_gold == 4
    assert result.class_name == "relevant"


def test_agreeing_gold_keeps_the_estimate_honest() -> None:
    # Gold confirms the model on every audited unit: the correction term
    # is zero, so the estimate equals the raw prevalence.
    gold = {_IDS[i]: 1 for i in range(5)}
    result = ppi_validate(_LABELS, _IDS, gold, class_index=1, class_name="relevant")
    assert result.status == "corrected"
    assert result.raw_accuracy == 1.0
    assert result.estimate == 1.0


def test_single_gold_item_answers_insufficient_data() -> None:
    result = ppi_validate(_LABELS, _IDS, {_IDS[0]: 1}, class_index=1, class_name="relevant")
    assert result.status == "insufficient_data"
    assert result.label == "uncorrected"  # never presented as validated
    assert result.lower is None and result.upper is None
    assert result.n_gold == 1


def test_gold_ids_off_the_build_raise_with_the_remediation() -> None:
    with pytest.raises(ValueError, match="no gold chunk ids match"):
        ppi_validate(_LABELS, _IDS, {"missing-id": 1}, class_index=1, class_name="relevant")


def test_empty_gold_raises() -> None:
    with pytest.raises(ValueError, match="no gold chunk ids match"):
        ppi_validate(_LABELS, _IDS, {}, class_index=1, class_name="relevant")


def test_misaligned_inputs_raise() -> None:
    with pytest.raises(ValueError, match="align"):
        ppi_validate(_LABELS, _IDS[:5], {_IDS[0]: 1}, class_index=1, class_name="relevant")
    with pytest.raises(ValueError, match="unique"):
        ppi_validate(
            np.array([1, 1]), ["dup", "dup"], {"dup": 1}, class_index=1, class_name="relevant"
        )


def test_non_integer_gold_labels_raise() -> None:
    with pytest.raises(ValueError, match="class index"):
        ppi_validate(_LABELS, _IDS, {_IDS[0]: True}, class_index=1, class_name="relevant")
    with pytest.raises(ValueError, match="class index"):
        ppi_validate(_LABELS, _IDS, {_IDS[0]: "relevant"}, class_index=1, class_name="relevant")


def test_to_dict_is_json_safe_with_fixed_keys() -> None:
    result = ppi_validate(_LABELS, _IDS, {_IDS[i]: 1 for i in range(3)}, 1, "relevant")
    payload = result.to_dict()
    assert list(payload) == [
        "class_name",
        "status",
        "label",
        "raw_accuracy",
        "estimate",
        "lower",
        "upper",
        "n_population",
        "n_gold",
        "effective_audit_n",
    ]
    assert payload["class_name"] == "relevant"
