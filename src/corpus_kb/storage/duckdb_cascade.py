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

The FK graph is read from ``duckdb_constraints()`` at call time, so a new
child table needs no code change. A multi-column FK is refused with
``InternalError`` — none exists in the mirror; a future one must be
modeled here before it ships.

Tenant scope: DuckDB has no row-level security, so every stage carries the
explicit ``doc_id = $1 AND tenant_id = $2`` predicate — the tenant
predicate IS the isolation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from corpus_kb.errors import InternalError
from corpus_kb.storage.sql_ident import quoted_identifier

if TYPE_CHECKING:
    from corpus_kb.storage.gateway import DuckDbGateway

logger = logging.getLogger(__name__)

#: (table, column, parent table, parent column) links that Postgres keeps
#: through ON DELETE SET NULL when a document dies. ``entities`` and
#: ``relations`` rows survive a document delete; ``research_units`` rows
#: die with the document, so their speaker link is nulled first. The
#: ``entities`` link has no FK in the mirror after the 040 rebuild — it is
#: declared here, not discovered.
SET_NULL_LINKS: tuple[tuple[str, str, str, str], ...] = (
    ("entities", "source_document_id", "documents", "doc_id"),
    ("relations", "chunk_id", "chunks", "chunk_id"),
    ("research_units", "speaker_id", "research_speakers", "speaker_id"),
)

_ANCHOR_PRED = "doc_id = $1 AND tenant_id = $2"

_CASCADE_MODELED = (
    "A multi-column foreign key needs a modeled delete cascade."
    " Model the table in storage.duckdb_cascade before it ships."
)


async def delete_document_cascade(
    gateway: DuckDbGateway, tenant_id: str, doc_id: str
) -> dict[str, int]:
    """Delete one document and every row the Postgres ON DELETE rules reach.

    Idempotent: an unknown ID deletes nothing and returns all-zero counts.
    Returns the per-table deleted-row counts (set-NULL stages keyed
    ``table.column``) so callers can report what moved.
    """
    edges = _edges_to_rows(await _fk_edge_rows(gateway))
    doomed = _doomed_tables(edges)
    doomed_set = set(doomed) | {"documents"}
    counts: dict[str, int] = {}

    # Stage 1: SET NULL links, one committed transaction. Committed before
    # any delete, so a stopped run leaves consistent links behind.
    async with gateway.transaction(tenant_id):
        for table, column, parent, parent_col in SET_NULL_LINKS:
            sql = _set_null_sql(table, column, parent, parent_col, edges, doomed_set)
            tag = await gateway.execute(sql, doc_id, tenant_id)
            counts[f"{table}.{column}"] = _tag_count(tag)

    # Stage k: one committed stage per doomed table, children first. The
    # documents row goes last.
    for table in doomed:
        counts[table] = await _delete_table(gateway, tenant_id, doc_id, table, edges)
    tag = await gateway.execute(
        f"DELETE FROM {quoted_identifier('documents')} WHERE {_ANCHOR_PRED}",
        doc_id,
        tenant_id,
    )
    counts["documents"] = _tag_count(tag)
    logger.info(
        "Document %s deleted on duckdb: %s", doc_id, {k: v for k, v in sorted(counts.items()) if v}
    )
    return counts


# --- doomed-set discovery ----------------------------------------------------


async def _fk_edge_rows(gateway: DuckDbGateway) -> list[dict]:
    """Every FOREIGN KEY edge in the live catalog, as raw rows."""
    return list(
        await gateway.fetch(
            "SELECT table_name, constraint_column_names, referenced_table,"
            " referenced_column_names"
            " FROM duckdb_constraints()"
            " WHERE constraint_type = 'FOREIGN KEY'"
            "   AND database_name = current_database()"
            "   AND schema_name = current_schema()"
        )
    )


def _edges_to_rows(rows: list[dict]) -> list[tuple[str, str, str, str]]:
    """Typed (child, child_col, parent, parent_col) edges; no multi-column FK."""
    edges: list[tuple[str, str, str, str]] = []
    for row in rows:
        cols = list(row["constraint_column_names"])
        ref_cols = list(row["referenced_column_names"])
        if len(cols) != 1 or len(ref_cols) != 1:
            raise InternalError(
                f"Multi-column foreign key on table {row['table_name']!r}.",
                remediation=_CASCADE_MODELED,
            )
        edges.append((str(row["table_name"]), cols[0], str(row["referenced_table"]), ref_cols[0]))
    return edges


