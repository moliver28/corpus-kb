"""The bulk re-embed run loop (issue #202; split from ``ingest_reingest``).

This module owns the batched execution: page deterministic keyset
batches, embed + store each batch through the ONE pipeline write path
(``PostgresIngestStore.store_vectors``), and report honest totals. The
ledger row around the loop lives in ``ingest_reingest_ledger``; the
probes/plan/surface contract lives in ``ingest_reingest``. The split
keeps every module under the 250-line rule.

Honesty carried here: all-zero vectors are counted as FAILURES (and land
flagged degraded via the pipeline write, so the next run's todo predicate
retries them); the first embedder errors ride the receipt (bounded) and
fail the ledger row; a degraded-but-working provider is a ``warning``,
never a silent success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..storage.duckdb_support import Row
from ..storage.gateway import SqlGateway
from ..utils.models import Chunk
from .ingest_cfg import matryoshka_slice
from .ingest_embed import _all_zero, build_embedding_input, embed_chunks
from .ingest_reingest_ledger import close_ledger_run, open_ledger_run, target_hash, write_progress
from .ingest_sql import IngestSql
from .ingest_store import PostgresIngestStore

__all__ = ["BatchOutcome", "execute_reingest_run"]

ZERO_UUID = "00000000-0000-0000-0000-000000000000"
_MAX_ERRORS = 5


@dataclass
class BatchOutcome:
    """One run's totals plus the honesty extras the receipt appends."""

    embedded: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    warning: str | None = None
    meta: dict[str, object] = field(default_factory=dict)


def _chunk_rows(rows: list[Row]) -> list[Chunk]:
    """The scanned rows as minimal Chunks (the exact bytes get re-embedded)."""
    return [
        Chunk(
            chunk_id=row["chunk_id"],
            document_id=row["doc_id"],
            text=row["text"],
            source_type="text",
            context_blurb=row["context_blurb"],
        )
        for row in rows
    ]


async def _embed_and_store_batch(
    store: PostgresIngestStore,
    chunks: list[Chunk],
    config: dict[str, object],
    pool: Any,
    model: str,
    dimensions: int,
    v1024_dim: int | None,
    meta: dict[str, object],
) -> tuple[int, int, str | None]:
    """Embed one batch, store it, and report (embedded, failed, embed_err).

    The write is the pipeline's own ``store_vectors``: vectors are
    upserts keyed by chunk id, and all-zero output lands FLAGGED (the
    failed count matches the degraded flags exactly).
    """
    text_override = {
        str(c.chunk_id): build_embedding_input(c.text, c.context_blurb) for c in chunks
    }
    _degraded, embed_err = await embed_chunks(
        chunks, config, pool=pool, text_override=text_override, meta=meta
    )
    vectors_1024: dict[str, list[float]] = {}
    if v1024_dim is not None:
        vectors_1024 = {
            str(c.chunk_id): matryoshka_slice(c.embedding, v1024_dim)
            for c in chunks
            if c.embedding is not None
        }
    await store.store_vectors(chunks, model, dimensions, vectors_1024)
    batch_failed = sum(1 for c in chunks if c.embedding is None or _all_zero([c.embedding]))
    return len(chunks) - batch_failed, batch_failed, embed_err


async def _run_batches(
    gateway: SqlGateway,
    store: PostgresIngestStore,
    sql: dict[str, str],
    config: dict[str, object],
    pool: Any,
    *,
    tenant_id: str,
    project_id: str | None,
    model: str,
    dimensions: int,
    v1024_dim: int | None,
    batch_size: int,
    run_id: str | None,
    chunks_todo: int,
    skipped_current: int,
) -> BatchOutcome:
    """Page deterministic keyset batches until the scan runs dry."""
    outcome = BatchOutcome()
    floor_doc = ZERO_UUID
    floor_chunk = ZERO_UUID
    while True:
        async with gateway.transaction(tenant_id) as tx:
            rows = await tx.fetch(
                sql["scan"],
                tenant_id,
                project_id,
                model,
                dimensions,
                floor_doc,
                floor_chunk,
                batch_size,
            )
        if not rows:
            break
        chunks = _chunk_rows(rows)
        batch_embedded, batch_failed, embed_err = await _embed_and_store_batch(
            store, chunks, config, pool, model, dimensions, v1024_dim, outcome.meta
        )
        outcome.embedded += batch_embedded
        outcome.failed += batch_failed
        if embed_err is not None:
            outcome.warning = embed_err
            if batch_failed and len(outcome.errors) < _MAX_ERRORS:
                outcome.errors.append(embed_err)
        floor_doc = str(rows[-1]["doc_id"])
        floor_chunk = str(rows[-1]["chunk_id"])
        if run_id:
            await write_progress(
                gateway,
                sql["progress"],
                run_id,
                tenant_id,
                chunks_todo,
                skipped_current,
                outcome.embedded,
                outcome.failed,
            )
    return outcome


def _attach_honesty_keys(receipt: dict[str, object], outcome: BatchOutcome) -> dict[str, object]:
    """Append the conditional keys (mode/note, warning, bounded errors)."""
    for key in ("embedding_mode", "embedding_note"):
        if outcome.meta.get(key) is not None:
            receipt[key] = outcome.meta[key]
    if outcome.warning is not None and not outcome.errors:
        receipt["warning"] = outcome.warning
    if outcome.errors:
        receipt["errors"] = outcome.errors
    return receipt


async def execute_reingest_run(
    gateway: SqlGateway,
    store: PostgresIngestStore,
    ingest_sql: IngestSql,
    sql: dict[str, str],
    config: dict[str, object],
    pool: Any,
    *,
    tenant_id: str,
    project_id: str | None,
    is_duckdb: bool,
    model: str,
    dimensions: int,
    v1024_dim: int | None,
    batch_size: int,
    receipt: dict[str, object],
    chunks_todo: int,
    skipped_current: int,
) -> dict[str, object]:
    """Run the ledger-backed bulk re-embed and finish the receipt.

    Zero-todo scopes answer without a ledger row (an honest no-op). The
    receipt gains ``run_id`` on ledger-backed completion, then the
    conditional honesty keys; ``chunks`` restates embedded + failed so
    the receipt never drifts from the work actually done.
    """
    if chunks_todo == 0:
        return receipt
    hash_value = target_hash(model, dimensions, project_id)
    run_id = await open_ledger_run(
        gateway,
        ingest_sql,
        tenant_id=tenant_id,
        project_id=project_id,
        hash_value=hash_value,
        is_duckdb=is_duckdb,
    )
    outcome = await _run_batches(
        gateway,
        store,
        sql,
        config,
        pool,
        tenant_id=tenant_id,
        project_id=project_id,
        model=model,
        dimensions=dimensions,
        v1024_dim=v1024_dim,
        batch_size=batch_size,
        run_id=run_id,
        chunks_todo=chunks_todo,
        skipped_current=skipped_current,
    )
    if run_id:
        await close_ledger_run(
            gateway,
            ingest_sql,
            tenant_id=tenant_id,
            run_id=run_id,
            hash_value=hash_value,
            embedded=outcome.embedded,
            failed=outcome.failed,
            errors=outcome.errors,
            receipt=receipt,
        )
        receipt["run_id"] = run_id
    if outcome.embedded + outcome.failed != chunks_todo:
        receipt["chunks"] = outcome.embedded + outcome.failed
    receipt["embedded"] = outcome.embedded
    receipt["failed"] = outcome.failed
    return _attach_honesty_keys(receipt, outcome)
