"""Shared helpers for the research projectors (todo-11 (c))."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.projections.event_reader import DomainNotification
from corpus_kb.storage.tenant_conn import tenant_connection

logger = logging.getLogger(__name__)

RESEARCH_PROJECTION_NAME = "ResearchProjection"

DEFAULT_TENANT = UUID("00000000-0000-0000-0000-000000000001")


def event_payload(notification: DomainNotification) -> dict[str, Any]:
    """Decoded event attrs as a plain dict, with aggregate_id attached."""
    payload = {
        key: value for key, value in vars(notification.event).items() if not key.startswith("_")
    }
    payload["aggregate_id"] = notification.originator_id
    return payload


def require_tenant(payload: dict[str, Any], notification: DomainNotification) -> UUID:
    """TENANT CONTRACT (r8): research events carry tenant_id; assert presence."""
    tenant = payload.get("tenant_id")
    if tenant is None:
        raise ValueError(
            f"event {notification.topic} missing tenant_id "
            f"(notification_id={notification.notification_id})"
        )
    return UUID(str(tenant))


def json_str(value: object) -> str:
    return json.dumps(value)


async def store_turn_texts(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    texts_by_sha: dict[str, str],
    media_type: str = "text",
) -> int:
    """Write turn texts ONCE to the immutable content-addressed store.

    Ingest-owned (NOT a read model): excluded from the rebuild drop-set.
    Idempotent by PK; existing entries are never rewritten (immutability).
    """
    if not texts_by_sha:
        return 0
    written = 0
    async with tenant_connection(pool, tenant_id) as conn:
        for text_sha, text in texts_by_sha.items():
            result = await conn.execute(
                """
                INSERT INTO research_transcript_text (tenant_id, text_sha256, text, media_type)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (tenant_id, text_sha256) DO NOTHING
                """,
                str(tenant_id),
                text_sha,
                text,
                media_type,
            )
            written += 0 if result.endswith("0") else 1
    return written


async def lookup_turn_texts(pool: asyncpg.Pool, tenant_id: UUID, shas: list[str]) -> dict[str, str]:
    """Resolve text_sha256 -> text for projection-side caching (read-only)."""
    if not shas:
        return {}
    async with tenant_connection(pool, tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT text_sha256, text FROM research_transcript_text
            WHERE tenant_id = $1 AND text_sha256 = ANY($2)
            """,
            str(tenant_id),
            shas,
        )
    return {row["text_sha256"]: row["text"] for row in rows}
