"""Stage 5b: the run result envelope."""

from __future__ import annotations

from typing import Any

from corpus_kb.chunking.hierarchy_settings import HierarchySettings

from ...extraction import ExtractStats
from ...rag.embedder_selection import POOLED_MODE
from ...utils.models import Chunk, Document, Entity, Relation


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
    pg_chunk_total: int,
    pg_vector_count: int,
    errors: list[str],
    run_id: str,
    statements: dict[str, Any] | None = None,
    title: str | None = None,
    textbook_summary: dict[str, object] | None = None,
) -> dict[str, Any]:
    """Stage 5: the run result dict (nothing reports success on a partial).

    Any stage error flips the status (the ledger row is 'failed' either
    way). Shadow keys are present ONLY when the hierarchy flag is on, so
    the flag-off result dict stays byte-identical to the pre-U48 shape.
    ``pg_chunk_total`` is the document's whole stored count; it stays
    honest when a re-run writes nothing (issue #222). ``title`` appears
    only when the per-book config carried one; ``textbook`` only when
    the textbook strategy ran its windowed parse.
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
        "pg_chunk_total": pg_chunk_total,
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
    if title is not None:
        result["title"] = title
    if textbook_summary is not None:
        result["textbook"] = textbook_summary
    if hierarchy_settings.enabled:
        result["shadow_chunk_count"] = shadow_chunk_count
        result["shadow_vector_count"] = shadow_vector_count
        if shadow_note is not None:
            result["shadow_note"] = shadow_note
    return result
