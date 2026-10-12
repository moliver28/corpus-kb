"""Release projector — CodebookVersion release events into migration 019's
four read tables (codebook_releases, release_gate_results, release_waivers,
audit_partitions is sampling-owned and written by the sampler, not here).

Replay idempotency: every handler either inserts with ON CONFLICT DO NOTHING
or runs a GUARDED transition UPDATE whose WHERE clause accepts both the
expected prior state (first apply) and the already-applied identical state
(re-apply). A DIFFERENT concurrent transition finds zero rows and raises —
the promote-fix race-guard pattern (commit e120372), applied to releases.

Released-row immutability: enforced in the DB by migration 019's trigger and
mirrored here — a released row only ever moves through the guarded
supersede/retire transitions; gate results and waivers attach to drafts only.
The reviewer-rubric event (ReviewerRubricRecorded) has no read table in P1:
the event store is its record; Wave 2 wires review_surface to it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

from corpus_kb.projections.research._common import event_payload, require_tenant
from corpus_kb.research.release.manifest import canonical_json_bytes
from corpus_kb.storage.tenant_conn import tenant_connection

logger = logging.getLogger(__name__)

SQL_INSERT_RELEASE = """
INSERT INTO codebook_releases (release_id, tenant_id, project_id, codebook_id,
    codebook_version, codebook_version_sha256, state, profile, parent_release_id,
    manifest_json, manifest_sha256, created_at, creator)
