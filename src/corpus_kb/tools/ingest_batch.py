"""Set-based Postgres batch writes for the ingest store (Wave 1 throughput).

One statement per partition of work instead of one statement per row:
``unnest`` array INSERT/UPDATE keeps the round trips flat while the exact
loop semantics stay: ``ON CONFLICT DO NOTHING`` + ``RETURNING`` for inserts
(returned ids are the input ids, so the id mapping is exact by
construction), one ``UPDATE ... FROM unnest`` for changed rows, and the
``DO UPDATE SET entity_id = entities.entity_id`` no-op rewrite that keeps
the canonical entity id flowing back. DuckDB keeps the per-row loop in
``ingest_store`` (single-writer local engine; see the note there).
"""

from __future__ import annotations

import json
from typing import Any

from ..storage.gateway import SqlGateway
from ..utils.models import Chunk, Entity, Relation

_CHUNK_SELECT_SQL = (
    "SELECT chunk_index, chunk_id, chunk_hash FROM chunks WHERE tenant_id = $1 AND doc_id = $2"
)

_CHUNK_INSERT_SQL = """
    INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text,
        source_type, entity_name, heading_path, file_path, start_line,
        end_line, metadata, chunk_hash, source_timestamp, context_blurb)
    SELECT cids, $2, $3, idx, txt, st, en, hp, fp, sl, el, md, ch, ts, cb
    FROM unnest($1::uuid[], $4::int[], $5::text[], $6::text[], $7::text[],
        $8::jsonb[], $9::text[], $10::int[], $11::int[], $12::jsonb[],
        $13::text[], $14::timestamptz[], $15::text[])
        AS t(cids, idx, txt, st, en, hp, fp, sl, el, md, ch, ts, cb)
    ON CONFLICT (tenant_id, doc_id, chunk_index) DO NOTHING
    RETURNING chunk_id
"""

_CHUNK_UPDATE_SQL = """
    UPDATE chunks AS c SET text = t.txt, chunk_hash = t.ch,
        source_timestamp = t.ts, context_blurb = t.cb, metadata = t.md,
        superseded_at = NULL, tombstoned_at = NULL
    FROM unnest($1::uuid[], $2::text[], $3::text[], $4::timestamptz[],
        $5::text[], $6::jsonb[]) AS t(cid, txt, ch, ts, cb, md)
    WHERE c.chunk_id = t.cid
"""

_DEGRADED_MIRROR_SQL = """
    UPDATE chunks AS c SET degraded = t.dg
    FROM unnest($1::uuid[], $2::bool[]) AS t(cid, dg)
    WHERE c.chunk_id = t.cid
"""

_ENTITY_UPSERT_SQL = """
    INSERT INTO entities (entity_id, tenant_id, name, entity_type,
        source_document_id, metadata)
    SELECT eids, $2, names, types, docs, meta
    FROM unnest($1::uuid[], $3::text[], $4::text[], $5::uuid[], $6::jsonb[])
        AS t(eids, names, types, docs, meta)
    ON CONFLICT (tenant_id, name, entity_type) DO UPDATE
        SET entity_id = entities.entity_id
    RETURNING entity_id, name, entity_type
"""

_RELATION_INSERT_SQL = """
    INSERT INTO relations (relation_id, tenant_id, source_entity_id,
        target_entity_id, relation_type, weight, metadata, chunk_id,
        confidence, extractor_id, model_version, prompt_version)
    SELECT rids, $2, srcs, tgts, rtypes, weights, meta, cids, confs,
        extractors, model_versions, prompt_versions
    FROM unnest($1::uuid[], $3::uuid[], $4::uuid[], $5::text[], $6::float8[],
        $7::jsonb[], $8::uuid[], $9::float8[], $10::text[], $11::text[],
        $12::text[])
        AS t(rids, srcs, tgts, rtypes, weights, meta, cids, confs,
             extractors, model_versions, prompt_versions)
    ON CONFLICT (tenant_id, source_entity_id, target_entity_id,
        relation_type, chunk_id) DO NOTHING
    RETURNING relation_id
"""


def _heading_path_param(chunk: Chunk) -> str | None:
    return json.dumps(chunk.heading_path) if chunk.heading_path else None


async def fetch_chunk_state(
    tx: SqlGateway, tenant_id: str, doc_id: str
) -> dict[int, tuple[str, str]]:
    """Existing (chunk_index -> (chunk_id, chunk_hash)) for one document."""
    rows = await tx.fetch(_CHUNK_SELECT_SQL, tenant_id, doc_id)
    return {int(r["chunk_index"]): (str(r["chunk_id"]), str(r["chunk_hash"])) for r in rows}


