"""G3 harness for todo 16: signal validity + human-parity evidence (offline).

Methodology (v5 §12 G3 + r10/r11):
  1. Stratified split of the gold fixture (todo-14 shape, seed 42): units
     scored against prototypes rebuilt from the calibration split only.
  2. Per-unit signals on the audit set (unit_id % 2 == 1 test half):
     margin = top1-top2 of the code-score matrix (real scores);
     interpretive / question_dependent from the gold records (real);
     hedge / h_tok / link_score are SEEDED SYNTHETIC columns (documented:
     the harness validates metric math + report schema; real values flow
     from live runs in todo 17's re-run);
     se = tier-3 cascade on the flagged minority, NLI judgments from the
     RECORDED qwen3 fixture (no network).
  3. Override label = the unit's top-1 code decision contradicts gold
     (accepted a gold-negative code, or the top-1 gold code fell outside
     the decision). Reviewer-override rows alone are selection-biased; the
     audit set here is the random half, per r10.
  4. Metrics per signal + combined priority: AUROC, AURC,
     accuracy@coverage at 80/90/95%, ECE (todo-14 machinery); non-
     predictive signals (AUROC < 0.55) land on the drop list.
  5. LLM-vs-human alpha per code vs the human-human alpha ceiling
     (production input; 0.85 fixture placeholder): < 0.60 flips the code to
     human-only routing + the human_parity_breach halt gate.

Evidence JSON path: $CORPUS_KB_G3_EVIDENCE when set (local evidence runs),
else the pytest tmp dir (CI never writes outside its sandbox).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from corpus_kb.coding.uncertainty.cascade import run_cascade, select_escalation
from corpus_kb.coding.uncertainty.nli_client import fixture_judge
from corpus_kb.coding.uncertainty.review_priority import ReviewSignal, priority_scores
from corpus_kb.research.calibration import conformal_sets
from corpus_kb.research.g3_audit import AuditRow, run_g3_audit
from corpus_kb.research.scoring import score_code_batch

_FIXTURE = Path(__file__).with_name("fixtures") / "research" / "gold_fixture.json"
_NLI = Path(__file__).with_name("fixtures") / "research" / "nli_recorded_qwen3.json"
SPLIT_SEED = 13
HUMAN_ALPHA_CEILING = 0.85
COVERAGE_LEVELS = (0.80, 0.90, 0.95)


def _load() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _nli_units() -> dict[str, dict[str, Any]]:
    doc = json.loads(_NLI.read_text(encoding="utf-8"))
    return {u["unit"]: u for u in doc["units"]}


def _fixture_nli_fn(a: str, b: str) -> bool:
    units = _nli_units()
    for name in ("ambiguous_two_themes", "clear_denial"):
        rationales = units[name]["rationales"]
        if a in rationales and b in rationales:
            return fixture_judge(units[name]["records"])(a, b)
    raise KeyError("pair not in recorded NLI fixtures")


def _split(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train = [r for r in records if int(r["unit_id"]) % 2 == 0]
    test = [r for r in records if int(r["unit_id"]) % 2 == 1]
    return train, test


def _unit_ids(records: list[dict[str, Any]]) -> list[int]:
    return sorted({int(r["unit_id"]) for r in records})


def _records_by_unit(records: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    by: dict[int, dict[str, Any]] = {}
    for r in records:
        by[int(r["unit_id"])] = r
    return by


def _view_prototypes(train: list[dict[str, Any]], view: str) -> dict[str, list[list[float]]]:
    pos = [r for r in train if int(r["label"]) == 1]
    return {
        code_id: [r[view] for r in pos if r["code_id"] == code_id]
        for code_id in {str(r["code_id"]) for r in pos}
    }


def _score_matrix(
    train: list[dict[str, Any]], test: list[dict[str, Any]], code_ids: list[str]
) -> np.ndarray:
    """Answer-view max-cosine of each test unit against each code's prototypes."""
    protos = _view_prototypes(train, "answer")
    by_unit = _records_by_unit(test)
    units = _unit_ids(test)
    matrix = np.zeros((len(units), len(code_ids)), dtype=np.float32)
    for row, unit_id in enumerate(units):
        answer = by_unit[unit_id]["answer"]
        for col, code_id in enumerate(code_ids):
            gold_vecs = protos.get(code_id, [])
            if gold_vecs:
                scores = score_code_batch([answer], [answer], [answer], {"answer": gold_vecs})
                matrix[row, col] = float(scores.answer[0])
    return matrix


