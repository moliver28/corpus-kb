"""Persistent embedding cache (issue #539): one SQL table, both backends.

The in-memory LRU in ``rag/embedder.py`` dies with the process, so a cold
CLI, server, or plugin-initiated ingest re-embeds identical texts. This
module adds a second tier: the ``embedding_cache_2560`` table (migration 046,
halfvec(2560) width). The SHA256 key covers the exact text — including the
query-side instruction prefix — so an instruction change invalidates every
entry on its own. Lookup filters on the model name, so a model flip never
serves a stale vector.

Degraded zero vectors are never stored: a table row must be a real model
output. An embedder that runs at a width other than the fixed schema width
(2560) skips this cache, because the table cannot hold its vectors.

The cache is best effort. Any SQL error is logged and treated as a miss;
the embedder keeps working without the table present.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable, Coroutine, Sequence
from typing import Any

from corpus_kb.dimensions import EMBEDDING_DIMENSIONS
from corpus_kb.storage.gateway import SqlGateway, gateway_backend

logger = logging.getLogger(__name__)

#: The fixed vector width the schema ships (migration 025 regime). Rows
#: outside this width cannot be stored, so the cache gates on it.
SCHEMA_EMBEDDING_WIDTH = EMBEDDING_DIMENSIONS

#: Keys per lookup round: batched reads keep the DuckDB single-writer gate
#: cheap (the gateway serves reads outside the gate only while no tx is open).
LOOKUP_CHUNK = 512


def sha256_key(text: str) -> str:
    """The cache key: SHA256 of the exact text (instruction prefix included)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_LOOKUP_SQL_PG = (
    "SELECT sha256, vector FROM embedding_cache_2560"
    " WHERE sha256 = ANY($1::VARCHAR[]) AND model = $2"
)
_LOOKUP_SQL_DUCK = (
    "SELECT sha256, vector FROM embedding_cache_2560"
    " WHERE sha256 IN (SELECT unnest($1::VARCHAR[])) AND model = $2"
)
_STORE_SQL_PG = (
    "INSERT INTO embedding_cache_2560 (sha256, model, vector) VALUES ($1, $2, $3::halfvec)"
    " ON CONFLICT (sha256) DO NOTHING"
)
# DuckDB twin column is FLOAT[2560]: the vector binds as a plain Python
# list and the halfvec cast must not appear (see analysis/meta_store.py).
_STORE_SQL_DUCK = (
    "INSERT INTO embedding_cache_2560 (sha256, model, vector) VALUES ($1, $2, $3)"
    " ON CONFLICT (sha256) DO NOTHING"
)


