"""Eval runner tests (OFFLINE): metric math, scripted search, honest skips.

No Postgres, no Ollama, no deepeval: a scripted connection returns the
canned dense/FTS/fusion rows (the same SQL markers the real arms emit),
a fake embedder answers ``embed_cached``, and the answer section runs
with ``--skip-answer-metrics`` or a missing extra. The metric numbers
are hand-computed and exact.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest

from corpus_kb.eval.golden import load_golden
from corpus_kb.eval.report import to_envelope
from corpus_kb.eval.runner import run_eval
from corpus_kb.research.retrieval import ResearchQuery, _child_uuid
from corpus_kb.research.retrieval_settings import RetrievalSettings

TENANT = "00000000-0000-0000-0000-000000000001"
PROJECT = "00000000-0000-0000-0000-000000000002"
CFG: dict[str, object] = {"embedding": {"model": "nomic-embed-text", "dimensions": 1024}}

REMEDY = "pip install 'corpus-kb[eval]'"


# ============================================================================
# Hand-computed metric math (through the runner's own imports)
# ============================================================================


def test_metric_math_on_chunk_id_strings() -> None:
    from corpus_kb.research.retrieval_eval import _mrr, _ndcg, _recall_k

    a, b, c, d = "a", "b", "c", "d"
    ranked = [a, b, c]
    relevant = frozenset({b, d})
    assert _recall_k(ranked, relevant, 3) == pytest.approx(1 / 2)
    assert _mrr(ranked, relevant) == pytest.approx(1 / 2)
    # One relevant hit at rank 2 (0-indexed 1): DCG = 1/log2(3);
    # IDCG = 1/log2(2) + 1/log2(3).
    expected = (1.0 / math.log2(3)) / (1.0 + 1.0 / math.log2(3))
    assert _ndcg(ranked, relevant, k=10) == pytest.approx(expected)
    assert _ndcg([b, a, c], frozenset({b}), k=10) == 1.0
    # Empty relevance scores 0 — never a fabricated perfect score.
    assert _recall_k(ranked, frozenset(), 3) == 0.0


# ============================================================================
# Scripted connection + embedder (the _unit_arms -> _rrf path)
# ============================================================================


class _FakeEmbedder:
    def __init__(self) -> None:
        self.embedded: list[str] = []

    async def embed_cached(self, tenant_id: UUID, text: str) -> list[float] | None:
        self.embedded.append(text)
        return [0.1] * 1024


class _ScriptedConn:
    """Answers the exact SQL markers the research arms emit."""

    def __init__(
        self,
        dense_rows: list[dict[str, object]],
        fts_rows: list[dict[str, object]],
        fused_rows: list[dict[str, object]],
    ) -> None:
        self.dense_rows = dense_rows
        self.fts_rows = fts_rows
        self.fused_rows = fused_rows
        self.sql: list[str] = []
        self.fusion_args: list[tuple[object, ...]] = []

    async def execute(self, sql: str, *args: object) -> str:
        self.sql.append(sql)
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        self.sql.append(sql)
        normalized = " ".join(sql.split())
        if "rrf_fusion" in normalized:
            self.fusion_args.append(args)
            return self.fused_rows
        if "<=>" in normalized:
            return self.dense_rows
        if "plainto_tsquery" in normalized:
            return self.fts_rows
        return []

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        self.sql.append(sql)
        return None

    def transaction(self):
        outer = self

        class _Tx:
            async def __aenter__(self) -> _ScriptedConn:
                return outer

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Tx()


class _ScriptedPool:
    def __init__(self, conn: _ScriptedConn) -> None:
        self._conn = conn
        self.closed = False

    def acquire(self):
        outer = self

        class _Acquire:
            async def __aenter__(self) -> _ScriptedConn:
                return outer._conn

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Acquire()

    async def close(self) -> None:
        self.closed = True


def _unit_row(unit_id: int) -> dict[str, object]:
    return {
        "unit_id": unit_id,
        "doc_id": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        "exchange_id": None,
        "seq": unit_id,
        "text": f"unit {unit_id} text",
        "role_in_exchange": "answer",
        "score": 0.5,
    }


def _scripted(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_ScriptedPool, _FakeEmbedder, _ScriptedConn]:
    conn = _ScriptedConn(
        dense_rows=[_unit_row(1), _unit_row(2)],
        fts_rows=[_unit_row(2)],
        # Fusion puts the NON-relevant unit first, the relevant one second.
        fused_rows=[
            {"chunk_id": _child_uuid("unit:2"), "text": "unit 2", "doc_id": "e" * 8, "score": 0.9},
            {"chunk_id": _child_uuid("unit:1"), "text": "unit 1", "doc_id": "e" * 8, "score": 0.8},
        ],
    )
    pool = _ScriptedPool(conn)
    embedder = _FakeEmbedder()
    monkeypatch.setattr("corpus_kb.eval.runner._build_embedder", lambda cfg, pool_: embedder)
    return pool, embedder, conn


@pytest.fixture()
def scripted(monkeypatch: pytest.MonkeyPatch) -> tuple[_ScriptedPool, _FakeEmbedder]:
    pool, embedder, _ = _scripted(monkeypatch)
    return pool, embedder


@pytest.fixture()
def scripted_triple(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_ScriptedPool, _FakeEmbedder, _ScriptedConn]:
    return _scripted(monkeypatch)


def _golden_file(tmp_path: Path, with_answer: bool = False) -> Path:
    record = {
        "query": "billing concern",
        "relevant_chunk_ids": [_child_uuid("unit:1")],
        "answer": "The billing answer." if with_answer else None,
    }
    path = tmp_path / "golden.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return path


async def test_runner_scores_retrieval_with_reused_metrics(
    scripted: tuple[_ScriptedPool, _FakeEmbedder], tmp_path: Path
) -> None:
    pool, _ = scripted
    records = load_golden(_golden_file(tmp_path))
    report = await run_eval(records, CFG, pool, k=2, skip_answer_metrics=True)
    assert report.status == "success"
    row = report.retrieval.per_record[0]
    # Relevant chunk fused at rank 2 of 2 -> recall@2 = 1.0, MRR = 0.5,
    # nDCG@10 = 1/log2(3) over the one-relevant ideal.
    assert row.recall_at_k == pytest.approx(1.0)
    assert row.mrr == pytest.approx(0.5)
    assert row.ndcg_at_10 == pytest.approx(1.0 / math.log2(3))
    assert row.retrieved_count == 2
    assert row.relevant_count == 1
    assert report.retrieval.n_records == 1
    assert report.retrieval.recall_at_k == pytest.approx(1.0)
    # The summary splits the golden set honestly.
    assert report.golden == {"records": 1, "with_answer": 0, "without_answer": 1}


async def test_runner_skip_answer_metrics_marks_the_skip(
    scripted: tuple[_ScriptedPool, _FakeEmbedder], tmp_path: Path
) -> None:
    pool, _ = scripted
    records = load_golden(_golden_file(tmp_path))
    report = await run_eval(records, CFG, pool, k=2, skip_answer_metrics=True)
    assert report.answer == {"skipped": "answer metrics disabled by --skip-answer-metrics"}


async def test_runner_without_answers_skips_the_answer_section(
    scripted: tuple[_ScriptedPool, _FakeEmbedder], tmp_path: Path
) -> None:
    pool, _ = scripted
    records = load_golden(_golden_file(tmp_path))
    report = await run_eval(records, CFG, pool, k=2)
    assert report.answer == {"skipped": "no golden record carries a reference answer"}


async def test_runner_missing_extra_reports_the_remediation(
    monkeypatch: pytest.MonkeyPatch,
    scripted: tuple[_ScriptedPool, _FakeEmbedder],
    tmp_path: Path,
) -> None:
    pool, _ = scripted

    def _raise(name: str) -> ModuleType:
        raise ModuleNotFoundError(f"No module named '{name}'")

    monkeypatch.setattr("corpus_kb.eval.deepeval_adapter._import_module", _raise)
    records = load_golden(_golden_file(tmp_path, with_answer=True))
    report = await run_eval(records, CFG, pool, k=2)
    assert report.answer == {"skipped": REMEDY}
    assert report.degraded_excluded == {"count": 0, "reasons": []}


async def test_runner_empty_golden_is_the_honest_empty_status(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")

    class _UnusedPool:
        def acquire(self):  # pragma: no cover - must never be reached
            raise AssertionError("an empty golden set must not touch the pool")

    report = await run_eval([], CFG, _UnusedPool(), k=10)
    assert report.status == "empty"
    envelope = to_envelope(report)
    assert envelope["status"] == "empty"


def test_envelope_key_set_is_pinned(
    scripted: tuple[_ScriptedPool, _FakeEmbedder], tmp_path: Path
) -> None:
    """House-style pin: the --json envelope keys are an exact contract."""
    pool, _ = scripted
    import asyncio

    records = load_golden(_golden_file(tmp_path))
    report = asyncio.run(run_eval(records, CFG, pool, k=2, skip_answer_metrics=True))
    payload = to_envelope(report)
    assert set(payload) == {"status", "result", "degraded_excluded"}
    result = payload["result"]
    assert isinstance(result, dict)
    assert set(result) == {"golden", "retrieval", "answer"}
    golden = result["golden"]
    retrieval = result["retrieval"]
    answer = result["answer"]
    degraded = payload["degraded_excluded"]
    assert isinstance(golden, dict) and isinstance(retrieval, dict)
    assert isinstance(answer, dict) and isinstance(degraded, dict)
    assert set(golden) == {"records", "with_answer", "without_answer"}
    assert set(retrieval) == {
        "recall_at_k",
        "mrr",
        "ndcg_at_10",
        "k",
        "n_records",
        "p95_latency_ms",
        "per_record",
    }
    assert set(answer) == {"skipped"}
    assert set(degraded) == {"count", "reasons"}
    row = retrieval["per_record"]
    assert isinstance(row, list) and row
    assert set(row[0]) == {
        "query",
        "relevant_count",
        "retrieved_count",
        "recall_at_k",
        "mrr",
        "ndcg_at_10",
        "latency_ms",
    }


def test_retrieval_settings_default_is_the_pinned_dataclass() -> None:
    """The runner's depth comes from the same settings the bench uses."""
    settings = RetrievalSettings()
    assert settings.vector_candidates > 0
    assert settings.rrf_k > 0