async def insert_chunks(
    tx: SqlGateway, tenant_id: str, doc_id: str, rows: list[tuple[Chunk, int]]
) -> set[str]:
    """Insert new chunks in one statement. Returns the ids that landed."""
    if not rows:
        return set()
    result = await tx.fetch(
        _CHUNK_INSERT_SQL,
        [c.chunk_id for c, _ in rows],
        tenant_id,
        doc_id,
        [i for _, i in rows],
        [c.text for c, _ in rows],
        [c.source_type for c, _ in rows],
        [c.entity_name for c, _ in rows],
        [_heading_path_param(c) for c, _ in rows],
        [c.metadata.get("file_path") if c.metadata else None for c, _ in rows],
        [c.start_line for c, _ in rows],
        [c.end_line for c, _ in rows],
        [json.dumps(c.metadata) for c, _ in rows],
        [c.chunk_hash for c, _ in rows],
        [c.source_timestamp for c, _ in rows],
        [c.context_blurb for c, _ in rows],
    )
    return {str(r["chunk_id"]) for r in result}


async def update_chunks(tx: SqlGateway, rows: list[tuple[Chunk, str]]) -> set[str]:
    """Rewrite changed chunks in one statement. Returns the rewritten ids."""
    if not rows:
        return set()
    cids = [cid for _, cid in rows]
    await tx.execute(
        _CHUNK_UPDATE_SQL,
        cids,
        [c.text for c, _ in rows],
        [c.chunk_hash for c, _ in rows],
        [c.source_timestamp for c, _ in rows],
        [c.context_blurb for c, _ in rows],
        [json.dumps(c.metadata) for c, _ in rows],
    )
    return set(cids)


async def mirror_degraded(tx: SqlGateway, cids: list[str], flags: list[bool]) -> None:
    """Copy the per-vector degraded flag onto the owning chunks rows."""
    if not cids:
        return
    await tx.execute(_DEGRADED_MIRROR_SQL, cids, flags)


async def upsert_entities(tx: SqlGateway, tenant_id: str, entities: list[Entity]) -> dict[str, str]:
    """Dedup-insert entities in one statement. Returns {extraction_id: canonical_id}.

    Inputs are deduped by (name, entity_type) first: the same entity seen in
    many chunks collapses to one row (the loop stored the first and mapped
    the rest to it). The no-op DO UPDATE keeps RETURNING populated, so every
    distinct key maps to its canonical id, stored or fresh alike.
    """
    deduped: dict[tuple[str, str], Entity] = {}
    for entity in entities:
        deduped.setdefault((entity.name, entity.entity_type), entity)
    if not deduped:
        return {}
    unique = list(deduped.values())
    result = await tx.fetch(
        _ENTITY_UPSERT_SQL,
        [e.entity_id for e in unique],
        tenant_id,
        [e.name for e in unique],
        [e.entity_type for e in unique],
        [e.source_document_id for e in unique],
        [entity_persisted_metadata(e) for e in unique],
    )
    canonical = {(str(r["name"]), str(r["entity_type"])): str(r["entity_id"]) for r in result}
    return {e.entity_id: canonical.get((e.name, e.entity_type), e.entity_id) for e in entities}


async def insert_relations(
    tx: SqlGateway,
    tenant_id: str,
    relations: list[Relation],
    model_version: str | None,
    prompt_version: str | None,
) -> int:
    """Insert relations in one statement. Returns the count that landed."""
    if not relations:
        return 0
    result = await tx.fetch(
        _RELATION_INSERT_SQL,
        [r.relation_id for r in relations],
        tenant_id,
        [r.source_entity_id for r in relations],
        [r.target_entity_id for r in relations],
        [r.relation_type for r in relations],
        [r.weight if r.weight else 1.0 for r in relations],
        [json.dumps(r.metadata) for r in relations],
        [r.chunk_id for r in relations],
        [r.confidence for r in relations],
        [r.extractor_id for r in relations],
        [model_version for _ in relations],
        [prompt_version for _ in relations],
    )
    return len(result)


def entity_persisted_metadata(entity: Entity) -> str:
    """Serialize entity metadata with provenance merged in (loop-parity copy).

    ``extractor`` and ``confidence`` live on the Entity model but have no
    dedicated columns in the entities table; without this merge they would
    be silently dropped at the write boundary.
    """
    persisted: dict[str, Any] = dict(entity.metadata)
    if entity.extractor_id and "extractor" not in persisted:
        persisted["extractor"] = entity.extractor_id
    if entity.confidence is not None and "confidence" not in persisted:
        persisted["confidence"] = entity.confidence
    return json.dumps(persisted)
