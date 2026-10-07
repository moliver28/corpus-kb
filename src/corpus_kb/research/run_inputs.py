"""Read-model inputs for the deductive run (todo 14).

Loads codable units with their three view vectors (A = answer unit, QA =
exchange, Q = question unit) and per-code theory (thresholds + exemplar
refs) from the research read models. All queries run inside a tenant
connection so FORCE RLS scopes every row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.projections.research._embed import _parse_pg_vector


@dataclass(frozen=True)
class UnitViews:
    """One codable unit with its three view vectors and exchange stance."""

    unit_id: int
    answer: list[float]
    qa: list[float]
    question: list[float]
    stance: str | None
    term_origin: str | None
    source_type: str | None = None


def _vecs(value: Any) -> list[float]:
    return _parse_pg_vector(value) if value else []


_UNIT_VIEW_SQL = """
SELECT u.unit_id, u.embedding AS a_vec, x.embedding AS qa_vec,
       q.embedding AS q_vec, x.stance, x.term_origin, d.source_type
FROM research_units u
JOIN research_exchanges x
  ON x.exchange_id = u.exchange_id AND x.tenant_id = u.tenant_id
JOIN documents d
  ON d.doc_id = u.doc_id
LEFT JOIN LATERAL (
    SELECT embedding FROM research_units
    WHERE exchange_id = u.exchange_id AND tenant_id = u.tenant_id
      AND role_in_exchange = 'question' AND embedding IS NOT NULL
    ORDER BY seq LIMIT 1
) q ON TRUE
WHERE u.tenant_id = $1 AND u.is_codable AND u.role_in_exchange = 'answer'
  AND u.embedding IS NOT NULL AND x.embedding IS NOT NULL
  AND ($2::uuid IS NULL OR u.project_id = $2::uuid)
"""

_GOLD_VIEW_SQL = """
SELECT u.embedding AS a_vec, x.embedding AS qa_vec, q.embedding AS q_vec,
       d.source_type
FROM research_units u
JOIN research_exchanges x
  ON x.exchange_id = u.exchange_id AND x.tenant_id = u.tenant_id
JOIN documents d
  ON d.doc_id = u.doc_id
LEFT JOIN LATERAL (
    SELECT embedding FROM research_units
    WHERE exchange_id = u.exchange_id AND tenant_id = u.tenant_id
      AND role_in_exchange = 'question' AND embedding IS NOT NULL
    ORDER BY seq LIMIT 1
) q ON TRUE
WHERE u.tenant_id = $1 AND u.text_sha256::text = ANY($2::text[])
  AND u.embedding IS NOT NULL AND x.embedding IS NOT NULL
"""


async def load_unit_views(
    conn: asyncpg.Connection, tenant_id: UUID, project_id: UUID | None
) -> list[UnitViews]:
    rows = await conn.fetch(_UNIT_VIEW_SQL, str(tenant_id), str(project_id) if project_id else None)
    return [
        UnitViews(
            unit_id=int(row["unit_id"]),
            answer=_vecs(row["a_vec"]),
            qa=_vecs(row["qa_vec"]),
            question=_vecs(row["q_vec"]),
            stance=row["stance"],
            term_origin=row["term_origin"],
            source_type=row["source_type"],
        )
        for row in rows
    ]


async def load_codes(
    conn: asyncpg.Connection, tenant_id: UUID, version_id: UUID
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT code_id, theory FROM code_registry
        WHERE tenant_id = $1 AND codebook_version_id = $2
        """,
        str(tenant_id),
        str(version_id),
    )
    codes: list[dict[str, Any]] = []
    for row in rows:
        theory = row["theory"]
        if isinstance(theory, str):
            theory = json.loads(theory or "{}")
        codes.append({"code_id": str(row["code_id"]), "theory": theory or {}})
    return codes


async def load_gold_views(
    conn: asyncpg.Connection, tenant_id: UUID, refs: list[str]
) -> list[tuple[list[float], list[float], list[float], str]]:
    """Gold exemplar (answer, qa, question) views + their document source type."""
    if not refs:
        return []
    rows = await conn.fetch(_GOLD_VIEW_SQL, str(tenant_id), refs)
    return [
        (
            _vecs(r["a_vec"]),
            _vecs(r["qa_vec"]),
            _vecs(r["q_vec"]),
            str(r["source_type"]),
        )
        for r in rows
    ]
