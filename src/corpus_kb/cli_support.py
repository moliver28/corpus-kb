"""Shared CLI plumbing (zero-drift Wave 3B split).

Everything the CLI command modules need that is not a command itself:
the asyncio runner + error mapper (``_run_async``/``_cli_error_exit``),
the cycle approval boundary (``_cli_approve``), the canonical tenant id
(imported from ``corpus_kb.contracts.model``), UUID coercions, the
``wired_pool`` bootstrap every coroutine shares, and payload shaping
(``cast_dict``/``cast_list``) for the printers.

Import-light on purpose (typer + contracts only): heavy imports (asyncpg,
config, the research stack) stay inside the functions, matching the
lazy-import discipline cli.py documents.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, NamedTuple, cast

import typer

from corpus_kb.contracts.model import DEFAULT_TENANT_ID

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import asyncpg

    from corpus_kb.projections.research._embed import ResearchEmbedder

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

    from corpus_kb.research import guide_copy

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


class Wired(NamedTuple):
    """What one CLI coroutine gets from the wired bootstrap."""

    pool: asyncpg.Pool
    cfg: dict[str, object]
    conn_str: str
    embedder: ResearchEmbedder | None


@asynccontextmanager
async def wired_pool(
    *, embedder: bool = False, cfg: dict[str, object] | None = None
) -> AsyncIterator[Wired]:
    """One bootstrap for every CLI coroutine: config, pool, (opt) embedder.

    load_config -> connection_string -> asyncpg.create_pool -> optional
    ``ResearchEmbedder(pool, create_embedder(cfg, pool))`` -> yield ->
    pool.close. The research ingest path prints its detection summary
    BEFORE the pool opens, so it passes ``cfg`` pre-loaded; ask and
    uncoded pass ``embedder=True``. Every other coroutine uses the
    defaults. The pool closes on exit, success or error.
    """
    import asyncpg

    from corpus_kb.config import load_config

    if cfg is None:
        cfg = load_config()
    db = cast(dict[str, object], cfg.get("database") or {})
    conn_str = str(db.get("connection_string", ""))
    pool = await asyncpg.create_pool(conn_str)
    try:
        research_embedder: ResearchEmbedder | None = None
        if embedder:
            from corpus_kb.projections.research._embed import ResearchEmbedder
            from corpus_kb.rag import create_embedder

            research_embedder = ResearchEmbedder(pool, create_embedder(cfg, pool))
        yield Wired(pool=pool, cfg=cfg, conn_str=conn_str, embedder=research_embedder)
    finally:
        await pool.close()


def cast_dict(payload: object) -> dict[str, object]:
    if isinstance(payload, dict):
        return payload
    return {}


def cast_list(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    return []
