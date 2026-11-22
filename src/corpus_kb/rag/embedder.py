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
import time
from collections import OrderedDict
from typing import Any, Protocol, cast, runtime_checkable

import asyncpg
import httpx
from ollama import Client
from ollama._types import EmbedResponse

from corpus_kb.dimensions import EMBEDDING_DIMENSIONS

from ..config import load_config
from ..errors import ConfigError
from ..utils.deterministic import HashRandom
from .embedder_cache import EmbeddingCache
from .embedder_cache import sha256_key as _sha256_key
from .embedder_math import _slice_normalize as _slice_normalize
from .embedder_math import assert_unit_or_zero as assert_unit_or_zero
from .embedder_math import l2_normalize as l2_normalize
from .embedder_usage import EmbedUsage, summarize_usage
from .model_profiles import native_dimensions

logger = logging.getLogger(__name__)

MAX_CACHE_SIZE = 10_000

# The shipped defaults (three-way sync: config.py defaults, both config.yaml
# files, this module). No silent other fallback exists; a config missing the
# key gets exactly this pair.
DEFAULT_EMBEDDING_MODEL = "qwen3-embedding:4b"
DEFAULT_EMBEDDING_DIMENSIONS = EMBEDDING_DIMENSIONS

# Bound on every embed call (seconds). Same contract as nli_client.py
# (commit 3e37bed): without a client timeout, a server that accepts TCP but
# never answers blocks the caller forever. 120s covers a cold model load on
# CPU; slower than that must DEGRADE (zero vectors) per the contract below.
EMBED_TIMEOUT_SECONDS = 120.0

# Hard cap on the ``input`` array per /api/embed call (issue #468): one
# giant batch can hold a worker far past any read timeout, because the read
# bound is per received chunk and keep-alive bytes can reset it. The
# ``embedding.max_batch_texts`` config key overrides this default.
EMBED_MAX_BATCH_TEXTS = 32

# Query-side instruction for instruction-tuned embedding models (e.g.
# Qwen3-Embedding). Applied to QUERIES only — documents are embedded raw.
# Todo 13's G1 ablation tunes the wording here, not at call sites.
# BYTE-IDENTICAL CONTRACT: the text is cache-keyed (sha256 of the instructed
# text); changing it invalidates every recorded fixture and cache entry.
QUERY_INSTRUCTION_PREFIX = (
    "Instruct: Given a qualitative research query, retrieve relevant interview exchanges\nQuery: "
)


def instruct(text: str) -> str:
    """Prefix a query with the instruction-tuned embedding instruction.

    Instruction-tuned embedding models expect the instruction on the QUERY
    side only; instructing documents too degrades retrieval. Gated per
    embedder instance by ``embedding.query_instruction`` (see
    ``OllamaEmbedder.instruct``) — the research grain keeps calling this
    module function directly (its own committed contract).
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
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for ``texts``, in input order."""
        ...

    def instruct(self, text: str) -> str:
        """Return the query-side instructed text (see module-level instruct)."""
        ...


