"""Release gate-data reads — the read-model inputs to ``assess``.

``cycle_reads.py`` pattern: the GATES are pure; THIS module only feeds them.
Plain SELECTs inside a tenant connection (FORCE RLS scopes every row) over
the artifacts the pipeline already produced, plus PURE builders that turn
raw rows into the evidence blocks ``GateInputs`` carries — the builders are
the seam in-memory fakes plug into, so gate tests never need a database.

Each evidence block is a JSON-safe dict whose shape the consuming gate
documents; ``gate_inputs_from_blocks`` maps evidence-name -> block onto the
``GateInputs`` fields (unknown names land in ``extra``).
"""

from __future__ import annotations

import json
import math
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.coding.uncertainty.cross_run_agreement import cross_run_agreement_batch
from corpus_kb.research.release.gate_types import GateInputs

_CODEBOOK_SELECT = """
SELECT code_id, name, brief_definition, inclusion_criteria, exclusion_criteria,
       examples, theory
FROM code_registry
WHERE tenant_id = $1 AND codebook_version_id = $2
ORDER BY code_id
"""


async def audit_rows(pool: asyncpg.Pool, tenant_id: UUID, scope: str) -> list[dict[str, object]]:
    """audit_partitions rows for one scope (U9 assignments, U17 design)."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT scope, unit_id, partition, stratum, inclusion_probability, seed, sampled_at "
            "FROM audit_partitions WHERE tenant_id = $1 AND scope = $2 ORDER BY audit_id",
            tenant_id,
            scope,
        )
    return [_audit_row_dict(row) for row in rows]


def _audit_row_dict(row: asyncpg.Record) -> dict[str, object]:
    inclusion = row["inclusion_probability"]
    seed = row["seed"]
    sampled_at = row["sampled_at"]
    return {
        "scope": str(row["scope"]),
        "unit_id": str(row["unit_id"]),
        "partition": str(row["partition"]),
        "stratum": str(row["stratum"]) if row["stratum"] is not None else None,
        "inclusion_probability": float(inclusion) if inclusion is not None else None,
        "seed": int(seed) if seed is not None else None,
        "sampled_at": str(sampled_at) if sampled_at is not None else None,
    }


async def codebook_categories(
    pool: asyncpg.Pool, tenant_id: UUID, version_id: UUID
) -> dict[str, object]:
    """The codebook document for U7: {"categories": [code dicts]}.

    code_registry has no parent column, so a hierarchical parent reference
    (when a run recorded one) is read from the code's theory block.
    """
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        rows = await conn.fetch(_CODEBOOK_SELECT, tenant_id, version_id)
    categories: list[dict[str, object]] = []
    for row in rows:
        theory_raw: Any = row["theory"]
        theory = theory_raw if isinstance(theory_raw, dict) else {}
        examples_raw: Any = row["examples"]
        categories.append(
            {
                "id": str(row["code_id"]),
                "name": row["name"],
                "brief_definition": row["brief_definition"],
                "inclusion_criteria": row["inclusion_criteria"],
                "exclusion_criteria": row["exclusion_criteria"],
                "examples": examples_raw if isinstance(examples_raw, list) else [],
                "theory": theory,
            }
        )
    return {"categories": categories}


async def latest_release_payload(
    pool: asyncpg.Pool, tenant_id: UUID, codebook_id: UUID
) -> dict[str, object]:
    """The newest draft/released manifest payload for one codebook (U10)."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        value = await conn.fetchval(
            "SELECT manifest_json FROM codebook_releases WHERE tenant_id = $1 "
            "AND codebook_id = $2 ORDER BY created_at DESC, release_id DESC LIMIT 1",
            tenant_id,
            codebook_id,
        )
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


async def run_metric_block(
    pool: asyncpg.Pool, tenant_id: UUID, run_id: UUID, key: str
) -> dict[str, object]:
    """One checkpoint metric block (reliability/heldout/stability/saturation/...)."""
    from corpus_kb.research.cycle_reads import run_checkpoint

    checkpoint = await run_checkpoint(pool, tenant_id, run_id)
    block = checkpoint.get(key)
    return dict(block) if isinstance(block, dict) else {}


