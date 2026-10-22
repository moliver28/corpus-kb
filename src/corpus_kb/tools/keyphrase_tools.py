"""MCP/HTTP/socket/CLI handler for the keyphrases op (AMR program).

One handler per operation, shared by every surface — the
``tools/topics_tools.py`` pattern. :func:`keyphrases_extract` ranks
candidate phrases over the tenant's selected chunks in three variants:

  * ``tfidf`` — sklearn ``TfidfVectorizer`` (n-grams 1-2, ``max_df``
    0.9); a candidate scores the MAX TF-IDF across the selected chunks.
  * ``embedding`` — the same candidates, ranked by cosine to the
    selected corpus centroid (stored chunk vectors first; the configured
    embedder when none are stored).
  * ``llm`` — one temperature-0 qwen3 call; the reply must be a JSON
    list of phrases. The model gives order, never scores: rows carry
    0.0 and the model's ranking order.

LAZY DEPENDENCIES (the P0 rule): sklearn and ollama are imported inside
function bodies only, so an extras-less install imports this module.
Honest outcomes: ``empty`` (no chunks, no candidates, unparseable llm
reply) and ``unavailable`` (unreachable embedder or llm), each with a
reason and one remedy. Floats are rounded to 6 places; ties break on
phrase ascending.
"""

from __future__ import annotations

from typing import Protocol, cast

import asyncpg
import numpy as np

from ..classification import engine as classification_engine
from ..errors import ValidationError
from ..labeling import engine as labeling_engine
from .ingest_common import DEFAULT_TENANT_ID, load_config_or_pass
from .keyphrase_llm import honest_envelope, llm_variant

__all__ = ["SUPPORTED_VARIANTS", "keyphrases_extract"]

SUPPORTED_VARIANTS = ("tfidf", "embedding", "llm")

# Extraction pins (code constants, recorded in params; not knobs).
NGRAM_LOW = 1
NGRAM_HIGH = 2
MAX_DOC_FREQUENCY = 0.9

_EMBED_REASON = "the embedder is unreachable; every vector came back zero."
_EMBED_REMEDY = "Start the embedding service (see docs/ADMIN.md), or use the tfidf variant."

_ROUND = 6


def _validate(variant: str, limit: int) -> int:
    """Typed param checks (validation_error family: 400 / -32602 / exit 1)."""
    if variant not in SUPPORTED_VARIANTS:
        raise ValidationError(
            f"variant {variant!r} is not supported. Supported: {', '.join(SUPPORTED_VARIANTS)}.",
            remediation=f"Use one of the supported variants: {', '.join(SUPPORTED_VARIANTS)}.",
        )
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValidationError(
            f"limit must be a positive int; got {limit!r}.",
            remediation="limit must be a positive whole number.",
        )
    return limit


def _select(
    chunks: list[labeling_engine.StoredChunk], chunk_ids: list[str] | None
) -> list[labeling_engine.StoredChunk]:
    """The selected chunks in read order (deterministic)."""
    if chunk_ids is None:
        return list(chunks)
    wanted = {str(chunk_id) for chunk_id in chunk_ids}
    return [chunk for chunk in chunks if chunk.chunk_id in wanted]


class _SparseColumn(Protocol):
    """The sklearn vectorizer output surface the candidate pass uses."""

    def max(self, axis: int) -> _SparseColumn: ...

    def toarray(self) -> np.ndarray[tuple[int, int], np.dtype[np.float64]]: ...


