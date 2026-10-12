"""Release MCP-tool functions (U5, P7, v6 §6).

House pattern (tools/research_tools.py): async functions over an asyncpg
pool that MCP wrappers call. The CLI (``corpus-kb release ...``) and the
HTTP routes (``api/research_http.py``) share the SAME underlying handlers
(``handlers/release_handlers.py``) — one handler per operation, three
transports, zero duplicated lifecycle logic. Tool functions return the
handlers' ``to_payload()`` dicts (stable JSON schemas).
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from corpus_kb.handlers import release_handlers

__all__ = [
    "release_assess",
    "release_compare",
    "release_execute",
    "release_request",
    "release_review",
    "release_status",
]

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


async def release_assess(
    pg_pool: asyncpg.Pool,
    codebook_version_id: str,
    profile: str | None = None,
    run_id: str | None = None,
    tenant_id: str = DEFAULT_TENANT,
) -> dict[str, object]:
    """Compose every release gate over current evidence; WRITES NOTHING."""
    result = await release_handlers.assess_release(
        pg_pool,
        UUID(tenant_id),
        UUID(codebook_version_id),
        profile_name=profile,
        run_id=UUID(run_id) if run_id else None,
    )
    return result.to_payload()


async def release_request(
    pg_pool: asyncpg.Pool,
    codebook_version_id: str,
    creator: str,
    codebook_id: str | None = None,
    profile: str | None = None,
    release_id: str | None = None,
    tenant_id: str = DEFAULT_TENANT,
) -> dict[str, object]:
    """Open a draft_candidate release pinned to the current content hash."""
    result = await release_handlers.request_release(
        pg_pool,
        UUID(tenant_id),
        UUID(codebook_version_id),
        creator,
        codebook_id=UUID(codebook_id) if codebook_id else None,
        profile_name=profile,
        release_id=UUID(release_id) if release_id else None,
    )
    return result.to_payload()


async def release_review(
    pg_pool: asyncpg.Pool,
    codebook_version_id: str,
    release_id: str,
    tenant_id: str = DEFAULT_TENANT,
) -> dict[str, object]:
    """The review packet: evidence, failing/not_evaluable gates, waivers,
    specific next actions."""
    result = await release_handlers.review_release(
        pg_pool,
        UUID(tenant_id),
        UUID(codebook_version_id),
        UUID(release_id),
    )
    return result.to_payload()


async def release_execute(
    pg_pool: asyncpg.Pool,
    codebook_version_id: str,
    release_id: str,
    approver: str,
    waivers: list[list[str]] | None = None,
    run_id: str | None = None,
    tenant_id: str = DEFAULT_TENANT,
) -> dict[str, object]:
    """Execute the release transition (requires an approver).

    ``waivers`` are [gate_id, justification] pairs (JSON has no tuples);
    each waives one required gate and is recorded with the approver. A
    blocked outcome comes back as ``{"status": "blocked", ...}`` data.
    """
    pairs: tuple[tuple[str, str], ...] = ()
    for pair in waivers or []:
        if len(pair) != 2:
            raise ValueError("each waiver must be a [gate_id, justification] pair")
        pairs = (*pairs, (str(pair[0]), str(pair[1])))
    result = await release_handlers.execute_release(
        pg_pool,
        UUID(tenant_id),
        UUID(codebook_version_id),
        UUID(release_id),
        approver,
        waivers=pairs,
        run_id=UUID(run_id) if run_id else None,
    )
    return result.to_payload()


async def release_status(
    pg_pool: asyncpg.Pool,
    codebook_version_id: str,
    tenant_id: str = DEFAULT_TENANT,
) -> dict[str, object]:
    """Active release, draft state, pending change proposals, next action."""
    result = await release_handlers.status_report(
        pg_pool,
        UUID(tenant_id),
        UUID(codebook_version_id),
    )
    return result.to_payload()


async def release_compare(
    codebook_version_id: str,
    old_release_id: str,
    new_release_id: str,
    tenant_id: str = DEFAULT_TENANT,
) -> dict[str, object]:
    """Diff two releases' manifests (pure; reads the aggregates only)."""
    result = await release_handlers.compare_version_releases(
        UUID(tenant_id),
        UUID(codebook_version_id),
        UUID(old_release_id),
        UUID(new_release_id),
    )
    return result.to_payload()
