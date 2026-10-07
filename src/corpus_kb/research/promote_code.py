"""Human-gated code promotion (todo 15, v5 §9.6) — `corpus-kb codebook promote`.

The proposed_code log NEVER auto-promotes: this module runs ONLY when a human
names the surface (CLI/MCP) with a proposal id and a name/definition. Steps:

  1. load the proposal (status must be 'proposed');
  2. PROMOTION RE-DERIVATION (r7 CRITICAL): resolve member unit_ids ->
     embeddings -> k-medoids answer-view prototypes built from the NEW code's
     member units, in the DEDUCTIVE unit-embedding space. The summary-space
     centroid snapshot is PROVENANCE ONLY — never a scoring prototype (the
     spaces differ in input text and possibly model/dim);
  3. PROMOTE-TIME DUPLICATE GATE: the derived prototypes' max cosine vs every
     existing code's answer-view prototypes; above ``tau_dup`` the promotion
     BLOCKS with a merge suggestion (tau_dup from config, or calibrated from
     gold when prototype sets exist);
  4. on pass: mint a NEW codebook_version aggregate (Created + CodeAdded +
     PrototypeUpdated with member text_sha256 refs) — events only; the code
     projector lands the read rows;
  5. mark the proposal promoted with the new version/code ids.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg
import numpy as np

from corpus_kb.research.prototypes import build_view_prototypes
from corpus_kb.storage.tenant_conn import tenant_connection

TAU_DUP_DEFAULT = 0.85
TAU_DUP_FLOOR = 0.7
TAU_DUP_CEIL = 0.95


@dataclass(frozen=True)
class DerivedPrototypes:
    """Answer-view prototypes re-derived from member units (deductive space)."""

    unit_ids: list[int]
    prototypes: list[list[float]]


def calibrate_tau_dup(
    prototypes_by_code: dict[str, list[list[float]]], margin: float = 0.05
) -> float | None:
    """tau_dup from gold prototype geometry: max inter-code cosine + margin.

    Calibrated (never hardcoded policy): with gold codes, anything above the
    WORST existing between-code separation is a duplicate. Clamped to
    [0.7, 0.95]. Returns None with fewer than two codes (no geometry to
    calibrate from — the config default applies).
    """
    sims: list[float] = []
    codes = sorted(prototypes_by_code)
    for i, code_a in enumerate(codes):
        for code_b in codes[i + 1 :]:
            for pa in prototypes_by_code[code_a]:
                for pb in prototypes_by_code[code_b]:
                    sims.append(float(np.dot(pa, pb)))
    if not sims:
        return None
    return float(min(TAU_DUP_CEIL, max(TAU_DUP_FLOOR, max(sims) + margin)))


def duplicate_gate(
    derived: list[list[float]],
    existing: dict[str, list[list[float]]],
    tau_dup: float,
) -> tuple[bool, str | None, float]:
    """Max-cos duplicate check. Returns (blocked, merge suggestion, max cos)."""
    worst = -1.0
    worst_code: str | None = None
    for code_id, protos in existing.items():
        for candidate in derived:
            for proto in protos:
                sim = float(np.dot(candidate, proto))
                if sim > worst:
                    worst = sim
                    worst_code = code_id
    if worst >= tau_dup and worst_code is not None:
        return True, worst_code, worst
    return False, None, worst


async def promote_proposal(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    proposed_id: int,
    name: str,
    definition: str,
    *,
    inclusion: str = "",
    exclusion: str = "",
    tau_dup: float | None = None,
    label: str | None = None,
) -> dict[str, object]:
    """Promote one proposed code into a new codebook version (human gate)."""
    from corpus_kb.handlers.research_handler import ResearchHandler
    from corpus_kb.research.inductive_store import load_unit_views_by_ids

    async with tenant_connection(pool, tenant_id) as conn:
        proposal = await _load_proposal(conn, tenant_id, proposed_id)
        if proposal["status"] != "proposed":
            return {
                "status": "error",
                "reason": f"proposal {proposed_id} is {proposal['status']}, not proposed",
            }
        views = await load_unit_views_by_ids(conn, tenant_id, proposal["member_unit_ids"])
        existing = await _existing_prototypes(conn, tenant_id)

    members = [views[u] for u in proposal["member_unit_ids"] if u in views]
    if not members:
        return {"status": "error", "reason": "no member unit views resolvable"}

    derived = DerivedPrototypes(
        unit_ids=[u for u in proposal["member_unit_ids"] if u in views],
        prototypes=build_view_prototypes([m[0] for m in members]),
    )

    effective_tau = tau_dup
    if effective_tau is None:
        calibrated = calibrate_tau_dup(existing)
        effective_tau = calibrated if calibrated is not None else TAU_DUP_DEFAULT
    blocked, closest, max_cos = duplicate_gate(derived.prototypes, existing, float(effective_tau))
    async with tenant_connection(pool, tenant_id) as conn:
        if blocked:
            await _mark_proposal(
                conn,
                tenant_id,
                proposed_id,
                status="duplicate_blocked",
                block_reason=(
                    f"max-cos {max_cos:.4f} >= tau_dup {float(effective_tau):.4f} vs "
                    f"existing code {closest}; consider merging instead"
                ),
            )
            return {
                "status": "duplicate_blocked",
                "proposal_id": proposed_id,
                "tau_dup": float(effective_tau),
                "max_cos": max_cos,
                "merge_suggestion": closest,
            }

    handler = ResearchHandler(pool)
    version = await _mint_version(
        pool,
        handler,
        tenant_id,
        name,
        definition,
        inclusion,
        exclusion,
        derived,
        label=label or proposal["suggested_label"],
    )
    async with tenant_connection(pool, tenant_id) as conn:
        await _mark_proposal(
            conn,
            tenant_id,
            proposed_id,
            status="promoted",
            promoted_version_id=version["version_id"],
            promoted_code_id=version["code_id"],
        )
    return {
        "status": "promoted",
        "proposal_id": proposed_id,
        "codebook_version_id": version["version_id"],
        "code_id": version["code_id"],
        "n_members": len(derived.unit_ids),
        "tau_dup": float(effective_tau),
        "max_cos": max_cos,
        "rederivation": "k-medoids over member A/QA/Q views (deductive space)",
    }


async def _mint_version(
    pool: asyncpg.Pool,
    handler: Any,
    tenant_id: UUID,
    name: str,
    definition: str,
    inclusion: str,
    exclusion: str,
    derived: DerivedPrototypes,
    label: str,
) -> dict[str, object]:
    from uuid import uuid4

    from corpus_kb.domain.codebook import CodebookVersion

    code_id = str(uuid4())
    codes = [
        {
            "code_id": code_id,
            "name": name,
            "definition": definition,
            "inclusion": inclusion,
            "exclusion": exclusion,
            "mode": "inductive",
            "is_interpretive": False,
            "allows_question_dependent": True,
        }
    ]
    n_versions = await _count_versions(pool, tenant_id)
    sha = handler_create_sha(codes)
    version = CodebookVersion(
        tenant_id=tenant_id,
        label=f"v{n_versions + 1}-inductive-{label or 'code'}",
        sha256=sha,
        paradigm="inductive",
        notes=f"promoted from proposed_code cluster (members={len(derived.unit_ids)})",
    )
    version.add_codes(tenant_id=tenant_id, codes=codes)
    version.update_prototypes(
        tenant_id=tenant_id,
        code_id=UUID(code_id),
        exemplar_text_sha256=await _member_text_sha256s(pool, tenant_id, derived.unit_ids),
    )
    handler.app.save(version)
    return {"version_id": str(version.id), "code_id": code_id}


def handler_create_sha(codes: list[dict[str, object]]) -> str:
    import hashlib

    return hashlib.sha256(repr(sorted(str(c) for c in codes)).encode()).hexdigest()


async def _count_versions(pool: asyncpg.Pool, tenant_id: UUID) -> int:
    async with tenant_connection(pool, tenant_id) as conn:
        value = await conn.fetchval(
            "SELECT COUNT(*) FROM codebook_versions WHERE tenant_id = $1",
            str(tenant_id),
        )
    return int(value or 0)


async def _member_text_sha256s(
    pool: asyncpg.Pool, tenant_id: UUID, unit_ids: list[int]
) -> list[str]:
    async with tenant_connection(pool, tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT text_sha256::text AS sha FROM research_units
            WHERE tenant_id = $1 AND unit_id = ANY($2::bigint[])
            ORDER BY unit_id
            """,
            str(tenant_id),
            unit_ids,
        )
    return [str(row["sha"]) for row in rows]


