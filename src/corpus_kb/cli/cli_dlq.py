"""DLQ admin: list and resolve projection dead-letter entries.

The DLQ records events a projection could not apply (projection_dlq rows,
resolved=false). Resolution is two steps: fix the blocking read-model gap
(often re-running the catchup after the referenced rows exist), then mark
the entry resolved. These commands make those steps one command each, so
long-running operators are not hand-writing tenant-scoped deletes.
"""

from __future__ import annotations

import json
from typing import Awaitable, Callable
from uuid import UUID

import typer

from corpus_kb.cli._plumbing import EXIT_FAILED, EXIT_OK
from corpus_kb.cli.cli_ingest import _load_config, _open_pool, _run_sync

PROJECTIONS = ("DocumentsProjection", "EmbedChunksProjection", "ResearchProjection")


def _tenant() -> UUID:
    from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID

    return UUID(DEFAULT_TENANT_ID)


async def _list_flow(json_output: bool) -> int:
    from corpus_kb.projections.catchup import advance_all_projections
    from corpus_kb.projections.dlq import DLQHandler

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        handler = DLQHandler(pool)
        entries: list[dict[str, object]] = []
        for name in PROJECTIONS:
            entries.extend(await handler.list_failures(name, _tenant()))
        # A catchup after the operator's fix re-projects blocked events.
        if entries:
            await advance_all_projections(pool, _tenant())
            fresh: list[dict[str, object]] = []
            for name in PROJECTIONS:
                fresh.extend(await handler.list_failures(name, _tenant()))
            entries = fresh
        if json_output:
            print(json.dumps({"entries": entries, "count": len(entries)}, indent=2, default=str))
            return EXIT_OK
        if not entries:
            typer.echo("dlq: no unresolved entries")
            return EXIT_OK
        for row in entries:
            typer.echo(
                f"{row['dlq_id']}  {row['projection_name']}  "
                f"{row['event_type']}  retries={row['retry_count']}  "
                f"event={str(row['event_id'])[:8]}...  "
                f"err={str(row['error_message'])[:70]}"
            )
        return EXIT_FAILED
    finally:
        await pool.close()


async def _retry_flow(dlq_id: str, json_output: bool) -> int:
    from corpus_kb.projections.catchup import advance_all_projections
    from corpus_kb.projections.dlq import DLQHandler

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        handler = DLQHandler(pool)
        await advance_all_projections(pool, _tenant())
        await handler.mark_resolved(UUID(dlq_id), _tenant())
    except Exception as exc:
        typer.echo(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    if json_output:
        print(json.dumps({"status": "success", "resolved": dlq_id}))
        return EXIT_OK
    typer.echo(f"dlq: resolved {dlq_id}")
    return EXIT_OK


def dlq_list(
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON."),
) -> int:
    """List unresolved DLQ entries across every projection."""
    return _run_sync(lambda: _list_flow(json_output))


def dlq_retry(
    dlq_id: str = typer.Argument(..., help="The dlq_id from corpus-kb dlq-list."),
    json_output: bool = typer.Option(False, "--json"),
) -> int:
    """Re-advance every projection, then mark one DLQ entry resolved."""
    return _run_sync(lambda: _retry_flow(dlq_id, json_output))


def register(app: typer.Typer) -> None:
    app.command("dlq-list")(dlq_list)
    app.command("dlq-retry")(dlq_retry)
