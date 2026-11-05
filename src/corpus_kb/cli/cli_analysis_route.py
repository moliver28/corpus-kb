"""The question-router front door (analysis routing program, wave 3).

Top-level command registered on the main app (see ``register``):
``corpus-kb analysis-route "QUESTION"``, one handler — the same shared
function the MCP + HTTP + socket surfaces call.

HONEST EXIT CODES (stable for scripting): ``0`` the command answered —
a routed answer is an answer in every status (resolved, ambiguous,
degraded) — ``1`` the command failed or the request was malformed, ``3``
configuration state. Mirrors ``cli_analysis_methods``. All help text is
plain English (ASD-STE100).
"""

from __future__ import annotations

import json
from typing import Any

import typer

from corpus_kb.cli._plumbing import _JSON_OPT, flow

# Reuse the ingest front door's shared plumbing (same honest-sync rule);
# cli_ingest imports corpus_kb.cli only lazily, so this import is acyclic.
from corpus_kb.cli.cli_ingest import _run_sync

# The three envelope statuses are all answers: a degraded or ambiguous
# envelope tells the truth about the question, so the command succeeded.
_ROUTE_OK_STATUSES = ("resolved", "ambiguous", "degraded")


def register(app: typer.Typer) -> None:
    """Attach the routing command to the main Typer app."""
    app.command("analysis-route")(analysis_route)


def _route_emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    print(f"analysis-route: {result.get('status', 'error')}")
    ranked = result.get("ranked")
    if isinstance(ranked, list) and ranked:
        top = ranked[0]
        if isinstance(top, dict):
            print(f"  method: {top.get('method_id')} ({top.get('name')})")
            why = top.get("why")
            if why:
                print(f"  why: {why}")


def analysis_route(
    question: str = typer.Argument(..., help="The question, in plain words"),
    method_id: str = typer.Option(
        None, "--method-id", help="Expert override: force this method id"
    ),
    json_output: bool = _JSON_OPT,
) -> int:
    """Route the question to the right analysis method."""
    kwargs: dict[str, Any] = {"question": question, "method_id": method_id}
    return _run_sync(
        lambda: flow(
            "analysis_route_tools",
            "analysis_route",
            kwargs,
            json_output,
            _route_emit,
            ok_statuses=_ROUTE_OK_STATUSES,
        )
    )
