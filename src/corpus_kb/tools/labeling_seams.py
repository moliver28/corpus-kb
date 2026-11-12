"""Shared store seams for the labeling handlers (gold-check and triage).

Both handlers read the same labeling store: the tenant's built labels and
the migration-028 table-presence check. The bodies live here exactly once;
each handler binds them to its module-level seam names (``_load_labels`` /
``_tables_present``) so tests can patch them per handler.
"""

from __future__ import annotations

import asyncpg

# The labeled-corpus read bound (the engine's read-batch scale; the gold
# check needs the FULL built population for an honest prevalence).
LABEL_READ_LIMIT = 200_000


async def load_labels(pg_pool: asyncpg.Pool, tenant_id: str) -> list[dict[str, object]]:
    """The label seam: the labeling store's built labels (tests fake it)."""
    from corpus_kb.labeling.writer import read_labels

    return await read_labels(pg_pool, tenant_id, LABEL_READ_LIMIT)


async def tables_present(pg_pool: asyncpg.Pool) -> bool:
    """The store seam (tests substitute fakes here)."""
    from corpus_kb.labeling.writer import labeling_tables_present

    return await labeling_tables_present(pg_pool)
