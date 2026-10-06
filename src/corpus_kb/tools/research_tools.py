"""Research MCP-tool functions (todo-11 (d)) — the named ingest surfaces.

House pattern (tools/ingest_tools.py): async functions over an asyncpg pool
that MCP wrappers call. The CLI (corpus-kb research ...) shares these.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from corpus_kb.handlers.research_handler import get_research_handler

__all__ = ["research_ingest", "research_ingest_transcript"]

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


async def research_ingest_transcript(
    file_path: str,
    pg_pool: asyncpg.Pool,
    project_id: str | None = None,
    title: str | None = None,
    tenant_id: str = DEFAULT_TENANT,
    force: bool = False,
) -> dict[str, object]:
    """Ingest ONE transcript file (turns -> roles -> exchanges -> events)."""
    handler = get_research_handler(pg_pool)
    return await handler.ingest_transcript(
        UUID(tenant_id),
        file_path,
        project_id=UUID(project_id) if project_id else None,
        title=title,
        force=force,
    )


async def research_ingest(
    path: str,
    pg_pool: asyncpg.Pool,
    project_id: str | None = None,
    tenant_id: str = DEFAULT_TENANT,
    force: bool = False,
) -> dict[str, object]:
    """Dynamic ingestion: file, directory, or glob with ledger dedup."""
    handler = get_research_handler(pg_pool)
    return await handler.ingest_path(
        UUID(tenant_id),
        path,
        project_id=UUID(project_id) if project_id else None,
        force=force,
    )


def supported_suffixes() -> set[str]:
    from corpus_kb.research.dynamic_ingest import SUPPORTED_SUFFIXES

    return set(SUPPORTED_SUFFIXES)
