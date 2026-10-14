"""Document-ingest embedder selection: late chunking with honest fallback.

:func:`select_ingest_embedder` implements the activation contract for the
DOCUMENT ingest pipeline (``run_pipeline -> embed_chunks``). Late chunking
activates IFF ALL of:

(a) ``embedding.late_chunking`` is true (default true — the intended
    default technique for document ingest, not an opt-in experiment),
(b) the optional ``latechunk`` extra is installed (sentence-transformers +
    transformers + torch), and
(c) ``embedding.model`` is a token-level-capable family (qwen3-embedding* —
    Ollama's endpoint serves pooled vectors only, so token states must come
    from the local HF stack).

Any failed condition degrades to the existing pooled embedder path with a
``note`` naming the concrete reason — never an exception for a missing
extra, never a silent downgrade. The RESEARCH pipeline is out of scope:
the G1 benchmark chose pooled embeddings there and that decision is
untouched.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

import asyncpg

from corpus_kb.rag.embedder import Embedder, create_embedder
from corpus_kb.rag.embedders import is_latechunk_installed
from corpus_kb.rag.embedders.late_chunk import LateChunkEmbedder, LateChunkSpan

LATE_CHUNKING_MODE = "late-chunking"
POOLED_MODE = "pooled"

# The only token-level-capable embedding family today (Qwen3-Embedding).
TOKEN_LEVEL_MODEL_PREFIX = "qwen3-embedding"

SpansProvider = Callable[[str, list[tuple[int, int]]], list[LateChunkSpan]]
LateChunkFactory = Callable[[], LateChunkEmbedder]


@dataclass
class IngestEmbeddingPlan:
    """The embedding decision for one document-ingest run.

    ``mode`` is ``"late-chunking"`` only when every activation condition
    held; ``note`` is None on happy paths and otherwise names the exact
    reason late chunking was unavailable (surfaced to callers as
    ``result["embedding_note"]``). ``spans_provider`` is set only in late
    mode: a bound ``embed_document_chunks`` turning (document text, chunk
    char spans) into late-chunked vectors.
    """

    mode: str
    embedder: Embedder
    note: str | None = None
    spans_provider: SpansProvider | None = None


def late_chunking_requested(config: dict[str, object]) -> bool:
    """Truthiness read of ``embedding.late_chunking`` (default True).

    Same semantics as the U32 matryoshka gate: a quoted "true" or 1 in a
    hand-edited YAML must request the feature, not slip past ``is True``.
    """
    embedding = config.get("embedding")
    if not isinstance(embedding, dict):
        return True
    return bool(embedding.get("late_chunking", True))


def _construct_late_chunk_embedder() -> LateChunkEmbedder:
    """Constructor seam — tests stub this instead of loading the HF stack.

    The HF stack load (AutoTokenizer + AutoModel + SentenceTransformer) is
    seconds-to-minutes of work, so the built-in constructor memoizes ONE
    instance for the process: directory ingest calls select_ingest_embedder
    per document, and reloading per document would make the flagship batch
    workload the slowest path in the repo. Keyed on the constructor
    defaults; a different model/window/stride needs a distinct embedder and
    should extend the key with those values.
    """
    global _late_embedder_singleton
    if _late_embedder_singleton is None:
        _late_embedder_singleton = LateChunkEmbedder()
    return _late_embedder_singleton


_late_embedder_singleton: LateChunkEmbedder | None = None


def select_ingest_embedder(
    config: dict[str, object],
    pool: asyncpg.Pool | None = None,
    late_chunk_factory: LateChunkFactory | None = None,
) -> IngestEmbeddingPlan:
    """Pick the document-ingest embedder per the activation contract.

    ``late_chunk_factory`` overrides the ``LateChunkEmbedder`` construction
    (test seam). A factory failure (e.g. the HF model cannot be fetched
    offline) is an honest pooled fallback with the reason in ``note`` —
    construction never raises out of this function.
    """
    if not late_chunking_requested(config):
        return IngestEmbeddingPlan(mode=POOLED_MODE, embedder=create_embedder(config, pool))
    embedding = cast(dict[str, object], config.get("embedding", {}))
    model = str(embedding.get("model", "nomic-embed-text"))
    if not is_latechunk_installed():
        return IngestEmbeddingPlan(
            mode=POOLED_MODE,
            embedder=create_embedder(config, pool),
            note=(
                "late_chunking requested but the latechunk extra is not installed. "
                'Pooled fallback. Run: pip install -e ".[latechunk]"'
            ),
        )
    if not model.startswith(TOKEN_LEVEL_MODEL_PREFIX):
        return IngestEmbeddingPlan(
            mode=POOLED_MODE,
            embedder=create_embedder(config, pool),
            note=(
                f"late_chunking requested but embedding model {model!r} is not a "
                f"token-level-capable family. Only {TOKEN_LEVEL_MODEL_PREFIX}* "
                "models qualify. Pooled fallback."
            ),
        )
    factory = (
        late_chunk_factory if late_chunk_factory is not None else _construct_late_chunk_embedder
    )
    try:
        embedder = factory()
    except Exception as exc:
        return IngestEmbeddingPlan(
            mode=POOLED_MODE,
            embedder=create_embedder(config, pool),
            note=(
                "late_chunking requested but LateChunkEmbedder failed to load "
                f"({type(exc).__name__}: {exc}). Pooled fallback."
            ),
        )
    # Dimension coherence: the activated embedder's real output width must
    # match embedding.dimensions — the configured value is what lands in the
    # vector store and the search corpus. A mismatch means ingest would write
    # wrong-width (or mislabeled) vectors; refuse with the remedy instead.
    configured_dims_raw = embedding.get("dimensions", 768)
    configured_dims = (
        configured_dims_raw
        if isinstance(configured_dims_raw, int) and not isinstance(configured_dims_raw, bool)
        else -1
    )
    if configured_dims > 0 and embedder.dimensions != configured_dims:
        return IngestEmbeddingPlan(
            mode=POOLED_MODE,
            embedder=create_embedder(config, pool),
            note=(
                "late_chunking requested but the activated model outputs "
                f"{embedder.dimensions}-dim vectors while embedding.dimensions is "
                f"{configured_dims}; pooled fallback (set embedding.dimensions to "
                f"{embedder.dimensions} to activate late chunking)"
            ),
        )
    return IngestEmbeddingPlan(
        mode=LATE_CHUNKING_MODE,
        embedder=embedder,
        spans_provider=embedder.embed_document_chunks,
    )
