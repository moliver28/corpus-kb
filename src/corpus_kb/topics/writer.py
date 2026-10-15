"""Postgres writes and reads for the topics layer (migration 026).

Tenant-scoped through ``storage.tenant_conn.tenant_connection`` — the
tables are RLS FORCED, so the tenant GUC lives inside the SAME
transaction as every statement (the shadow-writer house contract). A
build is a derived artifact: the write REPLACES the tenant's topic rows
wholesale (the ``ON DELETE CASCADE`` from ``chunk_topics`` clears the
old assignments with one statement).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import asyncpg

from corpus_kb.storage.tenant_conn import tenant_connection


@dataclass(frozen=True)
class TopicRow:
    """One topic ready for storage (reduction already applied)."""

    topic_index: int
    label: str
    reduced_label: str | None
    size: int
    keywords: list[object] = field(default_factory=list)
    params: dict[str, object] = field(default_factory=dict)


async def topics_tables_present(pg_pool: asyncpg.Pool) -> bool:
    """True when migration 026 has run (``to_regclass`` probe, no GUC)."""
    async with pg_pool.acquire() as conn:
        return await conn.fetchval("SELECT to_regclass('public.topics')") is not None


async def write_build(
    pg_pool: asyncpg.Pool,
    tenant_id: str,
    topics: list[TopicRow],
    assignments: list[tuple[str, int, float]],
) -> dict[str, object]:
    """Replace the tenant's topic rows with this build (one transaction).

    Returns the written counts plus the model-index -> topic-id mapping
    the response envelope serializes.
    """
    id_by_index: dict[int, str] = {}
    async with tenant_connection(pg_pool, tenant_id) as conn:
        # CASCADE takes the old chunk_topics rows with the old topics.
        await conn.execute("DELETE FROM topics WHERE tenant_id = $1", tenant_id)
        for topic in topics:
            topic_id = await conn.fetchval(
                """
                INSERT INTO topics (tenant_id, label, reduced_label, size, keywords, params)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb)
                RETURNING topic_id::text
                """,
                tenant_id,
                topic.label,
                topic.reduced_label,
                topic.size,
                json.dumps(topic.keywords),
                json.dumps(topic.params),
            )
            id_by_index[topic.topic_index] = str(topic_id)
        if assignments:
            await conn.executemany(
                """
                INSERT INTO chunk_topics (tenant_id, chunk_id, topic_id, probability)
                VALUES ($1, $2, $3::uuid, $4)
                ON CONFLICT (tenant_id, chunk_id) DO NOTHING
                """,
                [
                    (tenant_id, chunk_id, id_by_index[topic_index], probability)
                    for chunk_id, topic_index, probability in assignments
                ],
            )
    return {
        "topics_written": len(topics),
        "assignments_written": len(assignments),
        "topic_ids": id_by_index,
    }


async def read_topics(
    pg_pool: asyncpg.Pool, tenant_id: str, limit: int = 50
) -> list[dict[str, object]]:
    """The tenant's topics, largest first (tenant GUC; jsonb parsed)."""
    async with tenant_connection(pg_pool, tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT topic_id::text AS topic_id, label, reduced_label, size,
                   keywords, params, created_at
            FROM topics
            WHERE tenant_id = $1
            ORDER BY size DESC, label
            LIMIT $2
            """,
            tenant_id,
            int(limit),
        )
    return [_row_to_dict(row) for row in rows]


def _row_to_dict(row: asyncpg.Record) -> dict[str, object]:
    """One topics row -> JSON-safe dict (jsonb parsed, timestamp ISO)."""
    return {
        "topic_id": str(row["topic_id"]),
        "label": row["label"],
        "reduced_label": row["reduced_label"],
        "size": int(row["size"]),
        "keywords": _parse_jsonb(row["keywords"]),
        "params": _parse_jsonb(row["params"]),
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


def _parse_jsonb(raw: object) -> object:
    """asyncpg hands jsonb back as a string; parse it, keep raw on failure."""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw
