"""Shared plumbing for the catalogue-op CLI command modules.

``flow`` and ``exit_for``, the exit-code constants, and the shared option
singletons were copied module to module (Living Corpus W2-W5, AMR wave 2);
they live here once. The config and pool seam stays
``corpus_kb.cli.cli_ingest`` (the owning module): tests patch
``_load_config`` and ``_open_pool`` there, one canonical seam.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import typer

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 3

# Honest no-op statuses: the command answered, there is nothing to fix.
_OK_STATUSES = ("success", "disabled", "empty", "insufficient_data")

_JSON_OPT = typer.Option(False, "--json", help="Machine-readable JSON output")
_TENANT_OPT = typer.Option("00000000-0000-0000-0000-000000000001", "--tenant-id", help="Tenant id")


def exit_for(result: dict[str, object], ok_statuses: tuple[str, ...] = _OK_STATUSES) -> int:
    """Map one op envelope to the honest exit code."""
    status = str(result.get("status", "error"))
    if status in ok_statuses:
        return EXIT_OK
    if status == "unavailable":
        return EXIT_CONFIG
    return EXIT_FAILED


async def flow(
    module_name: str,
    op_name: str,
    kwargs: dict[str, Any],
    json_output: bool,
    emit: Callable[[dict[str, object], bool], None],
    ok_statuses: tuple[str, ...] = _OK_STATUSES,
) -> int:
    """One command flow: load config, open the pool, call, emit, map.

    The caller owns ``emit`` (each op prints its own summary lines); this
    function owns the pool lifecycle and the error mapping. The pool
    closes on every path (success, typed error, unexpected raise).
    """
    from corpus_kb.cli import cli_ingest
    from corpus_kb.errors import CorpusError

    module = __import__(f"corpus_kb.tools.{module_name}", fromlist=[op_name])
    handler: Callable[..., Awaitable[dict[str, object]]] = getattr(module, op_name)
    cfg = cli_ingest._load_config()
    pool = await cli_ingest._open_pool(cfg)
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
    emit(result, json_output)
    return exit_for(result, ok_statuses)
