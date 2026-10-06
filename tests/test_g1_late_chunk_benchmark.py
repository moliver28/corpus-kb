"""G1 A/B harness: late chunking vs naive+prefix vs reference windows (todo 13).

Arms (v5 5.3):
  (a) Qwen3-Embedding-0.6B LATE chunking â€” ONE forward pass over the whole
      fixture transcript; per-exchange answer/QA/question spans pooled from
      the token states;
  (b) Qwen3-Embedding-0.6B NAIVE per-text pooling + deterministic context
      prefix (production embed_exchange_texts shape);
  (b-bare) arm (b) with BARE queries (no instruct prefix) â€” the r10
      instruction-prefix ablation;
  (c) nomic via Ollama, same naive+prefix children â€” REFERENCE ONLY (it
      confounds model family with chunking and emits 768 dims, so it can
      never be promoted).

recall@10 / MRR are computed on the returned PARENT exchange (never the
child chunk), per source type. (a) vs (b) is the CAUSAL comparison: same
model, same children taxonomy, same query embeddings â€” only the pooling
strategy differs. The reranker is disabled everywhere.

Also owns the MRL 256-vs-1024 recall-delta artifact (r11: G1 owns the
delta; todos 17/F4 consume it), computed SQL-side against the 016-maintained
embedding_256 column of the fixture rows.

LOCAL EVIDENCE ONLY: CI never installs the latechunk extra (this module
skips), and the run needs live Postgres + Ollama + the HF model download.
"""

from __future__ import annotations

import json
import math
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
from corpus_kb.rag.embedder import OllamaEmbedder, instruct
from corpus_kb.rag.embedders import LateChunkEmbedder, is_latechunk_installed
from corpus_kb.research.chunking import build_context_prefix
from corpus_kb.research.promotion import assert_promotable
from corpus_kb.storage.tenant_conn import tenant_connection

pytestmark = [
    pytest.mark.requires_postgres,
    pytest.mark.requires_ollama,
    pytest.mark.skipif(
        not is_latechunk_installed(), reason="latechunk extra not installed (CI never installs it)"
    ),
]

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "research"
TENANT = UUID(TEST_TENANT)
OLLAMA_URL = "http://localhost:11434"
LATE_MODEL = "Qwen/Qwen3-Embedding-0.6B"
QWEN3_8B_CONFIG = {
    "embedding": {
        "model": "qwen3-embedding:8b-q8_0",
        "base_url": OLLAMA_URL,
        "dimensions": 4096,
        "batch_size": 8,
    }
}
NOMIC_CONFIG = {
    "embedding": {"model": "nomic-embed-text", "base_url": OLLAMA_URL, "dimensions": 768}
}
DOC_PREFIX = build_context_prefix(
    project="g1-benchmark", doc_title="retrieval_fixture", source_type="interview"
)


def _ollama_up() -> bool:
    import socket

    try:
        with socket.create_connection(("localhost", 11434), timeout=2):
            return True
    except OSError:
        return False


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


async def _ingest_fixture(pool) -> UUID:
    from corpus_kb.domain.application import get_app
    from corpus_kb.handlers.research_handler import reset_research_handler

    reset_research_handler()
    app = get_app()
    reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
    embedder = ResearchEmbedder(
        pool, OllamaEmbedder(QWEN3_8B_CONFIG), model_revision="qwen3-8b-mrl1024"
    )
    projection = ResearchProjection(pool, CheckpointManager(pool), DLQHandler(pool), embedder)
    handler = get_research_handler(pool)

    workdir = Path(tempfile.mkdtemp(prefix="task13-g1-"))
    target = workdir / "retrieval_fixture.md"
    shutil.copyfile(FIXTURES / "retrieval_fixture.md", target)
    project = uuid4()
    result = await handler.ingest_transcript(TENANT, str(target), project_id=project)
    assert result["status"] == "success"
    assert result["n_exchanges"] == 26
    await projection.catch_up(reader)
    return project


