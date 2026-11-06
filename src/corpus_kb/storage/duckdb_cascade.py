"""In-code delete cascade for the DuckDB gateway (issue #330 Part 1).

DuckDB 1.5.x (verified on the pinned wheel) cannot even declare the
Postgres delete actions — the parser rejects ``ON DELETE CASCADE``/``SET
NULL`` outright — and it checks a foreign key against the LAST COMMITTED
state, so a delete earlier in the same transaction is invisible to a later
stage's FK check. The mirror schema keeps plain FKs (orphan inserts still
fail); this module owns the delete actions Postgres delegates to its
constraints, for one document at a time:

- Stage 1 nulls the SET NULL links in one committed transaction.
- Then one committed stage per doomed table, children first. The
  ``documents`` row goes LAST, so a delete that stops midway is safe to
  run again (finished stages stay done; the anchor row still resolves
  every remaining stage).
- A self-referencing table (``shadow_chunks.parent_id``) deletes leaves
  first: one committed pass per descendant level, deepest first, with a
  cycle guard.

The stage SQL and the doomed-set order come from
:mod:`storage.duckdb_cascade_plan` (the static half, 250-line split).

Tenant scope: DuckDB has no row-level security, so every stage carries the
explicit ``doc_id = $1 AND tenant_id = $2`` predicate — the tenant
predicate IS the isolation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from corpus_kb.errors import InternalError
from corpus_kb.storage.duckdb_cascade_plan import (
    ANCHOR_PRED,
    SET_NULL_LINKS,
    doomed_pred,
    doomed_tables,
    fk_edges,
    self_reference,
    set_null_sql,
    tag_count,
)
from corpus_kb.storage.sql_ident import quoted_identifier

if TYPE_CHECKING:
    from corpus_kb.storage.gateway import DuckDbGateway

logger = logging.getLogger(__name__)

__all__ = ["SET_NULL_LINKS", "delete_document_cascade"]


async def delete_document_cascade(
    gateway: DuckDbGateway, tenant_id: str, doc_id: str
) -> dict[str, int]:
    """Delete one document and every row the Postgres ON DELETE rules reach.

    Idempotent: an unknown ID deletes nothing and returns all-zero counts.
    Returns the per-table deleted-row counts (set-NULL stages keyed
    ``table.column``) so callers can report what moved.
    """
    edges = await fk_edges(gateway)
    doomed = doomed_tables(edges)
    counts: dict[str, int] = {}

    # Stage 1: SET NULL links, one committed transaction. Committed before
    # any delete, so a stopped run leaves consistent links behind.
    async with gateway.transaction(tenant_id):
        for table, column, parent, parent_col in SET_NULL_LINKS:
            tag = await gateway.execute(
                set_null_sql(table, column, parent, parent_col, edges), doc_id, tenant_id
            )
            counts[f"{table}.{column}"] = tag_count(tag)

    # Stage k: one committed stage per doomed table, children first. The
    # documents row goes last.
    for table in doomed:
        counts[table] = await _delete_table(gateway, tenant_id, doc_id, table, edges)
    tag = await gateway.execute(
        f"DELETE FROM {quoted_identifier('documents')} WHERE {ANCHOR_PRED}",
        doc_id,
        tenant_id,
    )
    counts["documents"] = tag_count(tag)
    logger.info(
        "Document %s deleted on duckdb: %s", doc_id, {k: v for k, v in sorted(counts.items()) if v}
    )
    return counts


async def _delete_table(
    gateway: DuckDbGateway,
    tenant_id: str,
    doc_id: str,
    table: str,
    edges: list,
) -> int:
    """Delete one doomed table's rows; committed per stage (FK visibility)."""
    pred = doomed_pred(table, edges, set(doomed_tables(edges)) | {"documents"})
    self_ref = self_reference(edges, table)
    if self_ref is not None:
        return await _delete_self_referencing(gateway, tenant_id, doc_id, table, self_ref, pred)
    tag = await gateway.execute(
        f"DELETE FROM {quoted_identifier(table)} WHERE {pred}", doc_id, tenant_id
    )
    return tag_count(tag)


