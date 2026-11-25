"""The locked pipeline stages (unified-ingestion Wave 2).

``_run_document_stages`` opens the ledger row, runs the stage callables
in order (prepare -> store+embed -> shadow -> extract -> atomize ->
result), and closes the ledger — all under the advisory lock the caller
(the plan stage, ``pipeline.plan.run_pipeline``) already holds.

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
through every stage call; ``_run_document_stages`` is the document-first
orchestrator (the legacy keyword seam was removed — PR 280 F2).
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

from ...storage.gateway import SqlGateway
from ...storage.rag_backend import RagBackend
from ._facade import _ic
from .document import IngestDocument
from .envelope import _claimed_envelope, _envelope_from, _ledger_stages, _skipped_envelope
from .produce import _commit_document, _mirror_rag_backend, _produce_artifacts
from .sinks import ChunkSink, IngestSink

logger = logging.getLogger(__name__)

__all__ = ["_run_document_stages"]


def _declared_visibility(config: dict[str, Any]) -> str | None:
    """The caller-declared visibility for typed external-site ingests."""
    value = ((config or {}).get("document") or {}).get("visibility")
    return value if value in {"public", "internal"} else None


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


async def _run_document_stages(
    doc: IngestDocument,
    config: dict[str, Any],
    pg_pool: asyncpg.Pool | SqlGateway,
    tenant_id: str,
    rag_backend: RagBackend | None,
    validation_report: dict[str, Any] | None = None,
    validation_mode: str = "off",
    calibration_summary: dict[str, Any] | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Pipeline stages under the advisory lock: open the ledger, store, close.

    The store is resolved through the facade (``_ic().PostgresIngestStore``)
    and wrapped in the ``ChunkSink`` write port; every sink call is a
    one-line delegation to the store, so the write behavior is byte
    identical. ``doc.strategy`` (the resolved strategy name) only feeds the
    shadow hierarchy's boundary adapter choice.
    """
    ingest_store = _ic().PostgresIngestStore(pg_pool, tenant_id)
    ingest_sink: IngestSink = ChunkSink(ingest_store)
    if project_id:
        # The project link (migrations 016/036) rides metadata into the
        # document row's JSON, next to the column stamps below (Track C3).
        doc.document.metadata["project_id"] = project_id
    # The per-book title (issue #222): documents.title is the durable
    # channel — the store upsert reads it behind a COALESCE guard — while
    # metadata["title"] is the transport into that upsert. One read, one
    # value, and the result envelope carries the same value.
    title = _book_title(config)
    if title is not None:
        doc.document.metadata["title"] = title
    declared = _declared_visibility(config)
    if declared is not None:
        doc.document.metadata["visibility"] = declared

    # Hash-first dedupe (W4, F-06): the hash probe answers across path
    # spellings; the path probe stays as the fallback (subsumed, harmless).
    unchanged_doc = await ingest_sink.unchanged_hash_doc_id(doc.file_hash)
    if unchanged_doc is None:
        unchanged_doc = await ingest_sink.unchanged_doc_id(doc.path, doc.file_hash)
    if unchanged_doc is not None:
        origin = await ingest_store.document_origin_project(unchanged_doc)
        if project_id and origin != project_id:
            # The claim write happens BEFORE any chunk/embed/extract work:
            # reuse before re-ingest (spec s3). The origin document never
            # moves; membership rides the project_claims row.
            await ingest_store.claim_document(unchanged_doc, project_id, origin)
            return _claimed_envelope(doc, unchanged_doc, origin)
        return _skipped_envelope(doc, unchanged_doc)

    document_id, run_id = await ingest_sink.begin_run(doc.document, doc.file_hash)
    if project_id:
        # The project link (migrations 016/036) for ALL pipeline paths:
        # the ledger row and the document row both carry it. Both writes
        # are best effort — the link can never fail an ingest.
        await ingest_store.set_run_project(run_id, project_id)
        await ingest_store.set_document_project(document_id, project_id)
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

        envelope = _envelope_from(doc, artifacts, errors, run_id, title)
        if project_id and envelope.get("status") in ("success", "partial"):
            # Track C3: raw content-addressed storage into the project's
            # raw table, with honest envelope keys.
            from .project_raw import attach_project_raw

            await attach_project_raw(
                envelope, doc.text, doc.path, doc.is_file, pg_pool, tenant_id, project_id
            )
        return envelope
    except BaseException as exc:
        # BaseException, not Exception: asyncio.CancelledError (watch-mode
        # Ctrl-C) is a BaseException in 3.11+ and must still close the
        # ledger row — a run cancelled mid-flight must never strand at
        # 'running'. fail_run is best-effort and never raises; the
        # cancellation (or any other BaseException) propagates unchanged.
        await ingest_sink.fail_run(run_id, f"{type(exc).__name__}: {exc}")
        raise
