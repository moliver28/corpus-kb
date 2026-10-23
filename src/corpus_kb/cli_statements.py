"""Statement-atom CLI commands (Living Corpus W2).

Two commands over the shared handlers, mirroring cli_analysis_methods:
it reuses the cli_ingest plumbing and imports corpus_kb.cli nowhere, so
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

# Honest no-op statuses: the command answered, there is nothing to fix.
_OK_STATUSES = ("success", "disabled", "empty", "insufficient_data")

_JSON_OPT = typer.Option(False, "--json", help="Machine-readable JSON output")
_TENANT_OPT = typer.Option("00000000-0000-0000-0000-000000000001", "--tenant-id", help="Tenant id")


def register(app: typer.Typer) -> None:
    """Attach the two statement commands to the main Typer app."""
    app.command("statements-list")(statements_list)
    app.command("statements-timeline")(statements_timeline)


def _emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    print(f"statements: {result.get('status', 'error')}")
    for row in result.get("statements", [])[:10]:
        if isinstance(row, dict):
            print(f"  [{row.get('template_id')}] {row.get('text')}")


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
    """Map a statement-op envelope to the honest exit code."""
    status = str(result.get("status", "error"))
    if status in _OK_STATUSES:
        return EXIT_OK
    if status == "unavailable":
        return EXIT_CONFIG
    return EXIT_FAILED


def statements_list(
    tenant_id: str = _TENANT_OPT,
    template_id: str = typer.Option(None, "--template", help="Filter by claim template id"),
    document_id: str = typer.Option(None, "--document-id", help="Filter by document"),
    limit: int = typer.Option(50, "--limit", help="Max rows to return"),
    json_output: bool = _JSON_OPT,
) -> int:
    """List statement atoms newest-first."""
    kwargs: dict[str, Any] = {
        "tenant_id": tenant_id,
        "template_id": template_id,
        "document_id": document_id,
        "limit": limit,
    }
    return _run_sync(lambda: _flow("statements_tools", "statements_list", kwargs, json_output))


def statements_timeline(
    tenant_id: str = _TENANT_OPT,
    text_query: str = typer.Option(None, "--text", help="Substring to match"),
    limit: int = typer.Option(200, "--limit", help="Max rows to return"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Chronological statement history — the belief-history view."""
    kwargs = {
        "tenant_id": tenant_id,
        "text_query": text_query,
        "limit": limit,
    }
    return _run_sync(lambda: _flow("statements_tools", "statements_timeline", kwargs, json_output))
