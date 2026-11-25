"""The project-table op handlers (Track C2).

Five shared functions, one per catalogue row: ``project_provision``,
``project_list``, ``project_drop``, and the W4 claim pair
(``project_claim``, ``project_claim_list``). The table ops never write
SQL — persistence lives in ``corpus_kb.storage.project_tables`` (the ONE
sanctioned dynamic-DDL seam). The claim pair reads project_claims and
research_projects through parameter-bound statements (migration 048).
The MCP, HTTP, socket, and CLI surfaces are thin adapters over these
functions.
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

import asyncpg

from ..errors import NotFoundError, ProjectNotFoundError, ValidationError
from ..storage.gateway import SqlGateway, as_gateway
from ..storage.project_tables import (
    create_project_table,
    drop_project_table,
    list_project_tables,
)
from .ingest_common import DEFAULT_TENANT_ID

__all__ = [
    "project_claim",
    "project_claim_list",
    "project_drop",
    "project_list",
    "project_provision",
]


def _clean_slug(slug: object) -> str:
    """Validate and strip the caller's slug."""
    if not isinstance(slug, str) or not slug.strip():
        raise ValidationError(
            "slug is required.",
            remediation="Pass the project slug, like meeting-notes, then try again.",
        )
    return slug.strip()


_CLAIM_INSERT_SQL = (
    "INSERT INTO project_claims (tenant_id, project_id, doc_id, source_project_id)"
    " VALUES ($1, $2, $3, $4)"
    " ON CONFLICT (tenant_id, project_id, doc_id) DO NOTHING"
    " RETURNING project_id"
)


async def _resolve_project_id_tx(tx: SqlGateway, tenant_id: str, slug: str) -> str:
    """The research_projects id for one slug (by name); typed error when unknown."""
    row = await tx.fetchrow(
        "SELECT project_id::text AS project_id FROM research_projects"
        " WHERE tenant_id = $1 AND name = $2",
        tenant_id,
        slug,
    )
    if row is None:
        raise ProjectNotFoundError(f"No project matches the slug {slug!r}.")
    return str(row["project_id"])


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


async def project_claim(
    pg_pool: asyncpg.Pool,
    doc_id: str,
    slug: str,
    project_id: str | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, object]:
    """Claim one stored document into the named project; idempotent.

    The slug resolves to a research_projects row by name (unknown slug
    raises the typed project_not_found error); an unknown doc_id raises
    the typed not_found error. The claim rides the project_claims table
    (migration 048), so the origin document never moves. An explicit
    ``project_id`` overrides the slug resolution. Returns ``{status,
    doc_id, project_id, source_project_id, claimed}``.
    """
    clean_slug = _clean_slug(slug)
    # The gateway transaction sets the tenant GUC first, so the RLS-guarded
    # lookups and the claim insert see the right tenant (the store pattern).
    gateway = as_gateway(pg_pool)
    async with gateway.transaction(tenant_id) as tx:
        resolved = (
            project_id if project_id else await _resolve_project_id_tx(tx, tenant_id, clean_slug)
        )
        origin_row = await tx.fetchrow(
            "SELECT project_id::text AS origin_project_id FROM documents"
            " WHERE tenant_id = $1 AND doc_id = $2",
            tenant_id,
            doc_id,
        )
        if origin_row is None:
            raise NotFoundError(
                f"No document matches the id {doc_id!r}.",
                remediation="Check the doc id. List the documents to find a valid one.",
            )
        origin = origin_row["origin_project_id"]
        claimed_row = await tx.fetchrow(_CLAIM_INSERT_SQL, tenant_id, resolved, doc_id, origin)
    return {
        "status": "success",
        "doc_id": doc_id,
        "project_id": resolved,
        "source_project_id": origin,
        "claimed": claimed_row is not None,
    }


async def project_claim_list(
    pg_pool: asyncpg.Pool,
    doc_id: str | None = None,
    slug: str | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, object]:
    """The claim rows, filtered by doc or project; deterministic order.

    Entries order by (project_id, doc_id) so harnesses can snapshot the
    answer (zero-drift rule 9). Returns ``{status, entries, count}``.
    """
    clauses = ["c.tenant_id = $1"]
    args: list[object] = [tenant_id]
    if doc_id:
        args.append(doc_id)
        clauses.append(f"c.doc_id = ${len(args)}")
    # One gateway transaction carries the slug resolution AND the read:
    # the tenant GUC it sets first is what the FORCEd RLS on project_claims
    # answers to, so a direct pool read would silently return zero rows.
    gateway = as_gateway(pg_pool)
    async with gateway.transaction(tenant_id) as tx:
        if slug:
            clean_slug = _clean_slug(slug)
            resolved = await _resolve_project_id_tx(tx, tenant_id, clean_slug)
            args.append(resolved)
            clauses.append(f"c.project_id = ${len(args)}")
        sql = (
            "SELECT c.project_id::text AS project_id, c.doc_id::text AS doc_id,"
            " c.source_project_id::text AS source_project_id, c.claimed_at AS claimed_at"
            " FROM project_claims c WHERE "
            + " AND ".join(clauses)
            + " ORDER BY c.project_id, c.doc_id"
        )
        rows = await tx.fetch(sql, *args)
    entries = [
        {
            "project_id": row["project_id"],
            "doc_id": row["doc_id"],
            "source_project_id": row["source_project_id"],
            "claimed_at": str(row["claimed_at"]),
        }
        for row in rows
    ]
    return {"status": "success", "entries": entries, "count": len(entries)}
