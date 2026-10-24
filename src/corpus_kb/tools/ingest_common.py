"""Ingest pipeline orchestration: partition, chunk, embed, extract, store.

All writes go directly to Postgres via asyncpg (see ``ingest_store`` for the
write layer and the ``ingest_runs`` integrity ledger). No LanceDB, no SQLite.
The ingest pipeline is async — callers must await run_pipeline().

Zero-drift Wave 3B split: the embed stage (``ingest_embed``), the
extraction chain (``ingest_extract``), the degraded repair path
(``ingest_reembed``), and the shared config reads (``ingest_cfg``) live
in their own modules. This module re-exports every moved public name, so
existing imports (and the ``monkeypatch`` targets
``ingest_common.embed_chunks`` / ``ingest_common.PostgresIngestStore``,
resolved through this module's namespace by ``_run_locked_stages``)
keep working unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import asyncpg

from ..analysis.dates import plan_document_date
from ..config import load_config
from ..errors import ConfigError
from ..extraction import ExtractStats, extraction_backend_name
from ..extraction.cache import ExtractionCache
from ..ingest.hierarchy_settings import HierarchySettings, load_hierarchy_settings
from ..ingestion.base import (
    DETECT_HEAD_CHARS,
    PlanConformanceError,
    SourceProfile,
    chunks_from_plan,
    validate_plan,
)
from ..ingestion.registry import resolve_strategy
from ..ontology import Ontology, load_ontology
from ..profiles import load_profile, with_profile_overrides
from ..rag.embedder_selection import POOLED_MODE
from ..storage.gateway import DuckDbGateway, PostgresGateway, SqlGateway
from ..storage.rag_backend import RagBackend
from ..utils.models import Chunk, Document, Entity, Relation
from ..utils.tracing import (
    ATTR_BATCH_SIZE,
    ATTR_CHUNK_COUNT,
    ATTR_DEGRADED,
    ATTR_ELEMENT_COUNT,
    ATTR_EMBEDDING_MODE,
    ATTR_EMBEDDING_MODEL,
    ATTR_ENTITY_COUNT,
    ATTR_EXTRACT_BACKEND,
    ATTR_RELATION_COUNT,
    ATTR_ROW_COUNT,
    ATTR_SOURCE_TYPE,
    ATTR_STRATEGY,
    SPAN_CHUNK,
    SPAN_EMBED,
    SPAN_EXTRACT,
    SPAN_INGEST,
    SPAN_PARTITION,
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

logger = logging.getLogger(__name__)

# Public surface: the orchestrators defined here PLUS the stage functions
# moved to ingest_embed/ingest_extract/ingest_reembed (re-exported so
# historical import paths and the tests' monkeypatch targets —
# ``ingest_common.embed_chunks``, ``ingest_common.PostgresIngestStore`` —
# keep working unchanged).
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


async def run_pipeline(
    text: str,
    source_type: str,
    path: str,
    config: dict[str, Any],
    pg_pool: asyncpg.Pool | SqlGateway,
    tenant_id: str = DEFAULT_TENANT_ID,
    rag_backend: RagBackend | None = None,
    is_file: bool = False,
    strategy_name: str | None = None,
) -> dict[str, Any]:
    """Run the full ingest pipeline: resolve strategy, build chunks, embed,
    extract, store.

    The strategy is resolved ONCE per document (precedence: explicit
    ``strategy_name`` override > ``ingestion.strategies[source_type]`` config
    mapping > content detection > ``ingestion.default``); its ChunkPlan
    replaces the partition+chunk step. Everything downstream (embed ->
    extract -> store) is strategy-agnostic.

    ``pg_pool`` is the storage seam: an asyncpg pool (Postgres) or any
    ``SqlGateway`` (e.g. ``DuckDbGateway``) — ONE pipeline, both backends;
    ``database.backend`` in the config picks the statement variants.

    Integrity semantics (migration 022):

    - A per-(tenant, content-hash) advisory lock (``advisory_lock_ctx``:
      pg_try_advisory_lock on Postgres, an in-process lock on DuckDB)
      serializes concurrent ingests of the same content; contention returns
      an honest ``{"status": "skipped", "reason": "concurrent-run"}`` —
      never success.
    - Every run opens an ``ingest_runs`` ledger row (status 'running') before
      any stage writes and closes it after the last one. The unchanged
      short-circuit only fires for content whose last run COMPLETED with an
      empty stage error list, so a crashed partial ingest is never enshrined
      as success.

    Args:
        text: The full text content to ingest.
        source_type: Document source type ("code", "markdown", "text",
            "transcript", ...) — also the config-mapping and detection hint.
        path: Source path or a custom source identifier.
        config: Pipeline config dict.
        pg_pool: asyncpg pool (Postgres) or SqlGateway (either backend).
        tenant_id: Tenant ID for RLS.
        is_file: True if path points to a file; False if path is raw text source ID.
        strategy_name: Optional explicit ingestion-strategy override (a
            registered strategy name). Unknown names raise UnknownStrategyError.

    Returns:
        Result dict with keys: status ("success" when every stage was clean,
        "partial" when any stage recorded an error, "skipped" for the
        dedupe/concurrency short-circuits), document_id, path, source_type,
        size_bytes, chunk_count, entity_count, relation_count,
        pg_chunk_count, pg_vector_count, degraded, extractor_id, entities,
        errors, run_id (and embed_note when a fallback embedder rescued the
        embeddings) — plus the strategy provenance fields ``strategy``,
        ``strategy_basis``, ``detection_confidence``, ``detection_reason``
        (and ``warnings`` when non-empty).
    """
    with span(SPAN_INGEST, **{ATTR_SOURCE_TYPE: source_type}):
        # Profile layer (Living Corpus W5): one authored YAML drives the
        # paved pipeline. Loaded once here; strategy precedence lives in
        # the registry (explicit > profile > config map > detection >
        # default) and extraction overrides merge into a config copy.
        ingestion_profile = load_profile(config)
        config = with_profile_overrides(config, ingestion_profile)
        # `partition` covers strategy resolution + the strategy's parse (the
        # registry fused partition+chunk into one strategy call, so the plan's
        # chunk count doubles as the element count here). `chunk` covers the
        # plan -> Chunk model conversion.
        with span(SPAN_PARTITION) as partition_span:
            resolution = resolve_strategy(
                source_type,
                Path(path).name,
                text[:DETECT_HEAD_CHARS],
                config,
                strategy_override=strategy_name,
                profile=ingestion_profile,
            )
            profile = SourceProfile(source_type=source_type, is_file=is_file, path=path)
            plan = resolution.strategy.build_chunks(text, Path(path).name, profile)
            partition_span.set_attribute(ATTR_STRATEGY, resolution.strategy.name)
            partition_span.set_attribute(ATTR_ELEMENT_COUNT, len(plan.chunks))
        # Conformance escalation (Section 0): a non-conformant plan would
        # silently tombstone or corrupt rows downstream (ingest_store tombstones
        # chunk_index >= len(chunks)), so it FAILS here — before the advisory
        # lock and the ledger, while nothing has been written. plan.warnings
        # stays the soft channel for strategy self-reported notes.
        conformance_problems = validate_plan(plan)
        if conformance_problems:
            raise PlanConformanceError(
                f"ingestion strategy {resolution.strategy.name!r} produced a "
                "non-conformant plan: " + "; ".join(conformance_problems)
            )
        if resolution.detection is not None:
            plan.detection_confidence = resolution.detection.confidence
            plan.detection_reason = resolution.detection.reason
        # For files the strategy derives the document text from the parsed
        # content (previously the unstructured partition step did); binary
        # formats work because the strategy partitions the FILE, never decoding
        # bytes as UTF-8 first.
        text = plan.document_text
        document = build_document(path, source_type, text)
        # WP-1 date provenance: the declared document date (web
        # published_time/modified_time, pptx core-properties, a future
        # declared-date strategy) rides in metadata as ISO text, so the
        # Document model stays schema-stable. ingest_store lifts it into
        # the documents.document_date column on write.
        document_date = plan_document_date(chunk.metadata for chunk in plan.chunks)
        if document_date is not None:
            document.metadata["document_date"] = document_date.isoformat()
        with span(SPAN_CHUNK, **{ATTR_STRATEGY: resolution.strategy.name}) as chunk_span:
            chunks = chunks_from_plan(plan, document.document_id)
            chunk_span.set_attribute(ATTR_CHUNK_COUNT, len(chunks))
        file_hash = _sha256(text)

        # Serialize concurrent ingests of the same content per tenant. The
        # Postgres lock is session-scoped on its own pooled connection, so a
        # crashed process releases it server-side automatically; the DuckDB
        # lock is in-process (the file lock covers cross-process).
        async with advisory_lock_ctx(
            pool=_raw_pool(pg_pool), tenant_id=tenant_id, key=file_hash
        ) as acquired:
            if not acquired:
                return {
                    "status": "skipped",
                    "reason": "concurrent-run",
                    "path": path,
                    "source_type": source_type,
                    "errors": [],
                }
            result = await _run_locked_stages(
                document=document,
                chunks=chunks,
                file_hash=file_hash,
                text=text,
                source_type=source_type,
                path=path,
                config=config,
                pg_pool=pg_pool,
                tenant_id=tenant_id,
                rag_backend=rag_backend,
                is_file=is_file,
                empty_reason=plan.empty_reason,
                strategy=resolution.strategy.name,
            )
            # Strategy provenance (plan Section 0): which seam produced the
            # chunks. Attached here because resolution/plan live in this scope.
            result["strategy"] = resolution.strategy.name
            result["strategy_basis"] = resolution.basis
            result["profile"] = ingestion_profile.name if ingestion_profile is not None else None
            if resolution.detection is not None:
                result["detection_confidence"] = round(resolution.detection.confidence, 4)
                result["detection_reason"] = resolution.detection.reason
            if resolution.detected is not None:
                result["detected_strategy"] = resolution.detected
            if plan.warnings:
                result["warnings"] = list(plan.warnings)
            return result


async def _run_locked_stages(
    document: Document,
    chunks: list[Chunk],
    file_hash: str,
    text: str,
    source_type: str,
    path: str,
    config: dict[str, Any],
    pg_pool: asyncpg.Pool | SqlGateway,
    tenant_id: str,
    rag_backend: RagBackend | None,
    is_file: bool,
    empty_reason: str | None = None,
    strategy: str | None = None,
) -> dict[str, Any]:
    """Pipeline stages under the advisory lock: open the ledger, store, close.

    Zero-drift W4C xenon ratchet: the stage bodies moved to private helpers
    IN THIS FILE (``_prepare_chunks_for_store``, ``_stage_store_and_embed``,
    ``_stage_shadow_hierarchy``, ``_stage_extract_entities``,
    ``_stage_result_dict``) so the complexity gate holds at --max-absolute D.
    They resolve the module globals (``embed_chunks``,
    ``PostgresIngestStore``) at call time, so tests monkeypatching
    ``ingest_common.*`` attributes keep working unchanged. ``strategy``
    (the resolved strategy name) only feeds the shadow hierarchy's boundary
    adapter choice.
    """
    ingest_store = PostgresIngestStore(pg_pool, tenant_id)
    pool = _raw_pool(pg_pool)

    unchanged_doc = await ingest_store.unchanged_doc_id(document.path, file_hash)
    if unchanged_doc is not None:
        return {
            "status": "skipped",
            "document_id": unchanged_doc,
            "path": path,
            "source_type": source_type,
            "reason": "unchanged",
            "errors": [],
        }

    document_id, run_id = await ingest_store.begin_run(document, file_hash)
    try:
        errors: list[str] = []
        _prepare_chunks_for_store(
            document,
            chunks,
            path,
            is_file,
            config,
            source_type,
            text,
            document_id,
            errors,
            empty_reason,
        )

        (
            pg_chunk_count,
            pg_vector_count,
            degraded,
            embed_note,
            embed_meta,
        ) = await _stage_store_and_embed(ingest_store, chunks, config, text, pool, errors)

        (
            hierarchy_settings,
            shadow_chunk_count,
            shadow_vector_count,
            shadow_note,
        ) = await _stage_shadow_hierarchy(
            config, document, text, chunks, pg_pool, tenant_id, errors, strategy
        )

        with span(SPAN_EXTRACT) as extract_span:
            entities, relations, extractor_id, stats = await _stage_extract_entities(
                ingest_store, chunks, document_id, config, pool, errors
            )
            extract_span.set_attribute(ATTR_EXTRACT_BACKEND, extractor_id)
            extract_span.set_attribute(ATTR_ENTITY_COUNT, len(entities))
            extract_span.set_attribute(ATTR_RELATION_COUNT, len(relations))

        statements_summary = await _stage_atomize(
            ingest_store, chunks, entities, config, tenant_id, errors
        )

        # Mirror chunks into LlamaIndex RAG backend (additive path)
        if rag_backend is not None:
            try:
                await rag_backend.ingest(
                    document.document_id,
                    [c.model_dump() for c in chunks],
                )
            except Exception as exc:
                logger.warning("LlamaIndex ingest failed: %s", exc)
                errors.append(f"LlamaIndexError: {exc}")

        # Update document chunk_count
        document.chunk_count = len(chunks)
        try:
            await ingest_store.update_document(document, file_hash)
        except Exception as exc:
            logger.warning("Document update failed: %s", exc)
            errors.append(f"PostgresWriteError: {exc}")

        stages: dict[str, Any] = {
            "chunks": pg_chunk_count,
            "vectors": pg_vector_count,
            "entities": len(entities),
            "relations": len(relations),
            "degraded": degraded,
            "extractor_id": extractor_id,
            "errors": errors,
        }
        if hierarchy_settings.enabled:
            stages["shadow_chunk_count"] = shadow_chunk_count
            stages["shadow_vector_count"] = shadow_vector_count

        # Close the ledger LAST — completed only when no stage recorded an
        # error; failed with the error text otherwise.
        await ingest_store.finish_run(run_id, file_hash, stages, errors)

        return _stage_result_dict(
            document=document,
            chunks=chunks,
            source_type=source_type,
            path=path,
            degraded=degraded,
            embed_note=embed_note,
            embed_meta=embed_meta,
            empty_reason=empty_reason,
            hierarchy_settings=hierarchy_settings,
            shadow_chunk_count=shadow_chunk_count,
            shadow_vector_count=shadow_vector_count,
            shadow_note=shadow_note,
            entities=entities,
            relations=relations,
            extractor_id=extractor_id,
            stats=stats,
            pg_chunk_count=pg_chunk_count,
            pg_vector_count=pg_vector_count,
            errors=errors,
            run_id=run_id,
            statements=statements_summary,
        )
    except BaseException as exc:
        # BaseException, not Exception: asyncio.CancelledError (watch-mode
        # Ctrl-C) is a BaseException in 3.11+ and must still close the
        # ledger row — a run cancelled mid-flight must never strand at
        # 'running'. fail_run is best-effort and never raises; the
        # cancellation (or any other BaseException) propagates unchanged.
        await ingest_store.fail_run(run_id, f"{type(exc).__name__}: {exc}")
        raise


def _prepare_chunks_for_store(
    document: Document,
    chunks: list[Chunk],
    path: str,
    is_file: bool,
    config: dict[str, Any],
    source_type: str,
    text: str,
    document_id: str,
    errors: list[str],
    empty_reason: str | None,
) -> None:
    """Stage 1: identity + provenance + optional contextual blurbs.

    A zero-chunk plan must never complete clean: the EmptyPlanError seeds
    the stage error list BEFORE finish_run, so the ledger closes failed and
    the unchanged-probe can never enshrine an empty run as success. The
    strategy's empty_reason (when it gave one) is surfaced verbatim in the
    result dict by the caller.
    """
    if not chunks:
        errors.append(f"EmptyPlanError: {empty_reason or 'strategy produced no chunks'}")
    document.document_id = document_id
    for chunk in chunks:
        chunk.document_id = document_id
        chunk.chunk_hash = _sha256(chunk.text)
        chunk.source_timestamp = (
            datetime.fromtimestamp(Path(path).stat().st_mtime, tz=UTC)
            if is_file
            else datetime.now(UTC)
        )

    if _contextual_enabled(config, source_type):
        from ..rag.contextualizer import ContextGenerator

        blurbs = ContextGenerator(config).generate_blurbs(text, [c.text for c in chunks])
        for chunk, blurb in zip(chunks, blurbs, strict=True):
            chunk.context_blurb = blurb or None


async def _stage_store_and_embed(
    ingest_store: PostgresIngestStore,
    chunks: list[Chunk],
    config: dict[str, Any],
    text: str,
    pool: asyncpg.Pool | None,
    errors: list[str],
) -> tuple[int, int, bool, str | None, dict[str, Any]]:
    """Stage 2: store chunks, embed the new ones, store their vectors.

    Returns (pg_chunk_count, pg_vector_count, degraded, embed_note, embed_meta).
    Stage errors are appended to ``errors``; the dimension-switch guard's
    ConfigError propagates typed (a width mismatch is a config problem, not
    a write failure — the BaseException handler in the caller closes the
    ledger on the way out).
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


