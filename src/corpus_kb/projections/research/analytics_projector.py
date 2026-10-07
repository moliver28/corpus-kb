"""Analytics projector — CodingRun checkpoints + per-unit signals (todo-11 (c))."""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

from corpus_kb.projections.research._common import event_payload, require_tenant
from corpus_kb.storage.tenant_conn import tenant_connection

logger = logging.getLogger(__name__)


class AnalyticsProjector:
    """Projects CodingRun and CodingAssignment signal events."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def on_run_started(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO research_runs
                (run_id, tenant_id, llm_name, llm_version, embed_model,
                 model_revision, params, state)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, 'started')
                ON CONFLICT (run_id) DO NOTHING
                """,
                str(payload["aggregate_id"]),
                str(tenant_id),
                str(payload.get("llm_name", "")),
                str(payload.get("llm_version", "")),
                str(payload.get("embed_model", "")),
                str(payload.get("model_revision", "")),
                json.dumps(payload.get("params", {})),
            )

    async def on_checkpoint(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        # The event method param is `payload` (CodingRun.add_checkpoint), so
        # the checkpoint body rides the event under that key — never the
        # whole event dict (its auto timestamp is not JSON-serializable).
        checkpoint_body = payload.get("payload", {})
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                UPDATE research_runs SET state = 'checkpoint',
                    checkpoint = $3::jsonb
                WHERE run_id = $1 AND tenant_id = $2
                """,
                str(payload["aggregate_id"]),
                str(tenant_id),
                json.dumps(checkpoint_body),
            )

    async def on_run_stopped(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                UPDATE research_runs SET state = 'stopped', stopped_at = NOW()
                WHERE run_id = $1 AND tenant_id = $2
                """,
                str(payload["aggregate_id"]),
                str(tenant_id),
            )

    async def on_signals(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        signals = payload.get("signals", [])
        async with tenant_connection(self._pool, tenant_id) as conn:
            for signal in signals:
                await conn.execute(
                    """
                    INSERT INTO research_signals
                    (tenant_id, unit_id, run_id, tier, token_entropy, hedge_flag,
                     soft_cluster_entropy, semantic_entropy, n_clusters, link_score, extra)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                    ON CONFLICT (tenant_id, unit_id, run_id, tier) DO UPDATE SET
                        token_entropy = EXCLUDED.token_entropy,
                        hedge_flag = EXCLUDED.hedge_flag,
                        soft_cluster_entropy = EXCLUDED.soft_cluster_entropy,
                        semantic_entropy = EXCLUDED.semantic_entropy,
                        n_clusters = EXCLUDED.n_clusters,
                        link_score = EXCLUDED.link_score,
                        extra = EXCLUDED.extra
                    """,
                    str(tenant_id),
                    int(payload.get("unit_id", 0)),
                    _opt_str(payload.get("run_id")),
                    int(signal.get("tier", 0)),
                    signal.get("token_entropy"),
                    signal.get("hedge_flag"),
                    signal.get("soft_cluster_entropy"),
                    signal.get("semantic_entropy"),
                    signal.get("n_clusters"),
                    signal.get("link_score"),
                    json.dumps(signal.get("extra", {})),
                )


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)