async def _load_exchanges(pool, project: UUID) -> list[dict[str, object]]:
    async with tenant_connection(pool, TENANT) as conn:
        rows = await conn.fetch(
            """
            SELECT e.seq, e.question_text, e.qa_text, e.a_unit_ids, d.source_type
            FROM research_exchanges e
            JOIN documents d ON d.doc_id = e.doc_id
            WHERE e.tenant_id = $1 AND d.project_id = $2
            ORDER BY e.seq
            """,
            str(TENANT),
            str(project),
        )
        unit_ids = [int(u) for row in rows for u in list(row["a_unit_ids"])]
        unit_rows = await conn.fetch(
            "SELECT unit_id, seq, text FROM research_units WHERE unit_id = ANY($1::bigint[])",
            unit_ids,
        )
    texts_by_id = {int(r["unit_id"]): (int(r["seq"]), str(r["text"])) for r in unit_rows}
    exchanges: list[dict[str, object]] = []
    for row in rows:
        answers = sorted(
            (texts_by_id[int(u)] for u in row["a_unit_ids"] if int(u) in texts_by_id),
            key=lambda pair: pair[0],
        )
        exchanges.append(
            {
                "seq": int(row["seq"]),
                "question": str(row["question_text"] or ""),
                "qa": str(row["qa_text"] or ""),
                "answer": " ".join(text for _, text in answers),
                "source_type": str(row["source_type"] or "unknown"),
            }
        )
    return exchanges


def _rank(scores: dict[int, float], expected: int) -> int:
    ordered = sorted(scores, key=lambda seq: scores[seq], reverse=True)
    for position, seq in enumerate(ordered, start=1):
        if seq == expected:
            return position
    return 0


def _metrics(ranks: list[int]) -> tuple[float, float]:
    recall = sum(1 for r in ranks if 0 < r <= 10) / len(ranks)
    mrr = sum(1.0 / r for r in ranks if r > 0) / len(ranks)
    return recall, mrr


def _score_children(
    children_by_seq: dict[int, list[list[float]]], query: list[float]
) -> dict[int, float]:
    return {
        seq: max(_cosine(child, query) for child in children)
        for seq, children in children_by_seq.items()
    }


def _per_source_type(
    pairs: list[dict[str, object]],
    ranks: list[int],
    type_by_seq: dict[int, str],
) -> dict[str, dict[str, float]]:
    by_type: dict[str, list[int]] = {}
    for pair, rank in zip(pairs, ranks, strict=True):
        source = type_by_seq[int(pair["expected_exchange_seq"])]
        by_type.setdefault(source, []).append(rank)
    return {
        source: {"recall_at_10": m[0], "mrr": m[1], "n": float(len(rs))}
        for source, rs in by_type.items()
        for m in [_metrics(rs)]
    }


