"""Conflict-op CLI commands (Living Corpus W3).

Three commands over the shared handlers, mirroring cli_statements: it
reuses the cli_ingest plumbing and imports corpus_kb.cli nowhere, so
the import in cli.py stays acyclic.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import typer

from corpus_kb.cli.cli_ingest import _load_config, _open_pool, _run_sync

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 3

_OK_STATUSES = ("success", "disabled", "empty", "insufficient_data")

_JSON_OPT = typer.Option(False, "--json", help="Machine-readable JSON output")
_TENANT_OPT = typer.Option("00000000-0000-0000-0000-000000000001", "--tenant-id", help="Tenant id")


def register(app: typer.Typer) -> None:
    """Attach the three conflict commands to the main Typer app."""
    app.command("conflicts-scan")(conflicts_scan)
    app.command("conflicts-list")(conflicts_list)
    app.command("conflicts-resolve")(conflicts_resolve)


def _emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    print(f"conflicts: {result.get('status', 'error')}")
    for key in ("scanned", "flagged", "count", "resolved_verdict"):
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


def conflicts_scan(
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Run the contradiction cascade over live statements."""
    kwargs: dict[str, Any] = {"tenant_id": tenant_id}
    return _run_sync(lambda: _flow("conflicts_tools", "conflicts_scan", kwargs, json_output))


def conflicts_list(
    tenant_id: str = _TENANT_OPT,
    verdict: str = typer.Option(None, "--verdict", help="conflict|revision|uncertain"),
    limit: int = typer.Option(50, "--limit", help="Max rows to return"),
    json_output: bool = _JSON_OPT,
) -> int:
    """List recorded conflict flags newest-first."""
    kwargs = {"tenant_id": tenant_id, "verdict": verdict, "limit": limit}
    return _run_sync(lambda: _flow("conflicts_tools", "conflicts_list", kwargs, json_output))


def conflicts_resolve(
    conflict_id: str = typer.Argument(..., help="The conflict flag id"),
    resolved_verdict: str = typer.Option(..., "--verdict", help="conflict|revision|dismissed"),
    resolved_by: str = typer.Option(..., "--reviewer", help="Who decided"),
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Finalize one conflict flag."""
    kwargs = {
        "tenant_id": tenant_id,
        "conflict_id": conflict_id,
        "resolved_verdict": resolved_verdict,
        "resolved_by": resolved_by,
    }
    return _run_sync(lambda: _flow("conflicts_tools", "conflicts_resolve", kwargs, json_output))
