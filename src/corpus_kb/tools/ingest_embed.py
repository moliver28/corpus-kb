"""Embedding stage for the ingest pipeline (zero-drift Wave 3B split).

Moved byte-identical from ``ingest_common``: the zero-vector markers, the
single-sourced embedding-input assembly, and the embed stage itself.
``ingest_common`` re-exports the public names — tests patch
``ingest_common.embed_chunks`` and the pipeline resolves it through its
own module namespace.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ..rag import aembed_batch, create_embedder
from ..rag.embedder_selection import (
    POOLED_MODE,
    IngestEmbeddingPlan,
    late_chunking_requested,
    select_ingest_embedder,
)
from ..utils.models import Chunk
from .ingest_cfg import _nested_dict

if TYPE_CHECKING:
    import asyncpg


def _all_zero(vectors: list[list[float]]) -> bool:
    """Return True when every vector is entirely zeros (embedder failure marker)."""
    return all(all(v == 0.0 for v in vector) for vector in vectors)


def _embedding_failure_is_fatal(chunks: list[Chunk]) -> bool:
    """True when the embed output must fail the run rather than degrade it.

    Missing or all-zero embeddings are the embedder-failure marker: they would
    poison the search corpus, so the run records the EmbeddingError (and the
    ledger marks the run failed). A degraded run whose fallback provider
    produced real vectors stays error-free — the degraded flag carries the
    honesty, and the note is reported as ``embed_note`` instead.
    """
    embeddings = [c.embedding for c in chunks if c.embedding is not None]
    if not embeddings:
        return True
    return any(all(v == 0.0 for v in vector) for vector in embeddings)


def build_embedding_input(text: str, context_blurb: str | None) -> str:
    """Single-source the embedding-input assembly (Stage 3).

    Chunks with a contextual blurb embed the ``blurb\n\ntext`` form; plain
    chunks embed raw text. The reembed helper (reembed_degraded) and the
    ingest pipeline MUST produce byte-identical inputs for the same chunk or
    a repair would store a vector for a different text than a fresh ingest
    would.
    """
    return f"{context_blurb}\n\n{text}" if context_blurb else text


def _assign_embeddings(chunks: list[Chunk], vectors: list[list[float]]) -> None:
    for chunk, vector in zip(chunks, vectors, strict=True):
        chunk.embedding = vector


async def embed_chunks(
    chunks: list[Chunk],
    config: dict[str, object],
    pool: asyncpg.Pool | None = None,
    text_override: dict[str, str] | None = None,
    document_text: str | None = None,
    meta: dict[str, object] | None = None,
) -> tuple[bool, str | None]:
    """Embed chunk texts via the configured provider, returning (degraded, error).

    The primary embedder is selected by ``embedding.provider`` (default
    ``"pgml"``). When the primary returns zero-vectors, the
    ``embedding.fallback_provider`` embedder (default ``"ollama"``) is tried
    and its vectors repopulate the chunks. ``degraded`` is True whenever the
    primary failed — even when the fallback produced real vectors — so the
    flag always reflects which embedder actually produced the embeddings.

    On success, each chunk's ``embedding`` field is populated. On failure,
    returns ``(True, "ExceptionType: message")`` so the caller can report
    structured error info. ``text_override``, if provided, is keyed by
    chunk_id (str) and takes precedence over ``chunk.text`` -- used to embed
    the contextualized (blurb + text) form instead of raw chunk text.

    Late chunking (DOCUMENT ingest only): when the activation contract holds
    (``embedding.late_chunking`` + latechunk extra + qwen3-embedding model —
    see ``rag.embedder_selection.select_ingest_embedder``) and
    ``document_text`` is provided, chunks carrying source char spans embed
    via ``embed_document_chunks`` against the document (every chunk vector
    pools token states that saw the whole document); chunks lacking spans
    embed pooled with a per-chunk note. ``meta``, when given, receives
    ``"embedding_mode"`` always and ``"embedding_note"`` whenever a fallback
    note exists, so callers can surface the decision on the run result.
    """
    try:
        if not chunks:
            if meta is not None:
                meta["embedding_mode"] = POOLED_MODE
            return False, None

        if document_text is None:
            plan: IngestEmbeddingPlan | None = None
            mode = POOLED_MODE
            # Honest fallback when late chunking was requested but the caller
            # could not supply the document (e.g. the reembed repair path).
            note = (
                "late_chunking requested but no document text was provided to "
                "embed_chunks; pooled fallback"
                if late_chunking_requested(config)
                else None
            )
        else:
            # Off the loop: the default factory may construct the HF stack on
            # its first call (seconds-to-minutes; memoized per process after).
            plan = await asyncio.to_thread(select_ingest_embedder, config, pool)
            mode = plan.mode
            note = plan.note

        if plan is not None and plan.spans_provider is not None:
            assert document_text is not None  # plan only exists with document text
            # The late path runs synchronous HF/torch forwards; offload to a
            # worker thread so the server event loop (uvicorn + socket share
            # it) never blocks behind a multi-second window forward.
            vectors, span_note, spans_used = await asyncio.to_thread(
                _late_chunk_vectors, plan, chunks, document_text, text_override
            )
            if span_note is not None:
                note = f"{note}; {span_note}" if note else span_note
            if meta is not None:
                # Mode honesty: "late-chunking" only when at least one chunk
                # was actually late-chunked; an all-pooled run with the flag
                # on is reported as pooled (the note carries the reason).
                meta["embedding_mode"] = mode if spans_used > 0 else POOLED_MODE
                if note is not None:
                    meta["embedding_note"] = note
            if vectors and _all_zero(vectors):
                _assign_embeddings(chunks, vectors)
                return True, "Connection failed: LateChunkEmbedder returned zero vectors"
            _assign_embeddings(chunks, vectors)
            return False, None

        if meta is not None:
            meta["embedding_mode"] = mode
            if note is not None:
                meta["embedding_note"] = note

        embedding_cfg = _nested_dict(config, "embedding")
        provider_value = embedding_cfg.get("provider")
        provider = provider_value if isinstance(provider_value, str) else "pgml"

        embedder = plan.embedder if plan is not None else create_embedder(config, pool)
        texts = [(text_override or {}).get(str(chunk.chunk_id), chunk.text) for chunk in chunks]
        vectors = await aembed_batch(embedder, texts)

        if vectors and _all_zero(vectors):
            fallback_value = embedding_cfg.get("fallback_provider")
            fallback_provider = fallback_value if isinstance(fallback_value, str) else "ollama"
            if fallback_provider == provider:
                _assign_embeddings(chunks, vectors)
                return (
                    True,
                    f"Connection failed: {type(embedder).__name__} returned zero vectors",
                )
            fallback_config = dict(config)
            fallback_embedding = dict(embedding_cfg)
            fallback_embedding["provider"] = fallback_provider
            fallback_config["embedding"] = fallback_embedding
            fallback_embedder = create_embedder(fallback_config, pool)
            fallback_vectors = await aembed_batch(fallback_embedder, texts)
            if fallback_vectors and not _all_zero(fallback_vectors):
                _assign_embeddings(chunks, fallback_vectors)
                return (
                    True,
                    f"Connection failed: {provider} returned zero vectors; "
                    f"embeddings produced by fallback {fallback_provider}",
                )
            _assign_embeddings(chunks, vectors)
            return (
                True,
                f"Connection failed: {provider} and fallback "
                f"{fallback_provider} both returned zero vectors",
            )

        _assign_embeddings(chunks, vectors)
        return False, None
    except Exception as exc:
        return True, f"{type(exc).__name__}: {exc}"


def _late_chunk_vectors(
    plan: IngestEmbeddingPlan,
    chunks: list[Chunk],
    document_text: str,
    text_override: dict[str, str] | None,
) -> tuple[list[list[float]], str | None, int]:
    """Late-chunked vectors for span-carrying chunks, pooled for the rest.

    Chunks whose (source_start_char, source_end_char) span is a valid range
    into ``document_text`` embed via the plan's ``embed_document_chunks``
    (true late chunking — the contextualized blurb form is NOT used there,
    since spans point into the raw document). A span is only trusted when
    ``document_text[start:end]`` reproduces the chunk text verbatim — a
    fabricated or drifted span would pool an unrelated region while still
    labeling the vector late-chunked, so it falls back to pooled instead.
    Chunks without a trusted span embed via the plan embedder's
    ``embed_batch`` (single-span forward per text) with a per-chunk note so
    the fallback is never silent. Returns ``(vectors, note, spans_used)``;
    the caller reports the late-chunking mode only when ``spans_used > 0``.
    """
    spans_provider = plan.spans_provider
    assert spans_provider is not None
    late_idx: list[int] = []
    spans: list[tuple[int, int]] = []
    pooled_idx: list[int] = []
    for idx, chunk in enumerate(chunks):
        start, end = chunk.source_start_char, chunk.source_end_char
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start < end <= len(document_text)
            and document_text[start:end] == chunk.text
        ):
            late_idx.append(idx)
            spans.append((start, end))
        else:
            pooled_idx.append(idx)
    vectors: list[list[float]] = [[] for _ in chunks]
    note: str | None = None
    if late_idx:
        chunk_spans = spans_provider(document_text, spans)
        for idx, span in zip(late_idx, chunk_spans, strict=True):
            vectors[idx] = span.vector
    if pooled_idx:
        texts = [
            (text_override or {}).get(str(chunks[idx].chunk_id), chunks[idx].text)
            for idx in pooled_idx
        ]
        pooled = plan.embedder.embed_batch(texts)
        for idx, vector in zip(pooled_idx, pooled, strict=True):
            vectors[idx] = vector
        note = (
            f"late chunking: {len(pooled_idx)}/{len(chunks)} chunks lacked a trusted "
            "source char span; pooled fallback for those"
        )
    return vectors, note, len(late_idx)
