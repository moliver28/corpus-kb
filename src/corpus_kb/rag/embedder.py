"""Embedders for Corpus-KB.

OllamaEmbedder: calls a local Ollama instance, caching results in memory.
PgmlEmbedder: calls PostgresML for in-database embeddings.
FakeEmbedder: deterministic embedder for CI / degraded mode.

On connection failure all embedders log a warning and return zero-vectors
so callers can continue operating in degraded mode.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
from collections import OrderedDict
from typing import Protocol, cast, runtime_checkable

import asyncpg
import httpx
from ollama import Client
from ollama._types import EmbedResponse

from ..config import load_config

logger = logging.getLogger(__name__)

MAX_CACHE_SIZE = 10_000

# Boundary normalization tolerance (todo 13, r7): every research vector must
# be L2-normalized where the embedder emits it; zero vectors (degraded mode)
# are exempt so the house no-Ollama fallback keeps passing.
UNIT_NORM_TOLERANCE = 0.02

# Query-side instruction for instruction-tuned embedding models (e.g.
# Qwen3-Embedding). Applied to QUERIES only — documents are embedded raw.
# Todo 13's G1 ablation tunes the wording here, not at call sites.
QUERY_INSTRUCTION_PREFIX = (
    "Instruct: Given a qualitative research query, retrieve relevant interview exchanges\nQuery: "
)


def instruct(text: str) -> str:
    """Prefix a query with the instruction-tuned embedding instruction.

    Instruction-tuned embedding models expect the instruction on the QUERY
    side only; instructing documents too degrades retrieval.
    """
    return QUERY_INSTRUCTION_PREFIX + text


@runtime_checkable
class Embedder(Protocol):
    """Structural interface for embedder backends.

    Backends satisfy this without inheriting (structural typing): any object
    with ``dimensions``, ``embed``, ``embed_batch``, and ``instruct`` is an
    ``Embedder``. The async PgmlEmbedder is bridged at call sites via
    ``aembed_batch`` and is not required to provide ``instruct``.
    """

    dimensions: int

    def embed(self, text: str) -> list[float]:
        """Return a single embedding vector for ``text``."""

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for ``texts``, in input order."""

    def instruct(self, text: str) -> str:
        """Return the query-side instructed text (see module-level instruct)."""