def machine_profile_from_manifest(payload: dict[str, object]) -> dict[str, object]:
    """Pure: the machine_profile block out of a manifest payload (U10)."""
    block = payload.get("machine_profile")
    return dict(block) if isinstance(block, dict) else {}


def partitions_evidence(
    rows: list[dict[str, object]],
    *,
    rule: str = "disjoint",
    doc_by_unit: dict[str, str] | None = None,
    participant_by_unit: dict[str, str] | None = None,
) -> dict[str, object]:
    """Pure: U9 assignments out of audit_partitions-shaped rows.

    ``doc_by_unit`` / ``participant_by_unit`` are the optional identifier
    lookups ("when identifiers exist", v6 U9); units without one simply
    contribute no document/participant key.
    """
    assignments: list[dict[str, object]] = []
    for row in rows:
        raw_unit = row.get("unit_id")
        unit_id = str(raw_unit) if raw_unit is not None else None
        entry: dict[str, object] = {"unit_id": unit_id, "partition": row.get("partition")}
        if doc_by_unit and unit_id is not None and unit_id in doc_by_unit:
            entry["document_id"] = doc_by_unit[unit_id]
        if participant_by_unit and unit_id is not None and unit_id in participant_by_unit:
            entry["participant_id"] = participant_by_unit[unit_id]
        assignments.append(entry)
    return {"assignments": assignments, "rule": rule}


def audit_sample_evidence(rows: list[dict[str, object]]) -> dict[str, object]:
    """Pure: U17 block — only rows whose partition is the audit partition."""
    return {"rows": [dict(row) for row in rows if row.get("partition") == "audit"]}


def agreement_routing_evidence(
    run_label_blocks: list[dict[str, Any]], routed_ids: list[str]
) -> dict[str, object]:
    """Pure: U19 ledger from per-run label blocks ({unit_id: label} each).

    Computes the per-unit cross-run agreement (wave-1
    ``cross_run_agreement_batch``); a unit is ``routed`` when its id appears
    in ``routed_ids`` (the gray-zone queue's current membership).
    """
    if not run_label_blocks:
        return {"units": []}
    unit_ids = sorted({str(k) for block in run_label_blocks for k in block})
    labels_per_unit = [[block.get(uid) for block in run_label_blocks] for uid in unit_ids]
    scores = cross_run_agreement_batch(labels_per_unit)
    routed = set(routed_ids)
    units: list[dict[str, object]] = []
    for uid, score in zip(unit_ids, scores, strict=True):
        value = float(score)
        units.append(
            {
                "unit_id": uid,
                "agreement": None if math.isnan(value) else value,
                "routed": uid in routed,
            }
        )
    return {"units": units}


def gate_inputs_from_blocks(blocks: dict[str, object]) -> GateInputs:
    """Assemble a GateInputs from an evidence-name -> block mapping.

    Recognized keys mirror the GateInputs fields; anything else lands in
    ``extra``. Non-dict values for a known key are dropped (an evidence
    block is always a mapping; silently coercing junk would fabricate).
    """
    known = (
        "manifest",
        "canary",
        "determinism",
        "consensus",
        "codebook",
        "boundary",
        "partitions",
        "heldout",
        "prompt_variants",
        "alt_test_data",
        "ppi",
        "stability",
        "audit_sample",
        "agreement_calibration",
        "agreement_routing",
        "reliability",
    )

    def pick(key: str) -> dict[str, object] | None:
        value = blocks.get(key)
        return dict(value) if isinstance(value, dict) else None

    metrics = blocks.get("metrics")
    extra = {k: v for k, v in blocks.items() if k not in known and k != "metrics"}
    return GateInputs(
        **{key: pick(key) for key in known},  # keys are exactly the GateInputs fields
        metrics=dict(metrics) if isinstance(metrics, dict) else None,
        extra=extra,
    )


__all__ = [
    "agreement_routing_evidence",
    "audit_rows",
    "audit_sample_evidence",
    "codebook_categories",
    "gate_inputs_from_blocks",
    "latest_release_payload",
    "machine_profile_from_manifest",
    "partitions_evidence",
    "run_metric_block",
]
