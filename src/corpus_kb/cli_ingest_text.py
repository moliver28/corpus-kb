"""The ``corpus-kb ingest-text`` front door (the raw-text op's CLI leg).

Top-level command registered on the main app (see ``register``):

  * ``corpus-kb ingest-text TEXT`` — ingest raw text with a source label
    and a document type. ``-`` reads the text from stdin. The command
    calls the SAME handler the MCP + HTTP + socket surfaces call
    (``tools.ingest_common:ingest_text``), so every surface returns the
    same ingest envelope.

HONEST EXIT CODES (stable for scripting, documented in the command's
help): ``0`` the text was ingested, ``1`` the ingest failed, ``3`` the
database is unreachable (the shared ingest plumbing maps connection
failures there). Every outcome is PRINTED or emitted as ``--json``.
"""

from __future__ import annotations

import json
import sys

import typer

# Reuse the ingest front door's shared plumbing (same honest-sync rule);
# cli_ingest imports corpus_kb.cli only lazily, so this import is acyclic.
from corpus_kb.cli_ingest import _load_config, _open_pool, _run_sync

EXIT_OK = 0
EXIT_FAILED = 1


def register(app: typer.Typer) -> None:
    """Attach the ingest-text command to the main Typer app."""
    app.command("ingest-text")(ingest_text)


def _read_text(text: str) -> str:
    """The argument text, or stdin when the argument is ``-``."""
    return sys.stdin.read() if text == "-" else text


async def _ingest_text_flow(text: str, source: str, source_type: str, json_output: bool) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.ingest_common import ingest_text

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await ingest_text(
            text,
            pool,
            config=cfg,
            source=source,
            source_type=source_type,
        )
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    if json_output:
        # The EXACT pipeline envelope — no reshaping, no added keys.
        print(json.dumps(result, indent=2, default=str))
        return EXIT_OK if result.get("status") == "success" else EXIT_FAILED
    status = str(result.get("status", "error"))
    if status != "success":
        print(f"ingest-text: {status}")
        return EXIT_FAILED
    print(
        f"ingested {result.get('document_id')}: {result.get('chunk_count')} chunks, "
        f"{result.get('entity_count')} entities"
    )
    return EXIT_OK


def ingest_text(
    text: str = typer.Argument(..., help="The raw text to ingest ('-' reads stdin)"),
    source: str = typer.Option("raw_text", "--source", help="A source label for the document"),
    source_type: str = typer.Option("text", "--source-type", help="The document type"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Ingest raw text with a source label and a document type.

    Exit codes: 0 ingested, 1 failed, 3 the database is unreachable.
    """
    return _run_sync(lambda: _ingest_text_flow(_read_text(text), source, source_type, json_output))
