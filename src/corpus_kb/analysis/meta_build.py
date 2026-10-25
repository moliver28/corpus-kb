"""Meta-embedding builders (Living Corpus W6).

One aggregate embedding per high-level corpus object — the
coarse-to-fine retrieval layer:

- ``document``: centroid of the document's stored chunk vectors.
- ``topic``: centroid of the topic's member chunks (``chunk_topics``).
- ``belief_summary``: centroid of the chunk vectors that hold the
  belief's evidence statements — "what the corpus asserts here". A
  chunk can carry statements of several beliefs, so one chunk may feed
  several belief centroids.
- ``profile``: the one text-anchored kind. The active profile's
  description, data type, and templates are embedded through the
  configured provider (pgml in-database, else the local embedder); when
  no provider answers, the row is skipped with an honest note (never a
  zero-vector row).

Centroids are L2-normalized means of the stored vectors, so every meta
row lives in the same cosine space as the chunks (halfvec(2560),
migration 025). Zero or near-zero centroids are skipped: a vector with
no direction has undefined cosine and would poison the meta arm.

Determinism (zero-drift rule 9): every mapping read carries an ORDER
BY, vectors accumulate in that order, and groups emit in sorted-key
order — the floating-point mean is order-sensitive, so the accumulation
order is part of the contract.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import asyncpg
import numpy as np

logger = logging.getLogger(__name__)

MIN_CENTROID_NORM = 1e-6

# One SELECT per mapping (tenant GUC applied by tenant_connection).
# Every read carries a deterministic ORDER BY: the centroid mean is
# floating-point order-sensitive, so read order is part of the contract.
_DOC_OF_CHUNK_SQL = (
    "SELECT c.chunk_id::text AS chunk_id, c.doc_id::text AS doc_id, d.source AS label"
    " FROM chunks c JOIN documents d ON d.doc_id = c.doc_id"
    " WHERE c.tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
    " ORDER BY c.chunk_id"
)
_TOPIC_OF_CHUNK_SQL = (
    "SELECT ct.chunk_id::text AS chunk_id, t.topic_id::text AS topic_id,"
    " t.label AS label FROM chunk_topics ct"
    " JOIN topics t ON t.topic_id = ct.topic_id"
    " WHERE ct.tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
    " ORDER BY ct.chunk_id, t.topic_id"
)
# Many-to-many through belief_revisions: one chunk may feed several
# belief centroids.
_BELIEF_CHUNKS_SQL = (
    "SELECT DISTINCT br.belief_id::text AS belief_id, b.subject, b.relation, b.object,"
    " s.chunk_id::text AS chunk_id"
    " FROM belief_revisions br"
    " JOIN beliefs b ON b.belief_id = br.belief_id AND b.tenant_id = br.tenant_id"
    " JOIN statements s ON s.statement_id = br.statement_id"
    " WHERE br.tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
    " AND br.statement_id IS NOT NULL"
    " ORDER BY br.belief_id, s.chunk_id"
)


@dataclass(frozen=True)
class MetaRow:
    """One aggregate embedding ready for the upsert."""

    meta_kind: str
    meta_key: str
    label: str
    vector: list[float]
    source_count: int


def _centroid(vectors: list[np.ndarray]) -> list[float] | None:
    """L2-normalized mean; None when the mass has no direction."""
    if not vectors:
        return None
    matrix = np.stack(vectors).astype(np.float64)
    mean = matrix.mean(axis=0)
    norm = float(np.linalg.norm(mean))
    if norm < MIN_CENTROID_NORM:
        return None
    return (mean / norm).astype(np.float32).tolist()


def _group_centroids(
    kind: str,
    vectors: dict[str, np.ndarray],
    mapping: dict[str, tuple[str, str]],
) -> list[MetaRow]:
    """Centroid per group key; ``mapping`` maps chunk -> (key, label)."""
    grouped: dict[str, list[np.ndarray]] = {}
    labels: dict[str, str] = {}
    for chunk_id in sorted(mapping):
        key, label = mapping[chunk_id]
        vector = vectors.get(chunk_id)
        if vector is None:
            continue
        grouped.setdefault(key, []).append(vector)
        labels[key] = label
    rows: list[MetaRow] = []
    for key in sorted(grouped):
        centroid = _centroid(grouped[key])
        if centroid is None:
            continue
        rows.append(MetaRow(kind, key, labels[key], centroid, len(grouped[key])))
    return rows


def _belief_centroids(
    vectors: dict[str, np.ndarray],
    memberships: list[tuple[str, str, str]],
) -> list[MetaRow]:
    """Centroid per belief; ``memberships`` = (belief_id, label, chunk_id).

    Many-to-many on purpose: a chunk holding statements of two beliefs
    feeds both centroids.
    """
    grouped: dict[str, list[np.ndarray]] = {}
    labels: dict[str, str] = {}
    for belief_id, label, chunk_id in memberships:
        vector = vectors.get(chunk_id)
        if vector is None:
            continue
        grouped.setdefault(belief_id, []).append(vector)
        labels[belief_id] = label
    rows: list[MetaRow] = []
    for belief_id in sorted(grouped):
        centroid = _centroid(grouped[belief_id])
        if centroid is None:
            continue
        members = grouped[belief_id]
        rows.append(
            MetaRow(
                "belief_summary",
                belief_id,
                labels[belief_id],
                centroid,
                len(members),
            )
        )
    return rows


async def _load_mappings(
    pool: asyncpg.Pool, tenant_id: str
) -> tuple[
    dict[str, tuple[str, str]],
    dict[str, tuple[str, str]],
    list[tuple[str, str, str]],
]:
    """Chunk -> (group key, label) for docs/topics; belief memberships."""
    from corpus_kb.classification.engine import tenant_connection

    docs: dict[str, tuple[str, str]] = {}
    topics: dict[str, tuple[str, str]] = {}
    beliefs: list[tuple[str, str, str]] = []
    async with tenant_connection(pool, tenant_id) as conn:
        for row in await conn.fetch(_DOC_OF_CHUNK_SQL):
            docs[str(row["chunk_id"])] = (str(row["doc_id"]), str(row["label"] or ""))
        for row in await conn.fetch(_TOPIC_OF_CHUNK_SQL):
            topics[str(row["chunk_id"])] = (str(row["topic_id"]), str(row["label"] or ""))
        for row in await conn.fetch(_BELIEF_CHUNKS_SQL):
            label = f"{row['subject']} {row['relation']} {row['object']}"
            beliefs.append((str(row["belief_id"]), label, str(row["chunk_id"])))
    return docs, topics, beliefs


async def build_meta_rows(
    pool: asyncpg.Pool,
    tenant_id: str,
    kinds: list[str],
    config: dict[str, object] | None = None,
) -> tuple[list[MetaRow], list[str]]:
    """Build the requested meta rows; returns (rows, notes).

    Deterministic: chunks iterate in the vector reader's order, groups
    emit by sorted key, and every mapping read is ORDER-BYed.
    """
    notes: list[str] = []
    rows: list[MetaRow] = []
    docs, topics, beliefs = await _load_mappings(pool, tenant_id)
    from corpus_kb.classification.engine import read_stored_vectors

    stored = await read_stored_vectors(pool, tenant_id)
    vectors = {sv.chunk_id: sv.vector for sv in stored}
    if "document" in kinds:
        rows.extend(_group_centroids("document", vectors, docs))
    if "topic" in kinds:
        if not topics:
            notes.append("no topics built yet; run the topics build first")
        else:
            rows.extend(_group_centroids("topic", vectors, topics))
    if "belief_summary" in kinds:
        if not beliefs:
            notes.append("no beliefs with statement evidence yet")
        else:
            rows.extend(_belief_centroids(vectors, beliefs))
    if "profile" in kinds:
        profile_rows, profile_notes = await _profile_rows(config or {}, pool, tenant_id)
        rows.extend(profile_rows)
        notes.extend(profile_notes)
    return rows, notes


async def _profile_rows(
    config: dict[str, object], pool: asyncpg.Pool, tenant_id: str
) -> tuple[list[MetaRow], list[str]]:
    """Embed the active profile's summary text through the configured provider."""
    from corpus_kb.profiles import load_profile

    try:
        profile = load_profile(dict(config))
    except Exception as exc:
        return [], [f"profile meta skipped: {exc}"]
    if profile is None:
        return [], ["no active profile; profile meta skipped"]
    parts = [f"project profile {profile.name}", profile.description or profile.data_type]
    if profile.templates:
        parts.append("tracks: " + ", ".join(profile.templates))
    text = ". ".join(parts)
    vector = await _embed_text(config, pool, tenant_id, text)
    if vector is None or all(abs(v) < 1e-9 for v in vector):
        return [], ["embedder unavailable; profile meta skipped (honest fallback)"]
    return [MetaRow("profile", profile.name, profile.name, list(vector), 1)], []


async def _embed_text(
    config: dict[str, object], pool: asyncpg.Pool, tenant_id: str, text: str
) -> list[float] | None:
    """One text embedding via the configured provider (None when down)."""
    provider = str((config.get("embedding") or {}).get("provider") or "ollama")
    if provider == "pgml":
        from corpus_kb.classification.engine import tenant_connection

        model = str((config.get("embedding") or {}).get("model") or "qwen3-embedding:4b")
        try:
            async with tenant_connection(pool, tenant_id) as conn:
                value = await conn.fetchval("SELECT pgml.embed($1, $2)", model, text)
        except Exception as exc:
            logger.warning("pgml profile embed failed: %s", exc)
            return None
        return list(value) if value is not None else None
    from corpus_kb.rag.embedder import OllamaEmbedder

    return OllamaEmbedder(dict(config)).embed(text)
