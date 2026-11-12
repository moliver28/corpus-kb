"""Ledger I/O for the #202 bulk re-embed (split from ``ingest_reingest_run``).

One ``ingest_runs`` row (``source_type='reingest'``) opens before the
first batch, carries the cumulative counts in ``stages`` (updated per
batch, best-effort), and closes after the last one — completed only with
an empty error list. A crashed run leaves the honest 'running' row the
doctor surfaces. The split keeps every module under the 250-line rule.
"""

from __future__ import annotations

import hashlib
import json
import logging

from ..storage.gateway import SqlGateway
from .ingest_sql import IngestSql

logger = logging.getLogger(__name__)

__all__ = ["close_ledger_run", "open_ledger_run", "target_hash", "write_progress"]


def target_hash(model: str, dimensions: int, project_id: str | None) -> str:
    """The ledger start/finish hash: WHICH space this run moves the corpus to."""
    scope = project_id or "corpus"
    return hashlib.sha256(f"reingest:{model}:{dimensions}:{scope}".encode()).hexdigest()


async def open_ledger_run(
    gateway: SqlGateway,
    ingest_sql: IngestSql,
    *,
    tenant_id: str,
    project_id: str | None,
    hash_value: str,
    is_duckdb: bool,
) -> str | None:
    """Open the 'running' ledger row; stamp the project best-effort."""
    async with gateway.transaction(tenant_id) as tx:
        run_id = await tx.fetchval(
            ingest_sql.run_insert_sql,
            tenant_id,
            *(
                (f"reingest:{project_id or 'corpus'}", "reingest", hash_value)
                if is_duckdb
                else ("reingest", hash_value)
            ),
        )
        if run_id and project_id:
            try:
                await tx.execute(ingest_sql.set_run_project_sql, run_id, project_id, tenant_id)
            except Exception as exc:  # best effort: pre-036 ledger has no column
                logger.warning("Could not stamp project on run %s: %s", run_id, exc)
    return str(run_id) if run_id else None


async def write_progress(
    gateway: SqlGateway,
    progress_sql: str,
    run_id: str,
    tenant_id: str,
    chunks_todo: int,
    skipped_current: int,
    embedded: int,
    failed: int,
) -> None:
    """Best-effort per-batch ledger progress (a crash leaves honest counts)."""
    stages = {
        "reingest": {
            "chunks": chunks_todo,
            "embedded": embedded,
            "failed": failed,
            "skipped_current": skipped_current,
        }
    }
    try:
        async with gateway.transaction(tenant_id) as tx:
            await tx.execute(progress_sql, run_id, json.dumps(stages), tenant_id)
    except Exception as exc:
        logger.warning("Could not update reingest progress for run %s: %s", run_id, exc)


async def close_ledger_run(
    gateway: SqlGateway,
    ingest_sql: IngestSql,
    *,
    tenant_id: str,
    run_id: str,
    hash_value: str,
    embedded: int,
    failed: int,
    errors: list[str],
    receipt: dict[str, object],
) -> None:
    """Close the ledger row LAST: completed only with an empty error list."""
    stages = {
        "reingest": {
            "documents": receipt["documents"],
            "chunks": receipt["chunks"],
            "embedded": embedded,
            "failed": failed,
            "skipped_current": receipt["skipped_current"],
            "dry_run": False,
        }
    }
    async with gateway.transaction(tenant_id) as tx:
        await tx.execute(
            ingest_sql.finish_run_sql,
            run_id,
            "completed" if not errors else "failed",
            hash_value if not errors else None,
            json.dumps(stages),
            "; ".join(errors) if errors else None,
            tenant_id,
        )
