"""Tag and metadata handler — tags + key-value metadata for documents.

Storage: takes an ``asyncpg.Pool`` (legacy wiring; SQL and set_config
shapes byte-identical to the pre-port code) or a
:class:`~corpus_kb.storage.gateway.SqlGateway` (portable wiring — the
DuckDB arm, where tenancy rides the explicit ``tenant_id`` columns and
the gateway's ``transaction(tenant_id)`` scope; DuckDB has no RLS).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.storage.gateway import SqlGateway, is_gateway

logger = logging.getLogger(__name__)

_ADD_TAG_SQL = """INSERT INTO tags (tenant_id, name, color, description)
   VALUES ($1, $2, $3, $4) ON CONFLICT (tenant_id, name) DO NOTHING
   RETURNING tag_id, name"""
_FIND_TAG_SQL = "SELECT tag_id FROM tags WHERE tenant_id=$1 AND name=$2"
_INSERT_TAG_SQL = "INSERT INTO tags (tenant_id, name) VALUES ($1, $2)"
_TAG_DOCUMENT_SQL = """INSERT INTO document_tags (doc_id, tenant_id, tag_id)
   VALUES ($1, $2, $3) ON CONFLICT DO NOTHING"""
_UNTAG_SQL = """DELETE FROM document_tags WHERE doc_id=$1 AND tenant_id=$2 AND tag_id=(
       SELECT tag_id FROM tags WHERE tenant_id=$2 AND name=$3)"""
_DOC_TAGS_SQL = """SELECT t.name, t.color, t.description FROM tags t
   JOIN document_tags dt ON t.tag_id = dt.tag_id
   WHERE dt.doc_id = $1 AND dt.tenant_id = $2"""
# The portable (gateway) read adds the determinism tiebreak (rule 9);
# the Postgres read keeps its shipped shape byte-identical.
_DOC_TAGS_ORDERED_SQL = _DOC_TAGS_SQL + " ORDER BY t.name ASC"
_SET_METADATA_SQL = """INSERT INTO metadata (key, value, doc_id, tenant_id)
   VALUES ($1, $2, $3, $4)
   ON CONFLICT (key, tenant_id, doc_id) DO UPDATE SET value = $2"""


class TagHandler:
    """Handles tags and metadata for documents."""

    def __init__(self, store: asyncpg.Pool | SqlGateway) -> None:
        self._pool = store

    async def handle_add_tag(
        self,
        tenant_id: UUID,
        name: str,
        color: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        args = (str(tenant_id), name, color, description)
        if is_gateway(self._pool):
            async with self._pool.transaction(str(tenant_id)) as gw:
                row = await gw.fetchrow(_ADD_TAG_SQL, *args)
                return dict(row) if row else {"name": name, "status": "already_exists"}
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            row = await conn.fetchrow(_ADD_TAG_SQL, *args)
            return dict(row) if row else {"name": name, "status": "already_exists"}

    async def handle_tag_document(self, tenant_id: UUID, doc_id: UUID, tag: str) -> dict[str, Any]:
        args = (str(tenant_id), tag)
        if is_gateway(self._pool):
            async with self._pool.transaction(str(tenant_id)) as gw:
                tag_id = await self._tag_id(gw, args)
                await gw.execute(_TAG_DOCUMENT_SQL, str(doc_id), str(tenant_id), tag_id)
            return {"status": "success", "doc_id": str(doc_id), "tag": tag}
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            tag_id = await self._tag_id(conn, args)
            await conn.execute(_TAG_DOCUMENT_SQL, str(doc_id), str(tenant_id), tag_id)
        return {"status": "success", "doc_id": str(doc_id), "tag": tag}

    async def _tag_id(self, reader: Any, args: tuple[str, str]) -> str:
        row = await reader.fetchrow(_FIND_TAG_SQL, *args)
        if not row:
            await reader.execute(_INSERT_TAG_SQL, *args)
            row = await reader.fetchrow(_FIND_TAG_SQL, *args)
        return str(row["tag_id"])

    async def handle_untag_document(
        self, tenant_id: UUID, doc_id: UUID, tag: str
    ) -> dict[str, Any]:
        if is_gateway(self._pool):
            async with self._pool.transaction(str(tenant_id)) as gw:
                await gw.execute(_UNTAG_SQL, str(doc_id), str(tenant_id), tag)
            return {"status": "success", "doc_id": str(doc_id), "tag": tag}
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            await conn.execute(_UNTAG_SQL, str(doc_id), str(tenant_id), tag)
        return {"status": "success", "doc_id": str(doc_id), "tag": tag}

    async def handle_get_document_tags(self, tenant_id: UUID, doc_id: UUID) -> list[dict[str, Any]]:
        args = (str(doc_id), str(tenant_id))
        if is_gateway(self._pool):
            async with self._pool.transaction(str(tenant_id)) as gw:
                rows = await gw.fetch(_DOC_TAGS_ORDERED_SQL, *args)
            return [dict(r) for r in rows]
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            rows = await conn.fetch(_DOC_TAGS_SQL, *args)
            return [dict(r) for r in rows]

    async def handle_set_metadata(
        self, tenant_id: UUID, key: str, value: str, doc_id: UUID | None = None
    ) -> dict[str, Any]:
        args = (key, value, str(doc_id) if doc_id else None, str(tenant_id))
        if is_gateway(self._pool):
            async with self._pool.transaction(str(tenant_id)) as gw:
                await gw.execute(_SET_METADATA_SQL, *args)
            return {"status": "success", "key": key, "value": value}
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            await conn.execute(_SET_METADATA_SQL, *args)
        return {"status": "success", "key": key, "value": value}

    async def handle_get_metadata(
        self, tenant_id: UUID, key: str | None = None, doc_id: UUID | None = None
    ) -> list[dict[str, Any]]:
        sql, args = _metadata_select(tenant_id, key, doc_id)
        if is_gateway(self._pool):
            async with self._pool.transaction(str(tenant_id)) as gw:
                rows = await gw.fetch(sql, *args)
            return [dict(r) for r in rows]
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            rows = await conn.fetch(sql, *args)
            return [dict(r) for r in rows]


def _metadata_select(
    tenant_id: UUID, key: str | None, doc_id: UUID | None
) -> tuple[str, tuple[Any, ...]]:
    """The metadata read and its bound params, shared by both storages."""
    if key and doc_id:
        return (
            "SELECT key, value, doc_id FROM metadata WHERE key=$1 AND tenant_id=$2 AND doc_id=$3",
            (key, str(tenant_id), str(doc_id)),
        )
    if key:
        return (
            "SELECT key, value, doc_id FROM metadata WHERE key=$1 AND tenant_id=$2",
            (key, str(tenant_id)),
        )
    if doc_id:
        return (
            "SELECT key, value, doc_id FROM metadata WHERE tenant_id=$1 AND doc_id=$2",
            (str(tenant_id), str(doc_id)),
        )
    return (
        "SELECT key, value, doc_id FROM metadata WHERE tenant_id=$1",
        (str(tenant_id),),
    )


# ============================================================================
# Singleton
# ============================================================================

_tag_handler: TagHandler | None = None


def get_tag_handler() -> TagHandler:
    global _tag_handler
    if _tag_handler is None:
        raise RuntimeError("TagHandler not initialized. Call set_tag_handler() during startup.")
    return _tag_handler


def set_tag_handler(handler: TagHandler) -> None:
    global _tag_handler
    _tag_handler = handler


def reset_tag_handler() -> None:
    global _tag_handler
    _tag_handler = None
