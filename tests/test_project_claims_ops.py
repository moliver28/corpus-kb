"""W4-5 pins the claim operations: catalogue rows, handlers, import surface.

Registry-first: the two claim rows land in PROJECT_OPERATIONS with their
envelope tuples, the handlers raise the typed refusals, and the MCP
server module still builds over the new tools (the handler-signature
guard, R3 s1 step 3).
"""

from __future__ import annotations

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

    # One known project row, whatever the shared dev database holds today.
    # The GUC rides inside ONE transaction: a LOCAL set_config on an
    # autocommit connection evaporates with its statement (the store
    # documents this trap), and research_projects is RLS-guarded.
    async with pg_pool.acquire() as con, con.transaction():
        await con.execute(
            "SELECT set_config('app.current_tenant_id', $1, true)",
            "00000000-0000-0000-0000-000000000001",
        )
        row = await con.fetchrow("SELECT name FROM research_projects LIMIT 1")
        if row is None:
            await con.execute(
                "INSERT INTO research_projects (project_id, tenant_id, name) VALUES ($1, $2, $3)",
                "44444444-4444-4444-4444-444444444444",
                "00000000-0000-0000-0000-000000000001",
                "parity-notes",
            )
            name = "parity-notes"
        else:
            name = str(row["name"])
    with pytest.raises(NotFoundError):
        await project_claim(pg_pool, "00000000-0000-0000-0000-0000000000ff", name)


def test_mcp_server_builds_with_claim_tools() -> None:
    import corpus_kb.mcp_server  # noqa: F401 — the build is the assertion


async def test_claim_tools_registered_on_the_mcp_surface() -> None:
    from corpus_kb.mcp_server import build_mcp_server

    server = build_mcp_server(None)
    names = {tool.name for tool in await server.list_tools()}
    assert "project_claim" in names
    assert "project_claim_list" in names
