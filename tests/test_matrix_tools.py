"""The EECS math: confusion matrix and per-class precision/recall.

Pure functions, no I/O: the tests pin the contract the EECS report
section depends on — sorted-label determinism, integer cells, the
zero-division rules (an absent class scores 0.0, never NaN), and
6-place float rounding in every metric row.
"""

from __future__ import annotations

import pytest

from corpus_kb.tools.matrix_tools import confusion_matrix, per_class_precision_recall

Y_TRUE = ["spam", "spam", "ham", "ham", "ham"]
Y_PRED = ["spam", "ham", "ham", "ham", "spam"]


def test_confusion_matrix_counts_cells_against_sorted_labels() -> None:
    result = confusion_matrix(Y_TRUE, Y_PRED)
    assert result["labels"] == ["ham", "spam"]
    # rows = true label, columns = predicted label.
    assert result["matrix"] == [[2, 1], [1, 1]]


def test_confusion_matrix_honors_explicit_label_order() -> None:
    result = confusion_matrix(Y_TRUE, Y_PRED, labels=["spam", "ham"])
    assert result["labels"] == ["spam", "ham"]
    assert result["matrix"] == [[1, 1], [1, 2]]


def test_confusion_matrix_includes_a_label_no_row_reached() -> None:
    result = confusion_matrix(["a"], ["a"], labels=["a", "b", "c"])
    assert result["labels"] == ["a", "b", "c"]
    assert result["matrix"] == [[1, 0, 0], [0, 0, 0], [0, 0, 0]]


def test_confusion_matrix_rejects_misaligned_inputs() -> None:
    with pytest.raises(ValueError, match="align"):
        confusion_matrix(["a", "b"], ["a"])


def test_confusion_matrix_rejects_empty_inputs() -> None:
    with pytest.raises(ValueError, match="empty"):
        confusion_matrix([], [])


def test_confusion_matrix_explicit_order_is_the_caller_vocabulary() -> None:
    """Unreached explicit labels keep their zero row (no drop, no error)."""
    result = confusion_matrix(["a"], ["a"], labels=["a", "b"])
    assert result["matrix"] == [[1, 0], [0, 0]]


def test_per_class_precision_recall_math() -> None:
    rows = per_class_precision_recall(Y_TRUE, Y_PRED)
    by_label = {row["label"]: row for row in rows}
    ham = by_label["ham"]
    spam = by_label["spam"]
    # ham: tp=2, fp=1, fn=1 -> precision 2/3, recall 2/3, f1 2/3, support 3.
    assert ham["tp"] == 2 and ham["fp"] == 1 and ham["fn"] == 1
    assert ham["precision"] == pytest.approx(2 / 3)
    assert ham["recall"] == pytest.approx(2 / 3)
    assert ham["f1"] == pytest.approx(2 / 3)
    assert ham["support"] == 3
    # spam: tp=1, fp=1, fn=1 -> 0.5 across the board, support 2.
    assert spam["precision"] == pytest.approx(0.5)
    assert spam["recall"] == pytest.approx(0.5)
    assert spam["f1"] == pytest.approx(0.5)
    assert spam["support"] == 2


def test_per_class_rows_are_in_sorted_label_order() -> None:
    rows = per_class_precision_recall(Y_TRUE, Y_PRED)
    assert [row["label"] for row in rows] == ["ham", "spam"]


def test_per_class_zero_division_scores_zero_never_nan() -> None:
    rows = per_class_precision_recall(["a", "a"], ["b", "b"])
    by_label = {row["label"]: row for row in rows}
    # a: tp=0, fp=0, fn=2 -> precision 0.0 (no prediction), recall 0.0.
    assert by_label["a"]["precision"] == 0.0
    assert by_label["a"]["recall"] == 0.0
    assert by_label["a"]["f1"] == 0.0
    # b: tp=0, fp=2, fn=0 -> precision 0.0, recall 0.0 (no positives).
    assert by_label["b"]["precision"] == 0.0
    assert by_label["b"]["recall"] == 0.0


def test_per_class_metrics_are_rounded_to_six_places() -> None:
    y_true = ["a"] * 3 + ["b"]
    y_pred = ["a"] * 3 + ["a"]
    rows = per_class_precision_recall(y_true, y_pred)
    a = rows[0]
    # a: tp=3, fp=1, fn=0 -> precision 0.75, recall 1.0, f1 ~0.857143.
    assert a["precision"] == 0.75
    assert a["recall"] == 1.0
    assert a["f1"] == round(2 * 0.75 * 1.0 / 1.75, 6)


def test_per_class_matches_confusion_matrix_cells() -> None:
    matrix = confusion_matrix(Y_TRUE, Y_PRED)
    rows = per_class_precision_recall(Y_TRUE, Y_PRED)
    for index, row in enumerate(rows):
        tp = matrix["matrix"][index][index]
        fp = sum(matrix["matrix"][other][index] for other in range(len(rows))) - tp
        fn = sum(matrix["matrix"][index]) - tp
        assert row["tp"] == tp and row["fp"] == fp and row["fn"] == fn
