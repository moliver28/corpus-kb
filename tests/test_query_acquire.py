"""The arm-acquisition mechanics: one k selection per post-fusion run.

The behavioral pin keeps both post-fusion stages (RRF fuse, post-fuse)
on the SAME k in both selection cases (explicit ``query.k`` wins; the
``reranking.return_top_k`` default fills in). The source guard keeps the
selection a single expression (PR 280 F1): a second ``resolved_k``
re-computation is a drift trap — a future edit to one line and not the
other silently splits the fuse width from the return width.
"""

from __future__ import annotations

import inspect
from typing import Any

from corpus_kb.domain.models import SearchQuery
from corpus_kb.handlers.query_acquire import ArmRun, post_fusion
from corpus_kb.rag.rerank_settings import RerankSettings
from corpus_kb.research.retrieval_settings import RetrievalSettings
from corpus_kb.storage.search_backend import SearchOutcome


def _run(k: int | None) -> ArmRun:
    return ArmRun(
        query=SearchQuery(query="q", k=k),
        semantic_query="q",
        dynamic_predicates=[],
        degraded_pred="",
        over_retrieve=10,
        reasons=[],
    )


def _outcome() -> SearchOutcome:
    return SearchOutcome(
        vector_rows=[],
        lexical_rows=[],
        excluded_count=0,
        vector_index_mode="exact",
        lexical_index_mode="tsquery",
    )


async def _capture_k(
    monkeypatch: Any,
    run: ArmRun,
    rerank_settings: RerankSettings,
) -> list[int]:
    """Run post_fusion with both stages stubbed; return the k each saw."""
    from corpus_kb.handlers import query_stages

    seen: list[int] = []

    def stub_fuse(outcome: object, k: int, rrf_k: int) -> list[object]:
        del outcome, rrf_k
        seen.append(k)
        return []

    async def stub_post_fuse(*args: object) -> list[object]:
        seen.append(args[6])  # k is the 7th positional of post_fuse
        return []

    monkeypatch.setattr(query_stages, "fuse_arm_rows", stub_fuse)
    monkeypatch.setattr(query_stages, "post_fuse", stub_post_fuse)
    await post_fusion(None, rerank_settings, None, RetrievalSettings(), _outcome(), run)
    return seen


async def test_explicit_query_k_reaches_both_stages(monkeypatch: Any) -> None:
    """An explicit ``query.k`` is the one k for fusion AND the return width."""
    seen = await _capture_k(monkeypatch, _run(3), RerankSettings(return_top_k=9))
    assert seen == [3, 3]


async def test_default_top_k_reaches_both_stages(monkeypatch: Any) -> None:
    """A omitted ``query.k`` falls back to ``return_top_k`` in BOTH stages."""
    seen = await _capture_k(monkeypatch, _run(None), RerankSettings(return_top_k=9))
    assert seen == [9, 9]


def test_post_fusion_selects_k_once() -> None:
    """Single-sourcing guard: one k-selection expression, no ``resolved_k``."""
    from corpus_kb.handlers import query_acquire

    source = inspect.getsource(query_acquire)
    assert "resolved_k" not in source
    assert source.count("rerank_settings.return_top_k") == 1
