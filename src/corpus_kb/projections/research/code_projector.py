"""Code projector — CodebookVersion events into 012's codebook_versions +
code_registry (RECONCILIATION: the code leg REUSES the Wave-1 tables; the
aggregate id becomes codebook_versions.version_id and each event code_id
becomes code_registry.code_id, so ON CONFLICT gives idempotency without any
parallel table). Version diffs are the replay of the aggregate chain.

JSON writes diverge per engine and are declared: Postgres keeps its
byte-identical ``jsonb_set``/``::jsonb`` forms; DuckDB has no ``jsonb_set``
(W0 spike), so top-level theory keys go through the dialect's merge-patch
shim and inserts cast ``::JSON``.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from corpus_kb.projections._gateway import resolve_gateway
from corpus_kb.projections.research._common import event_payload, require_tenant
from corpus_kb.research.conn import Store, dialect_of, json_cast
from corpus_kb.storage.dialect import Dialect

logger = logging.getLogger(__name__)


class CodeProjector:
    """Projects CodebookVersion aggregate events into the Wave-1 code tables."""

    def __init__(self, pool: Store, gateway: Any = None) -> None:
        self._store = resolve_gateway(pool, gateway)
        self._dialect: Dialect = dialect_of(self._store)

    async def on_created(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        async with self._store.transaction(str(tenant_id)) as conn:
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
        async with self._store.transaction(str(tenant_id)) as conn:
            for code in codes:
                await self._upsert_code(conn, tenant_id, version_id, code)

    async def on_definition_refined(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        async with self._store.transaction(str(tenant_id)) as conn:
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
        if self._dialect.is_duckdb:
            set_theory = self._dialect.jsonb_set_shim("theory", "'{}'::JSON", "thresholds", "$4")
        else:
            set_theory = "jsonb_set(COALESCE(theory, '{}'::jsonb), '{thresholds}', $4)"
        async with self._store.transaction(str(tenant_id)) as conn:
            await conn.execute(
                f"""
                UPDATE code_registry SET
                    theory = {set_theory}
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
        async with self._store.transaction(str(tenant_id)) as conn:
            for keyword in keywords:
                # Bare ON CONFLICT DO NOTHING: the tenant-wide unique index on
                # lower(keyword) for inclusion rows intentionally blocks a
                # second code claiming the same term — the collision is a
                # CONFLICT FLAG for codebook review (todo 17), not a DLQ
                # condition; the full set still lands on theory.keywords.
                polarity = str(keyword.get("polarity", "positive"))
                kind = "inclusion" if polarity == "positive" else "exclusion"
                await conn.execute(
                    """
                    INSERT INTO code_keywords
                    (keyword, code_id, tenant_id, kind, source)
                    VALUES ($1, $2, $3, $4, 'event-sourced')
                    ON CONFLICT DO NOTHING
                    """,
                    str(keyword.get("term", "")),
                    str(payload.get("code_id", "")),
                    str(tenant_id),
                    kind,
                )
            # The full synthesized set (scores, provisional flags, method)
            # rides code_registry.theory.keywords — version-scoped, the
            # report's read model for the governance keyword section.
            if self._dialect.is_duckdb:
                set_keywords = self._dialect.jsonb_set_shim(
                    "theory", "'{}'::JSON", "keywords", "$4"
                )
            else:
                set_keywords = (
                    "jsonb_set(\n                    COALESCE(theory, '{}'::jsonb),"
                    " '{keywords}', $4::jsonb\n                )"
                )
            await conn.execute(
                f"""
                UPDATE code_registry SET theory = {set_keywords}
                WHERE tenant_id = $1 AND codebook_version_id = $2 AND code_id = $3
                """,
                str(tenant_id),
                str(payload["aggregate_id"]),
                str(payload.get("code_id", "")),
                json.dumps(keywords),
            )

    async def on_prototypes_updated(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        # The event carries exemplar text_sha256 REFS only (vectors are derived
        # data rebuilt through the embedding cache at run time — domain/codebook).
        exemplars = payload.get("exemplar_text_sha256", [])
        if self._dialect.is_duckdb:
            set_exemplars = self._dialect.jsonb_set_shim(
                "theory", "'{}'::JSON", "exemplar_text_sha256", "$4"
            )
        else:
            set_exemplars = (
                "jsonb_set(\n                        COALESCE(theory, '{}'::jsonb),\n"
                "                        '{exemplar_text_sha256}',\n                        $4\n"
                "                    )"
            )
        async with self._store.transaction(str(tenant_id)) as conn:
            await conn.execute(
                f"""
                UPDATE code_registry SET
                    theory = {set_exemplars}
                WHERE tenant_id = $1 AND codebook_version_id = $2 AND code_id = $3
                """,
                str(tenant_id),
                str(payload["aggregate_id"]),
                str(payload.get("code_id", "")),
                json.dumps([str(sha) for sha in exemplars]),
            )

    async def _upsert_code(
        self,
        conn: Any,
        tenant_id: UUID,
        version_id: UUID,
        code: dict[str, Any],
    ) -> None:
        examples_cast = json_cast(self._dialect, "$8")
        theory_cast = json_cast(self._dialect, "$9")
        await conn.execute(
            f"""
            INSERT INTO code_registry
            (code_id, tenant_id, codebook_version_id, name, brief_definition,
             inclusion_criteria, exclusion_criteria, examples, theory)
            VALUES ($1, $2, $3, $4, $5, $6, $7, {examples_cast}, {theory_cast})
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
