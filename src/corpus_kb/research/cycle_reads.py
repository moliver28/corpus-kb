"""Cycle gate-data reads (todo 20) — the read-model inputs to the checkpoints.

Plain SELECTs over the artifacts the stages already produced: the inductive
run's pending proposals, the code_registry theory blocks, the deductive
run's checkpoint (conformal block), and the latest codebook version. The
GATE EVALUATION itself is pure (cycle_gates); this module only feeds it.

U19 (adversarial review F2): the inductive checkpoint also consults
cross-run agreement — per-unit label blocks across the N-run consensus
passes, folded through ``gate_reads.agreement_routing_evidence`` against
the profile's ``agreement_routing_threshold`` — so units the runs disagree
about land in the SAME gray-zone halt queue. Single-run cycles have no
agreement evidence: the signal is honestly not evaluated (never fired on
a guess), exactly like an uncalibrated threshold.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.research.cycle_gates import (
    GATE_UNRELEASED_DRAFT,
    GateFinding,
    gate_from_deductive,
    gate_from_inductive,
    gate_from_report,
    gate_from_theories,
    gate_gray_zone_from_agreement,
    gate_pending_proposals,
)
from corpus_kb.storage.gateway import SqlGateway


async def gate_findings_for(
    stage: str,
    receipt: dict[str, Any],
    pool: asyncpg.Pool,
    tenant_id: UUID,
    cfg: dict[str, Any] | None = None,
) -> list[GateFinding]:
    """Evaluate the checkpoint gates that live at one stage boundary."""
    if stage == "inductive":
        findings = list(gate_from_inductive(receipt))
        run_id = receipt.get("run_id")
        # U48 N-run consensus receipts carry run_ids (list); a single-pass
        # receipt keeps working through run_id alone.
        run_ids = [str(r) for r in (receipt.get("run_ids") or [])] or (
            [str(run_id)] if run_id else []
        )
        if run_ids:
            pending = 0
            for rid in run_ids:
                pending += await pending_proposals(pool, tenant_id, UUID(rid))
            anchor = str(receipt.get("run_id") or run_ids[-1])
            found = gate_pending_proposals(pending, anchor)
            if found:
                findings.append(found)
        agreement = await _agreement_finding(receipt, run_ids, pool, tenant_id, cfg)
        if agreement is not None:
            findings.append(agreement)
        return findings
    if stage == "deductive":
        # U24/F8: an enforce-mode block IS a gate finding — the cycle halts
        # with an honest halt record instead of marching past a stage that
        # never ran.
        if str(receipt.get("status")) == "blocked_unreleased_draft":
            return [
                GateFinding(
                    GATE_UNRELEASED_DRAFT,
                    str(receipt.get("reason") or "codebook version is not released"),
                    {
                        "codebook_version_id": receipt.get("codebook_version_id"),
                        "enforcement": receipt.get("enforcement"),
                        "fix": receipt.get("fix"),
                    },
                )
            ]
        version = receipt.get("codebook_version_id")
        codes = await version_codes(pool, tenant_id, UUID(str(version))) if version else []
        run_id = receipt.get("run_id")
        checkpoint = await run_checkpoint(pool, tenant_id, UUID(str(run_id))) if run_id else {}
        pending = await pending_reviews(pool, tenant_id)
        pending_ids = await pending_review_ids(pool, tenant_id)
        return gate_from_theories(codes) + gate_from_deductive(
            receipt, checkpoint, pending, pending_ids
        )
    if stage == "report":
        return gate_from_report(receipt)
    return []


def _agreement_threshold(cfg: dict[str, Any] | None) -> float | None:
    """The profile's ``agreement_routing_threshold`` from config (None =
    unconfigured profile threshold -> not a routing signal, U18's rule)."""
    if cfg is None:
        return None
    from corpus_kb.research.release.enforcement_settings import release_guard_settings
    from corpus_kb.research.release.profiles import resolve_profile

    profile = resolve_profile(release_guard_settings(cfg).default_profile)
    return profile.agreement_routing_threshold