async def _stage_shadow_hierarchy(
    config: dict[str, Any],
    document: Document,
    text: str,
    chunks: list[Chunk],
    source: asyncpg.Pool | SqlGateway,
    tenant_id: str,
    errors: list[str],
    strategy: str | None = None,
) -> tuple[HierarchySettings, int, int, str | None]:
    """Stage 3: the U48/P6 side-by-side SHADOW hierarchy (additive only).

    The v1 chunks/vectors stay the live search corpus; any shadow failure is
    logged and isolated so it can never break the live ingest. ``strategy``
    (the resolved strategy name) steers the shadow tree's boundary adapter.
    Returns (hierarchy_settings, shadow_chunk_count, shadow_vector_count,
    shadow_note).
    """
    hierarchy_settings = load_hierarchy_settings(config)
    shadow_chunk_count = 0
    shadow_vector_count = 0
    shadow_note: str | None = None
    if hierarchy_settings.enabled:
        try:
            from ..tools.shadow_ingest import write_shadow_hierarchy

            shadow_chunk_count, shadow_vector_count, shadow_note = await write_shadow_hierarchy(
                pool=source,
                tenant_id=tenant_id,
                document_id=document.document_id,
                text=text,
                chunks=chunks,
                config=config,
                settings=hierarchy_settings,
                strategy=strategy,
            )
        except Exception as exc:
            logger.warning("Shadow hierarchy write failed: %s", exc)
            errors.append(f"ShadowWriteError: {exc}")
    return hierarchy_settings, shadow_chunk_count, shadow_vector_count, shadow_note


