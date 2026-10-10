"""U45: retrieval evaluation harness tests (offline; metrics + gates + loader).

The metric functions are pure and checked against hand-computed values. The
label loader and arm evaluator get Protocol-typed fake connections (no real
asyncpg), and the DB-backed live path is covered by requires_postgres suites
run by the orchestrator.
"""

from __future__ import annotations

import pytest

from corpus_kb.research.retrieval_eval import (
    ArmMetrics,
    EvalReport,
    _mrr,
    _ndcg,
    _ranked_unit_ids,
    _recall_k,
    evaluate_arms,
    insufficient_report,
    load_reviewed_labels,
    recommend_default,
)
from corpus_kb.research.retrieval_settings import RetrievalSettings


def test_recall_at_k_counts_relevant_in_top_k() -> None:
    ranked = [1, 2, 3, 4, 5]
    assert _recall_k(ranked, frozenset({2, 4}), 5) == 1.0
    assert _recall_k(ranked, frozenset({2, 4}), 2) == 0.5
    assert _recall_k(ranked, frozenset({9}), 5) == 0.0
    # Empty relevance sets score 0 (no crash, no fabricated perfection).
    assert _recall_k(ranked, frozenset(), 5) == 0.0


def test_mrr_reciprocal_rank_of_first_relevant() -> None:
    assert _mrr([7, 8, 9], frozenset({9})) == pytest.approx(1 / 3)
    assert _mrr([9, 8, 7], frozenset({9})) == 1.0
    assert _mrr([1, 2, 3], frozenset({4})) == 0.0


def test_ndcg_at_ten_matches_hand_computed_value() -> None:
    # One relevant at rank 1: DCG=1, IDCG=1 -> 1.0.
    assert _ndcg([5, 1, 2], frozenset({5})) == pytest.approx(1.0)
    # One relevant at rank 3 (0-indexed 2): (1/log2(4)) / 1.
    assert _ndcg([1, 2, 5], frozenset({5})) == pytest.approx(0.5)
    # Relevant at ranks 1 and 3: DCG = 1 + 1/log2(4) = 1.5;
    # IDCG = 1 + 1/log2(3); nDCG = 1.5 / (1 + 1/log2(3)) < 1.
    got = _ndcg([6, 1, 5], frozenset({5, 6}))
    assert got == pytest.approx(1.5 / (1.0 + 1.0 / np_log2_3()))
    # Both relevant in the top two ranks is the perfect ordering.
    assert _ndcg([5, 6, 1], frozenset({5, 6})) == pytest.approx(1.0)


def np_log2_3() -> float:
    import math

    return math.log2(3)


def test_ranked_unit_ids_skips_non_unit_children() -> None:
    payloads: list[dict[str, object]] = [
        {"_unit_id": 3, "text": "a"},
        {"text": "exchange child, no unit"},
        {"_unit_id": 1, "text": "b"},
    ]
    assert _ranked_unit_ids(payloads) == [3, 1]


def test_insufficient_report_and_default_gate() -> None:
    report = insufficient_report()
    assert report.status == "insufficient_data"
    assert recommend_default(report, latency_budget_ms=1000.0) == "insufficient_data"


def test_recommend_default_requires_rrf_beat_vector_within_budget() -> None:
    vector = ArmMetrics(recall_at_k=0.8, mrr=0.7, ndcg_at_10=0.6, p95_latency_ms=50.0, n_queries=5)
    rrf_good = ArmMetrics(
        recall_at_k=0.85, mrr=0.7, ndcg_at_10=0.6, p95_latency_ms=90.0, n_queries=5
    )
    rrf_slow = ArmMetrics(
        recall_at_k=0.9, mrr=0.7, ndcg_at_10=0.6, p95_latency_ms=500.0, n_queries=5
    )
    rrf_worse = ArmMetrics(
        recall_at_k=0.7, mrr=0.7, ndcg_at_10=0.6, p95_latency_ms=90.0, n_queries=5
    )
    ok = EvalReport(status="ok", k=10, arms={"vector": vector, "rrf": rrf_good})
    slow = EvalReport(status="ok", k=10, arms={"vector": vector, "rrf": rrf_slow})
    worse = EvalReport(status="ok", k=10, arms={"vector": vector, "rrf": rrf_worse})
    tie = EvalReport(status="ok", k=10, arms={"vector": vector, "rrf": vector})
    assert recommend_default(ok, latency_budget_ms=100.0) == "rrf"
    assert recommend_default(slow, latency_budget_ms=100.0) == "vector"
    assert recommend_default(worse, latency_budget_ms=100.0) == "vector"
    # A tie satisfies the spec's "RRF >= vector-only" bar exactly.
    assert recommend_default(tie, latency_budget_ms=100.0) == "rrf"


class _FakeConn:
    """Protocol-shaped asyncpg stand-in (SQL recorded, fixture rows returned)."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.sql: list[str] = []

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        self.sql.append(sql)
        return self.rows


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self):
        outer = self

        class _Acquire:
            async def __aenter__(self) -> _FakeConn:
                return outer._conn

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Acquire()


def test_load_reviewed_labels_joins_codes_and_skips_unreviewed() -> None:
    conn = _FakeConn(
        [
            {
                "code_id": "c1",
                "name": "Billing Concern",
                "brief_definition": "Mentions invoicing or payments.",
                "unit_ids": [11, 12],
            }
        ]
    )
    import asyncio

    labels = asyncio.run(load_reviewed_labels(_FakePool(conn), "t-uuid", "p-uuid"))
    assert len(labels) == 1
    assert labels[0].query_id == "c1"
    assert labels[0].relevant == frozenset({11, 12})
    assert "Billing Concern" in labels[0].text
    # Labels come ONLY from the reviewed statuses — pin the WHERE clause.
    assert "'confirmed'" in conn.sql[0] and "'overridden'" in conn.sql[0]


async def test_evaluate_arms_returns_insufficient_without_queries() -> None:
    class _UnusedPool:
        def acquire(self):  # pragma: no cover - must never be reached
            raise AssertionError("no queries means no pool use")

    report = await evaluate_arms(
        _UnusedPool(),
        embedder=None,
        queries=[],
        tenant_id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000002",
        settings=RetrievalSettings(),
    )
    assert report.status == "insufficient_data"
    assert report.arms == {}
