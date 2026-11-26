"""W4-5 pins the claim operations: catalogue rows, handlers, import surface.

Registry-first: the two claim rows land in PROJECT_OPERATIONS with their
envelope tuples, the handlers raise the typed refusals, and the MCP
server module still builds over the new tools (the handler-signature
guard, R3 s1 step 3).
"""

from __future__ import annotations

import uuid

import pytest

from corpus_kb.contracts.catalogue_project import (
    CLAIM_ENVELOPE,
    CLAIM_LIST_ENVELOPE,
    PROJECT_OPERATIONS,
)
from corpus_kb.errors import NotFoundError, ProjectNotFoundError


def _op(name: str):
    return next(op for op in PROJECT_OPERATIONS if op.name == name)


def test_claim_rows_exist() -> None:
    claim = _op("project_claim")
    assert claim.envelope_keys == CLAIM_ENVELOPE
    assert CLAIM_ENVELOPE == (
        "status",
        "doc_id",
        "project_id",
        "source_project_id",
        "claimed",
    )
    assert [param.name for param in claim.params] == [
        "doc_id",
        "slug",
        "project_id",
        "tenant_id",
    ]
    listing = _op("project_claim_list")
    assert listing.envelope_keys == CLAIM_LIST_ENVELOPE == ("status", "entries", "count")
    assert [param.name for param in listing.params] == ["doc_id", "slug", "tenant_id"]


def test_claim_surfaces_declare_all_four() -> None:
    claim = _op("project_claim")
    assert {surface.kind for surface in claim.surfaces} == {"mcp", "cli", "http", "socket"}
    listing = _op("project_claim_list")
    assert {surface.kind for surface in listing.surfaces} == {"mcp", "cli", "http", "socket"}


async def test_claim_unknown_slug_raises_project_not_found(pg_pool) -> None:
    from corpus_kb.tools.project_tools import project_claim

    with pytest.raises(ProjectNotFoundError):
        await project_claim(pg_pool, "00000000-0000-0000-0000-00000000000d", "no-such-slug")


async def test_claim_list_unknown_slug_raises_project_not_found(pg_pool) -> None:
    from corpus_kb.tools.project_tools import project_claim_list

    with pytest.raises(ProjectNotFoundError):
        await project_claim_list(pg_pool, slug="no-such-slug")


async def test_claim_unknown_doc_raises_not_found(pg_pool) -> None:
    from corpus_kb.tools.project_tools import project_claim

    # Own seeded row: the shared dev database keeps rows from earlier
    # runs, so "LIMIT 1" is not a lookup, and a W5 slug lookup dies on
    # the project before the doc does. The GUC rides inside ONE
    # transaction: a LOCAL set_config on an autocommit connection
    # evaporates with its statement, and research_projects is
    # RLS-guarded.
    slug = f"claim-doc-{uuid.uuid4().hex[:8]}"
    pid = str(uuid.uuid4())
    tenant = "00000000-0000-0000-0000-000000000001"
    async with pg_pool.acquire() as con, con.transaction():
        await con.execute(
            "SELECT set_config('app.current_tenant_id', $1, true)",
            tenant,
        )
        await con.execute(
            "INSERT INTO research_projects (project_id, tenant_id, name, slug)"
            " VALUES ($1, $2, $3, $4)",
            pid,
            tenant,
            slug,
            slug,
        )
    try:
        with pytest.raises(NotFoundError):
            await project_claim(pg_pool, "00000000-0000-0000-0000-0000000000ff", slug)
    finally:
        async with pg_pool.acquire() as con, con.transaction():
            await con.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                tenant,
            )
            await con.execute("DELETE FROM research_projects WHERE project_id = $1", pid)


def test_mcp_server_builds_with_claim_tools() -> None:
    import corpus_kb.mcp_server  # noqa: F401 — the build is the assertion


async def test_claim_tools_registered_on_the_mcp_surface() -> None:
    from corpus_kb.mcp_server import build_mcp_server

    server = build_mcp_server(None)
    names = {tool.name for tool in await server.list_tools()}
    assert "project_claim" in names
    assert "project_claim_list" in names
