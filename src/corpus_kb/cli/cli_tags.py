"""The ``corpus-kb`` tags/metadata read front door (issue #182).

Top-level commands registered on the main app (see ``register``):

  * ``corpus-kb get-document-tags ID`` — one document's tags.
  * ``corpus-kb get-metadata``         — stored metadata rows, filtered
    by ``--key`` and/or ``--doc-id``.

Each command calls the SAME shared tool function the MCP + HTTP + socket
surfaces call (``tools.tag_tools``), so every surface returns the same
envelope: ``{"status", "result"}``.

HONEST EXIT CODES (stable for scripting, documented in each command's
help): ``0`` the read answered (an empty list is still 0), ``1`` the
read failed, ``3`` the database is unreachable (the shared ingest
plumbing maps connection failures there). Every outcome is PRINTED or
emitted as ``--json``.
"""

from __future__ import annotations

import json

import typer

# Reuse the ingest front door's shared plumbing (same honest-sync rule);
# cli_ingest imports corpus_kb.cli only lazily, so this import is acyclic.
from corpus_kb.cli._plumbing import EXIT_FAILED, EXIT_OK
from corpus_kb.cli.cli_ingest import _load_config, _open_pool, _run_sync


def register(app: typer.Typer) -> None:
    """Attach the two tags/metadata read commands to the main Typer app."""
    app.command("get-document-tags")(get_document_tags)
    app.command("get-metadata")(get_metadata)


def _emit_rows(result: dict[str, object], json_output: bool, label: str) -> None:
    """Print the plain result rows, or the exact envelope with --json."""
    if json_output:
        # The EXACT tool envelope — no reshaping, no added keys.
        print(json.dumps(result, indent=2, default=str))
        return
    status = str(result.get("status", "error"))
    if status != "success":
        print(f"{label}: {status}")
        return
    rows = result.get("result")
    dicts = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    for row in dicts:
        print(json.dumps(row, default=str))
    if not dicts:
        print("no results")


async def _get_document_tags_flow(doc_id: str, json_output: bool) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.tag_tools import get_document_tags as tags_tool

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await tags_tool(pool, doc_id)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit_rows(result, json_output, label="get-document-tags")
    return EXIT_OK if result.get("status") == "success" else EXIT_FAILED


async def _get_metadata_flow(key: str | None, doc_id: str | None, json_output: bool) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.tag_tools import get_metadata as metadata_tool

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await metadata_tool(pool, key=key, doc_id=doc_id)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit_rows(result, json_output, label="get-metadata")
    return EXIT_OK if result.get("status") == "success" else EXIT_FAILED


def get_document_tags(
    doc_id: str = typer.Argument(..., help="The document UUID to read tags for"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """List one document's tags (name, color, description).

    Talks to the configured database directly; no server needed. Exit
    codes: 0 answered (an empty list is still 0), 1 failed, 3 the
    database is unreachable.
    """
    return _run_sync(lambda: _get_document_tags_flow(doc_id, json_output))


def get_metadata(
    key: str | None = typer.Option(None, "--key", help="Only rows with this metadata key"),
    doc_id: str | None = typer.Option(None, "--doc-id", help="Only rows for this document UUID"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Read stored metadata rows (key, value, doc_id).

    Talks to the configured database directly; no server needed. Exit
    codes: 0 answered (an empty list is still 0), 1 failed, 3 the
    database is unreachable.
    """
    return _run_sync(lambda: _get_metadata_flow(key, doc_id, json_output))
