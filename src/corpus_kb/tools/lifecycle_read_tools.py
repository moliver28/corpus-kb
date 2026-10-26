"""Lifecycle read tools (UI SPA program Task 5b, spec B2).

ONE read-only handler per lifecycle listing the Review / Codebook screens
consume, shared by every surface (CLI command, MCP tool, HTTP GET route,
socket arm) through the usual registry rows:

  * :func:`projects_list`       research_projects, newest first.
  * :func:`codebooks_list`      codebook_versions, newest first. A codebook
    IS an immutable, content-hashed version row in this schema.
  * :func:`codebook_proposals`  research_proposed_codes for the promote
    gate, newest first, one status filter.
  * :func:`coding_assignments`  research_assignments for the review queue,
    newest first, with ``review_status`` / ``code`` filters and offset
    pagination.

The shared plumbing (page window, deterministic ordering with unique
tiebreakers, backend-aware SELECT builder, tenant-scoped probe) lives in
``lifecycle_read_sql`` — one place per listing rule. Deterministic
ordering keeps every list snapshot-safe for harnesses.
"""

from __future__ import annotations

import asyncpg

from corpus_kb.errors import ValidationError
from corpus_kb.storage.gateway import SqlGateway
from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID
from corpus_kb.tools.ingest_store import as_gateway, gateway_backend
from corpus_kb.tools.lifecycle_read_sql import (
    DEFAULT_LIST_LIMIT,
    envelope,
    fetch_page,
    iso_text,
    page_size,
    select_page,
    uuid_text,
)

__all__ = [
    "codebook_proposals",
    "codebooks_list",
    "coding_assignments",
    "projects_list",
]


async def projects_list(
    pg_pool: asyncpg.Pool | SqlGateway,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict[str, object]:
    """List research projects, newest first; read-only projection."""
    args: list[object] = [tenant_id, page_size(limit), 0]
    select = select_page(
        "projects",
        gateway_backend(as_gateway(pg_pool)) == "duckdb",
        "project_id, name, created_at",
        [],
        args,
    )
    rows = await fetch_page(pg_pool, tenant_id, "projects", select, args)
    if isinstance(rows, dict):
        return rows
    return envelope(
        rows,
        lambda row: {
            "project_id": uuid_text(row["project_id"]),
            "name": row["name"],
            "created_at": iso_text(row["created_at"]),
        },
    )


async def codebooks_list(
    pg_pool: asyncpg.Pool | SqlGateway,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict[str, object]:
    """List codebook versions (the immutable codebooks), newest first."""
    args: list[object] = [tenant_id, page_size(limit), 0]
    select = select_page(
        "codebooks",
        gateway_backend(as_gateway(pg_pool)) == "duckdb",
        "version_id, label, sha256, paradigm, created_at",
        [],
        args,
    )
    rows = await fetch_page(pg_pool, tenant_id, "codebooks", select, args)
    if isinstance(rows, dict):
        return rows
    return envelope(
        rows,
        lambda row: {
            "version_id": uuid_text(row["version_id"]),
            "label": row["label"],
            "sha256": row["sha256"],
            "paradigm": row["paradigm"],
            "created_at": iso_text(row["created_at"]),
        },
    )


async def codebook_proposals(
    pg_pool: asyncpg.Pool | SqlGateway,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = DEFAULT_LIST_LIMIT,
    status: str = "proposed",
) -> dict[str, object]:
    """List proposed codes for the promote gate, newest first.

    ``status`` accepts the migration's own states: proposed, promoted,
    rejected, duplicate_blocked.
    """
    args: list[object] = [tenant_id, str(status), page_size(limit), 0]
    select = select_page(
        "proposals",
        gateway_backend(as_gateway(pg_pool)) == "duckdb",
        "proposed_id, cluster_id, run_id, suggested_label, n_members, status, "
        "block_reason, created_at",
        ["status = $2"],
        args,
    )
    rows = await fetch_page(pg_pool, tenant_id, "proposals", select, args)
    if isinstance(rows, dict):
        return rows
    return envelope(
        rows,
        lambda row: {
            "proposed_id": int(row["proposed_id"]),
            "cluster_id": row["cluster_id"],
            "run_id": uuid_text(row["run_id"]),
            "suggested_label": row["suggested_label"],
            "n_members": int(row["n_members"]),
            "status": row["status"],
            "block_reason": row["block_reason"],
            "created_at": iso_text(row["created_at"]),
        },
    )


async def coding_assignments(
    pg_pool: asyncpg.Pool | SqlGateway,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
    review_status: str | None = None,
    code: str | None = None,
) -> dict[str, object]:
    """List coding assignments for the review queue, newest first.

    Filters: ``review_status`` (the row status: auto, review, confirmed,
    overridden) and ``code`` (the code id). ``offset`` pages the window.
    """
    where: list[str] = []
    args: list[object] = [tenant_id]
    if review_status is not None:
        args.append(str(review_status))
        where.append(f"status = ${len(args)}")
    if code is not None:
        args.append(str(code))
        where.append(f"code_id = ${len(args)}")
    try:
        page_offset = int(offset)
    except (TypeError, ValueError):
        raise ValidationError("offset must be an integer") from None
    args.extend([page_size(limit), max(0, page_offset)])
    select = select_page(
        "assignments",
        gateway_backend(as_gateway(pg_pool)) == "duckdb",
        "assignment_id, assignment_aggregate_id, unit_id, code_id, cb_version_id, "
        "run_id, status, confidence, stance, evidence_basis, sim_answer, sim_qa, sim_q, "
        "created_at",
        where,
        args,
    )
    rows = await fetch_page(pg_pool, tenant_id, "assignments", select, args)
    if isinstance(rows, dict):
        return rows
    return envelope(
        rows,
        lambda row: {
            "assignment_id": int(row["assignment_id"]),
            "assignment_aggregate_id": uuid_text(row["assignment_aggregate_id"]),
            "unit_id": int(row["unit_id"]),
            "code_id": row["code_id"],
            "cb_version_id": uuid_text(row["cb_version_id"]),
            "run_id": uuid_text(row["run_id"]),
            "status": row["status"],
            "confidence": row["confidence"],
            "stance": row["stance"],
            "evidence_basis": row["evidence_basis"],
            "sim_answer": None if row["sim_answer"] is None else float(row["sim_answer"]),
            "sim_qa": None if row["sim_qa"] is None else float(row["sim_qa"]),
            "sim_q": None if row["sim_q"] is None else float(row["sim_q"]),
            "created_at": iso_text(row["created_at"]),
        },
    )
