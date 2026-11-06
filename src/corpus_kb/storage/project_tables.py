"""The ONE dynamic-DDL seam: per-project raw tables (owner ruling 2026-11-06).

Each research project gets its own physical raw table ``raw_<slug>``. This
module is the only place allowed to create or drop runtime tables (the
sanctioned exception in the zero-drift program). Identifiers go through
``storage.sql_ident`` only, and every runtime table gets one row in the
static ``project_tables`` registry (migration 043), so schema-parity,
doctor, backup, and migrate-data can enumerate them.

Schema per table: ``id`` PK, ``tenant_id``, ``project_id``, ``source_path``,
``source_hash`` (sha256 hex), ``content_type``, ``raw_bytes``, ``raw_text``,
``ingested_at``; unique ``(tenant_id, source_hash)`` so raw storage is
content-addressed. Postgres adds the 022-convention tenant RLS policy;
DuckDB keeps the plain ``tenant_id`` column (predicate discipline).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import asyncpg

from corpus_kb.errors import NotFoundError, ValidationError
from corpus_kb.storage.gateway import SqlGateway, as_gateway, gateway_backend
from corpus_kb.storage.sql_ident import check_identifier, quoted_identifier
from corpus_kb.storage.tenant_conn import tenant_connection

__all__ = [
    "drop_project_table",
    "list_project_tables",
    "raw_table_name",
    "store_raw_content",
]

_SLUG_RX = re.compile(r"[A-Za-z0-9-]+")

_REGISTRY_TABLE = "project_tables"


def raw_table_name(slug: str) -> str:
    """Validate a project slug and return its plain raw-table name.

    The slug is letters, digits, and hyphens; hyphens become underscores
    in the physical name (``meeting-notes`` -> ``raw_meeting_notes``).
    """
    clean = (slug or "").strip()
    if not clean or not _SLUG_RX.fullmatch(clean):
        raise ValidationError(
            "project slug must be letters, digits, and hyphens.",
            remediation="Pass a slug like meeting-notes, then try again.",
        )
    return check_identifier(f"raw_{clean.replace('-', '_')}")


async def _table_exists(gw: SqlGateway, backend: str, name: str) -> bool:
    """True when the physical table exists on this backend."""
    if backend == "postgres":
        found = await gw.fetchval("SELECT to_regclass($1)", f"public.{name}")
        return found is not None
    found = await gw.fetchval(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_name = $1",
        name,
    )
    return bool(int(found or 0))


_RAW_COLUMNS_PG = """
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL,
    project_id UUID NOT NULL,
    source_path TEXT,
    source_hash CHAR(64) NOT NULL,
    content_type TEXT,
    raw_bytes BYTEA,
    raw_text TEXT,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, source_hash)
"""

_RAW_COLUMNS_DUCKDB = """
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    project_id VARCHAR NOT NULL,
    source_path VARCHAR,
    source_hash VARCHAR NOT NULL,
    content_type VARCHAR,
    raw_bytes BLOB,
    raw_text VARCHAR,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, source_hash)
