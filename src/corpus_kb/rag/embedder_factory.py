"""Embedder selection and the async call bridge (moved from embedder.py).

``create_embedder`` picks the backend from ``embedding.provider``; the
Ollama arm also attaches the persistent SQL cache (issue #539) when a SQL
seam is available, so a cold process skips re-embedding unchanged texts.
``aembed_batch`` is the one await boundary: the pgml backend is async, the
sync backends run in a worker thread so the event loop stays live.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import asyncpg

from corpus_kb.storage.gateway import SqlGateway

from ..config import load_config
from ..errors import ConfigError
from .embedder import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_EMBEDDING_MODEL,
    Embedder,
    OllamaEmbedder,
    PgmlEmbedder,
    _int_or_default,
    _str_or_default,
)
from .embedder_cache import EmbeddingCache

DEFAULT_EMBEDDING_PROVIDER = "ollama"


def provider_label(embedding_cfg: dict[str, Any], key: str = "provider") -> str:
    """The provider name for ``key``; absent or non-str falls to the default."""
    value = embedding_cfg.get(key)
    return value if isinstance(value, str) else DEFAULT_EMBEDDING_PROVIDER


def create_embedder(
    config: dict[str, Any] | None = None,
    pool: asyncpg.Pool | SqlGateway | None = None,
) -> OllamaEmbedder | PgmlEmbedder:
    """Return the embedder selected by ``embedding.provider`` in config.

    - ``"ollama"`` (default): OllamaEmbedder, embedding via a local Ollama
      instance.
    - ``"pgml"``: PgmlEmbedder, embedding in-database via the pool. Without
      a pool it still constructs, but returns zero-vectors.

    Raises ``ValueError`` for any other provider.
    """
    from corpus_kb.storage.connect import pg_pool_of
    from corpus_kb.storage.gateway import as_gateway, is_gateway

    # Issue #539: keep the raw store — the cache attaches to a gateway too
    # (DuckDB), while pgml needs the plain pool (pg_pool_of drops gateways).
    raw_store = pool
    pool = pg_pool_of(pool)
    cfg = config or load_config()
    embedding = cast(dict[str, Any], cfg.get("embedding", {}))
    provider = _str_or_default(embedding, "provider", DEFAULT_EMBEDDING_PROVIDER)
    if provider == "pgml":
        return PgmlEmbedder(cfg, pool)
    if provider == "ollama":
        # Issue #539: with a SQL seam available, attach the persistent
        # cache so a cold process skips re-embedding unchanged texts.
        cache: EmbeddingCache | None = None
        if raw_store is not None:
            gateway = raw_store if is_gateway(raw_store) else as_gateway(raw_store)
            cache = EmbeddingCache(
                gateway,
                _str_or_default(embedding, "model", DEFAULT_EMBEDDING_MODEL),
                _int_or_default(embedding, "dimensions", DEFAULT_EMBEDDING_DIMENSIONS),
            )
        return OllamaEmbedder(cfg, cache=cache)
    raise ConfigError(f"Unknown embedding provider {provider!r}: expected 'pgml' or 'ollama'")


async def aembed_batch(
    embedder: OllamaEmbedder | PgmlEmbedder | Embedder, texts: list[str]
) -> list[list[float]]:
    """Embed ``texts`` with any embedder, awaiting the async pgml path.

    The ``Embedder`` protocol arm (todo 13) lets structural backends such as
    the late-chunking embedder flow through the same research boundary.
    Sync embedders run in a worker thread so the event loop stays live.
    """
    if isinstance(embedder, PgmlEmbedder):
        return await embedder.embed_batch(texts)
    return await asyncio.to_thread(embedder.embed_batch, texts)
