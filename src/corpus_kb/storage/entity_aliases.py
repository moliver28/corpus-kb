"""Entity-id alias resolution — one id space for graph entities (issue #455).

The entities read model dedupes on (tenant_id, name, entity_type): the
FIRST projected aggregate uuid becomes the canonical row id, and every
later ``Entity.Created`` for the same name becomes an ALIAS row that
points at it (written by ``projections/documents_rows.project_entity``).

This module owns the one lookup both consumers share:

- the relations projection resolves its endpoints through the aliases,
  so the relations FK only ever sees ids the entity table carries;
- the DuckDB graph store resolves ids on reads, so any id a caller ever
  received (the CLI ``add-entity`` return) answers like the canonical row.

SQL is written with ``$n`` placeholders; both gateway dialects accept
that shape (the DuckDB gateway translates).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

#: One single-value read: (sql, tenant_id, alias_entity_id) -> id or None.
AliasLookup = Callable[[str, str, str], Awaitable[object | None]]

#: Canonical entity id for one id in the entity id space (alias table first).
RESOLVE_ALIAS_SQL = (
    "SELECT entity_id FROM entity_id_aliases WHERE tenant_id = $1 AND alias_entity_id = $2"
)


async def resolve_entity_id(fetchval: AliasLookup, tenant_id: str, entity_id: str) -> str:
    """The canonical id for ``entity_id``: the alias table, else the id itself.

    ``fetchval(sql, tenant_id, alias_entity_id)`` returns the canonical id
    or None. An id that resolves keeps its canonical spelling; an unknown
    id passes through unchanged so callers keep their own error paths.
    """
    canonical = await fetchval(RESOLVE_ALIAS_SQL, tenant_id, entity_id)
    return entity_id if canonical is None else str(canonical)
