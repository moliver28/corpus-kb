"""The ``corpus-kb`` graph read front door (storage-portability §5.1).

Top-level commands registered on the main app (see ``register``); one
command per catalogue read op, each added with its row:

  * ``corpus-kb search-graph QUERY``          — find graph entities by name.

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
