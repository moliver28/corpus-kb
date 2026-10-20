# allow: SIZE_OK — four catalogue ops' CLI flow helpers in one module; a
# split would scatter each op's CLI contract across files.
"""The ``corpus-kb`` graph read front door (storage-portability §5.1).

Top-level commands registered on the main app (see ``register``); one
command per catalogue read op, each added with its row:

  * ``corpus-kb search-graph QUERY``          — find graph entities by name.
  * ``corpus-kb get-entity-relations ID``     — one entity's relations.
  * ``corpus-kb graph-neighbors ID``          — 1-3 hop neighbor rows.
  * ``corpus-kb graph-traverse ID``           — walk up to graph.max_hops.

Each command calls the SAME shared tool function the MCP + HTTP + socket
surfaces call (``tools.graph_read_tools``), so every surface returns the
same envelope.

HONEST EXIT CODES: ``0`` the command answered (an empty list is still 0),
``1`` the command failed, ``3`` the database is unreachable (the shared
ingest plumbing maps connection failures there). Every outcome is PRINTED
or emitted as ``--json``.
"""

from __future__ import annotations

import json

import typer

# Reuse the ingest front door's shared plumbing (same honest-sync rule);
# cli_ingest imports corpus_kb.cli only lazily, so this import is acyclic.
from corpus_kb.cli_ingest import _load_config, _open_pool, _run_sync

EXIT_OK = 0
EXIT_FAILED = 1


def register(app: typer.Typer) -> None:
    """Attach the graph read commands to the main Typer app."""
    app.command("search-graph")(search_graph)
    app.command("get-entity-relations")(get_entity_relations)
    app.command("graph-neighbors")(graph_neighbors)
    app.command("graph-traverse")(graph_traverse)


def _emit_rows(result: dict[str, object], json_output: bool, label: str) -> None:
    """Emit an envelope whose ``result`` is a row list (order preserved)."""
    if json_output:
        # The EXACT tool envelope — no reshaping, no added keys.
        print(json.dumps(result, indent=2, default=str))
        return
    status = str(result.get("status", "error"))
    if status != "success":
        print(f"graph: {status}")
        message = result.get("message")
        if message:
            print(f"  {message}")
        return
    rows = result.get("result")
    items = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    if not items:
        print(f"no {label}")
        return
    for row in items:
        print(json.dumps(row, default=str))
    if result.get("truncated") is True:
        print("result truncated at the limit")


def _exit_code(result: dict[str, object]) -> int:
    return EXIT_OK if result.get("status") == "success" else EXIT_FAILED


async def _search_graph_flow(
    query: str, entity_type: str | None, limit: int, json_out: bool
) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.graph_read_tools import search_graph

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await search_graph(pool, query, config=cfg, entity_type=entity_type, limit=limit)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit_rows(result, json_out, "entities")
    return _exit_code(result)


def search_graph(
    query: str = typer.Argument(..., help="The name text to find"),
    entity_type: str | None = typer.Option(None, "--entity-type", help="Only this type"),
    limit: int = typer.Option(100, "--limit", min=1, help="How many rows to show"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Search graph entities by name.

    Exit codes: 0 answered (an empty list is still 0), 1 failed, 3 the
    database is unreachable.
    """
    return _run_sync(lambda: _search_graph_flow(query, entity_type, limit, json_output))


async def _get_entity_relations_flow(entity_id: str, limit: int | None, json_out: bool) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.graph_read_tools import get_entity_relations

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await get_entity_relations(pool, entity_id, config=cfg, limit=limit)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit_rows(result, json_out, "relations")
    return _exit_code(result)


def get_entity_relations(
    entity_id: str = typer.Argument(..., help="The entity UUID"),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Cap the row count"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """List one entity's relations (ordered by relation ID).

    Exit codes: 0 answered (an empty list is still 0), 1 failed, 3 the
    database is unreachable.
    """
    return _run_sync(lambda: _get_entity_relations_flow(entity_id, limit, json_output))


async def _graph_neighbors_flow(
    entity_id: str,
    hops: int,
    direction: str,
    relation_types: tuple[str, ...],
    entity_types: tuple[str, ...],
    limit: int,
    json_out: bool,
) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.graph_read_tools import graph_neighbors

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await graph_neighbors(
            pool,
            entity_id,
            config=cfg,
            hops=hops,
            direction=direction,
            relation_types=list(relation_types) or None,
            entity_types=list(entity_types) or None,
            limit=limit,
        )
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit_rows(result, json_out, "neighbors")
    return _exit_code(result)


# B008: repeatable list options read module-level singleton defaults (typer
# reuses the same Option info object; the value lists stay per-call).
_RELATION_TYPE_OPTION = typer.Option(None, "--relation-type", help="Keep this type")
_ENTITY_TYPE_OPTION = typer.Option(None, "--entity-type", help="Keep this type")


def graph_neighbors(
    entity_id: str = typer.Argument(..., help="The entity UUID to start from"),
    hops: int = typer.Option(1, "--hops", min=1, max=3, help="Depth: 1 to 3"),
    direction: str = typer.Option("both", "--direction", help="in, out, or both"),
    relation_type: list[str] | None = _RELATION_TYPE_OPTION,
    entity_type: list[str] | None = _ENTITY_TYPE_OPTION,
    limit: int = typer.Option(100, "--limit", min=1, help="How many rows to show"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """List neighbors within 1-3 hops (ordered by depth, name, ID).

    Exit codes: 0 answered (an empty list is still 0), 1 failed, 3 the
    database is unreachable.
    """
    return _run_sync(
        lambda: _graph_neighbors_flow(
            entity_id,
            hops,
            direction,
            tuple(relation_type or ()),
            tuple(entity_type or ()),
            limit,
            json_output,
        )
    )


async def _graph_traverse_flow(
    entity_id: str,
    max_depth: int | None,
    relation_types: tuple[str, ...],
    entity_types: tuple[str, ...],
    limit: int,
    json_out: bool,
) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.graph_read_tools import graph_traverse

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await graph_traverse(
            pool,
            entity_id,
            config=cfg,
            max_depth=max_depth,
            relation_types=list(relation_types) or None,
            entity_types=list(entity_types) or None,
            limit=limit,
        )
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit_rows(result, json_out, "entities")
    return _exit_code(result)


_TRAVERSE_RELATION_TYPE_OPTION = typer.Option(None, "--relation-type", help="Keep this type")
_TRAVERSE_ENTITY_TYPE_OPTION = typer.Option(None, "--entity-type", help="Keep this type")


def graph_traverse(
    entity_id: str = typer.Argument(..., help="The entity UUID to start from"),
    max_depth: int | None = typer.Option(
        None, "--max-depth", min=2, help="Depth cap (default: graph.max_hops)"
    ),
    relation_type: list[str] | None = _TRAVERSE_RELATION_TYPE_OPTION,
    entity_type: list[str] | None = _TRAVERSE_ENTITY_TYPE_OPTION,
    limit: int = typer.Option(100, "--limit", min=1, help="How many rows to show"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Walk the graph up to the depth cap; reports whether rows were cut.

    Exit codes: 0 answered (an empty list is still 0), 1 failed, 3 the
    database is unreachable.
    """
    return _run_sync(
        lambda: _graph_traverse_flow(
            entity_id,
            max_depth,
            tuple(relation_type or ()),
            tuple(entity_type or ()),
            limit,
            json_output,
        )
    )
