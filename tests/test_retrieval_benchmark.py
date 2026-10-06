"""Retrieval quality benchmark (todo 12 acceptance).

Labeled Q->expected-exchange fixture (52 synthetic pairs): recall@10 AND
MRR of the parent-child stack (search answer/question/qa children, return
the parent exchange) must BOTH be >= the naive+prefix baseline variant
(units only, no exchange-level qa/question children) on the same fixture.

Live embedder required for meaningful vectors: qwen3-embedding (4096-d
native, MRL-sliced to 1024 at the research boundary) via the local Ollama;
marked requires_postgres + requires_ollama so CI (no services) skips. The
reranker is deliberately DISABLED for both arms so the comparison isolates
the chunking strategy.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from research_db import TEST_TENANT

from corpus_kb.handlers.research_handler import get_research_handler
from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.event_reader import EventReader
from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.projections.research_projection import ResearchProjection
from corpus_kb.rag.embedder import OllamaEmbedder
from corpus_kb.research.retrieval import ResearchQuery, research_search

pytestmark = [pytest.mark.requires_postgres, pytest.mark.requires_ollama]

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "research"
TENANT = UUID(TEST_TENANT)
OLLAMA_URL = "http://localhost:11434"
QWEN3_CONFIG = {
    "embedding": {
        "model": "qwen3-embedding:8b-q8_0",
        "base_url": OLLAMA_URL,
        "dimensions": 4096,
        "batch_size": 8,
    }
}


def _ollama_up() -> bool:
    import socket

    try:
        with socket.create_connection(("localhost", 11434), timeout=2):
            return True
    except OSError:
        return False


async def _ingest_fixture(pool) -> UUID:
    from corpus_kb.domain.application import get_app
    from corpus_kb.handlers.research_handler import reset_research_handler

    reset_research_handler()
    app = get_app()
    reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
    embedder = ResearchEmbedder(
        pool, OllamaEmbedder(QWEN3_CONFIG), model_revision="qwen3-8b-mrl1024"
    )
    projection = ResearchProjection(pool, CheckpointManager(pool), DLQHandler(pool), embedder)
    handler = get_research_handler(pool)

    workdir = Path(tempfile.mkdtemp(prefix="task12-benchmark-"))
    target = workdir / "retrieval_fixture.md"
    shutil.copyfile(FIXTURES / "retrieval_fixture.md", target)
    project = uuid4()
    result = await handler.ingest_transcript(TENANT, str(target), project_id=project)
    assert result["status"] == "success"
    assert result["n_exchanges"] == 26
    await projection.catch_up(reader)
    return project


def _metrics(ranks: list[int]) -> tuple[float, float]:
    recall_at_10 = sum(1 for r in ranks if 0 < r <= 10) / len(ranks)
    mrr = sum(1.0 / r for r in ranks if r > 0) / len(ranks)
    return recall_at_10, mrr


async def test_parent_child_recall_and_mrr_beat_naive_prefix_baseline(research_pool):
    if not _ollama_up():
        pytest.skip("Ollama not reachable")
    labels = json.loads((FIXTURES / "retrieval_fixture.labels.json").read_text(encoding="utf-8"))
    pairs = labels["pairs"]
    assert len(pairs) >= 50, "acceptance requires 50+ labeled pairs"
    project = await _ingest_fixture(research_pool)
    embedder = ResearchEmbedder(
        research_pool, OllamaEmbedder(QWEN3_CONFIG), model_revision="qwen3-8b-mrl1024"
    )

    def _rank(hits, expected_seq: int) -> int:
        for position, hit in enumerate(hits, start=1):
            if hit.exchange_seq == expected_seq:
                return position
        return 0

    rows: list[dict[str, object]] = []
    treatment_ranks: list[int] = []
    baseline_ranks: list[int] = []
    for pair in pairs:
        expected = int(pair["expected_exchange_seq"])
        treatment = await research_search(
            research_pool,
            embedder,
            ResearchQuery(
                query=pair["query"],
                tenant_id=TENANT,
                project_id=project,
                k=10,
                rerank=False,
            ),
        )
        baseline = await research_search(
            research_pool,
            embedder,
            ResearchQuery(
                query=pair["query"],
                tenant_id=TENANT,
                project_id=project,
                k=10,
                rerank=False,
                include_exchange_children=False,
            ),
        )
        t_rank, b_rank = _rank(treatment, expected), _rank(baseline, expected)
        treatment_ranks.append(t_rank)
        baseline_ranks.append(b_rank)
        rows.append(
            {
                "query": pair["query"],
                "expected_exchange_seq": expected,
                "treatment_rank": t_rank,
                "baseline_rank": b_rank,
                "fragment": pair["expected_answer_fragment"],
            }
        )

    t_recall, t_mrr = _metrics(treatment_ranks)
    b_recall, b_mrr = _metrics(baseline_ranks)
    report = {
        "fixture": "retrieval_fixture.md",
        "n_pairs": len(pairs),
        "embedder": "qwen3-embedding:8b-q8_0 (MRL 1024)",
        "treatment": {"recall@10": t_recall, "mrr": t_mrr},
        "baseline_naive_prefix": {"recall@10": b_recall, "mrr": b_mrr},
        "delta": {
            "recall@10": t_recall - b_recall,
            "mrr": t_mrr - b_mrr,
        },
        "per_query": rows,
    }
    out = Path(tempfile.gettempdir()) / "task12_retrieval_benchmark.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    d_recall, d_mrr = t_recall - b_recall, t_mrr - b_mrr
    print(f"\nrecall@10  treatment={t_recall:.4f}  baseline={b_recall:.4f}  delta={d_recall:+.4f}")
    print(f"MRR        treatment={t_mrr:.4f}  baseline={b_mrr:.4f}  delta={d_mrr:+.4f}")
    print(f"report: {out}")

    assert t_recall >= b_recall, (
        f"parent-child recall@10 {t_recall:.4f} < naive+prefix baseline {b_recall:.4f}"
    )
    assert t_mrr >= b_mrr, f"parent-child MRR {t_mrr:.4f} < naive+prefix baseline {b_mrr:.4f}"
