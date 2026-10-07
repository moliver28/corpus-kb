"""Code projector — CodebookVersion events into 012's codebook_versions +
code_registry (RECONCILIATION: the code leg REUSES the Wave-1 tables; the
aggregate id becomes codebook_versions.version_id and each event code_id
becomes code_registry.code_id, so ON CONFLICT gives idempotency without any
parallel table). Version diffs are the replay of the aggregate chain.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.projections.research._common import event_payload, require_tenant
from corpus_kb.storage.tenant_conn import tenant_connection

logger = logging.getLogger(__name__)


class CodeProjector:
    """Projects CodebookVersion aggregate events into the Wave-1 code tables."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def on_created(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO codebook_versions (version_id, tenant_id, label, sha256, paradigm)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (version_id) DO NOTHING
                """,
                str(payload["aggregate_id"]),
                str(tenant_id),
                str(payload.get("label", "")),
                str(payload.get("sha256", "")),
                str(payload.get("paradigm", "deductive")),
            )

    async def on_codes_added(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        version_id = UUID(str(payload["aggregate_id"]))
        codes = payload.get("codes", [])
        if not codes:
            return
        async with tenant_connection(self._pool, tenant_id) as conn:
            for code in codes:
                await self._upsert_code(conn, tenant_id, version_id, code)

    async def on_definition_refined(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                UPDATE code_registry SET
                    brief_definition = $4, full_definition = $4,
                    inclusion_criteria = $5, exclusion_criteria = $6
                WHERE tenant_id = $1 AND codebook_version_id = $2 AND code_id = $3
                """,
                str(tenant_id),
                str(payload["aggregate_id"]),
                str(payload.get("code_id", "")),
                str(payload.get("definition", "")),
                str(payload.get("inclusion", "")),
                str(payload.get("exclusion", "")),
            )

    async def on_thresholds(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        # tau_a/tau_qa/delta live in the theory JSONB (Wave-1's pool_floor /
        # residual_floor are pooling-calibration floors — different semantics,
        # never overwritten by coding thresholds).
        thresholds = {
            "tau_a": payload.get("tau_a"),
            "tau_qa": payload.get("tau_qa"),
            "delta": payload.get("delta"),
            "n_gold": payload.get("n_gold"),
            "cv_range": payload.get("cv_range", {}),
            "threshold_unreliable": payload.get("n_gold", 0) < 20,
        }
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                UPDATE code_registry SET
                    theory = jsonb_set(COALESCE(theory, '{}'::jsonb), '{thresholds}', $4)
                WHERE tenant_id = $1 AND codebook_version_id = $2 AND code_id = $3
                """,
                str(tenant_id),
                str(payload["aggregate_id"]),
                str(payload.get("code_id", "")),
                json.dumps(thresholds),
            )

    async def on_keywords(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        keywords = payload.get("keywords", [])
        async with tenant_connection(self._pool, tenant_id) as conn:
            for keyword in keywords:
                await conn.execute(
                    """
                    INSERT INTO code_keywords
                    (keyword, code_id, tenant_id, kind, source)
                    VALUES ($1, $2, $3, $4, 'event-sourced')
                    ON CONFLICT (keyword, code_id, kind, tenant_id) DO NOTHING
                    """,
                    str(keyword.get("term", "")),
                    str(payload.get("code_id", "")),
                    str(tenant_id),
                    str(keyword.get("polarity", "inclusion")),
                )

    async def on_prototypes_updated(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        # The event carries exemplar text_sha256 REFS only (vectors are derived
        # data rebuilt through the embedding cache at run time — domain/codebook).
        exemplars = payload.get("exemplar_text_sha256", [])
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                UPDATE code_registry SET
                    theory = jsonb_set(
                        COALESCE(theory, '{}'::jsonb),
                        '{exemplar_text_sha256}',
                        $4
                    )
                WHERE tenant_id = $1 AND codebook_version_id = $2 AND code_id = $3
                """,
                str(tenant_id),
                str(payload["aggregate_id"]),
                str(payload.get("code_id", "")),
                json.dumps([str(sha) for sha in exemplars]),
            )

    async def _upsert_code(
        self,
        conn: asyncpg.Connection,
        tenant_id: UUID,
        version_id: UUID,
        code: dict[str, Any],
    ) -> None:
        await conn.execute(
            """
            INSERT INTO code_registry
            (code_id, tenant_id, codebook_version_id, name, brief_definition,
             inclusion_criteria, exclusion_criteria, examples, theory)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb)
            ON CONFLICT (code_id, codebook_version_id, tenant_id) DO UPDATE SET
                name = EXCLUDED.name,
                brief_definition = EXCLUDED.brief_definition,
                inclusion_criteria = EXCLUDED.inclusion_criteria,
                exclusion_criteria = EXCLUDED.exclusion_criteria,
                examples = EXCLUDED.examples
            """,
            str(code.get("code_id", "")),
            str(tenant_id),
            str(version_id),
            str(code.get("name", "")),
            str(code.get("definition", "")),
            str(code.get("inclusion", "")),
            str(code.get("exclusion", "")),
            json.dumps(code.get("examples", [])),
            json.dumps(
                {
                    "mode": code.get("mode", "deductive"),
                    "is_interpretive": bool(code.get("is_interpretive", False)),
                    "allows_question_dependent": bool(code.get("allows_question_dependent", True)),
                    "parent_code_id": code.get("parent_code_id"),
                }
            ),
        )
