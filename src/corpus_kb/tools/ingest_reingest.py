"""Bulk re-embed for embedding-model switches (issue #202, ``ingest-reingest``).

When ``embedding.model`` or ``embedding.dimensions`` changes, the stored
vectors belong to the OLD space. This handler re-embeds every stored chunk
of one scope (the whole tenant corpus, or one project's documents) with
the CURRENT config — WITHOUT re-partitioning or re-extracting: it reads
the chunk rows, rebuilds the exact embed input a fresh ingest would use
(``build_embedding_input``), and writes through the ONE pipeline write
path (``PostgresIngestStore.store_vectors`` — the same upsert, the same
degraded-flag honesty). The ledger-backed run loop lives in
``ingest_reingest_run``; this module owns the probes, the plan, and the
surface contract.

Honesty and safety:

* RESUME: the todo predicate skips rows the target model already wrote
  well (same model, same width, live vector, no degraded flag), so a
  re-run after a crash (or a second owner) continues where the last run
  stopped and counts the rest as ``skipped_current``.
* DRY-RUN: ``dry_run`` answers counts + an estimated token size only —
  no embedder call, no write, no ledger row. The owner sizes the job
  first (the run can be long; off-peak friendly).
* LEDGER: one ``ingest_runs`` row (``source_type='reingest'``) opens
  before the first batch and closes after the last one; its ``stages``
  JSON carries the cumulative counts per batch, so a crashed run leaves
  an honest 'running' row the doctor surfaces (issue #17 spirit: no
  destructive deletes — vectors are upserts keyed by chunk id).
* WIDTH: a configured width the column cannot hold is the typed
  ConfigError (program standard 4), raised BEFORE the ledger row opens.
* NO LOCK: a concurrent re-ingest of new documents is safe (new chunks
  embed with the new model anyway) and two concurrent re-embed runs are
  idempotent (deterministic vectors, keyset batches) — so unlike the
  document pipeline, this path takes no advisory lock.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from ..storage.gateway import SqlGateway
from .ingest_cfg import vector_write_config
from .ingest_common import DEFAULT_TENANT_ID, load_config_or_pass
from .ingest_reingest_run import execute_reingest_run
from .ingest_reingest_sql import (
    WIDTH_PROBE_DUCKDB,
    WIDTH_PROBE_PG,
    duckdb_width_from_rows,
    reingest_sql_for,
    width_config_error,
    width_from_type,
)
from .ingest_sql import COLUMNS_PROBE_DUCKDB, COLUMNS_PROBE_PG, ingest_sql_for
from .ingest_store import PostgresIngestStore, as_gateway, gateway_backend

__all__ = ["DEFAULT_BATCH_SIZE", "ingest_reingest", "reingest_embeddings"]

DEFAULT_BATCH_SIZE = 128


def _receipt(
    config: dict[str, object],
    dry_run: bool,
    documents: int,
    chunks: int,
    skipped_current: int,
) -> dict[str, object]:
    """The stable receipt keys (the catalogue envelope); conditionals join later."""
    model, dimensions, _ = vector_write_config(config)
    return {
        "status": "success",
        "dry_run": dry_run,
        "target_model": model,
        "target_dimensions": dimensions,
        "documents": documents,
        "chunks": chunks,
        "embedded": 0,
        "failed": 0,
        "skipped_current": skipped_current,
    }


def _unavailable_receipt(config: dict[str, object], dry_run: bool) -> dict[str, object]:
    """The pre-migration answer: honest unavailable, never a crash."""
    receipt = _receipt(config, dry_run, 0, 0, 0)
    receipt.update(
        {
            "status": "unavailable",
            "reason": (
                "chunks_vectors columns missing (migration 022 not applied); run corpus-kb setup"
            ),
            "remedy": "run corpus-kb setup",
        }
    )
    return receipt


async def _probe_ready(
    tx: SqlGateway,
    *,
    is_duckdb: bool,
    dimensions: int,
) -> bool:
    """Probe the degraded columns and the vector width; raise on mismatch.

    Returns True when the store is ready. The width probe is advisory on
    an unreadable catalog (it defers to store_vectors' own probe).
    """
    present = await tx.fetchval(
        COLUMNS_PROBE_DUCKDB if is_duckdb else COLUMNS_PROBE_PG,
        *(() if is_duckdb else (["chunks", "chunks_vectors"],)),
    )
    if present != 2:
        return False
    if is_duckdb:
        width = duckdb_width_from_rows(await tx.fetch(WIDTH_PROBE_DUCKDB))
    else:
        raw_type = await tx.fetchval(WIDTH_PROBE_PG)
        width = width_from_type(raw_type if isinstance(raw_type, str) else None)
    if width is not None and width != dimensions:
        raise width_config_error(dimensions, width)
    return True


async def _plan_scope(
    gateway: SqlGateway,
    sql: dict[str, str],
    tenant_id: str,
    project_id: str | None,
    dimensions: int,
    model: str,
) -> dict[str, int]:
    """The scope plan: documents, total, todo, and skipped-current counts."""
    async with gateway.transaction(tenant_id) as tx:
        documents = int(await tx.fetchval(sql["plan_docs"], tenant_id, project_id) or 0)
        total = int(await tx.fetchval(sql["plan_total"], tenant_id, project_id) or 0)
        todo = int(
            await tx.fetchval(sql["plan_todo"], tenant_id, project_id, model, dimensions) or 0
        )
    return {"documents": documents, "total": total, "todo": todo, "skipped": total - todo}


async def reingest_embeddings(
    pg_pool: asyncpg.Pool | SqlGateway,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    project_id: str | None = None,
    dry_run: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, object]:
    """Re-embed one scope's stored chunks with the current embedding config.

    See the module docstring for the resume/dry-run/ledger contract. The
    receipt always carries the stable keys (status, dry_run, target_model,
    target_dimensions, documents, chunks, embedded, failed,
    skipped_current); ``est_tokens`` rides dry-run runs, ``run_id`` the
    ledger-backed runs, ``embedding_mode``/``embedding_note``/``warning``
    the degraded paths, and ``errors`` the first embedder failures.
    """
    config = load_config_or_pass(config)
    gateway = as_gateway(pg_pool)
    pool = pg_pool if isinstance(pg_pool, asyncpg.Pool) else None
    is_duckdb = gateway_backend(gateway) == "duckdb"
    sql = reingest_sql_for("duckdb" if is_duckdb else "postgres")
    ingest_sql = ingest_sql_for("duckdb" if is_duckdb else "postgres")
    model, dimensions, v1024_dim = vector_write_config(config)

    async with gateway.transaction(tenant_id) as tx:
        if not await _probe_ready(tx, is_duckdb=is_duckdb, dimensions=dimensions):
            return _unavailable_receipt(config, dry_run)
        plan = await _plan_scope(gateway, sql, tenant_id, project_id, dimensions, model)
        if dry_run:
            est_chars = int(
                await tx.fetchval(sql["plan_est"], tenant_id, project_id, model, dimensions) or 0
            )
            receipt = _receipt(config, True, plan["documents"], plan["todo"], plan["skipped"])
            receipt["est_tokens"] = est_chars // 4
            return receipt

    receipt = _receipt(config, False, plan["documents"], plan["todo"], plan["skipped"])
    store = PostgresIngestStore(pg_pool, tenant_id)
    return await execute_reingest_run(
        gateway,
        store,
        ingest_sql,
        sql,
        config,
        pool,
        tenant_id=tenant_id,
        project_id=project_id,
        is_duckdb=is_duckdb,
        model=model,
        dimensions=dimensions,
        v1024_dim=v1024_dim,
        batch_size=max(1, int(batch_size)),
        receipt=receipt,
        chunks_todo=plan["todo"],
        skipped_current=plan["skipped"],
    )


async def ingest_reingest(
    pg_pool: asyncpg.Pool | SqlGateway,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    project_id: str | None = None,
    dry_run: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, object]:
    """Bulk re-embed surface (thin wrapper: argument validation only).

    An unparsable ``project_id`` or a ``batch_size`` under 1 is a
    structured error, never a crash.
    """
    config = load_config_or_pass(config)
    if project_id is not None:
        try:
            UUID(project_id)
        except ValueError:
            return {"status": "error", "message": f"project_id is not a UUID: {project_id}"}
    if batch_size < 1:
        return {"status": "error", "message": f"batch_size must be >= 1, got {batch_size}"}
    return await reingest_embeddings(
        pg_pool,
        config,
        tenant_id,
        project_id=project_id,
        dry_run=dry_run,
        batch_size=batch_size,
    )