# ============================================================================
# ranked_children: the one rank seam (fused children, before parents)
# ============================================================================


def _children_query() -> ResearchQuery:
    return ResearchQuery(
        query="billing concern",
        tenant_id=UUID(TENANT),
        project_id=UUID(PROJECT),
        k=2,
        include_exchange_children=False,
    )


async def test_ranked_children_returns_fused_children_before_parents(
    scripted_triple: tuple[_ScriptedPool, _FakeEmbedder, _ScriptedConn],
) -> None:
    from corpus_kb.research.retrieval import ranked_children

    pool, embedder, _ = scripted_triple
    rows = await ranked_children(pool, embedder, _children_query())
    assert [str(row["chunk_id"]) for row in rows] == [
        _child_uuid("unit:2"),
        _child_uuid("unit:1"),
    ]


async def test_ranked_children_honors_settings_rrf_k(
    scripted_triple: tuple[_ScriptedPool, _FakeEmbedder, _ScriptedConn],
) -> None:
    from corpus_kb.research.retrieval import ranked_children
    from corpus_kb.research.retrieval_settings import RetrievalSettings

    pool, embedder, conn = scripted_triple
    settings = RetrievalSettings(rrf_k=42)
    await ranked_children(pool, embedder, _children_query(), retrieval_settings=settings)
    assert conn.fusion_args
    assert all(args[3] == 42 for args in conn.fusion_args)


