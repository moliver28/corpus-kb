"""MCP/HTTP/socket/CLI handler for the statement-atom read ops.

:func:`statements_list` and :func:`statements_timeline` are thin
envelope wrappers over :class:`corpus_kb.statements.store.StatementStore`
(the ONE statement read path; this module never writes SQL). Lazy
imports keep module load light (the P0 rule).
"""

from __future__ import annotations

import asyncpg

from ..errors import ValidationError
from .ingest_common import DEFAULT_TENANT_ID

__all__ = ["statements_list", "statements_timeline"]

_EMPTY_LIMIT_REASON = "limit must be a positive integer."
_EMPTY_LIMIT_REMEDY = "Pass a whole number of 1 or more, then retry."


def _check_limit(limit: int) -> None:
    if not isinstance(limit, int) or limit < 1:
        raise ValidationError(_EMPTY_LIMIT_REASON, remediation=_EMPTY_LIMIT_REMEDY)


async def statements_list(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    template_id: str | None = None,
    document_id: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    """Newest-first page of statement atoms.

    Returns ``{status, statements, count}`` — deterministic order
    (observed_at DESC, statement_id), stable for harness snapshots.
    """
    from ..statements import StatementStore

    _check_limit(limit)
    store = StatementStore(pg_pool, tenant_id)
    rows = await store.list_statements(
        pg_pool, template_id=template_id, document_id=document_id, limit=limit
    )
    return {"status": "success", "statements": rows, "count": len(rows)}


async def statements_timeline(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    text_query: str | None = None,
    limit: int = 200,
) -> dict[str, object]:
    """Chronological statement history — the belief-history view.

    Returns ``{status, statements, count}`` ordered by
    ``COALESCE(valid_at, observed_at) ASC`` with supersede state.
    """
    from ..statements import StatementStore

    _check_limit(limit)
    store = StatementStore(pg_pool, tenant_id)
    rows = await store.statements_timeline(pg_pool, text_query=text_query, limit=limit)
    return {"status": "success", "statements": rows, "count": len(rows)}
