"""Keyword synthesis surface (todo 17, v5 §13) — the runner side.

Bridges keyword_synthesis' pure math to the read models + event chain:

  1. load per-code member texts (positive contrast) and the rejected
     gray-zone / overridden texts (exclusion contrast, widened by a seeded
     random sample of uncoded units);
  2. synthesize positive + exclusion keyword lists (Monroe log-odds or
     c-TF-IDF) and match HIT LOCATION (answer/question/both) per member
     unit — only answer hits count as explicit evidence downstream;
  3. persist unit-grain hits to research_keyword_hits (migration 018);
  4. emit CodebookVersion.KeywordSetUpdated events via the ResearchHandler
     (the projector lands both the legacy code_keywords rows and the
     version-scoped theory.keywords JSONB the report reads);
  5. report cross-code CONFLICT FLAGS for codebook review.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.handlers.research_handler import ResearchHandler
from corpus_kb.research.keyword_synthesis import (
    SynthesizedKeyword,
    conflict_flags,
    hit_location,
    synthesize_exclusion_keywords,
    synthesize_keywords,
)
from corpus_kb.storage.tenant_conn import tenant_connection

UNIT_TEXTS_SQL = """
SELECT u.unit_id, u.text AS answer,
       COALESCE(q.text, '') AS question
FROM research_units u
LEFT JOIN LATERAL (
    SELECT text FROM research_units
    WHERE exchange_id = u.exchange_id AND tenant_id = u.tenant_id
      AND role_in_exchange = 'question'
    ORDER BY seq LIMIT 1
) q ON TRUE
WHERE u.unit_id = ANY($1::bigint[]) AND u.tenant_id = $2
"""

REJECTED_TEXTS_SQL = """
SELECT DISTINCT u.unit_id, u.text AS answer, COALESCE(q.text, '') AS question
FROM research_assignments ra
JOIN research_units u ON u.unit_id = ra.unit_id AND u.tenant_id = ra.tenant_id
LEFT JOIN LATERAL (
    SELECT text FROM research_units
    WHERE exchange_id = u.exchange_id AND tenant_id = u.tenant_id
      AND role_in_exchange = 'question'
    ORDER BY seq LIMIT 1
) q ON TRUE
WHERE ra.cb_version_id = $1
  AND (ra.status = 'overridden'
       OR (ra.status = 'review'
           AND (ra.rationale LIKE '%gray_zone%' OR ra.rationale LIKE '%conformal%')))
"""

UNCODED_POOL_SQL = """
SELECT u.unit_id, u.text FROM research_units u
WHERE u.is_codable AND u.role_in_exchange = 'answer'
  AND NOT EXISTS (
      SELECT 1 FROM research_assignments ra
      WHERE ra.tenant_id = u.tenant_id AND ra.unit_id = u.unit_id
  )
