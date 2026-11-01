"""The retrieval reranker seam (split from retrieval.py, issue 232).

Both rerank helpers share one scorer contract: the reranker exposes a
callable ``score(query, texts)`` (run off the event loop), raw scores are
min-max calibrated to ``[0, 1]``, and no scorer means a plain passthrough.
The child form breaks ties by chunk id so the order stays deterministic
(zero-drift rule 9).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, cast

from corpus_kb.research.citations import Citation


async def _rerank(query: str, citations: list[Citation], reranker: object) -> list[Citation]:
    score = getattr(reranker, "score", None)
    if not callable(score):
        return citations
    texts = [
        f"{c.question_text}\n{c.answer_text}" if c.question_text else c.answer_text
        for c in citations
    ]
    raw = await asyncio.to_thread(score, query, texts)
    if not raw:
        return citations
    scores = [float(value) for value in cast("list[float]", raw)]
    lo, hi = (min(scores), max(scores)) if scores else (0.0, 1.0)
    span = (hi - lo) or 1.0
    ranked = sorted(zip(citations, scores, strict=True), key=lambda pair: pair[1], reverse=True)
    return [replace(c, score=(s - lo) / span) for c, s in ranked]


async def _rerank_children(
    query: str, rows: list[dict[str, Any]], reranker: object
) -> list[dict[str, Any]]:
    """Rerank fused child rows in place of the parent-level ``_rerank``.

    Same scorer contract and the same min-max calibration as ``_rerank``;
    ties break by chunk id so the order stays deterministic (rule 9).
    """
    score = getattr(reranker, "score", None)
    if not callable(score):
        return rows
    raw = await asyncio.to_thread(score, query, [str(row["text"]) for row in rows])
    if not raw:
        return rows
    scores = [float(value) for value in cast("list[float]", raw)]
    lo, hi = (min(scores), max(scores)) if scores else (0.0, 1.0)
    span = (hi - lo) or 1.0
    ranked = sorted(
        zip(rows, scores, strict=True),
        key=lambda pair: (-pair[1], str(pair[0]["chunk_id"])),
    )
    return [dict(row, score=(s - lo) / span) for row, s in ranked]