async def _load_proposal(
    conn: asyncpg.Connection, tenant_id: UUID, proposed_id: int
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT proposed_id, cluster_id, run_id, batch_no, suggested_label,
               member_unit_ids, n_members, dbcv_relative_validity, status
        FROM research_proposed_codes
        WHERE tenant_id = $1 AND proposed_id = $2
        """,
        str(tenant_id),
        proposed_id,
    )
    if row is None:
        raise ValueError(f"proposal {proposed_id} not found for tenant")
    return {
        "proposed_id": int(row["proposed_id"]),
        "cluster_id": str(row["cluster_id"]),
        "run_id": str(row["run_id"]),
        "batch_no": int(row["batch_no"] or 0),
        "suggested_label": row["suggested_label"],
        "member_unit_ids": [int(x) for x in (row["member_unit_ids"] or [])],
        "n_members": int(row["n_members"] or 0),
        "dbcv_relative_validity": row["dbcv_relative_validity"],
        "status": str(row["status"]),
    }


async def _existing_prototypes(
    conn: asyncpg.Connection, tenant_id: UUID
) -> dict[str, list[list[float]]]:
    """Existing codes' answer-view prototypes rebuilt from gold exemplar refs."""
    from corpus_kb.research.run_inputs import load_gold_views

    rows = await conn.fetch(
        """
        SELECT code_id, theory FROM code_registry
        WHERE tenant_id = $1
        ORDER BY codebook_version_id DESC, code_id
        """,
        str(tenant_id),
    )
    seen: set[str] = set()
    result: dict[str, list[list[float]]] = {}
    for row in rows:
        code_id = str(row["code_id"])
        if code_id in seen:
            continue
        seen.add(code_id)
        theory = row["theory"]
        if isinstance(theory, str):
            theory = json.loads(theory or "{}")
        refs = list((theory or {}).get("exemplar_text_sha256") or [])
        if not refs:
            continue
        gold = await load_gold_views(conn, tenant_id, refs)
        answer_views = [g[0] for g in gold if g[0]]
        if answer_views:
            result[code_id] = build_view_prototypes(answer_views)
    return result


async def _mark_proposal(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    proposed_id: int,
    *,
    status: str,
    block_reason: str | None = None,
    promoted_version_id: str | None = None,
    promoted_code_id: str | None = None,
) -> None:
    await conn.execute(
        """
        UPDATE research_proposed_codes SET
            status = $3,
            block_reason = COALESCE($4, block_reason),
            promoted_version_id = COALESCE($5::uuid, promoted_version_id),
            promoted_code_id = COALESCE($6, promoted_code_id)
        WHERE tenant_id = $1 AND proposed_id = $2
        """,
        str(tenant_id),
        proposed_id,
        status,
        block_reason,
        promoted_version_id,
        promoted_code_id,
    )
