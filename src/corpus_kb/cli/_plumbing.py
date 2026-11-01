"""Shared plumbing for the catalogue-op CLI command modules.

``flow`` and ``exit_for``, the exit-code constants, and the shared option
singletons were copied module to module (Living Corpus W2-W5, AMR wave 2);
they live here once. The config and pool seam stays
``corpus_kb.cli.cli_ingest`` (the owning module): tests patch
``_load_config`` and ``_open_pool`` there, one canonical seam.
"""

from __future__ import annotations

import enum
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

import typer

from corpus_kb.contracts.model import DEFAULT_TENANT_ID

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 3

# Honest no-op statuses: the command answered, there is nothing to fix.
_OK_STATUSES = ("success", "disabled", "empty", "insufficient_data")

_JSON_OPT = typer.Option(False, "--json", help="Machine-readable JSON output")
_TENANT_OPT = typer.Option(DEFAULT_TENANT_ID, "--tenant-id", help="Tenant id")

# The ingest validation gate — the same values the MCP, HTTP, and socket
# surfaces accept on the ingest operations (tools.ingest_common).
_VALIDATE_OPT = typer.Option(
    None,
    "--validate",
    help="Validation gate for this run. Omit it to use the active profile value.",
)

# The research path has no profile lookup: its handler default is off.
_RESEARCH_VALIDATE_OPT = typer.Option(
    None,
    "--validate",
    help="Validation gate for this run. Omit it to use the handler default (off).",
)


class ValidateMode(enum.StrEnum):
    """The four documented caller modes the CLI exposes for ``--validate``.

    Other surfaces pass raw strings; ``resolve_mode`` also accepts
    ``transcript_meta`` there. The CLI offers only these four
    (docs/research/HARNESS-PLAYBOOKS.md).
    """

    OFF = "off"
    ADVISORY = "advisory"
    ENFORCE = "enforce"
    CALIBRATE = "calibrate"


class _ValidateKwarg(TypedDict, total=False):
    """The one handler keyword ``--validate`` may add."""

    validate: str


def validate_kwargs(validate: ValidateMode | None) -> _ValidateKwarg:
    """The ``validate`` handler keyword, or nothing when the flag is omitted.

    Omission keeps the surface default: the main pipeline uses the active
    profile's ``validate`` value; the research path uses ``off``.
    """
    return {} if validate is None else {"validate": validate.value}


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
