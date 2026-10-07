"""Offline tests for the G3 signal-validity harness (todo 16)."""

from __future__ import annotations

import numpy as np
import pytest

from corpus_kb.research.g3_audit import (
    HUMAN_PARITY_BREACH_GATE,
    AuditRow,
    accuracy_at_coverage,
    aurc,
    auroc,
    drop_non_predictive,
    human_parity_verdict,
    run_g3_audit,
    signal_metrics,
    two_rater_alpha,
)


def test_auroc_perfect_anti_and_ties():
    assert auroc(np.array([0.9, 0.8, 0.2, 0.1]), np.array([1, 1, 0, 0])) == 1.0
    assert auroc(np.array([0.1, 0.2, 0.8, 0.9]), np.array([1, 1, 0, 0])) == 0.0
    assert auroc(np.array([0.5, 0.5]), np.array([1, 0])) == 0.5
    assert auroc(np.array([0.3]), np.array([1])) == 0.5


def test_aurc_ranks_perfect_ordering_better_than_anti():
    labels = np.array([1, 1, 0, 0])
    perfect = aurc(np.array([0.9, 0.8, 0.2, 0.1]), labels)
    anti = aurc(np.array([0.1, 0.2, 0.8, 0.9]), labels)
    assert perfect < anti
    assert perfect < 0.25
    assert anti > 0.75


def test_accuracy_at_coverage():
    scores = np.array([0.9, 0.8, 0.2, 0.1])
    labels = np.array([1, 1, 0, 0])
    assert accuracy_at_coverage(scores, labels, 0.5) == 1.0
    assert accuracy_at_coverage(scores, labels, 0.75) == pytest.approx(2 / 3)
    assert accuracy_at_coverage(scores, labels, 1.0) == 0.5
    assert accuracy_at_coverage(np.array([]), np.array([], dtype=int), 0.8) == 0.0


def test_signal_metrics_includes_ece_when_confidences_given():
    metrics = signal_metrics(
        np.array([0.9, 0.1, 0.9, 0.1]),
        np.array([1, 0, 0, 0]),
        confidences=np.array([0.9, 0.1, 0.9, 0.1]),
    )
    assert "ece" in metrics
    assert 0.0 <= metrics["ece"] <= 1.0
    bare = signal_metrics(np.array([0.9, 0.1]), np.array([1, 0]))
    assert "ece" not in bare
    assert {"auroc", "aurc", "n", "acc_at_80", "acc_at_90", "acc_at_95"}.issubset(bare)


def test_two_rater_alpha_reuses_coincidence_matrix_formula():
    assert two_rater_alpha([1, 1, 0, 0], [1, 1, 0, 0]) == pytest.approx(1.0)
    assert two_rater_alpha([1, 0, 1, 0], [0, 1, 0, 1]) == pytest.approx(-0.75)
    assert two_rater_alpha([1, 1], [1, 0]) == pytest.approx(0.0)
    with pytest.raises(ValueError):
        two_rater_alpha([1, 0], [1])


def test_human_parity_gate_flips_below_floor():
    healthy = human_parity_verdict("c1", 0.72, 0.85)
    assert healthy.breach is False and healthy.human_only is False
    breached = human_parity_verdict("c2", 0.45, 0.85)
    assert breached.breach is True and breached.human_only is True
    unmeasured = human_parity_verdict("c3", None, 0.85)
    assert unmeasured.breach is True


def test_drop_non_predictive_uses_auroc_floor():
    metrics = {
        "strong": {"auroc": 0.85},
        "weak": {"auroc": 0.52},
        "coin_flip": {"auroc": 0.50},
    }
    assert drop_non_predictive(metrics) == ["weak", "coin_flip"]


def _rows():
    """Deterministic audit rows: c1 = LLM matches human (no breach);
    c2 = 3-of-4 disagreement (alpha < 0.60, breach)."""
    rows = []
    for margin, override in [(0.95, 0), (0.80, 0), (0.20, 0), (0.10, 1), (0.90, 0), (0.15, 1)]:
        rows.append(
            AuditRow(
                code_id="c1",
                signal_values={"margin": margin, "hedge": 1.0 - margin},
                override=override,
                llm_label=1 if margin > 0.5 else 0,
                human_label=1 if margin > 0.5 else 0,
            )
        )
    for margin, llm, human in [(0.9, 1, 1), (0.8, 1, 0), (0.3, 0, 1), (0.2, 0, 1)]:
        rows.append(
            AuditRow(
                code_id="c2",
                signal_values={"margin": margin, "hedge": 1.0 - margin},
                override=1 if llm != human else 0,
                llm_label=llm,
                human_label=human,
            )
        )
    return rows


def test_run_g3_report_schema_and_parity_gate():
    report = run_g3_audit(_rows(), ["margin", "hedge"], human_alpha_ceiling=0.85)
    assert report.n_audit == 10
    assert set(report.signal_metrics) == {"margin", "hedge"}
    assert "auroc" in report.combined_metrics and "ece" in report.combined_metrics
    verdicts = {v.code_id: v for v in report.human_parity}
    assert verdicts["c1"].llm_alpha == pytest.approx(1.0)
    assert verdicts["c1"].breach is False and verdicts["c1"].human_only is False
    assert verdicts["c2"].breach is True and verdicts["c2"].human_only is True
    assert report.parity_breach is True
    assert report.halt_gate == HUMAN_PARITY_BREACH_GATE
    assert verdicts["c2"].human_alpha == 0.85


def test_run_g3_empty_audit_degrades_cleanly():
    report = run_g3_audit([], ["margin"])
    assert report.n_audit == 0
    assert report.halt_gate is None
    assert report.notes == ["empty audit set"]