ORDER BY u.unit_id
"""


async def run_keyword_synthesis(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    codebook_version_id: UUID,
    method: str = "log_odds",
    top_n: int = 8,
    min_support: int = 15,
    widen_n: int = 0,
    seed: int = 42,
    emit_events: bool = True,
) -> dict[str, object]:
    """Synthesize + persist keyword sets for every code in one version."""
    async with tenant_connection(pool, tenant_id) as conn:
        members = await _member_unit_ids(conn, codebook_version_id)
        member_texts = {
            code: await _unit_texts(conn, tenant_id, ids) for code, ids in members.items()
        }
        rejected_rows = await conn.fetch(REJECTED_TEXTS_SQL, str(codebook_version_id))
        uncoded = await conn.fetch(UNCODED_POOL_SQL)
    rejected_texts = [str(row["answer"]) for row in rejected_rows]
    pool_texts = [str(row["text"]) for row in uncoded]

    positives: dict[str, list[SynthesizedKeyword]] = synthesize_keywords(
        {code: [str(r["answer"]) for r in texts] for code, texts in member_texts.items()},
        method=method,
        top_n=top_n,
        min_support=min_support,
    )
    exclusions = synthesize_exclusion_keywords(
        rejected_texts,
        [str(r["answer"]) for texts in member_texts.values() for r in texts],
        widen_pool_texts=pool_texts,
        widen_n=widen_n,
        seed=seed,
        top_n=top_n,
    )
    conflicts = conflict_flags(positives, exclusions)

    hits_by_code = await _match_and_persist_hits(
        pool, tenant_id, codebook_version_id, member_texts, positives, exclusions
    )

    if emit_events:
        handler = ResearchHandler(pool)
        for code_id, keywords in positives.items():
            payload = [
                {
                    "term": kw.term,
                    "polarity": kw.kind,
                    "score": kw.score,
                    "provisional": kw.provisional,
                    "method": kw.method,
                    "support_n": kw.support_n,
                }
                for kw in keywords
            ]
            handler.set_keywords(tenant_id, codebook_version_id, UUID(code_id), payload)

    return {
        "status": "success",
        "codebook_version_id": str(codebook_version_id),
        "method": method,
        "positive_counts": {code: len(kws) for code, kws in positives.items()},
        "exclusion_count": len(exclusions),
        "conflicts": [
            {"conflict_type": c.conflict_type, "term": c.term, "codes": c.codes} for c in conflicts
        ],
        "hit_locations": hits_by_code,
        "provisional_codes": sorted(
            code for code, kws in positives.items() if kws and kws[0].provisional
        ),
    }


async def _member_unit_ids(conn: asyncpg.Connection, version_id: UUID) -> dict[str, list[int]]:
    rows = await conn.fetch(
        """
        SELECT code_id, array_agg(ra.unit_id ORDER BY ra.unit_id) AS unit_ids
        FROM research_assignments ra
        WHERE ra.cb_version_id = $1 AND ra.status IN ('auto', 'confirmed', 'review')
        GROUP BY code_id
        """,
        str(version_id),
    )
    return {str(row["code_id"]): [int(x) for x in row["unit_ids"]] for row in rows}


async def _unit_texts(
    conn: asyncpg.Connection, tenant_id: UUID, unit_ids: list[int]
) -> list[dict[str, Any]]:
    if not unit_ids:
        return []
    rows = await conn.fetch(UNIT_TEXTS_SQL, unit_ids, str(tenant_id))
    return [
        {
            "unit_id": int(row["unit_id"]),
            "answer": str(row["answer"]),
            "question": str(row["question"]),
        }
        for row in rows
    ]


async def _match_and_persist_hits(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    version_id: UUID,
    member_texts: dict[str, list[dict[str, Any]]],
    positives: dict[str, list[SynthesizedKeyword]],
    exclusions: list[SynthesizedKeyword],
) -> dict[str, dict[str, int]]:
    """Populate research_keyword_hits and return per-code hit_location counts."""
    populations: dict[str, dict[str, int]] = {}
    async with tenant_connection(pool, tenant_id) as conn:
        await conn.execute(
            "DELETE FROM research_keyword_hits WHERE tenant_id = $1 AND cb_version_id = $2",
            str(tenant_id),
            str(version_id),
        )
        for code_id, keywords in positives.items():
            for kw in keywords:
                for row in member_texts.get(code_id, []):
                    location = _locate(kw, row)
                    if location is None:
                        continue
                    await _insert_hit(
                        conn,
                        tenant_id,
                        version_id,
                        code_id,
                        kw,
                        int(row["unit_id"]),
                        location,
                    )
                    populations.setdefault(code_id, {})[location] = (
                        populations.get(code_id, {}).get(location, 0) + 1
                    )
        for kw in exclusions:
            for code_id, rows in member_texts.items():
                for row in rows:
                    location = _locate(kw, row)
                    if location is None:
                        continue
                    await _insert_hit(
                        conn,
                        tenant_id,
                        version_id,
                        code_id,
                        kw,
                        int(row["unit_id"]),
                        location,
                    )
    return populations


def _locate(kw: SynthesizedKeyword, row: dict[str, Any]) -> str | None:
    return hit_location(kw.term, str(row["answer"]), str(row["question"]))


async def _insert_hit(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    version_id: UUID,
    code_id: str,
    keyword: SynthesizedKeyword,
    unit_id: int,
    location: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO research_keyword_hits
        (tenant_id, cb_version_id, code_id, keyword, kind, unit_id, hit_location)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (tenant_id, cb_version_id, code_id, kind, keyword, unit_id) DO NOTHING
        """,
        str(tenant_id),
        str(version_id),
        code_id,
        keyword.term,
        keyword.kind,
        unit_id,
        location,
    )
