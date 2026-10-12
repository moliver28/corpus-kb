"""U48/P6: query-time parent expansion over the shadow hierarchy.

Small-to-big, main-path form: after RRF fusion, each live hit's TEXT is
replaced with the 2048-token parent context of the shadow child row linked
to it (metadata ``v1_chunk_id`` -> child.parent_id -> chunk.parent_id).
Ids, scores, order and count are untouched — only the returned context
grows, so downstream attribution keeps pointing at the hit chunk.

Gated by ``ingest.hierarchy.expand_to_parent`` (default FALSE until the
shadow benchmark passes). Fail-open everywhere: any probe failure returns
the fused results unchanged — expansion must never break search (same
contract as the U21 selectivity probe and the rerank degraders).

The shadow is a side-by-side index: when a document has no shadow rows
(not yet re-ingested, or hierarchy disabled), its hits pass through
unexpanded.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from ..domain.models import SearchResult

logger = logging.getLogger(__name__)

# Deterministic parent pick per v1 hit: DISTINCT ON + lowest parent seq.
_PARENT_TEXT_SQL = """
WITH hit_children AS (
    SELECT sc.metadata->>'v1_chunk_id' AS v1_chunk_id,
           sc.parent_id AS chunk_level_id
    FROM shadow_chunks sc
    WHERE sc.tenant_id = $1
      AND sc.level = 'child'
      AND sc.metadata->>'v1_chunk_id' = ANY($2::text[])
),
parent_links AS (
    SELECT hc.v1_chunk_id, cl.parent_id AS parent_level_id
    FROM hit_children hc
    JOIN shadow_chunks cl ON cl.shadow_chunk_id = hc.chunk_level_id
    WHERE cl.tenant_id = $1 AND cl.parent_id IS NOT NULL
)
SELECT DISTINCT ON (pl.v1_chunk_id)
       pl.v1_chunk_id, p.text AS parent_text
FROM parent_links pl
JOIN shadow_chunks p ON p.shadow_chunk_id = pl.parent_level_id
WHERE p.tenant_id = $1
ORDER BY pl.v1_chunk_id, p.seq ASC
"""


class ParentReader(Protocol):
    """Minimal asyncpg surface the expansion probe needs."""

    async def fetch(self, sql: str, *args: object) -> list[Mapping[str, object]]:
        """Run the parent-text probe; returns the rows."""
        ...


class ParentPool(Protocol):
    """Minimal asyncpg.Pool surface (acquire -> ParentReader async context)."""

    def acquire(self) -> AbstractAsyncContextManager[ParentReader]:
        """Return an async context manager yielding a ParentReader."""
        ...


async def expand_to_parent(
    pool: ParentPool,
    tenant_id: str,
    results: list[SearchResult],
) -> list[SearchResult]:
    """Replace each hit's text with its shadow parent context, in place-order.

    Hits without a shadow link pass through unexpanded; order, scores,
    chunk ids and count are preserved.
    """
    if not results:
        return results
    hit_ids = [str(result.chunk_id) for result in results]
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_PARENT_TEXT_SQL, tenant_id, hit_ids)
    except Exception as exc:
        # Fail-open (module contract): a broken shadow probe must never break
        # search — same degrader shape as the U21 selectivity probe.
        logger.warning("Parent-expansion probe failed; returning hits unchanged: %s", exc)
        return results
    parent_text_by_hit = {str(row["v1_chunk_id"]): str(row["parent_text"]) for row in rows}
    if not parent_text_by_hit:
        return results
    return [
        (
            result.model_copy(update={"text": parent_text_by_hit[str(result.chunk_id)]})
            if str(result.chunk_id) in parent_text_by_hit
            else result
        )
        for result in results
    ]