def _answer_scores_by_unit(
    train: list[dict[str, Any]], records: list[dict[str, Any]], code_ids: list[str]
) -> dict[int, list[float]]:
    """Answer-view score rows (one per unit) against each code's train positives."""
    protos = _view_prototypes(train, "answer")
    by_unit = _records_by_unit(records)
    out: dict[int, list[float]] = {}
    for unit_id in _unit_ids(records):
        answer = by_unit[unit_id]["answer"]
        out[unit_id] = [
            float(score_code_batch([answer], [answer], [answer], {"answer": protos[c]}).answer[0])
            if protos.get(c)
            else 0.0
            for c in code_ids
        ]
    return out


def _conformal_sizes(
    train: list[dict[str, Any]], test: list[dict[str, Any]], code_ids: list[str]
) -> dict[int, int]:
    cal_rows, cal_labels = [], []
    cal_scores = _answer_scores_by_unit(train, train, code_ids)
    gold_by_unit: dict[int, dict[str, int]] = {}
    for r in train:
        gold_by_unit.setdefault(int(r["unit_id"]), {})[str(r["code_id"])] = int(r["label"])
    for unit_id, row_scores in cal_scores.items():
        labels = gold_by_unit.get(unit_id, {})
        cal_rows.append(row_scores)
        cal_labels.append([1 if labels.get(c) == 1 else 0 for c in code_ids])
    test_rows = _answer_scores_by_unit(train, test, code_ids)
    units = _unit_ids(test)
    sets, _coverage = conformal_sets(
        np.array(cal_rows, dtype=np.float32),
        np.array(cal_labels, dtype=int),
        np.array([test_rows[u] for u in units], dtype=np.float32),
    )
    return {unit_id: len(sets[i]) for i, unit_id in enumerate(units)}


def _build_audit_rows() -> tuple[list[AuditRow], dict[str, Any]]:
    doc = _load()
    code_ids = [c["code_id"] for c in doc["codes"]]
    interpretive = {c["code_id"] for c in doc["codes"] if c.get("is_interpretive")}
    train, test = _split(doc["records"])
    by_unit = _records_by_unit(test)
    units = _unit_ids(test)
    matrix = _score_matrix(train, test, code_ids)
    set_sizes = _conformal_sizes(train, test, code_ids)

    rng = np.random.default_rng(SPLIT_SEED)
    signals: list[ReviewSignal] = []
    for row, unit_id in enumerate(units):
        scores_sorted = np.sort(matrix[row])[::-1]
        margin = float(scores_sorted[0] - scores_sorted[1])
        record = by_unit[unit_id]
        signals.append(
            ReviewSignal(
                margin=margin,
                h_tok=float(rng.random()),
                hedge=int(rng.integers(0, 5)),
                interpretive=str(record["code_id"]) in interpretive,
                link_score=float(rng.random()),
                question_dependent=str(record.get("evidence_basis")) == "question_dependent",
            )
        )
    preliminary = priority_scores(signals)

    nli_doc = json.loads(_NLI.read_text(encoding="utf-8"))
    fixture_sets = {u["unit"]: u["rationales"] for u in nli_doc["units"]}
    names = list(fixture_sets)
    escalated_idx = select_escalation(preliminary)
    resample = {idx: fixture_sets[names[idx % len(names)]] for idx in escalated_idx}
    cascade = run_cascade(
        matrix,
        signals,
        resample_fn=lambda idx: resample[idx],
        nli_fn=_fixture_nli_fn,
        decision_reasons=["explicit"] * len(units),
        delta_amb=0.05,
    )
    se_by_unit = {
        r.unit_index: r.semantic_entropy for r in cascade.routed if r.semantic_entropy is not None
    }
    top1_by_unit = {r.unit_index: int(np.argmax(matrix[r.unit_index])) for r in cascade.routed}

    rows: list[AuditRow] = []
    for record in test:
        unit_id = int(record["unit_id"])
        row = units.index(unit_id)
        code_col = code_ids.index(str(record["code_id"]))
        human = int(record["label"])
        llm = int(top1_by_unit.get(unit_id, code_col) == code_col)
        sig = signals[row]
        rows.append(
            AuditRow(
                code_id=str(record["code_id"]),
                signal_values={
                    "margin": sig.margin,
                    "h_tok": float(sig.h_tok or 0.0),
                    "se": float(se_by_unit.get(unit_id, 0.0)),
                    "hedge": float(sig.hedge),
                    "interpretive": float(sig.interpretive),
                    "link_score": 1.0 - float(sig.link_score or 1.0),
                    "question_dependent": float(sig.question_dependent),
                },
                override=int(llm != human),
                llm_label=llm,
                human_label=human,
            )
        )
    meta = {
        "n_units_audit": len(units),
        "n_audit_rows": len(rows),
        "n_escalated": cascade.n_escalated,
        "escalation_proportion": cascade.escalation_proportion,
        "n_nli_calls": cascade.n_nli_calls,
        "reasons_closed_ok": set(cascade.reasons)
        <= {
            "overlap",
            "conformal",
            "interpretive",
            "gray_zone",
        },
        "conformal_set_sizes": {str(k_): v for k_, v in set_sizes.items()},
    }
    return rows, meta


