"""The ``corpus-kb`` chunk read front door (Phase-2 Wave 5).

Top-level command registered on the main app (see ``register``):

  * ``corpus-kb chunks-list DOC_ID`` — one document's chunks, one window
    in ingest order.

The command calls the SAME shared tool function the MCP + HTTP + socket
surfaces call (``tools.chunk_read_tools``), so every surface returns the
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
    """Attach the chunk read command to the main Typer app."""
    app.command("chunks-list")(chunks_list)


async def _chunks_list_flow(doc_id: str, limit: int, offset: int, json_out: bool) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.chunk_read_tools import chunks_list

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await chunks_list(pool, doc_id, config=cfg, limit=limit, offset=offset)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit_rows(result, json_out, "chunks")
    return _exit_code(result)


def chunks_list(
    doc_id: str = typer.Argument(..., help="The document ID"),
    limit: int = typer.Option(100, "--limit", min=1, help="How many rows to show"),
    offset: int = typer.Option(0, "--offset", min=0, help="Skip the first N rows"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """List one document's chunks (ordered by chunk index, then ID).

    Exit codes: 0 answered (an empty list is still 0), 1 failed, 3 the
    database is unreachable.
    """
    return _run_sync(lambda: _chunks_list_flow(doc_id, limit, offset, json_output))