def _doomed_tables(edges: list[tuple[str, str, str, str]]) -> list[str]:
    """Descendant tables of ``documents``, children first, self-FKs excluded.

    Depth-first post-order over the live FK graph: every table appears
    after the tables that reference it, so a stage deletes into already
    committed state (the only state DuckDB's FK checks can see). SET NULL
    edges never propagate doom — the referencing row SURVIVES the parent's
    delete with a NULL link (``relations`` via ``chunks`` is the live case).
    """
    set_null_edges = {(table, column) for table, column, _p, _pc in SET_NULL_LINKS}
    by_parent: dict[str, list[str]] = {}
    for child, child_col, parent, _parent_col in edges:
        if child == parent or (child, child_col) in set_null_edges:
            continue  # self-reference / survivor link: no doom propagates
        children = by_parent.setdefault(parent, [])
        if child not in children:
            children.append(child)

    order: list[str] = []
    seen: set[str] = set()

    def visit(table: str) -> None:
        for child in sorted(by_parent.get(table, [])):
            if child not in seen:
                seen.add(child)
                visit(child)
                order.append(child)

    visit("documents")
    return order


def _doomed_pred(
    table: str,
    edges: list[tuple[str, str, str, str]],
    doomed: set[str] | None = None,
    depth: int = 0,
) -> str:
    """SQL predicate selecting the doomed rows of one table.

    Anchored at ``documents`` (``doc_id = $1 AND tenant_id = $2``); a child
    table's rows are doomed when an FK column names a doomed parent row.
    Edges to parents OUTSIDE the doomed set are skipped (a doomed table can
    also reference a survivor, e.g. chunk_predictions -> label_sets), and
    SET NULL edges never doom (the referencing row survives with a NULL).
    Self-referencing edges are excluded (the level pass owns them); the
    depth cap turns an unforeseen cycle into a typed error.
    """
    if table == "documents":
        return _ANCHOR_PRED
    if depth > len(edges):
        raise InternalError(
            "The delete cascade could not resolve its table graph.",
            remediation="Check the foreign key graph on this database file.",
        )
    set_null_edges = {(t, c) for t, c, _p, _pc in SET_NULL_LINKS}
    parts: list[str] = []
    for child, child_col, parent, parent_col in sorted(edges):
        if child != table or parent == table:
            continue
        if (child, child_col) in set_null_edges:
            continue
        if doomed is not None and parent != "documents" and parent not in doomed:
            continue
        inner = _doomed_pred(parent, edges, doomed, depth + 1)
        parts.append(
            f"{quoted_identifier(child_col)} IN ("
            f"SELECT {quoted_identifier(parent_col)} FROM {quoted_identifier(parent)}"
            f" WHERE {inner})"
        )
    if not parts:
        raise InternalError(
            f"Table {table!r} is marked doomed but has no modeled link to the document.",
            remediation=_CASCADE_MODELED,
        )
    return "(" + " OR ".join(parts) + ")"


# --- stage execution ---------------------------------------------------------


async def _delete_table(
    gateway: DuckDbGateway,
    tenant_id: str,
    doc_id: str,
    table: str,
    edges: list[tuple[str, str, str, str]],
) -> int:
    """Delete one doomed table's rows; committed per stage (FK visibility)."""
    pred = _doomed_pred(table, edges, set(_doomed_tables(edges)) | {"documents"})
    self_ref = _self_reference(edges, table)
    if self_ref is not None:
        return await _delete_self_referencing(gateway, tenant_id, doc_id, table, self_ref, pred)
    tag = await gateway.execute(
        f"DELETE FROM {quoted_identifier(table)} WHERE {pred}", doc_id, tenant_id
    )
    return _tag_count(tag)


def _self_reference(edges: list[tuple[str, str, str, str]], table: str) -> tuple[str, str] | None:
    """(pk col, referencing col) when the table references itself, else None.

    For a self-FK the REFERENCED column is the pk (``shadow_chunks.
    shadow_chunk_id``) and the referencing column is the link
    (``parent_id``) — the reverse of the edge tuple's order.
    """
    for child, child_col, parent, parent_col in edges:
        if child == table == parent:
            return (parent_col, child_col)
    return None


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
            total += _tag_count(tag)
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


# --- SQL text ----------------------------------------------------------------


def _set_null_sql(
    table: str,
    column: str,
    parent: str,
    parent_col: str,
    edges: list[tuple[str, str, str, str]],
    doomed: set[str],
) -> str:
    """One SET NULL stage statement (identifiers quoted, values bound)."""
    inner = _doomed_pred(parent, edges, doomed)
    return (
        f"UPDATE {quoted_identifier(table)} SET {quoted_identifier(column)} = NULL"
        f" WHERE {quoted_identifier(column)} IN ("
        f"SELECT {quoted_identifier(parent_col)} FROM {quoted_identifier(parent)}"
        f" WHERE {inner})"
    )


def _tag_count(tag: str) -> int:
    """The row count from a PG-format status tag ("DELETE 3")."""
    _verb, _, count = tag.partition(" ")
    try:
        return int(count)
    except ValueError:
        return 0
