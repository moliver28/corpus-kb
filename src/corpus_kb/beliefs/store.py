"""Belief persistence (Living Corpus W4): rebuild-write and read paths.

The rebuild upserts one beliefs row per key with the exact posterior and
replaces the belief's revision log; the statements stay the source of
truth, so every log is deterministically re-derivable.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .eras import eras as segment_eras
from .math_core import DEFAULT_HALF_LIFE_DAYS, Evidence, Posterior, beta_interval
from .math_core import posterior as exact_posterior
from .rebuild import BeliefEvidence, build_evidence

if TYPE_CHECKING:
    import asyncpg

SCAN_SQL = """
SELECT s.statement_id::text, s.text, s.template_id, s.confidence,
       s.exchange_id, s.superseded_by::text, s.observed_at, s.entities,
       EXISTS (
           SELECT 1 FROM statement_conflicts c
           WHERE c.tenant_id = s.tenant_id
             AND c.verdict = 'conflict'
             AND (c.statement_a_id = s.statement_id OR c.statement_b_id = s.statement_id)
       ) AS conflicted
FROM statements s
WHERE s.tenant_id = $1
ORDER BY s.observed_at ASC, s.statement_id
LIMIT 2000
"""

UPSERT_BELIEF_SQL = """
INSERT INTO beliefs (
    tenant_id, subject, relation, object, alpha, beta,
    evidence_count, era_count, prior_strength, updated_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
ON CONFLICT (tenant_id, subject, relation, object) DO UPDATE SET
    alpha = EXCLUDED.alpha, beta = EXCLUDED.beta,
    evidence_count = EXCLUDED.evidence_count, era_count = EXCLUDED.era_count,
    prior_strength = EXCLUDED.prior_strength, updated_at = NOW()
RETURNING belief_id::text
"""

CURRENT_SQL = """
SELECT belief_id::text, subject, relation, object, alpha, beta,
       evidence_count, era_count, updated_at
FROM beliefs
WHERE tenant_id = $1
  AND ($2::text IS NULL OR subject = $2)
ORDER BY updated_at DESC, belief_id
LIMIT $3
"""

TIMELINE_SQL = """
SELECT stance, weight, observed_at
FROM belief_revisions
WHERE tenant_id = $1 AND belief_id = $2::uuid
ORDER BY observed_at ASC, revision_id
"""


def _names(item: dict[str, Any]) -> list[str]:
    entities = item.get("entities")
    if isinstance(entities, str):
        entities = json.loads(entities)
    return [e.get("name", "") for e in (entities or []) if isinstance(e, dict)]


def _tenant_ratio(statements: list[dict[str, Any]]) -> float | None:
    from .rebuild import stance_of

    stances = [stance_of(str(r.get("text", ""))) for r in statements]
    known = [s for s in stances if s]
    if not known:
        return None
    return known.count("positive") / len(known)


def _post_dict(post: Posterior) -> dict[str, Any]:
    return {
        "alpha": post.alpha,
        "beta": post.beta,
        "mean": post.mean,
        "interval_low": post.interval_low,
        "interval_high": post.interval_high,
        "evidence_count": post.evidence_count,
        "era_count": post.era_count,
    }


class BeliefStore:
    """Belief rebuild and reads over asyncpg."""

    def __init__(self, pool: asyncpg.Pool, tenant_id: str) -> None:
        self._pool = pool
        self._tenant_id = tenant_id

    async def rebuild(self, *, half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> dict[str, Any]:
        """Recompute every belief from the statements; returns the summary."""
        statements = await self._scan_statements()
        ratio = _tenant_ratio(statements)
        bundles: list[BeliefEvidence] = build_evidence(statements)
        now = datetime.now(UTC)
        written = 0
        for bundle in bundles:
            stances = [item for item, _sid in bundle.items]
            post = exact_posterior(
                stances,
                now=now,
                half_life_days=half_life_days,
                tenant_positive_ratio=ratio,
                era_count=len(segment_eras(stances)),
            )
            await self._write_belief(bundle, post)
            written += 1
        return {"status": "success", "beliefs": written, "tenant_ratio": ratio}

    async def _scan_statements(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", self._tenant_id
            )
            raw = await conn.fetch(SCAN_SQL, self._tenant_id)
        out = []
        for row in raw:
            item = dict(row)
            item["entity_names"] = _names(item)
            out.append(item)
        return out

    async def _write_belief(self, bundle: BeliefEvidence, post: Posterior) -> None:
        key = bundle.key
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", self._tenant_id
            )
            belief_id = await conn.fetchval(
                UPSERT_BELIEF_SQL,
                self._tenant_id,
                key.subject,
                key.relation,
                key.object,
                post.alpha,
                post.beta,
                post.evidence_count,
                post.era_count,
                6.0,
            )
            await conn.execute(
                "DELETE FROM belief_revisions WHERE tenant_id = $1 AND belief_id = $2::uuid",
                self._tenant_id,
                belief_id,
            )
            for item, statement_id in bundle.items:
                await conn.execute(
                    "INSERT INTO belief_revisions (tenant_id, belief_id, statement_id,"
                    " stance, weight, observed_at) VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6)",
                    self._tenant_id,
                    belief_id,
                    statement_id,
                    item.stance,
                    item.weight,
                    item.observed_at,
                )

    async def current(self, *, subject: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", self._tenant_id
            )
            rows = await conn.fetch(CURRENT_SQL, self._tenant_id, subject, limit)
        return [dict(row) for row in rows]

    async def timeline(
        self,
        *,
        subject: str,
        relation: str,
        object_name: str,
    ) -> dict[str, Any] | None:
        """The eras view for one belief (the ice-cream history).

        The reported posterior is the STORED one (pooled at rebuild), so
        timeline and current always agree on alpha/beta.
        """
        belief_id = await self._belief_id(subject, relation, object_name)
        if belief_id is None:
            return None
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", self._tenant_id
            )
            rows = await conn.fetch(TIMELINE_SQL, self._tenant_id, belief_id)
            stored = await conn.fetchrow(
                "SELECT alpha, beta, evidence_count, era_count FROM beliefs WHERE belief_id = $1",
                belief_id,
            )
        items = [
            Evidence(
                stance=row["stance"],
                weight=float(row["weight"]),
                observed_at=row["observed_at"],
            )
            for row in rows
        ]
        segments = segment_eras(items)
        # The STORED alpha/beta (pooled at rebuild) — recomputing here
        # without the tenant prior would disagree with beliefs_current.
        total = float(stored["alpha"]) + float(stored["beta"])
        low, high = beta_interval(float(stored["alpha"]), float(stored["beta"]))
        post = Posterior(
            alpha=float(stored["alpha"]),
            beta=float(stored["beta"]),
            mean=round(float(stored["alpha"]) / total, 6) if total else 0.5,
            interval_low=low,
            interval_high=high,
            evidence_count=int(stored["evidence_count"]),
            era_count=int(stored["era_count"]),
        )
        return {
            "belief_id": belief_id,
            "eras": [
                {
                    "start": e.start.isoformat(),
                    "end": e.end.isoformat(),
                    "positives": e.positives,
                    "negatives": e.negatives,
                    "mean": e.mean,
                }
                for e in segments
            ],
            "posterior": _post_dict(post),
        }

    async def _belief_id(self, subject: str, relation: str, object_name: str) -> str | None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", self._tenant_id
            )
            return await conn.fetchval(
                "SELECT belief_id::text FROM beliefs"
                " WHERE tenant_id = $1 AND subject = $2 AND relation = $3 AND object = $4",
                self._tenant_id,
                subject,
                relation,
                object_name,
            )