async def _stage_extract_entities(
    ingest_store: PostgresIngestStore,
    chunks: list[Chunk],
    document_id: str,
    config: dict[str, Any],
    pool: asyncpg.Pool | None,
    errors: list[str],
) -> tuple[list[Entity], list[Relation], str, ExtractStats]:
    """Stage 4: extract entities/relations and persist them.

    Every failure lands in ``errors`` with an honest ExtractStats note; the
    disabled path reports ``extractor_id="none"`` instead of pretending.
    """
    entities: list[Entity] = []
    relations: list[Relation] = []
    extractor_id = "none"
    stats = ExtractStats(backend="none", notes=("extraction disabled",))
    if not _extract_entities_flag(config):
        return entities, relations, extractor_id, stats
    try:
        cached_hits, pending, cache, cache_key = await _cache_lookup(
            ingest_store, chunks, config, pool
        )
        if pending:
            entities, relations, extractor_id, stats = await extract_with_fallback(
                pending, ontology(config), document_id, config, pool=pool
            )
        else:
            extractor_id = cache_key[0]
            stats = ExtractStats(
                backend=extractor_id,
                notes=("all chunks served from extraction cache",),
            )
        if cache is not None and pending:
            try:
                await _cache_store(cache, pending, entities, relations, cache_key)
            except Exception as exc:
                logger.warning("Extraction cache write skipped: %s", exc)
            stats = _stat_with_note(
                stats,
                f"extraction_cache: {len(cached_hits)} of {len(chunks)} chunks served from cache",
            )
        if cached_hits:
            for hit_entities, hit_relations in cached_hits.values():
                entities = hit_entities + entities
                relations = hit_relations + relations
        if entities:
            entity_id_map = await ingest_store.store_entities(entities)
            # The dedup keeps the canonical entity row; re-point the
            # relations at it so the FK cannot see extraction-time ids.
            relations = [
                relation.model_copy(
                    update={
                        "source_entity_id": entity_id_map.get(
                            relation.source_entity_id, relation.source_entity_id
                        ),
                        "target_entity_id": entity_id_map.get(
                            relation.target_entity_id, relation.target_entity_id
                        ),
                    }
                )
                for relation in relations
            ]
        if relations:
            graph_cfg = _nested_dict(config, "graph")
            await ingest_store.store_relations(
                relations,
                model_version=cast("str | None", graph_cfg.get("model_version")),
                prompt_version=cast("str | None", graph_cfg.get("prompt_version")),
            )
    except Exception as exc:
        logger.warning("Entity extraction failed: %s", exc)
        errors.append(f"ExtractionError: {exc}")
        stats = ExtractStats(
            backend=extractor_id,
            notes=(f"extraction failed: {type(exc).__name__}: {exc}",),
        )
    return entities, relations, extractor_id, stats


