"""Tag and metadata handler — tags + key-value metadata for documents."""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


class TagHandler:
    """Handles tags and metadata for documents."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def handle_add_tag(
        self,
        tenant_id: UUID,
        name: str,
        color: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            row = await conn.fetchrow(
                """INSERT INTO tags (tenant_id, name, color, description)
                   VALUES ($1, $2, $3, $4) ON CONFLICT (tenant_id, name) DO NOTHING
                   RETURNING tag_id, name""",
                str(tenant_id),
                name,
                color,
                description,
            )
            return dict(row) if row else {"name": name, "status": "already_exists"}

    async def handle_tag_document(
        self, tenant_id: UUID, doc_id: UUID, tag: str
    ) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            tag_row = await conn.fetchrow(
                "SELECT tag_id FROM tags WHERE tenant_id=$1 AND name=$2",
                str(tenant_id),
                tag,
            )
            if not tag_row:
                await conn.execute(
                    "INSERT INTO tags (tenant_id, name) VALUES ($1, $2)",
                    str(tenant_id),
                    tag,
                )
                tag_row = await conn.fetchrow(
                    "SELECT tag_id FROM tags WHERE tenant_id=$1 AND name=$2",
                    str(tenant_id),
                    tag,
                )
            await conn.execute(
                "INSERT INTO document_tags (doc_id, tenant_id, tag_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                str(doc_id),
                str(tenant_id),
                str(tag_row["tag_id"]),
            )
            return {"status": "success", "doc_id": str(doc_id), "tag": tag}

    async def handle_untag_document(
        self, tenant_id: UUID, doc_id: UUID, tag: str
    ) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            await conn.execute(
                """DELETE FROM document_tags WHERE doc_id=$1 AND tenant_id=$2 AND tag_id=(
                       SELECT tag_id FROM tags WHERE tenant_id=$2 AND name=$3)""",
                str(doc_id),
                str(tenant_id),
                tag,
            )
            return {"status": "success", "doc_id": str(doc_id), "tag": tag}

    async def handle_get_document_tags(
        self, tenant_id: UUID, doc_id: UUID
    ) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            rows = await conn.fetch(
                """SELECT t.name, t.color, t.description FROM tags t
                   JOIN document_tags dt ON t.tag_id = dt.tag_id
                   WHERE dt.doc_id = $1 AND dt.tenant_id = $2""",
                str(doc_id),
                str(tenant_id),
            )
            return [dict(r) for r in rows]

    async def handle_set_metadata(
        self, tenant_id: UUID, key: str, value: str, doc_id: Optional[UUID] = None
    ) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            await conn.execute(
                """INSERT INTO metadata (key, value, doc_id, tenant_id)
                   VALUES ($1, $2, $3, $4) ON CONFLICT (key, tenant_id, doc_id) DO UPDATE SET value = $2""",
                key,
                value,
                str(doc_id) if doc_id else None,
                str(tenant_id),
            )
            return {"status": "success", "key": key, "value": value}

    async def handle_get_metadata(
        self, tenant_id: UUID, key: Optional[str] = None, doc_id: Optional[UUID] = None
    ) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            if key and doc_id:
                rows = await conn.fetch(
                    "SELECT key, value, doc_id FROM metadata WHERE key=$1 AND tenant_id=$2 AND doc_id=$3",
                    key,
                    str(tenant_id),
                    str(doc_id),
                )
            elif key:
                rows = await conn.fetch(
                    "SELECT key, value, doc_id FROM metadata WHERE key=$1 AND tenant_id=$2",
                    key,
                    str(tenant_id),
                )
            elif doc_id:
                rows = await conn.fetch(
                    "SELECT key, value, doc_id FROM metadata WHERE tenant_id=$1 AND doc_id=$2",
                    str(tenant_id),
                    str(doc_id),
                )
            else:
                rows = await conn.fetch(
                    "SELECT key, value, doc_id FROM metadata WHERE tenant_id=$1",
                    str(tenant_id),
                )
            return [dict(r) for r in rows]


# ============================================================================
# Singleton
# ============================================================================

_tag_handler: Optional["TagHandler"] = None


def get_tag_handler() -> "TagHandler":
    global _tag_handler
    if _tag_handler is None:
        raise RuntimeError(
            "TagHandler not initialized. Call set_tag_handler() during startup."
        )
    return _tag_handler


def set_tag_handler(handler: "TagHandler") -> None:
    global _tag_handler
    _tag_handler = handler


def reset_tag_handler() -> None:
    global _tag_handler
    _tag_handler = None