class EmbeddingCache:
    """SQL-backed second tier behind the embedder's in-memory LRU."""

    def __init__(self, gateway: SqlGateway, model: str, dimensions: int) -> None:
        self._gateway = gateway
        self._model = model
        self._is_duckdb = gateway_backend(gateway) == "duckdb"
        self._enabled = dimensions == SCHEMA_EMBEDDING_WIDTH
        self._width_warned = False

    @property
    def enabled(self) -> bool:
        """True when the configured width fits the schema's fixed width."""
        return self._enabled

    def lookup_sync(self, keys: list[str]) -> dict[str, list[float]]:
        """Read cached vectors for ``keys``; best effort, miss on any error.

        Runs its own event loop, so call it only from a thread that has no
        running loop (``embed_batch`` runs through ``asyncio.to_thread``).
        When a loop IS running the lookup is skipped (the RAM tier still
        applies) rather than blocking the caller.
        """
        result = self._run_guarded(lambda: self.lookup(keys), "lookup")
        return result if result is not None else {}

    def store_sync(self, keys: list[str], vectors: list[list[float]]) -> None:
        """Write real (non-degraded) vectors; best effort, silent on error."""
        rows = [
            (key, vector)
            for key, vector in zip(keys, vectors, strict=True)
            if any(component != 0.0 for component in vector)
        ]
        if not rows:
            return
        store_keys = [key for key, _ in rows]
        store_vectors = [vector for _, vector in rows]
        self._run_guarded(lambda: self.store(store_keys, store_vectors), "store")

    def serve_missing_sync(
        self, missing: list[tuple[int, str]], results: list[list[float]]
    ) -> list[tuple[int, str]]:
        """Serve SQL-tier hits into ``results``; return still-missing texts.

        A hit here is a miss for the model but a cache hit for the usage
        snapshot. Degraded or skipped lookups change nothing, so the
        batch flows to the model unchanged.
        """
        if not missing:
            return missing
        stored = self.lookup_sync([sha256_key(text) for _, text in missing])
        still_missing: list[tuple[int, str]] = []
        for idx, text in missing:
            vector = stored.get(sha256_key(text))
            if vector is None:
                still_missing.append((idx, text))
            else:
                results[idx] = vector
        return still_missing

    def store_fresh_sync(
        self, missing: list[tuple[int, str]], results: list[list[float]], *, degraded: bool
    ) -> None:
        """Persist what the model really returned (issue #539).

        A degraded call stores nothing: the table must never serve a
        fabricated vector later.
        """
        if degraded or not missing:
            return
        keys = [sha256_key(text) for _, text in missing]
        self.store_sync(keys, [results[i] for i, _ in missing])

    def _run_guarded(
        self,
        make_coro: Callable[[], Coroutine[Any, Any, dict[str, list[float]] | None]],
        action: str,
    ) -> dict[str, list[float]] | None:
        """Run one cache coroutine off-loop; any failure counts as a miss.

        The coroutine is built only after the guard passes, so a skipped
        call never leaks an un-awaited coroutine.
        """
        if not self._enabled:
            return None
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            logger.debug(
                "embedding_cache_2560 %s skipped: embed_batch ran on the event loop.", action
            )
            return None
        try:
            return asyncio.run(make_coro())
        except Exception as exc:  # the cache must never break ingest
            logger.warning("embedding_cache_2560 %s failed; treating as miss: %s", action, exc)
            return None

    async def lookup(self, keys: list[str]) -> dict[str, list[float]]:
        """Read cached vectors for ``keys``, batched to keep reads cheap.

        Rows whose width differs from the schema width count as misses,
        and a width mismatch also disables further lookups.
        """
        sql = _LOOKUP_SQL_DUCK if self._is_duckdb else _LOOKUP_SQL_PG
        found: dict[str, list[float]] = {}
        for start in range(0, len(keys), LOOKUP_CHUNK):
            chunk = keys[start : start + LOOKUP_CHUNK]
            rows = await self._gateway.fetch(sql, chunk, self._model)
            for row in rows:
                vector = _vector_of(row)
                if len(vector) == SCHEMA_EMBEDDING_WIDTH:
                    found[str(row["sha256"])] = vector
                else:
                    self._disable_after_width_mismatch()
        return found

    async def store(self, keys: list[str], vectors: list[list[float]]) -> None:
        """Insert (sha256, model, vector) rows; existing keys stay as-is."""
        widths_ok = [len(vector) == SCHEMA_EMBEDDING_WIDTH for vector in vectors]
        if not all(widths_ok):
            self._disable_after_width_mismatch()
        args = [
            (key, self._model, _vector_value(vector, self._is_duckdb))
            for key, vector, ok in zip(keys, vectors, widths_ok, strict=True)
            if ok
        ]
        if not args:
            return
        sql = _STORE_SQL_DUCK if self._is_duckdb else _STORE_SQL_PG
        await self._gateway.executemany(sql, args)

    def _disable_after_width_mismatch(self) -> None:
        if not self._width_warned:
            self._width_warned = True
            logger.warning(
                "embedding_cache_2560 disabled: vector width does not match the schema width %d.",
                SCHEMA_EMBEDDING_WIDTH,
            )


def _vector_of(row: Any) -> list[float]:
    """Parse a driver-specific vector column into a list of floats.

    Postgres serves halfvec as ``'[v, v, ...]'`` text (no codec needed for
    reads); DuckDB serves FLOAT[2560] as a plain Python list.
    """
    raw = row["vector"]
    if isinstance(raw, str):
        return [float(part) for part in raw.strip("[ ]").split(",") if part]
    return [float(part) for part in raw]


def _vector_value(vector: list[float], is_duckdb: bool) -> Sequence[float] | str:
    """Bind-ready vector: plain list on DuckDB, halfvec literal on Postgres."""
    if is_duckdb:
        return vector
    return "[" + ",".join(repr(float(part)) for part in vector) + "]"
