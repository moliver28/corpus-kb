"""The analysis step ledger (TD-13, R11): planned and ad-hoc steps in one table.

``analysis_steps`` (migration 050 PG / 051 DuckDB) holds one row per step
(ids, run/parent, sequence, kind, operation, inputs digest, parameters,
prompt and model digests, UTC times, status, outputs, actor, and the PROV
names ``used``/``was_generated_by``/``was_derived_from``). Rows store
references and digests, never corpus text. ``record_step`` is the one-call
op-boundary helper; it is FAIL-OPEN (the trace is metadata, never data —
the ``OllamaEmbedder`` degrade precedent). ``start_step``/``finish_step``/
``fail_step`` raise, so the T2 protocol runner sees real failures.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Mapping
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from uuid import UUID, uuid4

from corpus_kb.coding._scope import CodingStore, tenant_scope

logger = logging.getLogger(__name__)

__all__ = [
    "digest_ids",
    "fail_step",
    "finish_step",
    "program_version",
    "record_step",
    "start_step",
    "step_key",
]

_INSERT_STEP = """
INSERT INTO analysis_steps (
    step_id, tenant_id, run_id, parent_step_id, seq, kind, operation,
    operation_version, inputs_table, inputs_column, inputs_filter,
    inputs_digest, parameters, prompt_id, prompt_version, prompt_digest,
    model_name, model_digest, step_key, status, actor, used,
    was_generated_by, was_derived_from
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16,
    $17, $18, $19, 'running', $20, $21::jsonb, $22, $23
)
"""


def program_version() -> str:
    """The installed corpus-kb build string; ledger keys die on upgrades."""
    try:
        return version("corpus-kb")
    except PackageNotFoundError:
        return "unknown"


def step_key(
    inputs_digest: str,
    parameters: Mapping[str, Any],
    prompt_version: str | None,
    model_digest: str | None,
) -> str:
    """SHA-256 over inputs, parameters, prompt version, model digest, program."""
    canonical = json.dumps(
        {
            "inputs_digest": inputs_digest,
            "parameters": dict(parameters),
            "prompt_version": prompt_version,
            "model_digest": model_digest,
            "program_version": program_version(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def digest_ids(ids: Iterable[str]) -> str:
    """SHA-256 over the sorted, deduplicated selected ids (never the text)."""
    canonical = "\n".join(sorted(set(ids)))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def start_step(
    pool: CodingStore,
    tenant_id: UUID,
    operation: str,
    *,
    kind: str = "ad_hoc",
    run_id: UUID | None = None,
    parent_step_id: UUID | None = None,
    seq: int = 0,
    operation_version: str | None = None,
    inputs_table: str | None = None,
    inputs_column: str | None = None,
    inputs_filter: str | None = None,
    inputs_digest: str | None = None,
    parameters: Mapping[str, Any] | None = None,
    prompt_id: str | None = None,
    prompt_version: str | None = None,
    prompt_digest: str | None = None,
    model_name: str | None = None,
    model_digest: str | None = None,
    actor: str | None = None,
    used: list[str] | None = None,
    generated_by: str | None = None,
    derived_from: str | None = None,
) -> UUID:
    """Insert one ``running`` ledger row and answer its id. Raises on failure."""
    params = dict(parameters or {})
    digest = inputs_digest or ""
    step_id = uuid4()
    row: list[Any] = [
        str(step_id),
        str(tenant_id),
        str(run_id) if run_id else None,
        str(parent_step_id) if parent_step_id else None,
        seq,
        kind,
        operation,
        operation_version,
        inputs_table,
        inputs_column,
        inputs_filter,
        digest,
        json.dumps(params, sort_keys=True),
        prompt_id,
        prompt_version,
        prompt_digest,
        model_name,
        model_digest,
        step_key(digest, params, prompt_version, model_digest),
        actor,
        json.dumps(list(used or [])),
        generated_by,
        derived_from,
    ]
    async with tenant_scope(pool, tenant_id) as conn:
        await conn.execute(_INSERT_STEP, *row)
        return step_id


async def finish_step(
    pool: CodingStore,
    tenant_id: UUID,
    step_id: UUID,
    *,
    outputs_table: str | None = None,
    outputs_rows: int | None = None,
) -> None:
    """Mark one step completed with its outputs. Raises on failure."""
    async with tenant_scope(pool, tenant_id) as conn:
        await conn.execute(
            "UPDATE analysis_steps SET status='completed', finished_at=now(), "
            "outputs_table=$3, outputs_rows=$4 "
            "WHERE step_id=$1 AND tenant_id=$2",
            str(step_id),
            str(tenant_id),
            outputs_table,
            outputs_rows,
        )


async def fail_step(pool: CodingStore, tenant_id: UUID, step_id: UUID) -> None:
    """Mark one step failed (status + end time). Raises on failure."""
    async with tenant_scope(pool, tenant_id) as conn:
        await conn.execute(
            "UPDATE analysis_steps SET status='failed', finished_at=now() "
            "WHERE step_id=$1 AND tenant_id=$2",
            str(step_id),
            str(tenant_id),
        )


async def record_step(
    pool: CodingStore,
    tenant_id: UUID,
    operation: str,
    *,
    kind: str = "ad_hoc",
    parameters: Mapping[str, Any] | None = None,
    used: list[str] | None = None,
    outputs_table: str | None = None,
    outputs_rows: int | None = None,
    actor: str | None = None,
) -> UUID | None:
    """One completed ledger row at an operation boundary; fail-open.

    The op functions in ``tools/`` call this once per invocation (TD-13
    point 2). Returns the step id, or ``None`` when the write degraded.
    """
    try:
        step_id = await start_step(
            pool,
            tenant_id,
            operation,
            kind=kind,
            parameters=parameters,
            used=used,
            actor=actor,
        )
        await finish_step(
            pool, tenant_id, step_id, outputs_table=outputs_table, outputs_rows=outputs_rows
        )
        return step_id
    except Exception:
        logger.warning("Step ledger write degraded for %s", operation, exc_info=True)
        return None