def _stat_with_note(stats: ExtractStats, note: str) -> ExtractStats:
    from dataclasses import replace

    return replace(stats, notes=(*stats.notes, note))


async def _stage_atomize(
    ingest_store: PostgresIngestStore,
    chunks: list[Chunk],
    entities: list[Entity],
    config: dict[str, Any],
    tenant_id: str,
    errors: list[str],
) -> dict[str, Any]:
    """Stage 5: atomize chunks into classified statement atoms (additive).

    Config-gated (``statements.enabled``) and failure-isolated: any error
    lands in ``errors`` and the summary reports zero stored — the ingest
    result never pretends atoms exist when the stage did not run.
    """
    cfg = cast(dict[str, Any], config.get("statements") or {})
    if cfg.get("enabled") is not True:
        return {"status": "disabled", "stored": 0}
    try:
        from ..statements import (
            StatementStore,
            atomize_chunk,
            load_templates,
            template_histogram,
        )

        catalogue = load_templates(
            cast("str", cfg.get("templates_dir") or "config/claim_templates")
        )
        atoms = [atom for chunk in chunks for atom in atomize_chunk(chunk, entities, catalogue)]
        store = StatementStore(ingest_store.gateway, tenant_id)
        stored = await store.store_statements(atoms)
        return {"status": "success", "stored": stored, "templates": template_histogram(atoms)}
    except Exception as exc:
        logger.warning("Statement atomization failed: %s", exc)
        errors.append(f"AtomizeError: {exc}")
        return {"status": "failed", "stored": 0}