"""


def _rls_statements(table: str) -> tuple[str, ...]:
    """The 022-convention tenant RLS policy for one runtime table."""
    quoted = quoted_identifier(table)
    return (
        f"ALTER TABLE {quoted} ENABLE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS raw_table_tenant_isolation ON {quoted}",
        f"CREATE POLICY raw_table_tenant_isolation ON {quoted} "
        f"USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID) "
        f"WITH CHECK (tenant_id = "
        f"NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)",
        f"ALTER TABLE {quoted} FORCE ROW LEVEL SECURITY",
    )


async def create_project_table(
    store: asyncpg.Pool | SqlGateway,
    tenant_id: str,
    project_id: str,
    slug: str,
) -> dict[str, Any]:
    """Create ``raw_<slug>`` plus its registry row; idempotent per tenant.

    Returns ``{table, registry_id, backend, already_provisioned}``. A
    repeat provision of the same table answers the existing registry row
    with ``already_provisioned: true`` and touches nothing.
    """
    table = raw_table_name(slug)
    gw = as_gateway(store)
    backend = gateway_backend(gw)
    existing = await gw.fetchval(
        f"SELECT id FROM {_REGISTRY_TABLE} WHERE tenant_id = $1 AND table_name = $2",
        tenant_id,
        table,
    )
    if existing is not None:
        return {
            "table": table,
            "registry_id": existing,
            "backend": backend,
            "already_provisioned": True,
        }
    columns = _RAW_COLUMNS_PG if backend == "postgres" else _RAW_COLUMNS_DUCKDB
    quoted = quoted_identifier(table)
    statements = [f"CREATE TABLE IF NOT EXISTS {quoted} ({columns})"]
    if backend == "postgres":
        statements.extend(_rls_statements(table))
    async with tenant_connection(gw, tenant_id) as conn:
        for statement in statements:
            await conn.execute(statement)
        registry_id = await conn.fetchval(
            f"INSERT INTO {_REGISTRY_TABLE} (tenant_id, project_id, table_name) "
            "VALUES ($1, $2, $3) "
            "ON CONFLICT (tenant_id, table_name) DO UPDATE SET table_name = EXCLUDED.table_name "
            "RETURNING id",
            tenant_id,
            project_id,
            table,
        )
    return {
        "table": table,
        "registry_id": registry_id,
        "backend": backend,
        "already_provisioned": False,
    }


async def list_project_tables(
    store: asyncpg.Pool | SqlGateway,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """The tenant's registry rows, each with a live row count.

    Doctor and the backup gate read the same registry. A registry row
    whose table is missing answers ``row_count: None`` (an honest gap); a
    table without a registry row is an orphan doctor reports.
    """
    gw = as_gateway(store)
    backend = gateway_backend(gw)
    rows = await gw.fetch(
        f"SELECT id, project_id, table_name, created_at FROM {_REGISTRY_TABLE} "
        "WHERE tenant_id = $1 ORDER BY created_at, table_name",
        tenant_id,
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        name = str(row["table_name"])
        entry: dict[str, Any] = {
            "registry_id": row["id"],
            "project_id": row["project_id"],
            "table": name,
            "created_at": row["created_at"],
            "row_count": None,
        }
        if await _table_exists(gw, backend, name):
            entry["row_count"] = int(
                await gw.fetchval(f"SELECT COUNT(*) FROM {quoted_identifier(name)}")
            )
        out.append(entry)
    return out


async def drop_project_table(
    store: asyncpg.Pool | SqlGateway,
    tenant_id: str,
    slug: str,
) -> dict[str, Any]:
    """Drop ``raw_<slug>`` and delete its registry row in one transaction.

    Returns ``{table, dropped_rows}``. A slug with no registry row is a
    not-found error, not a silent success.
    """
    table = raw_table_name(slug)
    gw = as_gateway(store)
    backend = gateway_backend(gw)
    quoted = quoted_identifier(table)
    async with tenant_connection(gw, tenant_id) as conn:
        registry_id = await conn.fetchval(
            f"SELECT id FROM {_REGISTRY_TABLE} WHERE tenant_id = $1 AND table_name = $2",
            tenant_id,
            table,
        )
        if registry_id is None:
            raise NotFoundError(
                f"No provisioned raw table for slug {slug!r} in this tenant.",
                remediation="Run project-list to see the provisioned slugs.",
            )
        dropped_rows = 0
        if await _table_exists(conn, backend, table):
            dropped_rows = int(await conn.fetchval(f"SELECT COUNT(*) FROM {quoted}"))
            await conn.execute(f"DROP TABLE IF EXISTS {quoted}")
        await conn.execute(f"DELETE FROM {_REGISTRY_TABLE} WHERE id = $1", registry_id)
    return {"table": table, "dropped_rows": dropped_rows}


async def store_raw_content(
    store: asyncpg.Pool | SqlGateway,
    tenant_id: str,
    project_id: str,
    slug: str,
    *,
    source_path: str,
    raw_bytes: bytes | None,
    raw_text: str | None,
    content_type: str | None,
) -> str:
    """Write one content-addressed raw row into ``raw_<slug>``.

    The sha256 of the bytes (or of the text when there are no bytes) is
    the content address; a repeat write of the same content is a no-op.
    Returns the source hash.
    """
    table = raw_table_name(slug)
    gw = as_gateway(store)
    address = raw_bytes if raw_bytes is not None else (raw_text or "").encode("utf-8")
    source_hash = hashlib.sha256(address).hexdigest()
    async with tenant_connection(gw, tenant_id) as conn:
        await conn.execute(
            f"INSERT INTO {quoted_identifier(table)} "
            "(tenant_id, project_id, source_path, source_hash, content_type, raw_bytes, raw_text) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) "
            "ON CONFLICT (tenant_id, source_hash) DO NOTHING",
            tenant_id,
            project_id,
            source_path,
            source_hash,
            content_type,
            raw_bytes,
            raw_text,
        )
    return source_hash
