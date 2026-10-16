"""The ``corpus-kb search`` front door (the core search op's CLI leg).

Top-level command registered on the main app (see ``register``):

  * ``corpus-kb search QUERY`` — hybrid vector + full-text search against
    the CONFIGURED database (no server needed). The command calls the
    SAME handler the MCP + HTTP + socket surfaces call
    (``tools.search_tools:search``), so every surface returns the same
    envelope: ``{"status", "result", "degraded_excluded"}`` (plus
    ``remedy`` only in the ``degraded_empty`` case).

HONEST EXIT CODES (stable for scripting, documented in the command's
help): ``0`` the search answered — including ``degraded_empty``, whose
printed remedy tells the user how to repair the degraded chunks — ``1``
the search failed (error envelope or ``CorpusError``), ``3`` the
database is unreachable (the shared ingest plumbing maps connection
failures there). Every outcome is PRINTED or emitted as ``--json``.
"""

from __future__ import annotations

import json

import typer

# Reuse the ingest front door's shared plumbing (same honest-sync rule);
# cli_ingest imports corpus_kb.cli only lazily, so this import is acyclic.
from corpus_kb.cli_ingest import _load_config, _open_pool, _run_sync

EXIT_OK = 0
EXIT_FAILED = 1

_SNIPPET_LIMIT = 160
_TRUNCATED = "..."


def register(app: typer.Typer) -> None:
    """Attach the search command to the main Typer app."""
    app.command("search")(search)


def _exit_for(result: dict[str, object]) -> int:
    """Map a search envelope to the honest exit code."""
    status = str(result.get("status", "error"))
    return EXIT_OK if status in ("success", "degraded_empty") else EXIT_FAILED


def _hits(result: dict[str, object]) -> list[dict[str, object]]:
    raw = result.get("result", [])
    if not isinstance(raw, list):
        return []
    return [hit for hit in raw if isinstance(hit, dict)]


def _snippet(text: str) -> str:
    """One flattened line of at most 160 characters."""
    flat = " ".join(text.split())
    if len(flat) <= _SNIPPET_LIMIT:
        return flat
    cut = _SNIPPET_LIMIT - len(_TRUNCATED)
    return flat[:cut].rstrip() + _TRUNCATED


def _print_hit(rank: int, hit: dict[str, object]) -> None:
    score = hit.get("score")
    doc_id = hit.get("doc_id")
    print(f"{rank}. [{score}] {doc_id} :: {_snippet(str(hit.get('text', '')))}")


def _emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        # The EXACT handler envelope — no reshaping, no added keys.
        print(json.dumps(result, indent=2, default=str))
        return
    status = str(result.get("status", "error"))
    if status not in ("success", "degraded_empty"):
        print(f"search: {status}")
        return
    hits = _hits(result)
    for rank, hit in enumerate(hits, start=1):
        _print_hit(rank, hit)
    if not hits:
        print("no results")
    if status == "degraded_empty":
        print(f"search: {status}")
        remedy = result.get("remedy")
        if remedy:
            print(f"  remedy: {remedy}")


async def _search_flow(
    query: str, k: int | None, source_type: str | None, json_output: bool
) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.search_tools import search as search_tool

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await search_tool(pool, query, config=cfg, k=k, source_type=source_type)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit(result, json_output)
    return _exit_for(result)


def search(
    query: str = typer.Argument(..., help="What to look for in the knowledge base"),
    k: int | None = typer.Option(
        None,
        "--k",
        min=1,
        help="How many hits to return (default: the reranking.return_top_k setting)",
    ),
    source_type: str | None = typer.Option(
        None,
        "--source-type",
        help="Filter by document type (for example text or transcript)",
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Search the knowledge base (hybrid vector + full-text).

    Talks to the configured database directly; no server needed. Exit
    codes: 0 answered (a degraded empty answer is still 0; the remedy
    prints), 1 the search failed, 3 the database is unreachable.
    """
    return _run_sync(lambda: _search_flow(query, k, source_type, json_output))
