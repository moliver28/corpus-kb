"""Shared CLI plumbing (zero-drift Wave 3B split).

Everything the CLI command modules need that is not a command itself:
the asyncio runner + error mapper (``_run_async``/``_cli_error_exit``),
the cycle approval boundary (``_cli_approve``), the canonical tenant id
(imported from ``corpus_kb.contracts.model``), UUID coercions, the
engine-aware store bootstrap every coroutine shares (cli_store), and payload shaping
(``cast_dict``/``cast_list``) for the printers.

Import-light on purpose (typer + contracts only): heavy imports (asyncpg,
config, the research stack) stay inside the functions, matching the
lazy-import discipline cli.py documents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import typer

from corpus_kb.contracts.model import DEFAULT_TENANT_ID

if TYPE_CHECKING:
    pass

# The canonical placeholder tenant (RLS default). contracts.model is the
# single source; cli.py keeps a ``_DEFAULT_TENANT`` alias of this name for
# backward-compatible imports.
DEFAULT_TENANT = DEFAULT_TENANT_ID


def _tenant():
    from uuid import UUID

    return UUID(DEFAULT_TENANT)


def _pid(project_id: str | None):
    from uuid import UUID

    return UUID(project_id) if project_id else None


async def _cli_approve(stage: str) -> bool:
    """The on-mode CLI approval boundary: one blocking y/N prompt.

    ASYNC by contract: cycle_render.approval awaits its ``approve`` argument,
    so a sync prompt here crashed with "object bool can't be used in 'await'
    expression" on BOTH approve and deny (review F-2).
    """
    from corpus_kb.research import guide_copy

    print(guide_copy.CYCLE_APPROVAL_STAGE_HEADER.format(stage=stage))
    try:
        reply = input(guide_copy.CYCLE_APPROVAL_PROMPT)
    except (EOFError, OSError):
        # Closed stdin (scripts, CI, detached editors) must not crash the
        # boundary with a traceback: no answer is a denial, same as "n".
        print(guide_copy.CYCLE_APPROVAL_NO_STDIN)
        return False
    return reply.strip().lower() in ("y", "yes")


async def _cli_approve_yes(stage: str) -> bool:
    """Unattended on-mode approval: approve every gate, never prompt.

    The harness drives the cycle with closed stdin (the ``--approve``
    flag selects this callback), so a headless run cannot hang on input.
    The stage argument matches the shared approval-callback shape.
    """
    return True


async def _cli_approve_no(stage: str) -> bool:
    """Unattended on-mode denial: deny every gate, never prompt.

    The ``--deny`` flag selects this callback. A denial stops the cycle
    with the documented approval-denied exit code (20).
    """
    return False


def _cli_error_exit(exc: BaseException) -> int | None:
    """Map an EXPECTED CLI failure to an exit code; None means re-raise.

    Covers the error classes a user can cause from the keyboard (unknown
    gate/proposal id, bad UUID, permission denied, system libpq too old);
    unexpected bugs keep their traceback (review F-8). eventsourcing
    re-wraps the driver error (``raise ProgrammingError(str(e)) from e``),
    so the caught exception often carries no sqlstate itself — the matcher
    walks the __cause__/__context__ chain and classifies the first
    recognized database error (F-3 residual).
    """
    import asyncpg
    import psycopg
    from eventsourcing.persistence import PersistenceError

    from corpus_kb.errors import CorpusError
    from corpus_kb.research import guide_copy

    if isinstance(exc, CorpusError):
        # The typed taxonomy (duckdb connect failures land here through
        # map_connect_error): one line, the remediation, the mapped exit.
        print(f"ERROR: {exc}")
        remediation = getattr(exc, "remediation", "")
        if remediation:
            print(remediation)
        return int(exc.cli_exit)

    if isinstance(exc, ValueError):
        # unknown gate name, unknown proposal id, malformed UUID, bad decision
        print(f"ERROR: {exc}")
        return 1

    def denied(err: BaseException) -> bool:
        return getattr(err, "sqlstate", "") == "42501" or "permission denied" in str(err)

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, asyncpg.PostgresError):
            if denied(current):
                print(f"ERROR: Postgres permission denied: {current}")
                print(guide_copy.DB_PERMISSION_DENIED_HINT)
            else:
                print(f"ERROR: Postgres error: {current}")
            return 1
        if isinstance(current, psycopg.Error):
            if isinstance(current, psycopg.NotSupportedError):
                print(f"ERROR: {current}")
                print(guide_copy.LIBPQ_TOO_OLD_HINT)
            elif denied(current):
                print(f"ERROR: Postgres permission denied: {current}")
                print(guide_copy.DB_PERMISSION_DENIED_HINT)
            else:
                print(f"ERROR: database error: {current}")
            return 1
        if isinstance(current, PersistenceError) and denied(current):
            # eventsourcing wrap whose driver error is not on the chain:
            # the wrap's own message still names the denial
            print(f"ERROR: Postgres permission denied: {current}")
            print(guide_copy.DB_PERMISSION_DENIED_HINT)
            return 1
        current = current.__cause__ or current.__context__
    return None


def _run_async(coro: object) -> int:
    """asyncio.run with expected-error presentation and honest exit codes.

    typer ignores a plain int return on failure paths, so BOTH failure shapes
    must raise: mapped expected errors (clean message + hint) and non-zero
    status codes from the coroutine (e.g. 2 = stage unavailable) exit with
    their real code instead of a silent 0.
    """
    import asyncio
    from collections.abc import Coroutine
    from typing import Any

    try:
        code = asyncio.run(cast("Coroutine[Any, Any, int]", coro))
    except Exception as exc:
        mapped = _cli_error_exit(exc)
        if mapped is None:
            raise
        raise typer.Exit(code=mapped) from None
    if code:
        raise typer.Exit(code=code)
    return code


def cast_dict(payload: object) -> dict[str, object]:
    if isinstance(payload, dict):
        return payload
    return {}


def cast_list(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    return []