SIGNAL_NAMES = (
    "margin",
    "h_tok",
    "se",
    "hedge",
    "interpretive",
    "link_score",
    "question_dependent",
)


def _confidence_columns(rows: list[AuditRow]) -> dict[str, list[float]]:
    """Rank-normalize every signal column to [0, 1] (ECE-valid confidences)."""
    from corpus_kb.coding.uncertainty.review_priority import rank_normalize

    out: dict[str, list[float]] = {}
    for name in SIGNAL_NAMES:
        values = [float(r.signal_values.get(name, 0.0)) for r in rows]
        out[name] = rank_normalize(values).tolist()
    return out


def test_g3_harness_full_report(tmp_path: Path) -> None:
    rows, meta = _build_audit_rows()
    assert meta["n_units_audit"] > 20
    assert 0.10 <= meta["escalation_proportion"] <= 0.20
    assert meta["reasons_closed_ok"] is True
    report = run_g3_audit(
        rows,
        list(SIGNAL_NAMES),
        human_alpha_ceiling=HUMAN_ALPHA_CEILING,
        confidence_columns=_confidence_columns(rows),
    )
    assert report.n_audit == meta["n_units_audit"]
    assert set(report.signal_metrics) == set(SIGNAL_NAMES)
    for metrics in report.signal_metrics.values():
        assert 0.0 <= float(metrics["auroc"]) <= 1.0
        for level in COVERAGE_LEVELS:
            assert 0.0 <= float(metrics[f"acc_at_{int(level * 100)}"]) <= 1.0
        assert 0.0 <= float(metrics["ece"]) <= 1.0
    for verdict in report.human_parity:
        expected_breach = verdict.llm_alpha is None or verdict.llm_alpha < 0.60
        assert verdict.breach == expected_breach
        assert verdict.human_only == expected_breach
    assert report.parity_breach == any(v.breach for v in report.human_parity)
    assert (report.halt_gate == "human_parity_breach") == report.parity_breach

    artifact = {
        "todo": 16,
        "harness": "G3",
        "split_seed": SPLIT_SEED,
        "human_alpha_ceiling_fixture_placeholder": HUMAN_ALPHA_CEILING,
        "audit_meta": meta,
        "signals": {
            name: {k: float(v) for k, v in m.items()} for name, m in report.signal_metrics.items()
        },
        "combined": {k: float(v) for k, v in report.combined_metrics.items()},
        "dropped_signals": report.dropped_signals,
        "human_parity": [
            {
                "code_id": v.code_id,
                "llm_alpha": v.llm_alpha,
                "human_alpha_ceiling": v.human_alpha,
                "breach": v.breach,
                "human_only": v.human_only,
            }
            for v in report.human_parity
        ],
        "halt_gate": report.halt_gate,
    }
    out = os.environ.get("CORPUS_KB_G3_EVIDENCE")
    target = Path(out) if out else tmp_path / "g3-report.json"
    target.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    assert target.exists()