class OllamaEmbedder:
    """Embedder that calls a local Ollama instance, caching results in memory.

    On connection failure the embedder logs a warning and returns zero-vectors
    so callers can continue operating in degraded mode.
    """

    def __init__(self, config: dict[str, object] | None = None) -> None:
        self._config = config or load_config()
        embedding = cast(dict[str, object], self._config.get("embedding", {}))

        self.model = _str_or_default(embedding, "model", "nomic-embed-text")
        self.base_url = _str_or_default(embedding, "base_url", "http://localhost:11434")
        self.batch_size = _int_or_default(embedding, "batch_size", 32)
        self.dimensions = _int_or_default(embedding, "dimensions", 768)

        self._client = Client(host=self.base_url)
        self._cache: OrderedDict[str, list[float]] = OrderedDict()

    def embed(self, text: str) -> list[float]:
        """Return a single embedding vector for ``text``."""
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for ``texts``, using the cache and batching."""
        if not texts:
            return []

        keys = [_sha256_key(text) for text in texts]
        results: list[list[float]] = [[] for _ in texts]
        missing: list[tuple[int, str]] = []

        for idx, key in enumerate(keys):
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                results[idx] = cached
            else:
                missing.append((idx, texts[idx]))

        if not missing:
            return results

        missing_texts = [text for _, text in missing]
        batch_size = max(1, self.batch_size)
        batched_results: list[list[float]] = []

        for start in range(0, len(missing_texts), batch_size):
            batch = missing_texts[start : start + batch_size]
            batched_results.extend(self._embed_batch(batch))

        for (idx, text), vector in zip(missing, batched_results, strict=True):
            key = _sha256_key(text)
            self._cache[key] = vector
            self._cache.move_to_end(key)
            if len(self._cache) > MAX_CACHE_SIZE:
                self._cache.popitem(last=False)
            results[idx] = vector

        return results

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            response: EmbedResponse = self._client.embed(
                model=self.model,
                input=texts,
            )
        except (ConnectionError, OSError, httpx.NetworkError):
            logger.warning(
                "Ollama connection failed at %s; returning zero vectors.",
                self.base_url,
            )
            return [[0.0] * self.dimensions for _ in texts]

        return [list(vector) for vector in response.embeddings]

    def embed_matryoshka(self, text: str, dim: int) -> list[float]:
        """Matryoshka-truncated embedding: reuses embed(), no extra network call."""
        return _slice_normalize(self.embed(text), dim)

    def embed_batch_matryoshka(self, texts: list[str], dim: int) -> list[list[float]]:
        return [_slice_normalize(v, dim) for v in self.embed_batch(texts)]

    def instruct(self, text: str) -> str:
        """Return the query-side instructed text (see module-level instruct)."""
        return instruct(text)


def _sha256_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def l2_normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector at the embedder boundary.

    A ZERO vector (the degraded-mode signal from a failed embed call) stays
    all-zero rather than dividing by zero — house zero-vector fallback
    contract (todo 13, r7/r8).
    """
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def assert_unit_or_zero(vec: list[float]) -> None:
    """Unit-norm boundary assert that NO-OPS on zero vectors.

    Degraded/no-Ollama runs emit zero vectors and must skip-pass (house
    contract); any other non-unit output is a boundary bug and raises.
    """
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return
    if abs(norm - 1.0) > UNIT_NORM_TOLERANCE:
        raise ValueError(f"expected unit-norm vector, got norm {norm:.6f}")


def _slice_normalize(vec: list[float], dim: int) -> list[float]:
    """Front-slice to `dim` dimensions and L2-renormalize (matryoshka truncation).

    A zero vector (the degraded-mode signal from a failed embed call) stays
    all-zero rather than dividing by zero.
    """
    sliced = vec[:dim]
    norm = sum(x * x for x in sliced) ** 0.5
    if norm == 0.0:
        return sliced
    return [x / norm for x in sliced]


def _str_or_default(config: dict[str, object], key: str, default: str) -> str:
    value = config.get(key, default)
    return str(value) if value is not None else default


def _int_or_default(config: dict[str, object], key: str, default: int) -> int:
    value = config.get(key, default)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    return default