async def test_ranked_children_floors_depth_at_vector_candidates(
    scripted_triple: tuple[_ScriptedPool, _FakeEmbedder, _ScriptedConn],
) -> None:
    from corpus_kb.research.retrieval import ranked_children
    from corpus_kb.research.retrieval_settings import RetrievalSettings

    pool, embedder, conn = scripted_triple
    settings = RetrievalSettings(vector_candidates=300)
    await ranked_children(pool, embedder, _children_query(), retrieval_settings=settings)
    assert conn.fusion_args
    assert all(args[2] == 300 for args in conn.fusion_args)


class _ReversingReranker:
    """Scores the LAST text highest, so a rerank flips the fused order."""

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [float(position) for position in range(len(texts))]


async def test_ranked_children_applies_the_reranker_seam_arg(
    scripted_triple: tuple[_ScriptedPool, _FakeEmbedder, _ScriptedConn],
) -> None:
    from corpus_kb.research.retrieval import ranked_children

    pool, embedder, _ = scripted_triple
    rows = await ranked_children(pool, embedder, _children_query(), reranker=_ReversingReranker())
    assert [str(row["chunk_id"]) for row in rows] == [
        _child_uuid("unit:1"),
        _child_uuid("unit:2"),
    ]
    # Min-max normalized rerank scores land on the children.
    scores = [float(row["score"]) for row in rows]
    assert scores == [pytest.approx(1.0), pytest.approx(0.0)]


async def test_ranked_children_rerank_flag_off_skips_the_reranker(
    scripted_triple: tuple[_ScriptedPool, _FakeEmbedder, _ScriptedConn],
) -> None:
    from corpus_kb.research.retrieval import ResearchQuery, ranked_children

    pool, embedder, _ = scripted_triple
    q = ResearchQuery(
        query="billing concern",
        tenant_id=UUID(TENANT),
        project_id=UUID(PROJECT),
        k=2,
        include_exchange_children=False,
        rerank=False,
    )
    rows = await ranked_children(pool, embedder, q, reranker=_ReversingReranker())
    assert [str(row["chunk_id"]) for row in rows] == [
        _child_uuid("unit:2"),
        _child_uuid("unit:1"),
    ]
