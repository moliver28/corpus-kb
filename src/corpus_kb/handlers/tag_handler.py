"""Tag and metadata handler — tags + key-value metadata for documents.

Storage: takes an ``asyncpg.Pool`` (legacy wiring) or a
:class:`~corpus_kb.storage.gateway.SqlGateway` (portable wiring — the
DuckDB arm, where tenancy rides the explicit ``tenant_id`` columns and
the gateway's ``transaction(tenant_id)`` scope; DuckDB has no RLS).

Every method runs inside :func:`storage.tenant_conn.tenant_connection`
(acquire, transaction, and set_config together), so the find-or-create
plus link in ``handle_tag_document`` commits as ONE transaction on both
storages. ``handle_untag_document`` reads the link and deletes it in the
same transaction; a missing link raises :class:`~corpus_kb.errors.NotFoundError`
— untag is never a silent ok. The tags read answers in name order on
both storages (rule 9). A blank tag name and a doc-less metadata write
cannot commit, so both raise :class:`~corpus_kb.errors.ValidationError`
before the database sees them.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.errors import InternalError, NotFoundError, ValidationError
from corpus_kb.handlers._singleton import SingletonSlot
from corpus_kb.storage.gateway import SqlConn, SqlGateway
from corpus_kb.storage.tenant_conn import tenant_connection

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
_FIND_LINK_SQL = """SELECT dt.tag_id FROM document_tags dt
   WHERE dt.doc_id=$1 AND dt.tenant_id=$2 AND dt.tag_id=(
       SELECT tag_id FROM tags WHERE tenant_id=$2 AND name=$3)"""
_DOC_TAGS_SQL = """SELECT t.name, t.color, t.description FROM tags t
   JOIN document_tags dt ON t.tag_id = dt.tag_id
   WHERE dt.doc_id = $1 AND dt.tenant_id = $2
   ORDER BY t.name ASC"""
_SET_METADATA_SQL = """INSERT INTO metadata (key, value, doc_id, tenant_id)
   VALUES ($1, $2, $3, $4)
   ON CONFLICT (key, tenant_id, doc_id) DO UPDATE SET value = $2"""


def _require_tag_name(name: str) -> None:
    """The schema accepts a blank name, so the handler rejects it first."""
    if name.strip() == "":
        raise ValidationError("The tag name is empty. Give a name with at least one character.")


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
        _require_tag_name(name)
        args = (str(tenant_id), name, color, description)
        async with tenant_connection(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(_ADD_TAG_SQL, *args)
            if row:
                return {"status": "success", "tag_id": str(row["tag_id"]), "name": row["name"]}
            return {"status": "already_exists", "name": name}

    async def handle_tag_document(self, tenant_id: UUID, doc_id: UUID, tag: str) -> dict[str, Any]:
        _require_tag_name(tag)  # this op creates a missing tag too
        args = (str(tenant_id), tag)
        async with tenant_connection(self._pool, tenant_id) as conn:
            tag_id = await self._tag_id(conn, args)
            await conn.execute(_TAG_DOCUMENT_SQL, str(doc_id), str(tenant_id), tag_id)
        return {"status": "success", "doc_id": str(doc_id), "tag": tag}

    async def _tag_id(self, reader: SqlConn, args: tuple[str, str]) -> str:
        """Find the tag's id, creating the bare tag when absent."""
        row = await reader.fetchrow(_FIND_TAG_SQL, *args)
        if not row:
            await reader.execute(_INSERT_TAG_SQL, *args)
            row = await reader.fetchrow(_FIND_TAG_SQL, *args)
        if row is None:
            raise InternalError("Tag row missing right after insert.")
        return str(row["tag_id"])

    async def handle_untag_document(
        self, tenant_id: UUID, doc_id: UUID, tag: str
    ) -> dict[str, Any]:
        async with tenant_connection(self._pool, tenant_id) as conn:
            link = await conn.fetchrow(_FIND_LINK_SQL, str(doc_id), str(tenant_id), tag)
            if link is None:
                raise NotFoundError(
                    f"This document does not carry the tag '{tag}'.",
                    remediation="Open the document's tag list. Use one of the listed names.",
                )
            await conn.execute(_UNTAG_SQL, str(doc_id), str(tenant_id), tag)
        return {"status": "success", "doc_id": str(doc_id), "tag": tag}

    async def handle_get_document_tags(self, tenant_id: UUID, doc_id: UUID) -> list[dict[str, Any]]:
        args = (str(doc_id), str(tenant_id))
        # One ordered read on both storages: the tag list the UI renders
        # must snapshot the same order everywhere (rule 9).
        async with tenant_connection(self._pool, tenant_id) as conn:
            rows = await conn.fetch(_DOC_TAGS_SQL, *args)
            return [dict(r) for r in rows]

    async def handle_set_metadata(
        self, tenant_id: UUID, key: str, value: str, doc_id: UUID | None = None
    ) -> dict[str, Any]:
        # The metadata PK is (key, tenant_id, doc_id), so a doc-less write
        # can never commit — answer the typed error, not a constraint 500.
        if doc_id is None:
            raise ValidationError(
                "A metadata write needs a document. Give the document id and try again."
            )
        args = (key, value, str(doc_id), str(tenant_id))
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(_SET_METADATA_SQL, *args)
        return {"status": "success", "key": key, "value": value}

    async def handle_get_metadata(
        self, tenant_id: UUID, key: str | None = None, doc_id: UUID | None = None
    ) -> list[dict[str, Any]]:
        sql, args = _metadata_select(tenant_id, key, doc_id)
        async with tenant_connection(self._pool, tenant_id) as conn:
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

_tag_handler = SingletonSlot[TagHandler](
    "TagHandler not initialized. Call set_tag_handler() during startup."
)


def get_tag_handler() -> TagHandler:
    return _tag_handler.get()


def set_tag_handler(handler: TagHandler) -> None:
    _tag_handler.set(handler)


def reset_tag_handler() -> None:
    _tag_handler.reset()
