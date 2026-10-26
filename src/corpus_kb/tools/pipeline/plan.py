"""Stage 1: plan the document (strategy resolution and the ChunkPlan).

``run_pipeline`` resolves ONE strategy per document (explicit
``strategy_name`` override > profile > ``ingestion.strategies`` config
mapping > content detection > ``ingestion.default``), validates the plan,
builds the canonical document and its chunks, then hands off to the
locked stages in ``pipeline.stages``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import asyncpg

from ...analysis.dates import plan_document_date
from ...ingestion.base import (
    DETECT_HEAD_CHARS,
    PlanConformanceError,
    SourceProfile,
    chunks_from_plan,
    validate_plan,
)
from ...ingestion.registry import resolve_strategy
from ...profiles import load_profile, with_profile_overrides
from ...storage.gateway import SqlGateway
from ...storage.rag_backend import RagBackend
from ...utils.tracing import (
    ATTR_CHUNK_COUNT,
    ATTR_ELEMENT_COUNT,
    ATTR_SOURCE_TYPE,
    ATTR_STRATEGY,
    SPAN_CHUNK,
    SPAN_INGEST,
    SPAN_PARTITION,
    span,
)
from ..ingest_store import DEFAULT_TENANT_ID
from ._facade import _ic
from .stages import _run_locked_stages

logger = logging.getLogger(__name__)


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
        document = _ic().build_document(path, source_type, text)
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
        file_hash = _ic()._sha256(text)

        # Serialize concurrent ingests of the same content per tenant. The
        # Postgres lock is session-scoped on its own pooled connection, so a
        # crashed process releases it server-side automatically; the DuckDB
        # lock is in-process (the file lock covers cross-process).
        async with _ic().advisory_lock_ctx(
            pool=_ic()._raw_pool(pg_pool), tenant_id=tenant_id, key=file_hash
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