def _cache_versions(config: dict[str, Any]) -> tuple[str, str, str]:
    """(backend, model_version, prompt_version) — the cache key suffix.

    The suffix folds in everything that changes extraction output for
    one chunk: backend name, extraction mode, and the GLiNER2/Relex
    model ids, so a config flip (customized → zero_shot, a model swap)
    can never replay stale cached extractions.
    """
    graph_cfg = cast(dict[str, Any], config.get("graph") or {})
    extraction_cfg = cast(dict[str, Any], config.get("extraction") or {})
    mode = str(extraction_cfg.get("mode") or "zero_shot")
    gliner_model = str((extraction_cfg.get("gliner") or {}).get("model") or "unset")
    relex_model = str((extraction_cfg.get("relex") or {}).get("model") or "unset")
    model_version = "|".join(
        [str(graph_cfg.get("model_version") or "unset"), mode, gliner_model, relex_model]
    )
    return (
        extraction_backend_name(config),
        model_version,
        str(graph_cfg.get("prompt_version") or "unset"),
    )


async def _cache_lookup(
    ingest_store: PostgresIngestStore,
    chunks: list[Chunk],
    config: dict[str, Any],
    pool: asyncpg.Pool | None,
) -> tuple[
    dict[str, tuple[list[Entity], list[Relation]]],
    list[Chunk],
    ExtractionCache | None,
    tuple[str, str, str],
]:
    """Read the extraction cache; return (hits, pending chunks, cache, key)."""
    cache_cfg = cast(dict[str, Any], config.get("extraction") or {}).get("cache")
    enabled = cache_cfg.get("enabled", True) if isinstance(cache_cfg, dict) else True
    key = _cache_versions(config)
    if not enabled or pool is None or not chunks:
        return {}, list(chunks), None, key
    cache = ExtractionCache(pool, ingest_store.tenant_id)
    hashes = [c.chunk_hash for c in chunks if c.chunk_hash]
    try:
        hits = await cache.get(hashes, backend=key[0], model_version=key[1], prompt_version=key[2])
    except Exception as exc:
        # A missing or unreadable cache table must never fail ingestion:
        # record the degradation and run uncached.
        logger.warning("Extraction cache unavailable; running uncached: %s", exc)
        return {}, list(chunks), None, key
    pending = [c for c in chunks if c.chunk_hash not in hits]
    return hits, pending, cache, key


