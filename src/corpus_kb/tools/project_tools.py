"""The project-table op handlers (Track C2).

Three shared functions, one per catalogue row: ``project_provision``,
``project_list``, ``project_drop``. This module never writes SQL —
persistence lives in ``corpus_kb.storage.project_tables`` (the ONE
sanctioned dynamic-DDL seam). The MCP, HTTP, socket, and CLI surfaces
are thin adapters over these functions.
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

import asyncpg

from ..errors import ValidationError
from ..storage.project_tables import (
    create_project_table,
    drop_project_table,
    list_project_tables,
)
from .ingest_common import DEFAULT_TENANT_ID

__all__ = ["project_drop", "project_list", "project_provision"]


def _clean_slug(slug: object) -> str:
    """Validate and strip the caller's slug."""
    if not isinstance(slug, str) or not slug.strip():
        raise ValidationError(
            "slug is required.",
            remediation="Pass the project slug, like meeting-notes, then try again.",
        )
    return slug.strip()


async def project_provision(
    pg_pool: asyncpg.Pool,
    slug: str,
    project_id: str | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, object]:
    """Create ``raw_<slug>`` plus its registry row; idempotent per tenant.

    Returns ``{status, table, registry_id, backend, already_provisioned}``.
    """
    clean_slug = _clean_slug(slug)
    if not project_id:
        # A missing project id derives a stable per-slug uuid, so the
        # registry row still names one project deterministically.
        project_id = str(uuid5(NAMESPACE_URL, f"{tenant_id}:{clean_slug}"))
    out = await create_project_table(pg_pool, tenant_id, project_id, clean_slug)
    return {
        "status": "success",
        "table": out["table"],
        "registry_id": out["registry_id"],
        "backend": out["backend"],
        "already_provisioned": out["already_provisioned"],
    }


async def project_list(
    pg_pool: asyncpg.Pool,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, object]:
    """The provisioned raw tables with a live row count for each.

    Returns ``{status, entries, count}``.
    """
    entries = await list_project_tables(pg_pool, tenant_id)
    return {"status": "success", "entries": entries, "count": len(entries)}


async def project_drop(
    pg_pool: asyncpg.Pool,
    slug: str,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, object]:
    """Drop ``raw_<slug>`` and its registry row in one transaction.

    Returns ``{status, table, dropped_rows}``.
    """
    clean_slug = _clean_slug(slug)
    out = await drop_project_table(pg_pool, tenant_id, clean_slug)
    return {
        "status": "success",
        "table": out["table"],
        "dropped_rows": out["dropped_rows"],
    }