class OllamaEmbedder:
    """Embedder that calls a local Ollama instance, caching results in memory.

    On connection failure the embedder logs a warning and returns zero-vectors
    so callers can continue operating in degraded mode.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        cache: EmbeddingCache | None = None,
    ) -> None:
        self._config = config or load_config()
        embedding = cast(dict[str, Any], self._config.get("embedding", {}))

        self.model = _str_or_default(embedding, "model", DEFAULT_EMBEDDING_MODEL)
        self.base_url = _str_or_default(embedding, "base_url", "http://localhost:11434")
        self.batch_size = _int_or_default(embedding, "batch_size", 32)
        # Issue #468: hard cap on the texts sent as one ``input`` array.
        self.max_batch_texts = max(
            1, _int_or_default(embedding, "max_batch_texts", EMBED_MAX_BATCH_TEXTS)
        )
        self.dimensions = _int_or_default(embedding, "dimensions", DEFAULT_EMBEDDING_DIMENSIONS)
        # Config-v2 gates: ``query_instruction`` toggles the query-side
        # prefix at instruct(); ``mrl_trained`` (with a configured width
        # BELOW the model's native width) arms the Ollama dimensions
        # passthrough.
        self.query_instruction = bool(embedding.get("query_instruction", True))
        self.mrl_trained = bool(embedding.get("mrl_trained", False))

        # Issue #468: explicit all-four httpx timeout. The read bound is a
        # per-chunk inactivity cap, not a total request cap, so the wall-
        # clock deadline in embed_batch bounds the whole call too.
        self._client = Client(
            host=self.base_url,
            timeout=httpx.Timeout(
                connect=EMBED_TIMEOUT_SECONDS,
                read=EMBED_TIMEOUT_SECONDS,
                write=EMBED_TIMEOUT_SECONDS,
                pool=EMBED_TIMEOUT_SECONDS,
            ),
        )
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        # Per-call usage accounting (issue #198): ``last_usage`` is the
        # snapshot of the most recent embed_batch call; ``_api_prompt_tokens``
        # carries the response's prompt token count of the last API call
        # (None when the API was not called or reported nothing).
        self.last_usage: EmbedUsage | None = None
        self._api_prompt_tokens: int | None = None
        # Issue #468 honesty note: set on degrade; None after a clean call.
        self.last_note: str | None = None
        # Issue #539: the SQL-backed second tier (optional; built by
        # create_embedder when a pool is available). The RAM LRU above
        # still sits in front of it.
        self._persist_cache = cache

    def embed(self, text: str) -> list[float]:
        """Return a single embedding vector for ``text``."""
        return self.embed_batch([text])[0]

    def _all_cached_usage(self, text_count: int, results: list[list[float]]) -> list[list[float]]:
        """Record an all-hit call: no backend call, no token count (issue #198)."""
        self.last_usage = EmbedUsage(prompt_tokens=None, embedded_texts=0, cached_texts=text_count)
        return results

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for ``texts``, using the cache and batching."""
        if not texts:
            self.last_usage = None
            return []

        keys = [_sha256_key(text) for text in texts]
        results: list[list[float]] = [[] for _ in texts]
        self.last_note = None

        missing = self._fill_from_ram_cache(keys, texts, results)
        if self._persist_cache is not None:
            missing = self._persist_cache.serve_missing_sync(missing, results)
        if not missing:
            return self._all_cached_usage(len(texts), results)

        missing_texts = [text for _, text in missing]
        batched_results, reported = self._embed_missing_in_batches(missing_texts)
        self.last_usage = summarize_usage(len(texts), len(missing), reported)
        self._admit_to_ram_cache(missing, batched_results, results)
        if self._persist_cache is not None:
            self._persist_cache.store_fresh_sync(
                missing, results, degraded=self.last_note is not None
            )
        return results

    def _fill_from_ram_cache(
        self, keys: list[str], texts: list[str], results: list[list[float]]
    ) -> list[tuple[int, str]]:
        """Serve RAM hits into ``results``; return the (idx, text) misses."""
        missing: list[tuple[int, str]] = []
        for idx, key in enumerate(keys):
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                results[idx] = cached
            else:
                missing.append((idx, texts[idx]))
        return missing

    def _admit_to_ram_cache(
        self,
        missing: list[tuple[int, str]],
        batched_results: list[list[float]],
        results: list[list[float]],
    ) -> None:
        """Store the fresh vectors in the RAM LRU and fill ``results``."""
        for (idx, text), vector in zip(missing, batched_results, strict=True):
            key = _sha256_key(text)
            self._cache[key] = vector
            self._cache.move_to_end(key)
            if len(self._cache) > MAX_CACHE_SIZE:
                self._cache.popitem(last=False)
            results[idx] = vector

    def _embed_missing_in_batches(
        self, missing_texts: list[str]
    ) -> tuple[list[list[float]], list[int | None]]:
        """Embed the misses in sub-batches; return vectors and token counts.

        Wall-clock deadline (issue #468): the deadline stops new sub-
        batches once the budget is spent; the tail returns zero vectors
        and sets the honesty note.
        """
        batch_size = max(1, min(self.batch_size, self.max_batch_texts))
        batched_results: list[list[float]] = []
        reported: list[int | None] = []
        deadline = time.monotonic() + EMBED_TIMEOUT_SECONDS

        for start in range(0, len(missing_texts), batch_size):
            if time.monotonic() >= deadline:
                batched_results.extend([[0.0] * self.dimensions for _ in missing_texts[start:]])
                self.last_note = "Embed deadline reached; remaining texts return zero vectors."
                break
            batch = missing_texts[start : start + batch_size]
            batched_results.extend(self._embed_batch(batch))
            reported.append(self._api_prompt_tokens)
        return batched_results, reported

    def pop_usage(self) -> EmbedUsage | None:
        """Take and clear the per-call snapshot in one step (momus F9).

        A reader that pops cannot count the same spend twice, and a
        second reader finds the snapshot gone. The read-and-clear is one
        attribute swap, so it closes the read-after-return window on the
        event loop. This does NOT make a shared instance thread-safe:
        the confinement contract in ``rag/embedder_usage.py`` still
        binds every receipt reader.
        """
        usage, self.last_usage = self.last_usage, None
        return usage

    def _request_dimensions(self) -> int | None:
        """The ``dimensions`` value to pass to Ollama, or None for native.

        MRL passthrough contract: only when the model is declared
        Matryoshka-trained AND the configured width is LOWER than the
        model's native width. Requesting the native width (or truncating a
        non-MRL model) is refused — the native call is byte-identical to
        the pre-v2 behavior.
        """
        if not self.mrl_trained:
            return None
        native = native_dimensions(self.model)
        if native is None or self.dimensions >= native:
            return None
        return self.dimensions

    def _validate_returned_width(self, vectors: list[list[float]]) -> None:
        """Raised-dim contract: the model must emit the configured width.

        A width mismatch means the config and the model disagree (wrong
        ``embedding.dimensions``, or a model whose output is not what its
        profile says). Zero vectors from the degraded fallback never reach
        this check (that path returns early).
        """
        for vector in vectors:
            if len(vector) != self.dimensions:
                raise ConfigError(
                    f"Embedding model {self.model!r} returned {len(vector)} dimensions "
                    f"but embedding.dimensions is configured as {self.dimensions}. "
                    "The schema stores exactly the configured width.",
                    remediation=(
                        "Set embedding.dimensions to the model's native output width "
                        f"(native width for {self.model!r}: "
                        f"{native_dimensions(self.model)}), then re-ingest. If the model "
                        "is not the intended one, set embedding.model first. Check "
                        "docs/ADMIN.md for the model-schema matrix."
                    ),
                )

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        # The snapshot reader reads this after the call; reset first so a
        # failed call can never leak the previous batch's count.
        self._api_prompt_tokens = None
        try:
            request_dimensions = self._request_dimensions()
            if request_dimensions is not None:
                response: EmbedResponse = self._client.embed(
                    model=self.model,
                    input=texts,
                    dimensions=request_dimensions,
                )
            else:
                response = self._client.embed(
                    model=self.model,
                    input=texts,
                )
        # httpx.TimeoutException is a SIBLING of NetworkError (both under
        # TransportError): an expired EMBED_TIMEOUT_SECONDS bound must
        # degrade to zero vectors exactly like a refused connection.
        except (ConnectionError, OSError, httpx.NetworkError, httpx.TimeoutException):
            logger.warning(
                "Ollama connection failed at %s; returning zero vectors.",
                self.base_url,
            )
            self.last_note = f"Ollama call failed at {self.base_url}; returning zero vectors."
            return [[0.0] * self.dimensions for _ in texts]

        # Honest usage accounting (issue #198): keep what the response
        # reported; a missing count stays None (unknown, not zero).
        self._api_prompt_tokens = response.prompt_eval_count
        self.last_note = None
        vectors = [list(vector) for vector in response.embeddings]
        self._validate_returned_width(vectors)
        return vectors

    def embed_matryoshka(self, text: str, dim: int) -> list[float]:
        """Matryoshka-truncated embedding: reuses embed(), no extra network call."""
        return _slice_normalize(self.embed(text), dim)

    def embed_batch_matryoshka(self, texts: list[str], dim: int) -> list[list[float]]:
        return [_slice_normalize(v, dim) for v in self.embed_batch(texts)]

    def instruct(self, text: str) -> str:
        """Query-side instructed text, gated by ``embedding.query_instruction``.

        False/absent-falsy returns the text unchanged (documents and
        instruction-less models must embed raw); the prefix text itself is
        byte-identical (it is cache-keyed).
        """
        if not self.query_instruction:
            return text
        return instruct(text)


def _str_or_default(config: dict[str, Any], key: str, default: str) -> str:
    value = config.get(key, default)
    return str(value) if value is not None else default


def _int_or_default(config: dict[str, Any], key: str, default: int) -> int:
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
        config: dict[str, Any] | None = None,
        pool: asyncpg.Pool | None = None,
    ) -> None:
        self._config = config or load_config()
        embedding = cast(dict[str, Any], self._config.get("embedding", {}))
        self.model = _str_or_default(embedding, "model", DEFAULT_EMBEDDING_MODEL)
        self.dimensions = _int_or_default(embedding, "dimensions", DEFAULT_EMBEDDING_DIMENSIONS)
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

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        embedding = cast(
            dict[str, Any],
            (config or load_config()).get("embedding", {}),
        )
        self.dimensions = _int_or_default(embedding, "dimensions", DEFAULT_EMBEDDING_DIMENSIONS)
        self.model = "fake-embedder"
        self.query_instruction = bool(embedding.get("query_instruction", True))

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
        """Query-side instructed text, gated by ``embedding.query_instruction``."""
        if not self.query_instruction:
            return text
        return instruct(text)

    def _vector_for(self, text: str) -> list[float]:
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)
        rng = HashRandom(seed)
        return [rng.uniform(-1.0, 1.0) for _ in range(self.dimensions)]