VALUES ($1, $2, $3, $4, $5, $6, 'draft_candidate', $7, $8, $9, $10, $11, $12)
ON CONFLICT (release_id) DO NOTHING
"""

SQL_UPSERT_GATE = """
INSERT INTO release_gate_results (tenant_id, release_id, gate_id, status,
    value, threshold, reason, evidence_refs, evaluator, evaluated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
ON CONFLICT (tenant_id, release_id, gate_id) DO UPDATE SET
    status = EXCLUDED.status,
    value = EXCLUDED.value,
    threshold = EXCLUDED.threshold,
    reason = EXCLUDED.reason,
    evidence_refs = EXCLUDED.evidence_refs,
    evaluator = EXCLUDED.evaluator,
    evaluated_at = EXCLUDED.evaluated_at
"""

SQL_INSERT_WAIVER = """
INSERT INTO release_waivers (tenant_id, release_id, gate_id, justification,
    approver, created_at)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (tenant_id, release_id, gate_id) DO NOTHING
"""

SQL_RELEASE = """
UPDATE codebook_releases SET state = 'released', approver = $3, released_at = $4
WHERE release_id = $1 AND tenant_id = $2
  AND (state = 'draft_candidate'
       OR (state = 'released' AND approver = $3 AND released_at = $4))
"""

SQL_SUPERSEDE = """
UPDATE codebook_releases SET state = 'superseded',
    superseded_by_release_id = $3, superseded_at = $4
WHERE release_id = $1 AND tenant_id = $2
  AND (state = 'released'
       OR (state = 'superseded' AND superseded_by_release_id = $3
           AND superseded_at = $4))
"""

SQL_RETIRE = """
UPDATE codebook_releases SET state = 'retired', retired_at = $3
WHERE release_id = $1 AND tenant_id = $2
  AND (state = 'released' OR (state = 'retired' AND retired_at = $3))
"""


class ReleaseProjector:
    """Projects release lifecycle events into migration 019's tables."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def handle(self, notification: Any) -> None:
        """Dispatch one notification on the CodebookVersion aggregate."""
        name = str(notification.topic).rsplit(".", 1)[-1]
        handlers = {
            "CodebookReleaseRequested": self.on_release_requested,
            "GateEvaluated": self.on_gate_evaluated,
            "WaiverRecorded": self.on_waiver_recorded,
            "CodebookReleased": self.on_released,
            "CodebookSuperseded": self.on_superseded,
            "CodebookRetired": self.on_retired,
            "CodebookChangeProposed": self.on_change_proposed,
        }
        handler = handlers.get(name)
        if handler is None:
            return
        await handler(notification)

    async def on_release_requested(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        manifest = payload.get("manifest_json") or {}
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                SQL_INSERT_RELEASE,
                str(payload["release_id"]),
                str(tenant_id),
                payload.get("project_id"),
                str(payload["codebook_id"]),
                str(payload["aggregate_id"]),
                str(payload.get("codebook_version_sha256", "")),
                str(payload.get("profile", "team-codebook")),
                (str(payload["parent_release_id"]) if payload.get("parent_release_id") else None),
                canonical_json_bytes(dict(manifest)).decode("utf-8"),
                str(payload["manifest_sha256"]),
                str(payload.get("requested_at", "")),
                str(payload.get("creator", "")),
            )

    async def on_gate_evaluated(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                SQL_UPSERT_GATE,
                str(tenant_id),
                str(payload["release_id"]),
                str(payload["gate_id"]),
                str(payload["status"]),
                _json_param(payload.get("value")),
                _json_param(payload.get("threshold")),
                str(payload.get("reason", "")),
                _json_param(list(payload.get("evidence_refs") or [])),
                _json_param(dict(payload.get("evaluator") or {})),
                str(payload.get("evaluated_at", "")),
            )

    async def on_waiver_recorded(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                SQL_INSERT_WAIVER,
                str(tenant_id),
                str(payload["release_id"]),
                str(payload["gate_id"]),
                str(payload["justification"]),
                str(payload["approver"]),
                str(payload.get("recorded_at", "")),
            )

    async def on_released(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        async with tenant_connection(self._pool, tenant_id) as conn:
            updated = await conn.execute(
                SQL_RELEASE,
                str(payload["release_id"]),
                str(tenant_id),
                str(payload["approver"]),
                str(payload["released_at"]),
            )
        self._require_transition(updated, "CodebookReleased", payload)

    async def on_superseded(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        async with tenant_connection(self._pool, tenant_id) as conn:
            updated = await conn.execute(
                SQL_SUPERSEDE,
                str(payload["release_id"]),
                str(tenant_id),
                str(payload["superseded_by_release_id"]),
                str(payload["superseded_at"]),
            )
        self._require_transition(updated, "CodebookSuperseded", payload)

    async def on_retired(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        async with tenant_connection(self._pool, tenant_id) as conn:
            updated = await conn.execute(
                SQL_RETIRE,
                str(payload["release_id"]),
                str(tenant_id),
                str(payload["retired_at"]),
            )
        self._require_transition(updated, "CodebookRetired", payload)

    async def on_change_proposed(self, notification: Any) -> None:
        """The successor draft is linked to its released parent (U25 data)."""
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        manifest = payload.get("successor_manifest_json") or {}
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                SQL_INSERT_RELEASE,
                str(payload["successor_release_id"]),
                str(tenant_id),
                payload.get("project_id"),
                str(payload.get("codebook_id", payload["release_id"])),
                str(payload["aggregate_id"]),
                str(payload.get("codebook_version_sha256", "")),
                str(payload.get("profile", "team-codebook")),
                str(payload["release_id"]),
                canonical_json_bytes(dict(manifest)).decode("utf-8"),
                str(payload["successor_manifest_sha256"]),
                str(payload.get("proposed_at", "")),
                str(payload.get("proposed_by", "")),
            )

    @staticmethod
    def _require_transition(result: Any, event_name: str, payload: dict[str, Any]) -> None:
        count = int(str(result).split()[-1]) if str(result).startswith("UPDATE") else 0
        if count == 0:
            raise RuntimeError(
                f"release transition {event_name} for {payload.get('release_id')} "
                "applied to zero rows: the release row is in a conflicting state "
                "(concurrent transition or missing draft)"
            )


def _json_param(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)
