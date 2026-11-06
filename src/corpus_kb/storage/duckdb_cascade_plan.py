"""Delete-cascade planning for DuckDB: the FK graph and the stage SQL.

Split from :mod:`duckdb_cascade` (250-line rule). This module owns the
STATIC side of the in-code delete cascade (issue #330): reading the FK
graph from ``duckdb_constraints()`` at call time, ordering the doomed
tables children-first, building each stage's SQL predicate anchored at
the documents row, and the SET NULL stage statements. The EXECUTOR
lives in :mod:`duckdb_cascade`.

House rules honored here: identifiers pass through
:mod:`storage.sql_ident` before interpolation (migration 037's rule),
and the tenant predicate is explicit — DuckDB has no row-level
security.
"""

from __future__ import annotations

from corpus_kb.errors import InternalError
from corpus_kb.storage.sql_ident import quoted_identifier

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

ANCHOR_PRED = "doc_id = $1 AND tenant_id = $2"

_CASCADE_MODELED = (
    "A multi-column foreign key needs a modeled delete cascade."
    " Model the table in storage.duckdb_cascade before it ships."
)

#: A typed FK edge: (child table, child column, parent table, parent column).
Edge = tuple[str, str, str, str]


async def fk_edges(gateway) -> list[Edge]:
    """Every FOREIGN KEY edge in the live catalog (call-time read)."""
    rows = list(
        await gateway.fetch(
            "SELECT table_name, constraint_column_names, referenced_table,"
            " referenced_column_names"
            " FROM duckdb_constraints()"
            " WHERE constraint_type = 'FOREIGN KEY'"
            "   AND database_name = current_database()"
            "   AND schema_name = current_schema()"
        )
    )
    edges: list[Edge] = []
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


def doomed_tables(edges: list[Edge]) -> list[str]:
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


def doomed_pred(
    table: str,
    edges: list[Edge],
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
        return ANCHOR_PRED
    if depth > len(edges):
        raise InternalError(
            "The delete cascade could not resolve its table graph.",
            remediation="Check the foreign key graph on this database file.",
        )
    set_null_edges = {(t, c) for t, c, _p, _pc in SET_NULL_LINKS}
    parts: list[str] = []
    for child, child_col, parent, parent_col in sorted(edges):
        if child != table or parent == table or (child, child_col) in set_null_edges:
            continue
        if doomed is not None and parent != "documents" and parent not in doomed:
            continue
        inner = doomed_pred(parent, edges, doomed, depth + 1)
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


def set_null_sql(table: str, column: str, parent: str, parent_col: str, edges: list[Edge]) -> str:
    """One SET NULL stage statement (identifiers quoted, values bound)."""
    inner = doomed_pred(parent, edges, set(doomed_tables(edges)) | {"documents"})
    return (
        f"UPDATE {quoted_identifier(table)} SET {quoted_identifier(column)} = NULL"
        f" WHERE {quoted_identifier(column)} IN ("
        f"SELECT {quoted_identifier(parent_col)} FROM {quoted_identifier(parent)}"
        f" WHERE {inner})"
    )


def self_reference(edges: list[Edge], table: str) -> tuple[str, str] | None:
    """(pk col, referencing col) when the table references itself, else None.

    For a self-FK the REFERENCED column is the pk (``shadow_chunks.
    shadow_chunk_id``) and the referencing column is the link
    (``parent_id``) — the reverse of the edge tuple's order.
    """
    for child, child_col, parent, parent_col in edges:
        if child == table == parent:
            return (parent_col, child_col)
    return None


def tag_count(tag: str) -> int:
    """The row count from a PG-format status tag ("DELETE 3")."""
    _verb, _, count = tag.partition(" ")
    try:
        return int(count)
    except ValueError:
        return 0
