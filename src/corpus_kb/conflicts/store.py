"""Conflict persistence (Living Corpus W3): flag-don't-delete writes,
deterministic reads, and the human resolve path."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from corpus_kb.storage.store_utils import entity_names
from corpus_kb.storage.tenant_conn import tenant_connection

from .cascade import CascadeVerdict

if TYPE_CHECKING:
    import asyncpg

INSERT_SQL = """
INSERT INTO statement_conflicts (
    tenant_id, statement_a_id, statement_b_id, anchor_name, kind,
    gate_reached, verdict, nli_verdict, metadata
) VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9::jsonb)
ON CONFLICT (tenant_id, statement_a_id, statement_b_id, kind) DO NOTHING
RETURNING conflict_id
"""

LIST_SQL = """
SELECT conflict_id::text, statement_a_id::text, statement_b_id::text,
       anchor_name, kind, gate_reached, verdict, nli_verdict,
       resolved_verdict, resolved_by, metadata, created_at
FROM statement_conflicts
WHERE tenant_id = $1
  AND ($2::text IS NULL OR verdict = $2)
ORDER BY created_at DESC, conflict_id
LIMIT $3
"""

RESOLVE_SQL = """
UPDATE statement_conflicts
SET resolved_verdict = $2, resolved_by = $3
WHERE tenant_id = $1 AND conflict_id = $4::uuid
RETURNING statement_a_id::text, statement_b_id::text
"""

SUPERSEDE_SQL = """
UPDATE statements
SET superseded_by = $2::uuid
WHERE tenant_id = $1 AND statement_id = $3::uuid AND superseded_by IS NULL
"""

SCAN_SQL = """
SELECT statement_id::text, text, template_id, entities, valid_at, observed_at,
       superseded_by::text
FROM statements
WHERE tenant_id = $1 AND superseded_by IS NULL
ORDER BY observed_at DESC, statement_id
LIMIT 500
"""


def conflict_row(
    verdict: CascadeVerdict, tenant_id: str
) -> tuple[str, str, str, str, str, str, str, str | None, str]:
    """The 9-value insert tuple (pure data; the store owns the SQL)."""
    return (
        tenant_id,
        verdict.statement_a_id,
        verdict.statement_b_id,
        verdict.anchor_name,
        verdict.kind,
        verdict.gate_reached,
        verdict.verdict,
        verdict.nli_verdict,
        json.dumps({"gate_reached": verdict.gate_reached}),
    )


class ConflictStore:
    """Conflict writes, reads, and the resolve path over asyncpg."""

    def __init__(self, pool: asyncpg.Pool, tenant_id: str) -> None:
        self._pool = pool
        self._tenant_id = tenant_id

    async def record(self, verdicts: list[CascadeVerdict]) -> int:
        """Insert flags write-once; returns rows actually inserted."""
        written = 0
        async with tenant_connection(self._pool, self._tenant_id) as conn:
            for verdict in verdicts:
                row = conflict_row(verdict, self._tenant_id)
                inserted = await conn.fetchval(
                    INSERT_SQL,
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                )
                if inserted is not None:
                    written += 1
        return written

    async def fetch_scan_rows(self) -> list[dict[str, Any]]:
        """Live statements with entity names unfolded for the cascade."""
        async with tenant_connection(self._pool, self._tenant_id) as conn:
            rows = await conn.fetch(SCAN_SQL, self._tenant_id)
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["entity_names"] = entity_names(item.get("entities"))
            out.append(item)
        return out

    async def list_conflicts(
        self, *, verdict: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        async with tenant_connection(self._pool, self._tenant_id) as conn:
            rows = await conn.fetch(LIST_SQL, self._tenant_id, verdict, limit)
        return [dict(row) for row in rows]

    async def resolve(
        self, conflict_id: str, resolved_verdict: str, resolved_by: str
    ) -> dict[str, Any] | None:
        """Finalize one flag; a confirmed revision supersedes statement A."""
        async with tenant_connection(self._pool, self._tenant_id) as conn:
            rows = await conn.fetch(
                RESOLVE_SQL, self._tenant_id, resolved_verdict, resolved_by, conflict_id
            )
            if not rows:
                return None
            row = rows[0]
            if resolved_verdict == "revision":
                # The older claim (A) is superseded by the newer (B).
                await conn.execute(
                    SUPERSEDE_SQL,
                    self._tenant_id,
                    row["statement_b_id"],
                    row["statement_a_id"],
                )
        return dict(row)