async def _cache_store(
    cache: ExtractionCache,
    pending: list[Chunk],
    entities: list[Entity],
    relations: list[Relation],
    key: tuple[str, str, str],
) -> None:
    """Write this run's per-chunk results under the cache key suffix."""
    fresh: dict[str, tuple[list[Entity], list[Relation]]] = {}
    for chunk in pending:
        if not chunk.chunk_hash:
            continue
        fresh[chunk.chunk_hash] = (
            [e for e in entities if e.chunk_id == chunk.chunk_id],
            [r for r in relations if r.chunk_id == chunk.chunk_id],
        )
    if fresh:
        await cache.put(fresh, backend=key[0], model_version=key[1], prompt_version=key[2])


def _stage_result_dict(
    *,
    document: Document,
    chunks: list[Chunk],
    source_type: str,
    path: str,
    degraded: bool,
    embed_note: str | None,
    embed_meta: dict[str, Any],
    empty_reason: str | None,
    hierarchy_settings: HierarchySettings,
    shadow_chunk_count: int,
    shadow_vector_count: int,
    shadow_note: str | None,
    entities: list[Entity],
    relations: list[Relation],
    extractor_id: str,
    stats: ExtractStats,
    pg_chunk_count: int,
    pg_vector_count: int,
    errors: list[str],
    run_id: str,
    statements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stage 5: the run result dict (nothing reports success on a partial).

    Any stage error flips the status (the ledger row is 'failed' either
    way). Shadow keys are present ONLY when the hierarchy flag is on, so
    the flag-off result dict stays byte-identical to the pre-U48 shape.
    """
    result: dict[str, Any] = {
        "status": "success" if not errors else "partial",
        "document_id": document.document_id,
        "path": path,
        "source_type": source_type,
        "size_bytes": document.size_bytes,
        "chunk_count": len(chunks),
        "entity_count": len(entities),
        "relation_count": len(relations),
        "pg_chunk_count": pg_chunk_count,
        "pg_vector_count": pg_vector_count,
        "degraded": degraded,
        "extractor_id": extractor_id,
        # Honest extraction accounting: requested vs kept vs where the
        # difference went, plus failure notes from the fallback chain.
        "extraction": {
            "extractor_id": extractor_id,
            "requested_entities": stats.requested_entities,
            "kept_entities": stats.kept_entities,
            "requested_relations": stats.requested_relations,
            "kept_relations": stats.kept_relations,
            "dropped_by_cap": stats.dropped_by_cap,
            "dropped_by_confidence": stats.dropped_by_confidence,
            "dropped_by_other": stats.dropped_by_other,
            "duration_ms": round(stats.duration_ms, 3),
            "notes": list(stats.notes),
        },
        "entities": {entity.name: entity.entity_id for entity in entities},
        "errors": errors,
        "run_id": run_id,
        "statements": statements or {"status": "disabled", "stored": 0},
    }
    if embed_note is not None:
        result["embed_note"] = embed_note
    # Honest embedding accounting (late-chunking wiring): WHICH technique
    # produced the vectors, and — only when late chunking was requested
    # but unavailable — the concrete reason it fell back.
    result["embedding_mode"] = str(embed_meta.get("embedding_mode", POOLED_MODE))
    if embed_meta.get("embedding_note") is not None:
        result["embedding_note"] = embed_meta["embedding_note"]
    if empty_reason is not None:
        result["empty_reason"] = empty_reason
    if hierarchy_settings.enabled:
        result["shadow_chunk_count"] = shadow_chunk_count
        result["shadow_vector_count"] = shadow_vector_count
        if shadow_note is not None:
            result["shadow_note"] = shadow_note
    return result


async def ingest_text(
    text: str,
    pg_pool: asyncpg.Pool | SqlGateway,
    source_type: str = "text",
    config: dict[str, Any] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    source: str = "raw_text",
) -> dict[str, Any]:
    """Ingest raw text with optional type hint and source identifier.

    Lives here (rather than only in ``ingest_tools``) so tests and other
    ingest-side code can import it alongside ``run_pipeline`` without a
    circular import; ``ingest_tools.ingest_text`` re-exports this function.

    ``source_type`` is the DOCUMENT source type (it feeds the
    ``ingestion.strategies`` config mapping and content detection); use the
    explicit ``run_pipeline(strategy_name=...)`` override to pin a strategy.
    """
    if source_type not in {"code", "markdown", "text", "transcript"}:
        return {"status": "error", "message": f"Invalid source_type: {source_type}"}
    config = load_config_or_pass(config)
    return await run_pipeline(text, source_type, source, config, pg_pool, tenant_id, is_file=False)
