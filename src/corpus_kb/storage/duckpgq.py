# allow: SIZE_OK — one responsibility (the DuckPGQ traversal branch); most
# lines are declarative SQL text plus the one-shot install/load helper.
"""DuckPGQ traversal branch for the DuckDB graph store (opt-in).

Active when ``graph.duckdb_graph_backend: duckpgq`` (and the database
backend is duckdb). The extension installs from the community repository
on first use; ANY failure raises the typed
``DuckPGQUnavailableError``, the store warns once, and that connection
stays on the recursive-CTE path permanently.

Engine reality (probed on duckdb 1.5.6, windows_amd64, duckpgq community):

  - Bound parameters do not reach ``GRAPH_TABLE`` matcher input, and a
    session variable could change between two statements of one
    transaction, so start/tenant values travel as quoted literals (the
    only escaping in SQL standard strings is a doubled quote).
  - Undirected patterns (``-[e]-``) crash the binder, a quantified
    pattern cannot carry an edge predicate, and a UNION ALL of two
    GRAPH_TABLE arms aborts the database. Fixed single-pattern chains
    are stable and do carry edge predicates.
  - One statement per orientation chain therefore renders each walk:
    for depth ``k`` the walk is ``2**k`` chains (one per out/in
    permutation), depths 1..3. Deep walks are not expressible; the
    store falls back. The per-statement rows merge in
    ``merge_min_depth_rows`` (zero-drift rule 9: the pinned
    ``(depth, name, entity_id)`` order, min depth per node).

Every edge carries its tenant predicate, so the walk cannot leave the
tenant through an intermediate hop; this matches the CTE, which filters
``relations.tenant_id`` per edge. The property-graph DDL is a module
constant over our fixed schema table names — no user data reaches it.
"""

from __future__ import annotations

from itertools import product
from typing import TYPE_CHECKING, Any

from ..errors import DatabaseUnavailableError
from .graph_sql import MAX_NEIGHBOR_HOPS, validate_depth

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

DUCKDB_GRAPH_BACKENDS = ("cte", "duckpgq")


class DuckPGQUnavailableError(DatabaseUnavailableError):
    """DuckPGQ could not install, load, or run; the CTE path takes over."""


# Static DDL over our fixed schema names. Never format anything into it.
_PROPERTY_GRAPH_DDL = """
CREATE PROPERTY GRAPH corpus_kb_graph
  VERTEX TABLES (entities)
  EDGE TABLES (relations
    SOURCE KEY (source_entity_id) REFERENCES entities (entity_id)
    DESTINATION KEY (target_entity_id) REFERENCES entities (entity_id))
"""


def _literal(value: str) -> str:
    """One quoted SQL string literal; a doubled quote is the only escape."""
    return "'" + value.replace("'", "''") + "'"


def _literal_list(values: list[str]) -> str:
    """A quoted IN-list for edge and vertex type filters."""
    return ", ".join(_literal(value) for value in values)


def install_and_load(conn: DuckDBPyConnection) -> None:
    """Install the community extension and load it into this connection."""
    conn.execute("INSTALL duckpgq FROM community")
    conn.execute("LOAD duckpgq")


def ensure_duckpgq(conn: DuckDBPyConnection) -> None:
    """Install and load the extension; create the static property graph.

    Idempotent: an existing property graph is left as found. Any engine
    failure raises the typed ``DuckPGQUnavailableError`` — the caller
    warns once and keeps that connection on the recursive-CTE path.
    """
    import duckdb

    try:
        install_and_load(conn)
        try:
            conn.execute(_PROPERTY_GRAPH_DDL)
        except duckdb.Error as exc:
            if "already exists" not in str(exc).lower():
                raise
    except duckdb.Error as exc:
        raise DuckPGQUnavailableError(
            "DuckPGQ traversal is not usable here; the store uses the CTE path.",
            remediation=(
                "Check the DuckDB version and the graph tables,"
                " or keep graph.duckdb_graph_backend at cte."
            ),
        ) from exc


def _edge_where(alias: str, tenant_id: str, relation_types: list[str] | None) -> str:
    """The tenant predicate, plus the relation type filter when given."""
    predicate = f"{alias}.tenant_id = {_literal(tenant_id)}"
    if relation_types:
        predicate += f" AND {alias}.relation_type IN ({_literal_list(relation_types)})"
    return predicate


def _vertex_where(alias: str, tenant_id: str, entity_types: list[str] | None) -> str:
    """The tenant predicate, plus the entity type filter when given."""
    predicate = f"{alias}.tenant_id = {_literal(tenant_id)}"
    if entity_types:
        predicate += f" AND {alias}.entity_type IN ({_literal_list(entity_types)})"
    return predicate


