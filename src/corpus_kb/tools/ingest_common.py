"""Ingest pipeline facade: locks, config reads, and the embed+store stage.

All writes go directly to Postgres via asyncpg (see ``ingest_store`` for the
write layer and the ``ingest_runs`` integrity ledger). No LanceDB, no SQLite.
The ingest pipeline is async — callers must await run_pipeline().

Unified-ingestion Wave 2: the pipeline stages moved to the
``tools/pipeline/`` package (``document.py``: the canonical IngestDocument
and its char-span coordinate system; ``stages.py``: one callable per stage;
``sinks.py``: the IngestSink write port wrapping ingest_store verbatim).
This module stays the facade: it re-exports every moved public name, so
existing imports and the ``monkeypatch`` targets
``ingest_common.embed_chunks`` / ``ingest_common.PostgresIngestStore`` /
``ingest_common.run_pipeline`` keep working unchanged. The stages resolve
these names through THIS module's namespace at call time.

Kept here (not moved): the advisory-lock pair (its SQL is pinned by a
source-inspection test), the shared config-read helpers, and the
embed+store stage ``_stage_store_and_embed`` (its ``build_embedding_input``
call site is pinned the same way; moving it needs a test change, queued
for Wave 3).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import asyncpg

from ..config import load_config
from ..errors import ConfigError
from ..ontology import Ontology, load_ontology
from ..rag.embedder_selection import POOLED_MODE
from ..storage.gateway import DuckDbGateway, PostgresGateway, SqlGateway
from ..utils.models import Chunk, Document
from ..utils.tracing import (
    ATTR_BATCH_SIZE,
    ATTR_CHUNK_COUNT,
    ATTR_DEGRADED,
    ATTR_EMBEDDING_MODE,
    ATTR_EMBEDDING_MODEL,
    ATTR_ROW_COUNT,
    SPAN_EMBED,
    SPAN_STORE,
    span,
)
from .ingest_cfg import (
    _nested_dict,
    contextual_apply_to,
    matryoshka_slice,
    vector_write_config,
)
from .ingest_embed import (
    _all_zero,
    _assign_embeddings,
    _embedding_failure_is_fatal,
    _late_chunk_vectors,
    build_embedding_input,
    embed_chunks,
)
from .ingest_extract import (
    _actual_extractor_id,
    _extract_entities_flag,
    _extractor_name,
    extract_with_fallback,
)
from .ingest_reembed import reembed_degraded
from .ingest_store import DEFAULT_TENANT_ID, PostgresIngestStore
from .pipeline.plan import run_pipeline
from .pipeline.sinks import IngestSink
from .pipeline.stages import _run_locked_stages

logger = logging.getLogger(__name__)

# Public surface: the facade-kept helpers PLUS the stage functions moved
# to the pipeline package and the sibling stage modules (re-exported so
# historical import paths and the tests' monkeypatch targets —
# ``ingest_common.embed_chunks``, ``ingest_common.PostgresIngestStore``,
# ``ingest_common.run_pipeline`` — keep working unchanged).
__all__ = [
    "DEFAULT_TENANT_ID",
    "PostgresIngestStore",
    "_actual_extractor_id",
    "_all_zero",
    "_assign_embeddings",
    "_contextual_enabled",
    "_embedding_failure_is_fatal",
    "_extract_entities_flag",
    "_extractor_name",
    "_late_chunk_vectors",
    "_nested_dict",
    "_run_locked_stages",
    "_sha256",
    "advisory_lock_ctx",
    "build_document",
    "build_embedding_input",
    "embed_chunks",
    "extract_with_fallback",
    "ingest_text",
    "load_config_or_pass",
    "matryoshka_slice",
    "ontology",
    "reembed_degraded",
    "run_pipeline",
    "vector_write_config",
]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# The advisory-lock SQL stays owned (and pinned) by the orchestrator: the
# session-scoped pair runs on one held pooled connection, byte-identical to
# the pre-port inline SQL. Gateways without a server-side advisory lock use
# the in-process lock in advisory_lock_ctx below.
_ADVISORY_LOCK_SQL = "SELECT pg_try_advisory_lock(hashtextextended($1 || ':' || $2, 0))"
_ADVISORY_UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtextextended($1 || ':' || $2, 0))"

# In-process content locks for gateways with no server-side advisory lock
# (DuckDB allows one writer; the file lock covers cross-process safety).
_CONTENT_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def _raw_pool(source: asyncpg.Pool | SqlGateway | None) -> asyncpg.Pool | None:
    """The underlying asyncpg pool, or None when a gateway was handed over.

    Same duck-typing as ``ingest_store.as_gateway``: only real gateway
    drivers divert; anything else is pool-shaped (pool doubles included).
    """
    if source is None or isinstance(source, (PostgresGateway, DuckDbGateway)):
        return None
    return cast("asyncpg.Pool", source)


@asynccontextmanager
async def advisory_lock_ctx(
    *,
    pool: asyncpg.Pool | None,
    tenant_id: str,
    key: str,
) -> AsyncIterator[bool]:
    """Serialize concurrent ingests of one content hash, per tenant.

    One seam, two drivers. Postgres keeps the session-scoped
    ``pg_try_advisory_lock`` pair on one held pooled connection (a crashed
    process releases it server-side). Gateways without a server-side lock
    (DuckDB) use an in-process ``asyncio.Lock`` keyed by
    ``(tenant_id, key)``. Yields True when the lock was taken, False when
    another run of the same content is in flight (the caller reports
    ``skipped/concurrent-run`` — never success).
    """
    if pool is not None:
        async with pool.acquire() as lock_conn:
            acquired = await lock_conn.fetchval(_ADVISORY_LOCK_SQL, tenant_id, key)
            if not acquired:
                yield False
                return
            try:
                yield True
            finally:
                await lock_conn.execute(_ADVISORY_UNLOCK_SQL, tenant_id, key)
        return
    lock_key = (tenant_id, key)
    lock = _CONTENT_LOCKS.setdefault(lock_key, asyncio.Lock())
    if lock.locked():
        yield False
        return
    async with lock:
        try:
            yield True
        finally:
            if _CONTENT_LOCKS.get(lock_key) is lock:
                del _CONTENT_LOCKS[lock_key]


def load_config_or_pass(config: dict[str, Any] | None) -> dict[str, Any]:
    """Return the provided config dict, or load the default config if None."""
    return config if config is not None else load_config()


DEFAULT_INGEST_CONCURRENCY = 4


def file_ingest_concurrency(config: dict[str, Any], pool: asyncpg.Pool | SqlGateway) -> int:
    """Bounded file-level ingest concurrency from ``ingestion.concurrency``.

    The value is capped at two below the pool size so parallel files cannot
    take every connection. DuckDB is a single-writer engine, so it stays
    serial. Never below 1.
    """
    ingestion = config.get("ingestion")
    raw = ingestion.get("concurrency", DEFAULT_INGEST_CONCURRENCY) if ingestion else None
    try:
        limit = int(raw) if raw is not None else DEFAULT_INGEST_CONCURRENCY
    except (TypeError, ValueError):
        limit = DEFAULT_INGEST_CONCURRENCY
    limit = max(1, limit)
    if isinstance(pool, DuckDbGateway):
        return 1
    maxsize = getattr(pool, "_maxsize", None)
    if isinstance(maxsize, int):
        limit = min(limit, max(1, maxsize - 2))
    return max(1, limit)


def _contextual_enabled(config: dict[str, Any], source_type: str) -> bool:
    """Return True if contextual retrieval should run for this source_type.

    Contextualization is on unconditionally when
    ``ingest.contextualize.enabled`` is True. Otherwise it is on only for
    source_types listed in ``ingest.contextualize.enabled_source_types``
    (default-on allowlist for interview/research data per the approved
    feature default). The legacy ``contextual.*`` namespace is an alias
    the loader applies before this read.
    """
    cfg = _nested_dict(_nested_dict(config, "ingest"), "contextualize")
    if bool(cfg.get("enabled", False)):
        return True
    allowlist = cfg.get("enabled_source_types", [])
    return isinstance(allowlist, list) and source_type in allowlist


def ontology(config: dict[str, Any]) -> Ontology:
    """Load the ontology from ``graph.ontology_path`` in config, falling back to default."""
    graph = _nested_dict(config, "graph")
    ontology_path = graph.get("ontology_path")
    if not isinstance(ontology_path, str):
        ontology_path = "config/ontology.yaml"
    return load_ontology(ontology_path)


def build_document(path: str, source_type: str, text: str) -> Document:
    """Construct a Document model from raw text."""
    return Document(
        path=path,
        source_type=source_type,
        content=text,
        size_bytes=len(text.encode("utf-8")),
    )


async def _stage_store_and_embed(
    ingest_store: IngestSink,
    chunks: list[Chunk],
    config: dict[str, Any],
    text: str,
    pool: asyncpg.Pool | None,
    errors: list[str],
) -> tuple[int, int, bool, str | None, dict[str, Any]]:
    """Stage 2: store chunks, embed the new ones, store their vectors.

    Returns (pg_chunk_count, pg_vector_count, degraded, embed_note, embed_meta).
    ``ingest_store`` is the pipeline sink (the ChunkSink wrapper or the bare
    store — both satisfy the port). Stage errors are appended to
    ``errors``; the dimension-switch guard's ConfigError propagates typed
    (a width mismatch is a config problem, not a write failure — the
    BaseException handler in the caller closes the ledger on the way out).
    """
    embed_note: str | None = None
    pg_chunk_count = 0
    pg_vector_count = 0
    inserted_chunk_ids: set[str] = set()

    # W3 apply_to semantics (write-time blurb targets). The blurb was
    # computed ONCE in _prepare_chunks_for_store; ``apply_to`` decides
    # where it lands: "lexical" -> the chunks.context_blurb column (the
    # FTS expression reads it), "embedding" -> the ``blurb\n\ntext`` embed
    # input. When lexical is OFF the column write is NULL — the blurb is
    # captured here BEFORE the clear so the embed input can still carry it.
    # Trade-off (documented in docs/INGESTION.md): with lexical OFF the
    # blurb is never persisted, so a later reembed repair rebuilds those
    # vectors from raw text.
    embedding_applies, lexical_applies = contextual_apply_to(config)
    embed_blurbs = {str(c.chunk_id): c.context_blurb for c in chunks if c.context_blurb}
    if not lexical_applies:
        for chunk in chunks:
            chunk.context_blurb = None

    try:
        with span(SPAN_STORE) as store_span:
            inserted_chunk_ids = await ingest_store.store_chunks(chunks)
            pg_chunk_count = len(inserted_chunk_ids)
            store_span.set_attribute(ATTR_CHUNK_COUNT, pg_chunk_count)
    except Exception as exc:
        logger.warning("Postgres chunk write failed: %s", exc)
        errors.append(f"PostgresWriteError: {exc}")

    new_chunks = [c for c in chunks if str(c.chunk_id) in inserted_chunk_ids]

    embed_texts_override = {
        str(c.chunk_id): build_embedding_input(
            c.text, embed_blurbs.get(str(c.chunk_id)) if embedding_applies else None
        )
        for c in new_chunks
    }
    embed_meta: dict[str, Any] = {}
    # One shared config read for the vector write (model, dimensions,
    # matryoshka dim) — the exact keys+defaults the reembed repair path
    # reads (ingest_cfg.vector_write_config). Read before the embed span so
    # the span can record the model name.
    embedding_model, dimensions, v1024_dim = vector_write_config(config)
    with span(SPAN_EMBED, **{ATTR_EMBEDDING_MODEL: embedding_model}) as embed_span:
        degraded, embed_err = await embed_chunks(
            new_chunks,
            config,
            pool=pool,
            text_override=embed_texts_override,
            document_text=text,
            meta=embed_meta,
        )
        embed_span.set_attribute(ATTR_BATCH_SIZE, len(new_chunks))
        embed_mode = str(embed_meta.get("embedding_mode", POOLED_MODE))
        embed_span.set_attribute(ATTR_EMBEDDING_MODE, embed_mode)
        embed_span.set_attribute(ATTR_DEGRADED, degraded)
    if embed_err is not None:
        if _embedding_failure_is_fatal(new_chunks):
            logger.warning("embed_chunks: %s", embed_err)
            errors.append(f"EmbeddingError: {embed_err}")
        else:
            # The fallback provider produced real vectors: degraded, but
            # everything stored. A note, NOT an error — otherwise no run
            # on a fallback-dependent host would ever count as completed
            # and the dedupe probe would never fire.
            embed_note = embed_err

    vectors_1024: dict[str, list[float]] = {}
    if v1024_dim is not None and not degraded:
        for c in new_chunks:
            if c.embedding is not None:
                vectors_1024[str(c.chunk_id)] = matryoshka_slice(c.embedding, v1024_dim)

    try:
        with span(SPAN_STORE, **{ATTR_EMBEDDING_MODEL: embedding_model}) as store_span:
            pg_vector_count = await ingest_store.store_vectors(
                new_chunks,
                embedding_model,
                dimensions=dimensions,
                vectors_1024=vectors_1024 or None,
            )
            store_span.set_attribute(ATTR_ROW_COUNT, pg_vector_count)
    except ConfigError:
        raise
    except Exception as exc:
        logger.warning("Postgres vector write failed: %s", exc)
        errors.append(f"PostgresWriteError: {exc}")
        pg_chunk_count = 0
        pg_vector_count = 0
    return pg_chunk_count, pg_vector_count, degraded, embed_note, embed_meta


async def ingest_text(
    text: str,
    pg_pool: asyncpg.Pool | SqlGateway,
    source_type: str = "text",
    config: dict[str, Any] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    source: str = "raw_text",
    validate: str | None = None,
) -> dict[str, Any]:
    """Ingest raw text with optional type hint and source identifier.

    Lives here (rather than only in ``ingest_tools``) so tests and other
    ingest-side code can import it alongside ``run_pipeline`` without a
    circular import; ``ingest_tools.ingest_text`` re-exports this function.

    ``source_type`` is the DOCUMENT source type (it feeds the
    ``ingestion.strategies`` config mapping and content detection); use the
    explicit ``run_pipeline(strategy_name=...)`` override to pin a strategy.
    ``validate`` is the optional validation gate (off | advisory | enforce |
    calibrate); None falls back to the active profile's ``validate`` value,
    then ``off``. ``off`` keeps the envelope byte-identical.
    """
    if source_type not in {"code", "markdown", "text", "transcript"}:
        return {"status": "error", "message": f"Invalid source_type: {source_type}"}
    config = load_config_or_pass(config)
    return await run_pipeline(
        text,
        source_type,
        source,
        config,
        pg_pool,
        tenant_id,
        is_file=False,
        validate_mode=validate,
    )
