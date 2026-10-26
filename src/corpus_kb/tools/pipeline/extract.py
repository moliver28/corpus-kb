"""Stage 4: entity/relation extraction, cache, and persistence."""

from __future__ import annotations

import logging
from typing import Any, cast

import asyncpg

from ...extraction import ExtractStats, extraction_backend_name
from ...extraction.cache import ExtractionCache
from ...utils.models import Chunk, Entity, Relation
from ..ingest_cfg import _nested_dict
from ._facade import _ic
from .sinks import IngestSink

logger = logging.getLogger(__name__)


async def _stage_extract_entities(
    ingest_sink: IngestSink,
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
    if not _ic()._extract_entities_flag(config):
        return entities, relations, extractor_id, stats
    try:
        cached_hits, pending, cache, cache_key = await _cache_lookup(
            ingest_sink, chunks, config, pool
        )
        if pending:
            entities, relations, extractor_id, stats = await _ic().extract_with_fallback(
                pending, _ic().ontology(config), document_id, config, pool=pool
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
            entity_id_map = await ingest_sink.store_entities(entities)
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
            await ingest_sink.store_relations(
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
    ingest_sink: IngestSink,
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
    cache = ExtractionCache(pool, ingest_sink.tenant_id)
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
