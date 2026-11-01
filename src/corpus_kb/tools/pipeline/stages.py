"""The locked pipeline stages (unified-ingestion Wave 2).

``_run_locked_stages`` opens the ledger row, runs the stage callables in
order (prepare -> store+embed -> shadow -> extract -> atomize -> result),
and closes the ledger — all under the advisory lock the caller (the plan
stage, ``pipeline.plan.run_pipeline``) already holds.

One small module per stage: ``plan`` (strategy resolution and the
ChunkPlan), ``prepare`` (chunk identity and provenance), ``shadow`` (the
additive shadow tree), ``extract`` (extraction, cache, persistence),
``atomize`` (statement atoms), ``result`` (the run envelope), ``produce``
(the artifact run that packs the stage outputs). The store+embed stage
stays in ``tools/ingest_common.py``: its ``build_embedding_input`` call
site is pinned by a source-inspection test, so moving it is deferred to
Wave 3. Every name the tests monkeypatch (``PostgresIngestStore``,
``embed_chunks``, ``extract_with_fallback``, ``run_pipeline``) is
resolved through the ``ingest_common`` facade at CALL time via ``_ic()``,
so the historical patch seams keep working unchanged.

The canonical ``IngestDocument`` is built ONCE in ``plan`` and threaded
through every stage call. ``_run_document_stages`` is the document-first
orchestrator; ``_run_locked_stages`` keeps the historical keyword
signature as a thin seam (the pinned ingest tests call it that way) and
delegates to it.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

from ...storage.gateway import SqlGateway
from ...storage.rag_backend import RagBackend
from ...utils.models import Chunk, Document
from ._facade import _ic
from .document import IngestDocument
from .produce import StageArtifacts, _commit_document, _mirror_rag_backend, _produce_artifacts
from .result import _stage_result_dict
from .sinks import ChunkSink, IngestSink

logger = logging.getLogger(__name__)

__all__ = ["_run_document_stages", "_run_locked_stages"]


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


def _skipped_envelope(doc: IngestDocument, unchanged_doc: str) -> dict[str, Any]:
    """The unchanged-content skip envelope (no ledger row is opened)."""
    return {
        "status": "skipped",
        "document_id": unchanged_doc,
        "path": doc.path,
        "source_type": doc.source_type,
        "reason": "unchanged",
        "errors": [],
    }


def _ledger_stages(
    artifacts: StageArtifacts,
    errors: list[str],
    validation_report: dict[str, Any] | None,
    validation_mode: str,
    calibration_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    """The ledger stages JSONB: stage counts plus additive payloads.

    The shadow keys appear only when the hierarchy flag is on. Wave 4
    calibrate mode: the validation report rides the ledger stages JSONB
    (the Wave 7 calibration feed) without blocking and without touching
    the envelope. Wave 7: the calibration sweep summary rides it the same
    way; a refused sweep writes nothing (its reason went to the log).
    """
    stages: dict[str, Any] = {
        "chunks": artifacts.pg_chunk_count,
        "vectors": artifacts.pg_vector_count,
        "entities": len(artifacts.entities),
        "relations": len(artifacts.relations),
        "degraded": artifacts.degraded,
        "extractor_id": artifacts.extractor_id,
        "errors": errors,
    }
    if artifacts.hierarchy_settings.enabled:
        stages["shadow_chunk_count"] = artifacts.shadow_chunk_count
        stages["shadow_vector_count"] = artifacts.shadow_vector_count
    if validation_mode == "calibrate" and validation_report is not None:
        stages["validation"] = validation_report
    if calibration_summary:
        stages["calibration"] = calibration_summary
    return stages


def _envelope_from(
    doc: IngestDocument,
    artifacts: StageArtifacts,
    errors: list[str],
    run_id: str,
    title: str | None,
) -> dict[str, Any]:
    """The run result envelope, built from the canonical document."""
    return _stage_result_dict(
        document=doc.document,
        chunks=doc.chunks,
        source_type=doc.source_type,
        path=doc.path,
        degraded=artifacts.degraded,
        embed_note=artifacts.embed_note,
        embed_meta=artifacts.embed_meta,
        empty_reason=doc.empty_reason,
        hierarchy_settings=artifacts.hierarchy_settings,
        shadow_chunk_count=artifacts.shadow_chunk_count,
        shadow_vector_count=artifacts.shadow_vector_count,
        shadow_note=artifacts.shadow_note,
        entities=artifacts.entities,
        relations=artifacts.relations,
        extractor_id=artifacts.extractor_id,
        stats=artifacts.stats,
        pg_chunk_count=artifacts.pg_chunk_count,
        pg_chunk_total=artifacts.pg_chunk_total,
        pg_vector_count=artifacts.pg_vector_count,
        errors=errors,
        run_id=run_id,
        statements=artifacts.statements,
        title=title,
    )


async def _run_document_stages(
    doc: IngestDocument,
    config: dict[str, Any],
    pg_pool: asyncpg.Pool | SqlGateway,
    tenant_id: str,
    rag_backend: RagBackend | None,
    validation_report: dict[str, Any] | None = None,
    validation_mode: str = "off",
    calibration_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pipeline stages under the advisory lock: open the ledger, store, close.

    The store is resolved through the facade (``_ic().PostgresIngestStore``)
    and wrapped in the ``ChunkSink`` write port; every sink call is a
    one-line delegation to the store, so the write behavior is byte
    identical. ``doc.strategy`` (the resolved strategy name) only feeds the
    shadow hierarchy's boundary adapter choice.
    """
    ingest_sink: IngestSink = ChunkSink(_ic().PostgresIngestStore(pg_pool, tenant_id))
    # The per-book title (issue #222): documents.title is the durable
    # channel — the store upsert reads it behind a COALESCE guard — while
    # metadata["title"] is the transport into that upsert. One read, one
    # value, and the result envelope carries the same value.
    title = _book_title(config)
    if title is not None:
        doc.document.metadata["title"] = title

    unchanged_doc = await ingest_sink.unchanged_doc_id(doc.path, doc.file_hash)
    if unchanged_doc is not None:
        return _skipped_envelope(doc, unchanged_doc)

    document_id, run_id = await ingest_sink.begin_run(doc.document, doc.file_hash)
    try:
        errors: list[str] = []
        artifacts = await _produce_artifacts(
            ingest_sink, doc, config, pg_pool, tenant_id, document_id, errors
        )
        if rag_backend is not None:
            await _mirror_rag_backend(rag_backend, doc, errors)
        await _commit_document(ingest_sink, doc, errors)

        stages = _ledger_stages(
            artifacts, errors, validation_report, validation_mode, calibration_summary
        )

        # Close the ledger LAST — completed only when no stage recorded an
        # error; failed with the error text otherwise.
        await ingest_sink.finish_run(run_id, doc.file_hash, stages, errors)

        return _envelope_from(doc, artifacts, errors, run_id, title)
    except BaseException as exc:
        # BaseException, not Exception: asyncio.CancelledError (watch-mode
        # Ctrl-C) is a BaseException in 3.11+ and must still close the
        # ledger row — a run cancelled mid-flight must never strand at
        # 'running'. fail_run is best-effort and never raises; the
        # cancellation (or any other BaseException) propagates unchanged.
        await ingest_sink.fail_run(run_id, f"{type(exc).__name__}: {exc}")
        raise


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
) -> dict[str, Any]:
    """Legacy keyword seam: build the canonical document, run the stages.

    The pinned ingest tests call this entry with the historical keyword
    arguments; the plan stage calls ``_run_document_stages`` directly with
    the ``IngestDocument`` it already built. Behavior is identical.
    """
    return await _run_document_stages(
        IngestDocument(
            document=document,
            text=text,
            file_hash=file_hash,
            source_type=source_type,
            path=path,
            is_file=is_file,
            chunks=chunks,
            empty_reason=empty_reason,
            strategy=strategy,
        ),
        config,
        pg_pool,
        tenant_id,
        rag_backend,
        validation_report=validation_report,
        validation_mode=validation_mode,
        calibration_summary=calibration_summary,
    )