async def _delete_self_referencing(
    gateway: DuckDbGateway,
    tenant_id: str,
    doc_id: str,
    table: str,
    self_ref: tuple[str, str],
    pred: str,
) -> int:
    """Leaves first for a self-referencing table, one committed pass per level.

    ``shadow_chunks`` is the live case: doomed rows form a parent tree (and
    doomed parents link doomed children), so a parent's delete only clears
    its FK check once every doomed row referencing it is gone AND
    committed. The pass loop deletes the child-free rows of the doomed
    set, commits, and repeats; a doomed cycle would never go child-free
    and raises instead of looping.
    """
    pk_col, parent_col = self_ref
    anchor_ids = [
        str(row["pk"])
        for row in await gateway.fetch(
            f"SELECT {quoted_identifier(pk_col)} AS pk FROM"
            f" {quoted_identifier(table)} WHERE {pred}",
            doc_id,
            tenant_id,
        )
    ]
    doomed_ids = await _descendants(gateway, table, pk_col, parent_col, anchor_ids)
    if not doomed_ids:
        return 0
    rows = list(
        await gateway.fetch(
            f"SELECT {quoted_identifier(pk_col)} AS pk,"
            f" {quoted_identifier(parent_col)} AS parent"
            f" FROM {quoted_identifier(table)}"
            f" WHERE list_contains($1::VARCHAR[], {quoted_identifier(pk_col)})",
            doomed_ids,
        )
    )
    child_count: dict[str, int] = dict.fromkeys(doomed_ids, 0)
    parent_of: dict[str, str | None] = {}
    for row in rows:
        pk = str(row["pk"])
        parent = row["parent"]
        parent_of[pk] = str(parent) if parent is not None else None
        if parent is not None and str(parent) in child_count and str(parent) != pk:
            child_count[str(parent)] += 1
    total = 0
    remaining = set(doomed_ids)
    while remaining:
        ready = sorted(pk for pk, n in child_count.items() if pk in remaining and n == 0)
        if not ready:
            raise InternalError(
                f"Cycle in {table}.{parent_col} during the delete cascade.",
                remediation="Fix the parent links on this table, then run the delete again.",
            )
        async with gateway.transaction(tenant_id):
            tag = await gateway.execute(
                f"DELETE FROM {quoted_identifier(table)} WHERE"
                f" list_contains($1::VARCHAR[], {quoted_identifier(pk_col)})",
                ready,
            )
            total += tag_count(tag)
        for pk in ready:
            remaining.discard(pk)
            parent = parent_of.get(pk)
            if parent is not None and parent in child_count:
                child_count[parent] -= 1
    return total


async def _descendants(
    gateway: DuckDbGateway,
    table: str,
    pk_col: str,
    parent_col: str,
    anchor_ids: list[str],
) -> list[str]:
    """The doomed id set: anchors plus everything parent-linked to them.

    Breadth-first over ``parent_col``; an id already in the set is skipped
    (diamonds are legal — a row can be linked to the document AND to a
    parent). The loop terminates because the seen set only grows.
    """
    if not anchor_ids:
        return []
    doomed = set(anchor_ids)
    frontier = anchor_ids
    while frontier:
        rows = list(
            await gateway.fetch(
                f"SELECT {quoted_identifier(pk_col)} AS pk FROM"
                f" {quoted_identifier(table)}"
                f" WHERE list_contains($1::VARCHAR[], {quoted_identifier(parent_col)})",
                frontier,
            )
        )
        frontier = []
        for row in rows:
            pk = str(row["pk"])
            if pk not in doomed:
                doomed.add(pk)
                frontier.append(pk)
    return sorted(doomed)
