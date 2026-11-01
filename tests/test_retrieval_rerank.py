"""Rerank seam tests (issue 232 follow-up): passthroughs and calibration.

The eval suite drives ``_rerank_children`` end to end through
``ranked_children``; these tests pin the seam contract directly: no scorer
or empty scores mean a plain passthrough, higher scorer output ranks
first, and scores span exactly [0, 1] (min-max calibration).
"""

from __future__ import annotations

from uuid import uuid4

from corpus_kb.research.citations import Citation
from corpus_kb.research.retrieval_rerank import _rerank, _rerank_children


def _citation(seq: int, score: float = 0.5) -> Citation:
    return Citation(
        doc_id=uuid4(),
        doc_title=f"interview-{seq}",
        project_name="billing",
        exchange_seq=seq,
        question_text=f"question {seq}?",
        answer_text=f"answer {seq}",
        answer_highlight=(0, 6),
        matched_kind="answer",
        score=score,
        unit_seqs=(seq,),
    )


def _child_row(seq: int, score: float = 0.5) -> dict[str, object]:
    return {
        "chunk_id": f"chunk-{seq:03d}",
        "text": f"text {seq}",
        "source": "",
        "doc_id": "d",
        "score": score,
    }


class _Scorer:
    """Reranker stand-in: each text scores as its trailing number."""

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [float(text.rsplit(" ", 1)[1]) for text in texts]


class _EmptyScorer:
    """Reranker stand-in that returns no scores (the empty-raw branch)."""

    def score(self, query: str, texts: list[str]) -> list[float]:
        return []


async def test_rerank_passthrough_without_a_callable_scorer() -> None:
    """No ``score`` attribute means the citations pass through untouched."""
    citations = [_citation(1), _citation(2)]
    assert await _rerank("q", citations, object()) is citations


async def test_rerank_orders_citations_and_calibrates_scores() -> None:
    """Higher scorer output ranks first; scores span exactly [0, 1]."""
    citations = [_citation(1), _citation(3), _citation(2)]
    ranked = await _rerank("q", citations, _Scorer())
    assert [c.exchange_seq for c in ranked] == [3, 2, 1]
    assert [c.score for c in ranked] == [1.0, 0.5, 0.0]


async def test_rerank_passthrough_on_empty_scores() -> None:
    """An empty score list never reorders or rescales the citations."""
    citations = [_citation(1), _citation(2)]
    assert await _rerank("q", citations, _EmptyScorer()) is citations


async def test_rerank_children_passthrough_without_a_callable_scorer() -> None:
    """No ``score`` attribute means the fused rows pass through untouched."""
    rows = [_child_row(1), _child_row(2)]
    assert await _rerank_children("q", rows, object()) is rows


async def test_rerank_children_passthrough_on_empty_scores() -> None:
    """An empty score list never fabricates an order or scores."""
    rows = [_child_row(1), _child_row(2)]
    assert await _rerank_children("q", rows, _EmptyScorer()) is rows