class PgmlEmbedder:
    """Embedder that calls PostgresML for in-database embeddings.

    Sends each sub-batch of at most ``batch_size`` texts in a single
    ``SELECT * FROM pgml.embed($1, $2::text[])`` call via asyncpg.
    On connection failure logs a warning and returns zero-vectors.
    """

    DEFAULT_BATCH_SIZE = 100
    MAX_BATCH_SIZE = 100

    def __init__(
        self,
        config: dict[str, object] | None = None,
        pool: asyncpg.Pool | None = None,
    ) -> None:
        self._config = config or load_config()
        embedding = cast(dict[str, object], self._config.get("embedding", {}))
        self.model = _str_or_default(embedding, "model", "nomic-embed-text")
        self.dimensions = _int_or_default(embedding, "dimensions", 768)
        raw_batch_size = _int_or_default(embedding, "batch_size", self.DEFAULT_BATCH_SIZE)
        self.batch_size = max(1, min(self.MAX_BATCH_SIZE, raw_batch_size))
        self._pool = pool

    async def embed(self, text: str) -> list[float]:
        """Return a single embedding vector for ``text``."""
        result = await self.embed_batch([text])
        return result[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for ``texts`` via PostgresML.

        One SQL round-trip per sub-batch of at most ``batch_size`` texts;
        the whole sub-batch is sent as a single TEXT[] array parameter.
        Falls back to zero-vectors on connection failure.
        """
        if not texts:
            return []
        if self._pool is None:
            logger.warning("PgmlEmbedder has no pool; returning zero vectors.")
            return [[0.0] * self.dimensions for _ in texts]

        results: list[list[float]] = []
        try:
            async with self._pool.acquire() as conn:
                for start in range(0, len(texts), self.batch_size):
                    batch = texts[start : start + self.batch_size]
                    rows = await conn.fetch(
                        "SELECT * FROM pgml.embed($1, $2::text[])",
                        self.model,
                        batch,
                    )
                    if len(rows) != len(batch):
                        logger.warning(
                            "pgml.embed returned %d rows for %d texts; "
                            "returning zero vectors for this sub-batch.",
                            len(rows),
                            len(batch),
                        )
                        results.extend([[0.0] * self.dimensions for _ in batch])
                        continue
                    results.extend(list(row["embed"]) for row in rows)
        except Exception as exc:
            logger.warning("PostgresML embed failed: %s; returning zero vectors.", exc)
            return [[0.0] * self.dimensions for _ in texts]
        return results


class FakeEmbedder:
    """Deterministic embedder that does not require Ollama.

    Useful for CI or local runs where the embedding service is unavailable.
    Each vector is deterministically derived from the SHA256 hash of the text
    and has length ``dimensions``.
    """

    def __init__(self, config: dict[str, object] | None = None) -> None:
        embedding = cast(
            dict[str, object],
            (config or load_config()).get("embedding", {}),
        )
        self.dimensions = _int_or_default(embedding, "dimensions", 768)
        self.model = "fake-embedder"

    def embed(self, text: str) -> list[float]:
        """Return a deterministic vector for ``text``."""
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return deterministic vectors for ``texts``."""
        return [self._vector_for(text) for text in texts]

    def embed_matryoshka(self, text: str, dim: int) -> list[float]:
        """Matryoshka-truncated embedding: reuses embed(), no extra network call."""
        return _slice_normalize(self.embed(text), dim)

    def embed_batch_matryoshka(self, texts: list[str], dim: int) -> list[list[float]]:
        return [_slice_normalize(v, dim) for v in self.embed_batch(texts)]

    def instruct(self, text: str) -> str:
        """Return the query-side instructed text (see module-level instruct)."""
        return instruct(text)

    def _vector_for(self, text: str) -> list[float]:
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)
        rng = random.Random(seed)
        return [rng.uniform(-1.0, 1.0) for _ in range(self.dimensions)]


DEFAULT_EMBEDDING_PROVIDER = "pgml"


def create_embedder(
    config: dict[str, object] | None = None,
    pool: asyncpg.Pool | None = None,
) -> OllamaEmbedder | PgmlEmbedder:
    """Return the embedder selected by ``embedding.provider`` in config.

    - ``"pgml"`` (default): PgmlEmbedder, embedding in-database via the pool.
      Without a pool it still constructs, but returns zero-vectors.
    - ``"ollama"``: OllamaEmbedder, embedding via a local Ollama instance.

    Raises ``ValueError`` for any other provider.
    """
    cfg = config or load_config()
    embedding = cast(dict[str, object], cfg.get("embedding", {}))
    provider = _str_or_default(embedding, "provider", DEFAULT_EMBEDDING_PROVIDER)
    if provider == "pgml":
        return PgmlEmbedder(cfg, pool)
    if provider == "ollama":
        return OllamaEmbedder(cfg)
    raise ValueError(f"Unknown embedding provider {provider!r}: expected 'pgml' or 'ollama'")


async def aembed_batch(
    embedder: OllamaEmbedder | PgmlEmbedder | Embedder, texts: list[str]
) -> list[list[float]]:
    """Embed ``texts`` with any embedder, awaiting the async pgml path.

    The ``Embedder`` protocol arm (todo 13) lets structural backends such as
    the late-chunking embedder flow through the same research boundary.
    """
    if isinstance(embedder, PgmlEmbedder):
        return await embedder.embed_batch(texts)
    return embedder.embed_batch(texts)