async def _agreement_finding(
    receipt: dict[str, Any],
    run_ids: list[str],
    pool: asyncpg.Pool,
    tenant_id: UUID,
    cfg: dict[str, Any] | None,
) -> GateFinding | None:
    """U19: low cross-run agreement -> the gray-zone halt queue.

    Fires only with REAL multi-run data (>= 2 runs) and a configured
    routing threshold; both missing means not_evaluable, never a pass.
    """
    threshold = _agreement_threshold(cfg)
    if threshold is None or len(run_ids) < 2:
        return None
    from corpus_kb.research.release.gate_reads import agreement_routing_evidence

    evidence = agreement_routing_evidence(
        await run_label_blocks(pool, tenant_id, run_ids),
        await routed_unit_ids(pool, tenant_id),
    )
    raw_units = evidence["units"]
    units = [u for u in raw_units if isinstance(u, dict)] if isinstance(raw_units, list) else []
    measured = [
        u
        for u in units
        if isinstance(u.get("agreement"), (int, float)) and float(u["agreement"]) < threshold
    ]
    if not measured:
        return None
    return gate_gray_zone_from_agreement(
        measured,
        {str(u["unit_id"]) for u in units if u.get("routed")},
        threshold=threshold,
    )


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


async def pending_review_ids(pool: asyncpg.Pool, tenant_id: UUID, limit: int = 10) -> list[str]:
    """Capped assignment UUIDs still awaiting a decision (halt listing).

    The gray-zone halt prints these with the exact accept/override command so
    the documented remedy never requires hand-written SQL.
    """
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT assignment_aggregate_id::text AS id FROM research_assignments "
            "WHERE tenant_id = $1 AND status = 'review' "
            "ORDER BY assignment_aggregate_id LIMIT $2",
            tenant_id,
            limit,
        )
    return [str(row["id"]) for row in rows]


async def version_codes(pool: asyncpg.Pool, tenant_id: UUID, version_id: UUID) -> list[dict]:
    from corpus_kb.research.run_inputs import load_codes
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        return await load_codes(conn, tenant_id, version_id)


async def run_checkpoint(
    pool: asyncpg.Pool | SqlGateway, tenant_id: UUID, run_id: UUID
) -> dict[str, object]:
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


async def run_label_blocks(
    pool: asyncpg.Pool, tenant_id: UUID, run_ids: list[str]
) -> list[dict[str, str]]:
    """Per-run unit label blocks ({unit_id: label}) from the inductive proposals.

    One block per run, in ``run_ids`` order: every proposed code contributes
    its member units under its label (the suggested label, falling back to
    the cluster id). Units a run left unproposed carry no label in that
    run's block — a run that did not label a unit cannot agree with one
    that did (the wave-1 agreement rule).
    """
    from corpus_kb.storage.tenant_conn import tenant_connection

    if len(run_ids) < 2:
        return []
    async with tenant_connection(pool, tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT run_id::text AS run_id, suggested_label, cluster_id, member_unit_ids "
            "FROM research_proposed_codes "
            "WHERE tenant_id = $1 AND run_id = ANY($2::uuid[]) ORDER BY proposed_id",
            tenant_id,
            run_ids,
        )
    blocks: dict[str, dict[str, str]] = {rid: {} for rid in run_ids}
    for row in rows:
        label = str(row["suggested_label"] or row["cluster_id"])
        block = blocks.setdefault(str(row["run_id"]), {})
        for unit_id in row["member_unit_ids"] or []:
            block.setdefault(str(unit_id), label)
    return [blocks[rid] for rid in run_ids if rid in blocks]


async def routed_unit_ids(pool: asyncpg.Pool, tenant_id: UUID) -> list[str]:
    """Unit ids currently in the gray-zone review queue (status='review')."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT unit_id::text AS unit_id FROM research_assignments "
            "WHERE tenant_id = $1 AND status = 'review' ORDER BY unit_id",
            tenant_id,
        )
    return [str(row["unit_id"]) for row in rows]
