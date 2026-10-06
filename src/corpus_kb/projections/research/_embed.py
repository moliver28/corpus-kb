"""Cached 1024-dim embedding boundary for the research read models.

Contract (todo-11 (c) vector strategy, decision D6):
  * research vectors are EXACTLY 1024 dims — native, or MRL slice-normalized
    from >=1024 (migration-010 `_slice_normalize` precedent). A below-1024
    embedder (nomic 768) ABSTAINS: NULL vector + cache-miss, never padded.
  * every embed goes through the write-once embedding_cache keyed
    (tenant_id, content_sha256, model, model_revision); a cache hit makes
    ZERO embedder calls (rebuild asserts this with a call counter).
  * zero/non-unit vectors are REJECTED — a degraded embed call must never
    poison the write-once cache (r8 CACHE INTEGRITY).
"""

from __future__ import annotations

import logging
import math
from typing import Any

import asyncpg

from corpus_kb.rag.embedder import OllamaEmbedder, PgmlEmbedder, aembed_batch, assert_unit_or_zero
from corpus_kb.storage.tenant_conn import tenant_connection

logger = logging.getLogger(__name__)

RESEARCH_DIMENSIONS = 1024


class ResearchEmbedder:
    """embedding_cache-backed 1024-dim embedder for the research projectors."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        embedder: OllamaEmbedder | PgmlEmbedder | Any,
        model_revision: str = "1024",
    ) -> None:
        self._pool = pool
        self._embedder = embedder
        self.model = str(getattr(embedder, "model", "unknown"))
        self.model_revision = model_revision

    async def embed_cached(self, tenant_id: Any, text: str) -> list[float] | None:
        """Return a 1024-dim unit vector for text, or None to abstain.

        None means: below-1024 embedder, degraded zero-vector, or non-unit
        output — callers store NULL and may retry later; the cache stays
        clean either way.
        """
        import hashlib

        content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cached = await self._cache_get(tenant_id, content_sha)
        if cached is not None:
            return cached

        vector = await self._embed_1024(text)
        if vector is None:
            return None
        await self._cache_put(tenant_id, content_sha, vector)
        return vector

    async def _cache_get(self, tenant_id: Any, content_sha: str) -> list[float] | None:
        async with tenant_connection(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT embedding FROM embedding_cache
                WHERE tenant_id = $1 AND content_sha256 = $2
                  AND model = $3 AND model_revision = $4
                """,
                str(tenant_id),
                content_sha,
                self.model,
                self.model_revision,
            )
        if row is None:
            return None
        return _parse_pg_vector(row["embedding"])

    async def _cache_put(self, tenant_id: Any, content_sha: str, vector: list[float]) -> None:
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO embedding_cache
                (tenant_id, content_sha256, model, model_revision, dim, embedding)
                VALUES ($1, $2, $3, $4, $5, $6::vector)
                ON CONFLICT (tenant_id, content_sha256, model, model_revision) DO NOTHING
                """,
                str(tenant_id),
                content_sha,
                self.model,
                self.model_revision,
                RESEARCH_DIMENSIONS,
                _to_pg_vector(vector),
            )

    async def _embed_1024(self, text: str) -> list[float] | None:
        raw = (await aembed_batch(self._embedder, [text]))[0]
        if not raw or all(v == 0.0 for v in raw):
            logger.warning("research embed degraded (zero vector); abstaining, not caching")
            return None
        if len(raw) < RESEARCH_DIMENSIONS:
            logger.warning(
                "research embedder %s emits %d dims < %d; abstaining (never pad)",
                self.model,
                len(raw),
                RESEARCH_DIMENSIONS,
            )
            return None
        sliced = _slice_normalize(raw, RESEARCH_DIMENSIONS)
        try:
            assert_unit_or_zero(sliced)
        except ValueError:
            logger.warning("research embed non-unit output; abstaining")
            return None
        return sliced


def _parse_pg_vector(value: Any) -> list[float]:
    """asyncpg returns pgvector columns as their text form ('[a,b,...]')."""
    if isinstance(value, str):
        return [float(part) for part in value.strip("[]").split(",") if part]
    return [float(v) for v in value]


def _slice_normalize(vec: list[float], dim: int) -> list[float]:
    sliced = vec[:dim]
    norm = math.sqrt(sum(x * x for x in sliced))
    if norm == 0.0:
        return sliced
    return [x / norm for x in sliced]


def _to_pg_vector(vec: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.9g}" for v in vec) + "]"


async def apply_unit_embeddings(
    pool: asyncpg.Pool,
    embedder: ResearchEmbedder,
    tenant_id: Any,
    doc_id: Any,
    texts_by_sha: dict[str, str],
    prefixes_by_sha: dict[str, str] | None = None,
) -> None:
    """Embed unit texts through the cache and maintain embedding_256.

    The embedded string is the DETERMINISTIC context prefix + clean text
    (todo-12, v5 5.2 contextual retrieval); the stored text column stays
    clean for generation and citation. The prefix is a pure function of
    row context, so rebuilds hit the same cache entries and make zero
    embedder calls beyond misses.
    """
    prefixes = prefixes_by_sha or {}
    for text_sha, text in texts_by_sha.items():
        embed_text = f"{prefixes[text_sha]}\n{text}" if prefixes.get(text_sha) else text
        vector = await embedder.embed_cached(tenant_id, embed_text)
        if vector is None:
            continue
        async with tenant_connection(pool, tenant_id) as conn:
            await conn.execute(
                """
                UPDATE research_units SET
                    embedding = $3::vector,
                    embedding_256 = l2_normalize(subvector($3::vector, 1, 256)),
                    embedding_model = $4, model_revision = $5, dimensions = $6
                WHERE tenant_id = $1 AND doc_id = $2 AND text_sha256 = $7
                """,
                str(tenant_id),
                str(doc_id),
                "[" + ",".join(f"{v:.9g}" for v in vector) + "]",
                embedder.model,
                embedder.model_revision,
                1024,
                text_sha,
            )


async def embed_exchange_texts(
    pool: asyncpg.Pool,
    embedder: ResearchEmbedder,
    tenant_id: Any,
    doc_id: Any,
    texts_by_exchange_id: dict[int, str],
) -> int:
    """Embed exchange qa texts (prefix + qa_text) into research_exchanges.

    The exchange is the interview RETRIEVAL parent and the qa CHILD source:
    its embedding indexes the combined "Q: ... A: ..." text so queries
    matching either side of the exchange retrieve it. Cache-backed like the
    unit path; returns the number of exchange vectors written.
    """
    written = 0
    for exchange_id, embed_text in texts_by_exchange_id.items():
        vector = await embedder.embed_cached(tenant_id, embed_text)
        if vector is None:
            continue
        async with tenant_connection(pool, tenant_id) as conn:
            result = await conn.execute(
                """
                UPDATE research_exchanges SET
                    embedding = $3::vector,
                    embedding_256 = l2_normalize(subvector($3::vector, 1, 256)),
                    embedding_model = $4, model_revision = $5, dimensions = $6
                WHERE tenant_id = $1 AND doc_id = $2 AND exchange_id = $7
                """,
                str(tenant_id),
                str(doc_id),
                "[" + ",".join(f"{v:.9g}" for v in vector) + "]",
                embedder.model,
                embedder.model_revision,
                1024,
                int(exchange_id),
            )
        written += 0 if result.endswith("0") else 1
    return written
