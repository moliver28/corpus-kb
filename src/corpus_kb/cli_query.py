"""The ``corpus-kb query`` / ``verify-answer`` front door (surface parity).

Top-level commands registered on the main app (see ``register``):

  * ``corpus-kb query QUESTION``       — route one question to the vector,
    SQL, or graph backend (the same handler POST /api/query calls).
  * ``corpus-kb verify-answer TEXT``   — check each claim of an answer
    against the cited chunks (the same handler POST /api/verify calls).

Each command calls the SAME shared tool function the MCP + HTTP + socket
surfaces call (``tools.query_tools``), so every surface returns the same
envelope: ``{"status": "success", "result": ...}``.

HONEST EXIT CODES (stable for scripting, documented in each command's
help): ``0`` answered — an honest abstention (an empty answer, nothing
cited) is still 0 — ``1`` failed, ``3`` the database is unreachable (the
shared ingest plumbing maps connection failures there). Every outcome is
PRINTED or emitted as ``--json``.
"""

from __future__ import annotations

import json

import typer

# Reuse the ingest front door's shared plumbing (same honest-sync rule);
# cli_ingest imports corpus_kb.cli only lazily, so this import is acyclic.
from corpus_kb.cli_ingest import _load_config, _open_pool, _run_sync

EXIT_OK = 0
EXIT_FAILED = 1

_ROUTED_ROUTE = "route"
_ROUTED_RESULTS = "results"


def register(app: typer.Typer) -> None:
    """Attach the two query commands to the main Typer app."""
    app.command("query")(query)
    app.command("verify-answer")(verify_answer)


def _emit_query(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        # The EXACT tool envelope — no reshaping, no added keys.
        print(json.dumps(result, indent=2, default=str))
        return
    if result.get("status") != "success":
        print(f"query: {result.get('status', 'error')}")
        return
    payload = result.get("result")
    body = payload if isinstance(payload, dict) else {}
    print(f"route: {body.get(_ROUTED_ROUTE, 'unknown')}")
    hits = body.get(_ROUTED_RESULTS)
    rows = [row for row in hits if isinstance(row, dict)] if isinstance(hits, list) else []
    for rank, row in enumerate(rows, start=1):
        text = " ".join(str(row.get("text", "")).split())
        print(f"{rank}. {row.get('doc_id', '?')} :: {text[:160]}")
    if not rows:
        print("no results")


def _emit_verify(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    if result.get("status") != "success":
        print(f"verify-answer: {result.get('status', 'error')}")
        return
    payload = result.get("result")
    body = payload if isinstance(payload, dict) else {}
    print(f"groundedness: {body.get('groundedness')} abstained: {body.get('abstained')}")
    verdicts = body.get("verdicts")
    rows = [row for row in verdicts if isinstance(row, dict)] if isinstance(verdicts, list) else []
    for rank, row in enumerate(rows, start=1):
        print(f"{rank}. {row.get('verdict', '?'):8s} claim: {row.get('claim', '')[:140]}")


async def _query_flow(question: str, k: int | None, json_output: bool) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.query_tools import query

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await query(question, config=cfg, k=k)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit_query(result, json_output)
    return EXIT_OK if result.get("status") == "success" else EXIT_FAILED


async def _verify_flow(answer: str, chunk_ids: list[str], json_output: bool) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.query_tools import verify_answer

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await verify_answer(pool, answer, config=cfg, chunk_ids=chunk_ids)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit_verify(result, json_output)
    return EXIT_OK if result.get("status") == "success" else EXIT_FAILED


def query(
    question: str = typer.Argument(..., help="The question to route and answer"),
    k: int | None = typer.Option(None, "--k", min=1, help="How many hits per backend"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Ask one question; the router picks the vector, SQL, or graph backend.

    Exit codes: 0 answered (an honest empty answer is still 0), 1 failed,
    3 the database is unreachable.
    """
    return _run_sync(lambda: _query_flow(question, k, json_output))


# Module-level singleton (B008): mutable-annotated typer defaults must not
# call typer.Option inline.
_CHUNK_ID_OPTION: typer.Option = typer.Option(
    None, "--chunk-id", help="A cited chunk UUID (repeatable)"
)


def verify_answer(
    answer: str = typer.Argument(..., help="The answer text to verify"),
    chunk_ids: list[str] | None = _CHUNK_ID_OPTION,
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Check each claim of an answer against the cited chunks.

    Exit codes: 0 verified (an abstention is still 0 and is reported),
    1 failed, 3 the database is unreachable.
    """
    return _run_sync(lambda: _verify_flow(answer, list(chunk_ids or []), json_output))