def _candidate_phrases(texts: list[str]) -> dict[str, float]:
    """TF-IDF candidates: phrase -> max score across texts (thread-pinned)."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    from ..classification.tier_a import thread_pin

    with thread_pin():
        vectorizer = TfidfVectorizer(ngram_range=(NGRAM_LOW, NGRAM_HIGH), max_df=MAX_DOC_FREQUENCY)
        matrix = cast("_SparseColumn", vectorizer.fit_transform(texts))
        phrases = vectorizer.get_feature_names_out()
        scores = matrix.max(axis=0).toarray()[0]
    return {str(phrase): float(score) for phrase, score in zip(phrases, scores, strict=True)}


def _all_zero(vectors: list[list[float]]) -> bool:
    """True when every vector is the degraded zero vector."""
    return all(all(value == 0.0 for value in vector) for vector in vectors) and bool(vectors)


def _centroid(vectors: list[list[float]]) -> list[float]:
    """The L2-normalized mean (a pure direction, scale-free for cosine)."""
    count = float(len(vectors))
    width = len(vectors[0])
    mean = [sum(vector[position] for vector in vectors) / count for position in range(width)]
    norm = sum(value * value for value in mean) ** 0.5
    if norm == 0.0:
        return mean
    return [value / norm for value in mean]


def _cosine(left: list[float], right: list[float]) -> float:
    """Plain cosine; two zero vectors score 0.0."""
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = sum(value * value for value in left) ** 0.5
    norm_right = sum(value * value for value in right) ** 0.5
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return float(dot / (norm_left * norm_right))


async def _embedding_scores(
    pg_pool: asyncpg.Pool,
    tenant_id: str,
    config: dict[str, object] | None,
    chunks: list[labeling_engine.StoredChunk],
    phrases: list[str],
) -> dict[str, object]:
    """Candidate -> cosine to the centroid; an honest unavailable on failure."""
    from ..rag import embedder as embedder_mod

    texts = [chunk.text for chunk in chunks]
    stored = await classification_engine.read_stored_vectors(pg_pool, tenant_id)
    selected = {chunk.chunk_id for chunk in chunks}
    usable = [
        list(vector.vector)
        for vector in stored
        if vector.chunk_id in selected and any(value != 0.0 for value in vector.vector)
    ]
    embedder = embedder_mod.create_embedder(config)
    centroid: list[float]
    if usable:
        widths = {len(vector) for vector in usable}
        if len(widths) > 1:
            raise ValidationError(
                f"mixed embedding dimensions in stored vectors: {sorted(widths)}.",
                remediation="Re-ingest so every chunk carries the configured width.",
            )
        centroid = _centroid(usable)
    else:
        chunk_vectors = await embedder_mod.aembed_batch(embedder, texts)
        if _all_zero(chunk_vectors):
            return honest_envelope(
                "unavailable", _EMBED_REASON, _EMBED_REMEDY, "embedding", len(chunks)
            )
        centroid = _centroid(chunk_vectors)
    phrase_vectors = await embedder_mod.aembed_batch(embedder, phrases)
    if _all_zero(phrase_vectors):
        return honest_envelope(
            "unavailable", _EMBED_REASON, _EMBED_REMEDY, "embedding", len(chunks)
        )
    return {
        phrase: round(_cosine(vector, centroid), _ROUND)
        for phrase, vector in zip(phrases, phrase_vectors, strict=True)
    }


async def keyphrases_extract(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    variant: str = "tfidf",
    limit: int = 20,
    chunk_ids: list[str] | None = None,
) -> dict[str, object]:
    """Rank keyphrase candidates over the selected chunks.

    Returns ``{status, keyphrases, variant, screened_count}`` (rows:
    phrase, score) plus reason/remedy on the honest guard paths and a
    params record on success.
    """
    bounded = _validate(variant, limit)
    cfg = load_config_or_pass(config)
    chunks = _select(await labeling_engine.read_chunks(pg_pool, tenant_id), chunk_ids)
    if not chunks:
        return honest_envelope(
            "empty",
            "no chunks matched the selection (the tenant has no chunks, or none of the "
            "given chunk ids are stored).",
            "Ingest documents first, or pass stored chunk ids.",
            variant,
            0,
        )
    texts = [chunk.text for chunk in chunks]
    if variant == "llm":
        return await llm_variant(texts, bounded, len(chunks))
    candidates = _candidate_phrases(texts)
    if not candidates:
        return honest_envelope(
            "empty",
            "the selected chunks produced no candidate phrases (max_df 0.9 dropped every term).",
            "Select more varied chunks, or lower the shared-term floor by adding chunks.",
            variant,
            len(chunks),
        )
    if variant == "tfidf":
        scored = candidates
    else:
        embedding_scores = await _embedding_scores(
            pg_pool, tenant_id, cfg, chunks, sorted(candidates)
        )
        if "status" in embedding_scores:
            return cast("dict[str, object]", embedding_scores)
        scored = cast("dict[str, float]", embedding_scores)
    rows = [
        {"phrase": phrase, "score": round(float(score), _ROUND)} for phrase, score in scored.items()
    ]
    rows.sort(key=lambda row: (-float(row["score"]), str(row["phrase"])))
    return {
        "status": "success",
        "keyphrases": rows[:bounded],
        "variant": variant,
        "screened_count": len(chunks),
        "params": {
            "ngram_range": [NGRAM_LOW, NGRAM_HIGH],
            "max_df": MAX_DOC_FREQUENCY,
            "candidate_count": len(candidates),
            "chunks_selected": len(chunks),
        },
    }