def _chain(
    orientations: tuple[str, ...],
    tenant_id: str,
    entity_id: str,
    entity_types: list[str] | None,
    relation_types: list[str] | None,
) -> str:
    """One fixed-depth chain statement: one statement per orientation mix."""
    depth = len(orientations)
    start_vertex = (
        f"(s:entities WHERE {_vertex_where('s', tenant_id, None)}"
        f" AND s.entity_id = {_literal(entity_id)})"
    )
    pattern: list[str] = [start_vertex]
    columns = [
        "v.entity_id AS entity_id",
        "v.name AS name",
        "v.entity_type AS entity_type",
        "v.metadata AS metadata",
        f"{depth} AS depth",
    ]
    for hop, orientation in enumerate(orientations, start=1):
        edge = f"r{hop}"
        reached = "v" if hop == depth else f"x{hop}"
        # "out" follows the edge arrow; "in" walks against it.
        arrow_left, arrow_right = ("<-", "-") if orientation == "in" else ("-", "->")
        edge_pattern = (
            f"{arrow_left}[{edge}:relations WHERE {_edge_where(edge, tenant_id, relation_types)}]"
        )
        reached_pattern = (
            f"{arrow_right}({reached}:entities"
            f" WHERE {_vertex_where(reached, tenant_id, entity_types)})"
        )
        pattern.append(edge_pattern + reached_pattern)
    return (
        f"SELECT gt.entity_id AS entity_id, gt.name AS name,"
        f" gt.entity_type AS entity_type, gt.metadata AS metadata, gt.depth AS depth"
        f" FROM GRAPH_TABLE (corpus_kb_graph MATCH {''.join(pattern)}"
        f" COLUMNS ({', '.join(columns)})) gt"
    )


def _start_row(tenant_id: str, entity_id: str) -> str:
    """The depth-0 base row: the recursive CTE's start seed, as SQL."""
    return (
        "SELECT s.entity_id::text AS entity_id, s.name AS name,"
        " s.entity_type AS entity_type, s.metadata::text AS metadata, 0 AS depth"
        " FROM entities s"
        f" WHERE s.tenant_id = {_literal(tenant_id)} AND s.entity_id = {_literal(entity_id)}"
    )


def _allowed_combos(direction: str, depth: int) -> list[tuple[str, ...]]:
    """The orientation permutations one direction admits at one depth."""
    combos: list[tuple[str, ...]] = list(product(("out", "in"), repeat=depth))
    if direction == "out":
        return [combo for combo in combos if set(combo) == {"out"}]
    if direction == "in":
        return [combo for combo in combos if set(combo) == {"in"}]
    return combos


def traversal_statements(
    direction: str,
    max_depth: int,
    tenant_id: str,
    entity_id: str,
    entity_types: list[str] | None = None,
    relation_types: list[str] | None = None,
    include_start: bool = False,
) -> list[str]:
    """Walk rows within hops 1..max_depth, as one or more queries.

    Each list element is a self-contained statement (one orientation
    chain per depth); the store runs them separately and merges the
    rows — two GRAPH_TABLE arms in one statement crash this build.
    Depths past ``MAX_NEIGHBOR_HOPS`` raise the typed error (the store
    falls back to the recursive CTE, which owns deeper walks).
    ``include_start`` adds the depth-0 base row the recursive CTE seeds
    (the bfs visited map needs it).
    """
    if direction not in ("in", "out", "both"):
        raise ValueError(f"direction must be one of ('in', 'out', 'both'), got {direction!r}")
    validate_depth(max_depth)
    if int(max_depth) > MAX_NEIGHBOR_HOPS:
        raise DuckPGQUnavailableError(
            f"DuckPGQ covers walks up to {MAX_NEIGHBOR_HOPS} hops in this build.",
            remediation="Use the cte graph backend for deeper walks.",
        )
    statements: list[str] = []
    if include_start:
        statements.append(_start_row(tenant_id, entity_id))
    for depth in range(1, int(max_depth) + 1):
        for combo in _allowed_combos(direction, depth):
            statements.append(_chain(combo, tenant_id, entity_id, entity_types, relation_types))
    return statements


def _sort_key(row: dict[str, Any]) -> tuple[int, str, str]:
    """The pinned total order shared with the CTE path."""
    return (int(row["depth"]), str(row["name"]), str(row["entity_id"]))


def merge_min_depth_rows(
    rows: list[dict[str, Any]],
    limit: int | None = None,
    exclude: str | None = None,
) -> list[dict[str, Any]]:
    """Keep each node's shallowest row; pin (depth, name, entity_id) order.

    Ties break on the same (depth, name, entity_id) key the CTE path
    orders by, so equal-depth duplicates are stable too. ``limit`` caps
    the result after the merge, like the CTE's outer LIMIT. ``exclude``
    drops one node (the walk's start): the CTE's path-array guard never
    returns it, while the engine's walk rows can cycle back to it.
    """
    shallowest: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row["entity_id"])
        if exclude is not None and key == exclude:
            continue
        current = shallowest.get(key)
        if current is None or _sort_key(row) < _sort_key(current):
            shallowest[key] = row
    merged = sorted(shallowest.values(), key=_sort_key)
    if limit is not None:
        merged = merged[: int(limit)]
    return merged
