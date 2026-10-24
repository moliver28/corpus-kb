"""Belief-op CLI commands (Living Corpus W4).

Four commands over the shared handlers, mirroring cli_conflicts: it
reuses the cli_ingest plumbing and imports corpus_kb.cli nowhere, so
the import in cli.py stays acyclic.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import typer

from corpus_kb.cli_ingest import _load_config, _open_pool, _run_sync

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 3

_OK_STATUSES = ("success", "disabled", "empty", "insufficient_data")

_JSON_OPT = typer.Option(False, "--json", help="Machine-readable JSON output")
_TENANT_OPT = typer.Option("00000000-0000-0000-0000-000000000001", "--tenant-id", help="Tenant id")


def register(app: typer.Typer) -> None:
    """Attach the four belief commands to the main Typer app."""
    app.command("beliefs-current")(beliefs_current)
    app.command("beliefs-timeline")(beliefs_timeline)
    app.command("beliefs-predict")(beliefs_predict)
    app.command("beliefs-compare")(beliefs_compare)


def _emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    print(f"beliefs: {result.get('status', 'error')}")
    for key in ("count", "subject", "probability", "interval_low", "interval_high"):
        if key in result:
            print(f"  {key}: {result[key]}")


async def _flow(module_name: str, op_name: str, kwargs: dict[str, Any], json_output: bool) -> int:
    """One command flow: load config, open the pool, call, emit, map."""
    from corpus_kb.errors import CorpusError

    module = __import__(f"corpus_kb.tools.{module_name}", fromlist=[op_name])
    handler: Callable[..., Awaitable[dict[str, object]]] = getattr(module, op_name)
    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await handler(pool, cfg, **kwargs)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        remediation = getattr(exc, "remediation", None)
        if remediation:
            print(f"  {remediation}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit(result, json_output)
    return _exit_for(result)


def _exit_for(result: dict[str, object]) -> int:
    status = str(result.get("status", "error"))
    if status in _OK_STATUSES:
        return EXIT_OK
    if status == "unavailable":
        return EXIT_CONFIG
    return EXIT_FAILED


def beliefs_current(
    tenant_id: str = _TENANT_OPT,
    subject: str = typer.Option(None, "--subject", help="Filter by subject"),
    limit: int = typer.Option(50, "--limit", help="Max rows"),
    rebuild: bool = typer.Option(True, "--rebuild/--no-rebuild", help="Recompute first"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Current belief posteriors."""
    kwargs: dict[str, Any] = {
        "tenant_id": tenant_id,
        "subject": subject,
        "limit": limit,
        "rebuild": rebuild,
    }
    return _run_sync(lambda: _flow("beliefs_tools", "beliefs_current", kwargs, json_output))


def beliefs_timeline(
    subject: str = typer.Option(..., "--subject", help="Belief subject"),
    relation: str = typer.Option(..., "--relation", help="Belief relation (template id)"),
    object: str = typer.Option(..., "--object", help="Belief object"),
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Era history for one belief."""
    kwargs = {
        "tenant_id": tenant_id,
        "subject": subject,
        "relation": relation,
        "object": object,
    }
    return _run_sync(lambda: _flow("beliefs_tools", "beliefs_timeline", kwargs, json_output))


def beliefs_predict(
    subject: str = typer.Option(..., "--subject", help="Belief subject"),
    relation: str = typer.Option(..., "--relation", help="Belief relation (template id)"),
    object: str = typer.Option(..., "--object", help="Belief object"),
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Posterior predictive for one belief."""
    kwargs = {
        "tenant_id": tenant_id,
        "subject": subject,
        "relation": relation,
        "object": object,
    }
    return _run_sync(lambda: _flow("beliefs_tools", "beliefs_predict", kwargs, json_output))


def beliefs_compare(
    subject: str = typer.Option(..., "--subject", help="Belief subject"),
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """One subject's beliefs across objects."""
    kwargs = {"tenant_id": tenant_id, "subject": subject}
    return _run_sync(lambda: _flow("beliefs_tools", "beliefs_compare", kwargs, json_output))
