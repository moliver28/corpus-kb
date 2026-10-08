"""Cycle gate-data reads (todo 20) — the read-model inputs to the checkpoints.

Plain SELECTs over the artifacts the stages already produced: the inductive
run's pending proposals, the code_registry theory blocks, the deductive
run's checkpoint (conformal block), and the latest codebook version. The
GATE EVALUATION itself is pure (cycle_gates); this module only feeds it.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.research.cycle_gates import (
    GateFinding,
    gate_from_deductive,
    gate_from_inductive,
    gate_from_report,
    gate_from_theories,
    gate_pending_proposals,
)


async def gate_findings_for(
    stage: str,
    receipt: dict[str, Any],
    pool: asyncpg.Pool,
    tenant_id: UUID,
) -> list[GateFinding]:
    """Evaluate the checkpoint gates that live at one stage boundary."""
    if stage == "inductive":
        findings = list(gate_from_inductive(receipt))
        run_id = receipt.get("run_id")
        if run_id:
            pending = await pending_proposals(pool, tenant_id, UUID(str(run_id)))
            found = gate_pending_proposals(pending, str(run_id))
            if found:
                findings.append(found)
        return findings
    if stage == "deductive":
        version = receipt.get("codebook_version_id")
        codes = await version_codes(pool, tenant_id, UUID(str(version))) if version else []
        run_id = receipt.get("run_id")
        checkpoint = await run_checkpoint(pool, tenant_id, UUID(str(run_id))) if run_id else {}
        pending = await pending_reviews(pool, tenant_id)
        return gate_from_theories(codes) + gate_from_deductive(receipt, checkpoint, pending)
    if stage == "report":
        return gate_from_report(receipt)
    return []


async def latest_version_id(pool: asyncpg.Pool, tenant_id: UUID) -> UUID | None:
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT version_id FROM codebook_versions WHERE tenant_id = $1 "
            "ORDER BY created_at DESC, version_id DESC LIMIT 1",
            tenant_id,
        )
    return UUID(str(row["version_id"])) if row else None


async def pending_proposals(pool: asyncpg.Pool, tenant_id: UUID, run_id: UUID) -> int:
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        value = await conn.fetchval(
            "SELECT count(*) FROM research_proposed_codes "
            "WHERE tenant_id = $1 AND run_id = $2 AND status = 'proposed'",
            tenant_id,
            run_id,
        )
    return int(value or 0)


async def pending_reviews(pool: asyncpg.Pool, tenant_id: UUID) -> int:
    """UNRESOLVED review-queue items (status='review'), the F-7 gate input.

    Tenant-scoped on purpose: escalations from earlier runs stay unresolved
    until a human acts, so the gate reflects the QUEUE, not one run's
    routing volume.
    """
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        value = await conn.fetchval(
            "SELECT count(*) FROM research_assignments WHERE tenant_id = $1 AND status = 'review'",
            tenant_id,
        )
    return int(value or 0)


async def version_codes(pool: asyncpg.Pool, tenant_id: UUID, version_id: UUID) -> list[dict]:
    from corpus_kb.research.run_inputs import load_codes
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        return await load_codes(conn, tenant_id, version_id)


async def run_checkpoint(pool: asyncpg.Pool, tenant_id: UUID, run_id: UUID) -> dict[str, object]:
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        value = await conn.fetchval(
            "SELECT checkpoint FROM research_runs WHERE run_id = $1 AND tenant_id = $2",
            run_id,
            tenant_id,
        )
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}
