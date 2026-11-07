"""The ``corpus-kb`` entity read front door (Phase-2 Wave 4).

Top-level command registered on the main app (see ``register``):

  * ``corpus-kb list-entities`` — the graph entities, one name-ordered
    window.

The command calls the SAME shared tool function the MCP + HTTP + socket
surfaces call (``tools.entity_read_tools``), so every surface returns the
same envelope.

HONEST EXIT CODES: ``0`` the command answered (an empty list is still 0),
``1`` the command failed, ``3`` the database is unreachable (the shared
ingest plumbing maps connection failures there). Every outcome is PRINTED
or emitted as ``--json``.
"""

from __future__ import annotations

import typer

# Reuse the ingest front door's shared plumbing (same honest-sync rule);
# cli_ingest imports corpus_kb.cli only lazily, so this import is acyclic.
from corpus_kb.cli._plumbing import EXIT_FAILED
from corpus_kb.cli.cli_graph_hops import _emit_rows, _exit_code
from corpus_kb.cli.cli_ingest import _load_config, _open_pool, _run_sync


def register(app: typer.Typer) -> None:
    """Attach the entity read command to the main Typer app."""
    app.command("list-entities")(list_entities)


async def _list_entities_flow(
    entity_type: str | None, limit: int, offset: int, json_out: bool
) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.entity_read_tools import list_entities

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await list_entities(
            pool, config=cfg, entity_type=entity_type, limit=limit, offset=offset
        )
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit_rows(result, json_out, "entities")
    return _exit_code(result)


def list_entities(
    entity_type: str | None = typer.Option(None, "--entity-type", help="Only this type"),
    limit: int = typer.Option(100, "--limit", min=1, help="How many rows to show"),
    offset: int = typer.Option(0, "--offset", min=0, help="Skip the first N rows"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """List graph entities (ordered by name, then ID).

    Exit codes: 0 answered (an empty list is still 0), 1 failed, 3 the
    database is unreachable.
    """
    return _run_sync(lambda: _list_entities_flow(entity_type, limit, offset, json_output))
