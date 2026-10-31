"""The locked pipeline stages (unified-ingestion Wave 2).

``_run_locked_stages`` opens the ledger row, runs the stage callables in
order (prepare -> store+embed -> shadow -> extract -> atomize -> result),
and closes the ledger — all under the advisory lock the caller (the plan
stage, ``pipeline.plan.run_pipeline``) already holds.

One small module per stage: ``plan`` (strategy resolution and the
ChunkPlan), ``prepare`` (chunk identity and provenance), ``shadow`` (the
additive shadow tree), ``extract`` (extraction, cache, persistence),
``atomize`` (statement atoms), ``result`` (the run envelope). The
store+embed stage stays in ``tools/ingest_common.py``: its
``build_embedding_input`` call site is pinned by a source-inspection
test, so moving it is deferred to Wave 3. Every name the tests
monkeypatch (``PostgresIngestStore``, ``embed_chunks``,
``extract_with_fallback``, ``run_pipeline``) is resolved through the
``ingest_common`` facade at CALL time via ``_ic()``, so the historical
patch seams keep working unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

from ...storage.gateway import SqlGateway
from ...storage.rag_backend import RagBackend
from ...utils.models import Chunk, Document
from ...utils.tracing import (
    ATTR_ENTITY_COUNT,
    ATTR_EXTRACT_BACKEND,
    ATTR_RELATION_COUNT,
    SPAN_EXTRACT,
    span,
)
from ._facade import _ic
from .atomize import _stage_atomize
from .document import IngestDocument
from .extract import _stage_extract_entities
from .prepare import _prepare_chunks_for_store
from .result import _stage_result_dict
from .shadow import _stage_shadow_hierarchy
from .sinks import ChunkSink, IngestSink

logger = logging.getLogger(__name__)

__all__ = ["_run_locked_stages"]


def _book_title(config: dict[str, Any]) -> str | None:
    """The per-book title from the config's ``ingestion`` block, or None.

    The queue worker forwards the row's ``title`` there (issue #222). The
    envelope carries the key only when a title was set, so the default
    ingest shape stays byte-identical. The store lifts the title into
    documents.title on the direct upsert; the Ingested event carries it
    to the projection's own upsert.
    """
    ingestion = config.get("ingestion")
    raw = ingestion.get("title") if isinstance(ingestion, dict) else None
    if not isinstance(raw, str):
        return None
    title = raw.strip()
    return title or None


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
    validation_report: dict[str, Any] | None = None,
    validation_mode: str = "off",
    calibration_summary: dict[str, Any] | None = None,
    textbook_summary: dict[str, object] | None = None,
) -> dict[str, Any]:
    """Pipeline stages under the advisory lock: open the ledger, store, close.

    The store is resolved through the facade (``_ic().PostgresIngestStore``)
    and wrapped in the ``ChunkSink`` write port; every sink call is a
    one-line delegation to the store, so the write behavior is byte
    identical. ``strategy`` (the resolved strategy name) only feeds the
    shadow hierarchy's boundary adapter choice.
    """
    ingest_sink: IngestSink = ChunkSink(_ic().PostgresIngestStore(pg_pool, tenant_id))
    pool = _ic()._raw_pool(pg_pool)
    # The per-book title (issue #222): documents.title is the durable
    # channel — the store upsert reads it behind a COALESCE guard — while
    # metadata["title"] is the transport into that upsert. One read, one
    # value, and the result envelope carries the same value.
    title = _book_title(config)
    if title is not None:
        document.metadata["title"] = title
    canonical = IngestDocument(
        document=document,
        text=text,
        file_hash=file_hash,
        source_type=source_type,
        path=path,
        is_file=is_file,
        chunks=chunks,
        empty_reason=empty_reason,
        strategy=strategy,
    )

    unchanged_doc = await ingest_sink.unchanged_doc_id(canonical.path, canonical.file_hash)
    if unchanged_doc is not None:
        return {
            "status": "skipped",
            "document_id": unchanged_doc,
            "path": path,
            "source_type": source_type,
            "reason": "unchanged",
            "errors": [],
        }

    document_id, run_id = await ingest_sink.begin_run(canonical.document, canonical.file_hash)
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
            pg_chunk_total,
            pg_vector_count,
            degraded,
            embed_note,
            embed_meta,
        ) = await _ic()._stage_store_and_embed(ingest_sink, chunks, config, text, pool, errors)

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
                ingest_sink, chunks, document_id, config, pool, errors
            )
            extract_span.set_attribute(ATTR_EXTRACT_BACKEND, extractor_id)
            extract_span.set_attribute(ATTR_ENTITY_COUNT, len(entities))
            extract_span.set_attribute(ATTR_RELATION_COUNT, len(relations))

        statements_summary = await _stage_atomize(
            ingest_sink, chunks, entities, config, tenant_id, errors
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
            await ingest_sink.update_document(document, file_hash)
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
        # Wave 4 calibrate mode: the validation report rides the ledger
        # stages JSONB (the Wave 7 calibration feed) without blocking and
        # without touching the envelope.
        if validation_mode == "calibrate" and validation_report is not None:
            stages["validation"] = validation_report
        # Wave 7: the calibration sweep summary rides the ledger the same
        # way. A refused sweep writes nothing (its reason went to the log).
        if calibration_summary:
            stages["calibration"] = calibration_summary

        # Close the ledger LAST — completed only when no stage recorded an
        # error; failed with the error text otherwise.
        await ingest_sink.finish_run(run_id, file_hash, stages, errors)

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
            pg_chunk_total=pg_chunk_total,
            pg_vector_count=pg_vector_count,
            errors=errors,
            run_id=run_id,
            statements=statements_summary,
            title=title,
            textbook_summary=textbook_summary,
        )
    except BaseException as exc:
        # BaseException, not Exception: asyncio.CancelledError (watch-mode
        # Ctrl-C) is a BaseException in 3.11+ and must still close the
        # ledger row — a run cancelled mid-flight must never strand at
        # 'running'. fail_run is best-effort and never raises; the
        # cancellation (or any other BaseException) propagates unchanged.
        await ingest_sink.fail_run(run_id, f"{type(exc).__name__}: {exc}")
        raise
