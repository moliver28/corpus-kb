"""The ``corpus-kb ask`` front door (UX program Wave 1).

Top-level command registered on the main app (see ``register``):

  * ``corpus-kb ask QUESTION`` — retrieve evidence, generate a cited
    answer, verify its claims (the same shared tool POST /api/ask and
    the socket ``ask`` method call: ``tools.ask_tools.ask``).

Every surface returns the same envelope:
``{"status", "result", "degraded_excluded"}``.

HONEST EXIT CODES (stable for scripting, documented in the command's
help): ``0`` answered — an honest ``empty`` (no evidence) or ``degraded``
(generation down, evidence only) is still 0 with the note printed —
``1`` failed, ``3`` the database is unreachable (the shared ingest
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

# Statuses that ARE the operation answering (honest outcomes, not errors).
_ANSWER_STATUSES = frozenset({"success", "empty", "degraded"})


def register(app: typer.Typer) -> None:
    """Attach the ask command to the main Typer app."""
    app.command("ask")(ask)


def _exit_for(result: dict[str, object]) -> int:
    """Map an ask envelope to the honest exit code."""
    status = str(result.get("status", "error"))
    return EXIT_OK if status in _ANSWER_STATUSES else EXIT_FAILED


def _emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        # The EXACT tool envelope — no reshaping, no added keys.
        print(json.dumps(result, indent=2, default=str))
        return
    status = str(result.get("status", "error"))
    if status not in _ANSWER_STATUSES:
        print(f"ask: {status}")
        return
    payload = result.get("result")
    body = payload if isinstance(payload, dict) else {}
    note = body.get("note")
    if note:
        print(f"note: {note}")
    raw = body.get("sentences")
    rows = [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []
    for row in rows:
        cites = ", ".join(str(c) for c in row.get("citations", []))
        print(f"{row.get('text', '')} [{cites}]")
    if not rows:
        print("no citable answer")
    raw_sources = body.get("sources")
    sources = (
        [src for src in raw_sources if isinstance(src, dict)]
        if isinstance(raw_sources, list)
        else []
    )
    if sources:
        print("sources:")
        for src in sources:
            print(f"  [{src.get('n')}] {src.get('source')} (score {src.get('score')})")
    verify = body.get("verify")
    if isinstance(verify, dict) and not verify.get("abstained", True):
        print(f"groundedness: {verify.get('groundedness')}")


async def _ask_flow(question: str, k: int | None, json_output: bool) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.ask_tools import ask as ask_tool

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await ask_tool(question, config=cfg, k=k, pg_pool=pool)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit(result, json_output)
    return _exit_for(result)


def ask(
    question: str = typer.Argument(..., help="The question to answer from the knowledge base"),
    k: int | None = typer.Option(None, "--k", min=1, help="How many evidence chunks to retrieve"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Answer a question with cited sentences from retrieved evidence.

    Exit codes: 0 answered (an honest empty or degraded answer is still
    0; the note prints), 1 failed, 3 the database is unreachable.
    """
    return _run_sync(lambda: _ask_flow(question, k, json_output))
