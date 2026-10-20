"""WP-0 perf fixture: the 100k synthetic-chunk topics build, staged.

NOT part of the default suite: the test is ``slow``-marked AND env-gated
(``CORPUS_KB_PERF_RUN=1``), so a plain ``pytest`` run skips it. Run it
once per machine to publish the measured per-stage wall clock:

    CORPUS_KB_PERF_RUN=1 pytest tests/test_topics_scale_perf.py -m slow -s -q

What it measures, through the REAL engine path (real bertopic + umap +
hdbscan when installed; ``importorskip`` otherwise):

- read   (simulated): the reader's CPU work without SQL — the halfvec
  text parse plus the frozen chunk assembly for 100k rows;
- fit:    ``fit_transform`` on the seeded 20k sample;
- transform: the remaining 80k assigned through UMAP transform +
  hdbscan approximate_predict, one call;
- write  (simulated): the writer's Python-side serialization without
  SQL — topic keywords/params json plus the 100k assignment rows.

The SQL legs need a live Postgres with an ingested corpus; the fixture
stands in for them with their Python-side cost and says so. Acceptance
(issue #137 WP-0): the whole build finishes inside 2 hours.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import numpy as np
import pytest

from corpus_kb.topics import engine
from corpus_kb.topics.engine import StoredChunk
from corpus_kb.topics.settings import TopicsSettings

PERF_CHUNKS = 100_000
PERF_DIM = 64
PERF_CLUSTERS = 10
SYNTH_SEED = 7

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("CORPUS_KB_PERF_RUN") != "1",
        reason="perf fixture: set CORPUS_KB_PERF_RUN=1 (skipped by default)",
    ),
]


def _timed_module() -> Any:
    """Wrap the real bertopic module so fit/transform stages time apart."""
    real = pytest.importorskip("bertopic")
    stages: dict[str, float] = {}

    class _TimedModel:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def fit_transform(self, documents: list[str], embeddings: Any = None) -> Any:
            start = time.perf_counter()
            out = self._inner.fit_transform(documents, embeddings=embeddings)
            stages["fit"] = time.perf_counter() - start
            return out

        def transform(self, documents: list[str], embeddings: Any = None) -> Any:
            start = time.perf_counter()
            out = self._inner.transform(documents, embeddings=embeddings)
            stages["transform"] = time.perf_counter() - start
            return out

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    class _TimedModule:
        def BERTopic(self, **kwargs: Any) -> _TimedModel:  # noqa: N802
            return _TimedModel(real.BERTopic(**kwargs))

    return _TimedModule(), stages


def _synthetic_chunks() -> list[StoredChunk]:
    """100k vectors in 10 seeded clusters (the engine is dim-agnostic)."""
    rng = np.random.default_rng(SYNTH_SEED)
    centers = rng.uniform(-1.0, 1.0, size=(PERF_CLUSTERS, PERF_DIM)).astype(np.float32)
    labels = rng.integers(0, PERF_CLUSTERS, size=PERF_CHUNKS)
    vectors = centers[labels] + rng.normal(0.0, 0.05, size=(PERF_CHUNKS, PERF_DIM)).astype(
        np.float32
    )
    texts = [f"cluster {int(label)} document {i}" for i, label in enumerate(labels)]
    vector_texts = ["[" + ",".join(f"{value:.4f}" for value in row) + "]" for row in vectors]
    start = time.perf_counter()
    chunks = [
        StoredChunk(
            chunk_id=f"perf-{i}",
            text=texts[i],
            vector=engine._parse_halfvec_text(vector_texts[i]),
            doc_id=f"doc-{i % (PERF_CHUNKS // 5)}",
        )
        for i in range(PERF_CHUNKS)
    ]
    print(f"[perf] read (simulated: halfvec parse + assembly): {time.perf_counter() - start:.1f}s")
    return chunks


def _simulated_write(result: Any) -> None:
    """The writer's Python side: json rows for topics + every assignment."""
    start = time.perf_counter()
    topic_payloads = [
        json.dumps(
            {
                "topic_index": topic.topic_index,
                "label": topic.label,
                "size": topic.size,
                "keywords": topic.keywords,
                "params": result.params,
            }
        )
        for topic in result.topics
    ]
    assignment_payloads = [
        (chunk_id, topic_index, probability)
        for chunk_id, topic_index, probability in result.assignments
    ]
    print(
        f"[perf] write (simulated: {len(topic_payloads)} topic rows + "
        f"{len(assignment_payloads)} assignment rows): {time.perf_counter() - start:.1f}s"
    )


async def test_topics_build_100k_stage_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The engine path end-to-end at 100k, each stage timed, under 2 hours."""
    pytest.importorskip("umap")  # the deterministic reducer behind the fit
    module, stages = _timed_module()
    monkeypatch.setattr(engine, "load_bertopic", lambda: module)
    # load_umap stays untouched: the real deterministic UMAP runs.

    chunks = _synthetic_chunks()
    assert len(chunks) == PERF_CHUNKS

    settings = TopicsSettings(enabled=True, llm_reduction=False, min_topic_size=500)
    build_start = time.perf_counter()
    result, _model = engine.build_topics_with_model(chunks, settings)
    total = time.perf_counter() - build_start

    _simulated_write(result)
    fit_s = stages.get("fit", 0.0)
    transform_s = stages.get("transform", 0.0)
    print(f"[perf] fit (20k sample): {fit_s:.1f}s")
    print(f"[perf] transform (80k remainder): {transform_s:.1f}s")
    print(f"[perf] build total (fit+transform): {total:.1f}s")
    print(
        f"[perf] topics={len(result.topics)} outliers={result.outlier_count} "
        f"assignments={len(result.assignments)}"
    )

    assert result.params["assignment_mode"] == "fit+transform"
    assert result.params["fit_sample_size"] == 20_000
    assert result.params["total_chunks"] == PERF_CHUNKS
    assert len(result.doc_topics) == PERF_CHUNKS
    assert len(result.topics) >= 1
    # The WP-0 acceptance (issue #137): 100k completes inside 2 hours.
    assert total < 7200.0
