"""Pinned-run resolution for the notebook views (issue #132, pure move).

The evidence view reads ONE codebook version and ONE run. The pinned run
is the newest ``research_runs`` row with assignment rows for the resolved
version (``started_at DESC, run_id`` — a stable tiebreak). A newer run
therefore REPLACES a view instead of appending to it; superseded runs stay
in the tables for the report surfaces and human reviews. Rows written
before versioning existed (no run, no version tag) surface only while no
run-tagged rows exist for the version, so legacy data is never hidden.

Pure move out of ``notebook_evidence.py`` for the 250-line rule; imports
stay stable because ``notebook_evidence`` re-exports the readers.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.storage.gateway import SqlGateway

_PINNED_RUN_SQL = """
SELECT r.run_id
FROM research_runs r
JOIN research_assignments ra ON ra.run_id = r.run_id AND ra.tenant_id = r.tenant_id
WHERE r.tenant_id = $1 AND ra.cb_version_id = $2
ORDER BY r.started_at DESC, r.run_id
LIMIT 1
"""

_RUN_CHECKPOINT_SQL = """
SELECT checkpoint FROM research_runs
WHERE tenant_id = $1 AND run_id = $2 AND checkpoint IS NOT NULL
"""


async def _pinned_run_for_version(
    conn: asyncpg.Connection | SqlGateway, tenant_id: UUID, version_id: UUID
) -> UUID | None:
    """Newest run with assignment rows for this version; None means legacy.

    Returns None both when nothing exists and when only pre-versioning rows
    (run_id IS NULL) exist; the evidence SQL handles both with
    ``run_id IS NOT DISTINCT FROM NULL``.
    """
    row = await conn.fetchrow(_PINNED_RUN_SQL, str(tenant_id), str(version_id))
    if row is None or row["run_id"] is None:
        return None
    return UUID(str(row["run_id"]))


async def _pinned_checkpoint(
    conn: asyncpg.Connection | SqlGateway, tenant_id: UUID, pinned_run: UUID | None
) -> dict[str, Any]:
    """The pinned run's checkpoint body; {} when no run is pinned."""
    if pinned_run is None:
        return {}
    row = await conn.fetchrow(_RUN_CHECKPOINT_SQL, str(tenant_id), str(pinned_run))
    if row is None:
        return {}
    checkpoint = row["checkpoint"]
    if isinstance(checkpoint, str):
        checkpoint = json.loads(checkpoint or "{}")
    return dict(checkpoint or {})


def _conformal_per_unit(checkpoint: dict[str, Any]) -> dict[int, int]:
    """Per-unit conformal set sizes (>1 only) from the pinned checkpoint."""
    per_unit = (checkpoint.get("conformal") or {}).get("per_unit") or {}
    return {int(unit_id): int(size) for unit_id, size in dict(per_unit).items()}


def _ranking_per_unit(checkpoint: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Per-unit review-priority rows from the pinned checkpoint (issue #132).

    Present only when the run recorded a ``review_priority`` block, so
    pre-wiring snapshots stay byte-identical.
    """
    block = checkpoint.get("review_priority") or {}
    rows = block.get("units") or []
    return {int(row["unit_id"]): dict(row) for row in rows if row.get("unit_id") is not None}
