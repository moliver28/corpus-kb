"""Statement atom persistence (Living Corpus W2).

One SQL surface for both gateways (the DuckDB gateway translates the
``$n`` placeholders), write-once per (tenant, statement_hash, chunk),
read paths for the two catalogue ops. Statements are never updated in
place — the W3 cascade supersedes them by writing ``superseded_by``.
Row tuples are built by ``atomize.statement_row`` (pure, no SQL).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .atomize import StatementAtom, statement_row

if TYPE_CHECKING:
    import asyncpg

INSERT_SQL = """
INSERT INTO statements (
    tenant_id, chunk_id, document_id, statement_hash, text, template_id,
    template_version, start_char, end_char, entities, exchange_id,
    valid_at, observed_at, confidence, metadata
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12, $13, $14, $15::jsonb)
ON CONFLICT (tenant_id, statement_hash, chunk_id) DO NOTHING
RETURNING statement_id
"""

LIST_SQL = """
SELECT statement_id::text, chunk_id::text, document_id::text, statement_hash,
       text, template_id, template_version, start_char, end_char, entities,
       exchange_id, valid_at, observed_at, confidence, superseded_by::text, metadata
FROM statements
WHERE tenant_id = $1
  AND ($2::text IS NULL OR template_id = $2)
  AND ($3::uuid IS NULL OR document_id = $3::uuid)
ORDER BY observed_at DESC, statement_id
LIMIT $4
"""

TIMELINE_SQL = """
SELECT statement_id::text, chunk_id::text, document_id::text, statement_hash,
       text, template_id, template_version, entities,
       exchange_id, valid_at, observed_at, confidence, superseded_by::text, metadata
FROM statements
WHERE tenant_id = $1
  AND ($2::text IS NULL OR strpos(lower(text), lower($2)) > 0)
ORDER BY COALESCE(valid_at, observed_at) ASC, statement_id
LIMIT $3
"""


def _row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    if isinstance(data.get("entities"), str):
        data["entities"] = json.loads(data["entities"])
    return data


class StatementStore:
    """Statement writes and reads over one SqlGateway."""

    def __init__(self, gateway: Any, tenant_id: str) -> None:
        self._gateway = gateway
        self._tenant_id = tenant_id

    async def store_statements(
        self,
        atoms: list[StatementAtom],
        *,
        model_version: str | None = None,
        prompt_version: str | None = None,
    ) -> int:
        """Insert atoms write-once; returns rows actually inserted."""
        if not atoms:
            return 0
        written = 0
        async with self._gateway.transaction(self._tenant_id) as tx:
            for atom in atoms:
                row = statement_row(
                    atom,
                    self._tenant_id,
                    model_version=model_version,
                    prompt_version=prompt_version,
                )
                inserted = await tx.fetchval(
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
                    row[9],
                    row[10],
                    row[11],
                    row[12],
                    row[13],
                    row[14],
                )
                if inserted is not None:
                    written += 1
        return written

    async def list_statements(
        self,
        pool: asyncpg.Pool,
        *,
        template_id: str | None = None,
        document_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Deterministic newest-first page of statement rows.

        The tenant GUC rides the read: RLS on statements filters on the
        ``app.current_tenant_id`` setting, so the WHERE predicate alone
        hides every row when the setting is absent.
        """
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", self._tenant_id
            )
            rows = await conn.fetch(LIST_SQL, self._tenant_id, template_id, document_id, limit)
        return [_row_to_dict(row) for row in rows]

    async def statements_timeline(
        self,
        pool: asyncpg.Pool,
        *,
        text_query: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Chronological view (valid_at first) — the belief history view."""
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", self._tenant_id
            )
            rows = await conn.fetch(TIMELINE_SQL, self._tenant_id, text_query, limit)
        return [_row_to_dict(row) for row in rows]
