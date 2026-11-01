"""Stages 1-5: produce one run's artifacts from the canonical document.

``_produce_artifacts`` runs the producing stage callables in order
(prepare -> store+embed -> shadow -> extract -> atomize) and packs what
they returned into one ``StageArtifacts`` value, so the ledger stage
(``pipeline.stages``) never juggles ten loose locals. The post-production
write-backs (the additive LlamaIndex mirror and the document refresh)
live here too — both are failure-isolated: a failure logs one line and
lands in the stage error list, it never aborts the run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import asyncpg

from ...chunking.hierarchy_settings import HierarchySettings
from ...extraction import ExtractStats
from ...storage.gateway import SqlGateway
from ...storage.rag_backend import RagBackend
from ...utils.models import Entity, Relation
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
from .shadow import _stage_shadow_hierarchy
from .sinks import IngestSink

logger = logging.getLogger(__name__)

__all__ = ["StageArtifacts", "_commit_document", "_mirror_rag_backend", "_produce_artifacts"]


@dataclass
class StageArtifacts:
    """What the producing stages returned for one run.

    One value per run: the store+embed outcome, the shadow-hierarchy
    outcome, and the extraction outcome. The ledger stage and the result
    envelope both read from it; nothing here is mutated after the run.
    """

    pg_chunk_count: int
    pg_chunk_total: int
    pg_vector_count: int
    degraded: bool
    embed_note: str | None
    embed_meta: dict[str, Any]
    hierarchy_settings: HierarchySettings
    shadow_chunk_count: int
    shadow_vector_count: int
    shadow_note: str | None
    entities: list[Entity]
    relations: list[Relation]
    extractor_id: str
    stats: ExtractStats
    statements: dict[str, Any]


async def _produce_artifacts(
    ingest_sink: IngestSink,
    doc: IngestDocument,
    config: dict[str, Any],
    pg_pool: asyncpg.Pool | SqlGateway,
    tenant_id: str,
    document_id: str,
    errors: list[str],
) -> StageArtifacts:
    """Run the producing stages in order and pack their outputs.

    Order is load-bearing: prepare (identity/provenance) -> store+embed ->
    shadow hierarchy -> extract (under the SPAN_EXTRACT span) -> atomize.
    ``_stage_store_and_embed`` is resolved through the facade so the
    historical ``embed_chunks`` patch seam keeps working.
    """
    pool = _ic()._raw_pool(pg_pool)
    _prepare_chunks_for_store(doc, config, document_id, errors)

    (
        pg_chunk_count,
        pg_chunk_total,
        pg_vector_count,
        degraded,
        embed_note,
        embed_meta,
    ) = await _ic()._stage_store_and_embed(ingest_sink, doc.chunks, config, doc.text, pool, errors)

    (
        hierarchy_settings,
        shadow_chunk_count,
        shadow_vector_count,
        shadow_note,
    ) = await _stage_shadow_hierarchy(
        config, doc.document, doc.text, doc.chunks, pg_pool, tenant_id, errors, doc.strategy
    )

    with span(SPAN_EXTRACT) as extract_span:
        entities, relations, extractor_id, stats = await _stage_extract_entities(
            ingest_sink, doc.chunks, document_id, config, pool, errors
        )
        extract_span.set_attribute(ATTR_EXTRACT_BACKEND, extractor_id)
        extract_span.set_attribute(ATTR_ENTITY_COUNT, len(entities))
        extract_span.set_attribute(ATTR_RELATION_COUNT, len(relations))

    statements = await _stage_atomize(ingest_sink, doc.chunks, entities, config, tenant_id, errors)

    return StageArtifacts(
        pg_chunk_count=pg_chunk_count,
        pg_chunk_total=pg_chunk_total,
        pg_vector_count=pg_vector_count,
        degraded=degraded,
        embed_note=embed_note,
        embed_meta=embed_meta,
        hierarchy_settings=hierarchy_settings,
        shadow_chunk_count=shadow_chunk_count,
        shadow_vector_count=shadow_vector_count,
        shadow_note=shadow_note,
        entities=entities,
        relations=relations,
        extractor_id=extractor_id,
        stats=stats,
        statements=statements,
    )


async def _mirror_rag_backend(
    rag_backend: RagBackend, doc: IngestDocument, errors: list[str]
) -> None:
    """Mirror chunks into the LlamaIndex RAG backend (additive path)."""
    try:
        await rag_backend.ingest(
            doc.document.document_id,
            [chunk.model_dump() for chunk in doc.chunks],
        )
    except Exception as exc:
        logger.warning("LlamaIndex ingest failed: %s", exc)
        errors.append(f"LlamaIndexError: {exc}")


async def _commit_document(ingest_sink: IngestSink, doc: IngestDocument, errors: list[str]) -> None:
    """Refresh the stored document row after the stages (chunk_count)."""
    doc.document.chunk_count = len(doc.chunks)
    try:
        await ingest_sink.update_document(doc.document, doc.file_hash)
    except Exception as exc:
        logger.warning("Document update failed: %s", exc)
        errors.append(f"PostgresWriteError: {exc}")