async def test_g1_late_vs_naive_prefix_with_ablation_and_mrl_delta(research_pool):
    if not _ollama_up():
        pytest.skip("Ollama not reachable")
    labels = json.loads((FIXTURES / "retrieval_fixture.labels.json").read_text(encoding="utf-8"))
    pairs = labels["pairs"]
    assert len(pairs) >= 50, "acceptance requires 50+ labeled pairs"

    project = await _ingest_fixture(research_pool)
    exchanges = await _load_exchanges(research_pool, project)
    assert len(exchanges) == 26
    seqs = [int(row["seq"]) for row in exchanges]
    type_by_seq = {int(row["seq"]): str(row["source_type"]) for row in exchanges}

    late = LateChunkEmbedder(LATE_MODEL, device="cpu")

    # Arm (a): late chunking â€” ONE forward pass over the whole transcript.
    turns: list[str] = [DOC_PREFIX]
    spans: list[tuple[int, int]] = []
    for row in exchanges:
        q = f"Q: {row['question']}"
        a = f"A: {row['answer']}"
        q_lo = len(turns)
        turns.extend([q, a])
        spans.extend([(q_lo, q_lo + 1), (q_lo, q_lo + 2), (q_lo + 1, q_lo + 2)])
    span_vectors = late.embed_spans(turns, spans)
    assert late.forward_calls == 1, "late chunking must embed the fixture in ONE forward pass"
    late_children = {seqs[i]: span_vectors[3 * i : 3 * i + 3] for i in range(len(exchanges))}
    late_query_vecs = late.encode_pooled([instruct(p["query"]) for p in pairs])

    # Arm (b): naive per-text pooling + deterministic prefix; same queries.
    naive_children: dict[int, list[list[float]]] = {}
    for row in exchanges:
        naive_children[int(row["seq"])] = late.encode_pooled(
            [
                f"{DOC_PREFIX}\nA: {row['answer']}",
                f"{DOC_PREFIX}\n{row['qa']}",
                f"{DOC_PREFIX}\nQ: {row['question']}",
            ]
        )

    # Arm (c): reference only â€” nomic (768d) naive+prefix via Ollama.
    nomic = OllamaEmbedder(NOMIC_CONFIG)
    nomic_texts: list[str] = []
    for row in exchanges:
        nomic_texts.extend(
            [
                f"{DOC_PREFIX}\nA: {row['answer']}",
                f"{DOC_PREFIX}\n{row['qa']}",
                f"{DOC_PREFIX}\nQ: {row['question']}",
            ]
        )
    nomic_vecs = nomic.embed_batch(nomic_texts)
    nomic_children = {seqs[i]: nomic_vecs[3 * i : 3 * i + 3] for i in range(len(exchanges))}
    nomic_query_vecs = nomic.embed_batch([nomic.instruct(p["query"]) for p in pairs])

    late_ranks: list[int] = []
    naive_ranks: list[int] = []
    bare_ranks: list[int] = []
    nomic_ranks: list[int] = []
    per_query: list[dict[str, object]] = []
    for pair, lq, nq in zip(pairs, late_query_vecs, nomic_query_vecs, strict=True):
        expected = int(pair["expected_exchange_seq"])
        bare_query = late.encode_pooled([pair["query"]])[0]
        r_late = _rank(_score_children(late_children, lq), expected)
        r_naive = _rank(_score_children(naive_children, lq), expected)
        r_bare = _rank(_score_children(naive_children, bare_query), expected)
        r_nomic = _rank(_score_children(nomic_children, nq), expected)
        late_ranks.append(r_late)
        naive_ranks.append(r_naive)
        bare_ranks.append(r_bare)
        nomic_ranks.append(r_nomic)
        per_query.append(
            {
                "query": pair["query"],
                "expected_exchange_seq": expected,
                "late_rank": r_late,
                "naive_rank": r_naive,
                "bare_rank": r_bare,
                "nomic_rank": r_nomic,
            }
        )

    arms = {
        "late_chunk": {
            "recall_at_10": _metrics(late_ranks)[0],
            "mrr": _metrics(late_ranks)[1],
            "dimensions": late.dimensions,
            "model": LATE_MODEL,
            "per_source_type": _per_source_type(pairs, late_ranks, type_by_seq),
        },
        "naive_prefix": {
            "recall_at_10": _metrics(naive_ranks)[0],
            "mrr": _metrics(naive_ranks)[1],
            "dimensions": late.dimensions,
            "model": LATE_MODEL,
            "per_source_type": _per_source_type(pairs, naive_ranks, type_by_seq),
        },
        "naive_prefix_bare_queries": {
            "recall_at_10": _metrics(bare_ranks)[0],
            "mrr": _metrics(bare_ranks)[1],
            "dimensions": late.dimensions,
            "model": LATE_MODEL,
            "per_source_type": _per_source_type(pairs, bare_ranks, type_by_seq),
        },
        "reference_nomic_windows": {
            "recall_at_10": _metrics(nomic_ranks)[0],
            "mrr": _metrics(nomic_ranks)[1],
            "dimensions": nomic.dimensions,
            "model": "nomic-embed-text",
            "role": "reference-only (confounds model family with chunking; non-promotable)",
            "per_source_type": _per_source_type(pairs, nomic_ranks, type_by_seq),
        },
    }

    # MRL 256-vs-1024 delta (r11): SQL-side against the 016-maintained
    # embedding_256 column, using the stored qwen3-8b fixture vectors.
    raw8b = OllamaEmbedder(QWEN3_8B_CONFIG)
    ranks_1024: list[int] = []
    ranks_256: list[int] = []
    async with tenant_connection(research_pool, TENANT) as conn:
        for pair in pairs:
            qv = raw8b.embed_matryoshka(raw8b.instruct(pair["query"]), 1024)
            for target, ranks in (("1024", ranks_1024), ("256", ranks_256)):
                if target == "1024":
                    order = "e.embedding::halfvec(1024) <=> $3::halfvec(1024)"
                else:
                    order = "e.embedding_256 <=> l2_normalize(subvector($3::vector, 1, 256))"
                rows = await conn.fetch(
                    f"""
                    SELECT e.seq FROM research_exchanges e
                    JOIN documents d ON d.doc_id = e.doc_id
                    WHERE e.tenant_id = $1 AND d.project_id = $2
                      AND e.embedding IS NOT NULL
                    ORDER BY {order}
                    LIMIT 10
                    """,
                    str(TENANT),
                    str(project),
                    str(qv),
                )
                found = 0
                for position, row in enumerate(rows, start=1):
                    if int(row["seq"]) == int(pair["expected_exchange_seq"]):
                        found = position
                        break
                ranks.append(found)

    recall_1024 = _metrics(ranks_1024)[0]
    recall_256 = _metrics(ranks_256)[0]

    winner_name, winner_arm = max(
        (("late_chunk", arms["late_chunk"]), ("naive_prefix", arms["naive_prefix"])),
        key=lambda item: (item[1]["recall_at_10"], item[1]["mrr"]),
    )
    halted = False
    try:
        assert_promotable(int(winner_arm["dimensions"]))
    except Exception as exc:  # r5: a non-1024 winner HALTS promotion
        halted = True
        winner_arm["promotion_error"] = str(exc)

    report = {
        "gate": "G1",
        "fixture": "tests/fixtures/research/retrieval_fixture.md",
        "n_pairs": len(pairs),
        "metric_grain": "returned PARENT exchange (small-to-big), reranker disabled",
        "arms": arms,
        "causal_comparison": {
            "late_minus_naive": {
                "recall_at_10": arms["late_chunk"]["recall_at_10"]
                - arms["naive_prefix"]["recall_at_10"],
                "mrr": arms["late_chunk"]["mrr"] - arms["naive_prefix"]["mrr"],
            }
        },
        "prefix_ablation": {
            "prefix_minus_bare": {
                "recall_at_10": arms["naive_prefix"]["recall_at_10"]
                - arms["naive_prefix_bare_queries"]["recall_at_10"],
                "mrr": arms["naive_prefix"]["mrr"] - arms["naive_prefix_bare_queries"]["mrr"],
            }
        },
        "mrl_recall_delta": {
            "recall_at_10_1024": recall_1024,
            "recall_at_10_256": recall_256,
            "delta_256_minus_1024": recall_256 - recall_1024,
            "path": "SQL-side embedding_256 (016) vs embedding::halfvec(1024), qwen3-8b rows",
        },
        "winner": {
            "arm": winner_name,
            "promotable": not halted,
            "halted": halted,
            "forward_passes_late_arm": late.forward_calls,
        },
        "per_query": per_query,
    }
    out = Path(tempfile.gettempdir()) / "task13_g1_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k != "per_query"}, indent=2))
    print(f"report: {out}")

    assert not halted, "G1 winner must be 1024-promotable on this fixture"
    assert recall_1024 >= 0.5, "MRL delta needs a sane 1024-d reference arm"
    delta = arms["naive_prefix"]["recall_at_10"] - arms["naive_prefix_bare_queries"]["recall_at_10"]
    assert delta > -0.05, "instruction prefix must not catastrophically hurt retrieval (r10)"
