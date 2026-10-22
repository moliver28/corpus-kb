"""Non-search CRUD reads (pure move out of ``query_crud.py``, 250-line rule).

``sql_query``, ``list_entities``, and ``verify_answer`` as module
functions; ``QueryHandler`` keeps its thin delegating methods so every
surface keeps its exact call shape. Logic is byte-identical to the
pre-split functions. The search family (``search_similar``,
``search_context``, ``list_documents``) stays in ``query_crud.py`` — the
determinism audit scans that file's score ORDER BY clauses and the
context-proximity pin reads it directly.

The tenant-context and gateway notes in ``query_crud``'s module docstring
apply here unchanged (same acquire shape: gateway ``transaction`` scope or
:func:`storage.tenant_conn.tenant_connection`).
"""

from __future__ import annotations

from typing import Any, cast

import asyncpg

from corpus_kb.domain.models import (
    EntityResult,
    ListEntitiesQuery,
    SQLQuery,
    VerifyAnswerQuery,
    VerifyAnswerResult,
)
from corpus_kb.rag.judge import OllamaJudge
from corpus_kb.storage.gateway import SqlGateway, is_gateway
from corpus_kb.storage.tenant_conn import tenant_connection
from corpus_kb.utils.json_safe import json_safe

_VERIFY_CHUNKS_SQL = "SELECT chunk_id, text FROM chunks WHERE tenant_id = $1 AND chunk_id = ANY($2)"
_VERIFY_CHUNKS_SQL_GATEWAY = (
    "SELECT chunk_id, text FROM chunks WHERE tenant_id = $1 AND chunk_id = ANY($2::VARCHAR[])"
)


async def sql_query(pool: asyncpg.Pool | SqlGateway, query: SQLQuery) -> list[dict[str, Any]]:
    """Execute a read-only SQL query."""
    if is_gateway(pool):
        async with pool.transaction(str(query.tenant_id)) as reader:
            rows = await reader.fetch(query.sql, *query.params.values())
        return [json_safe(dict(row)) for row in rows]
    async with tenant_connection(pool, str(query.tenant_id)) as conn:
        rows = await conn.fetch(query.sql, *query.params.values())
        return [json_safe(dict(row)) for row in rows]


async def list_entities(
    pool: asyncpg.Pool | SqlGateway, query: ListEntitiesQuery
) -> list[EntityResult]:
    """List entities, optionally filtered by type."""
    if query.entity_type:
        sql = """
            SELECT entity_id, name, entity_type, metadata
            FROM entities
            WHERE tenant_id = $1 AND entity_type = $2
            ORDER BY name ASC
            LIMIT $3
            """
        args = (str(query.tenant_id), query.entity_type, query.limit)
    else:
        sql = """
            SELECT entity_id, name, entity_type, metadata
            FROM entities
            WHERE tenant_id = $1
            ORDER BY name ASC
            LIMIT $2
            """
        args = (str(query.tenant_id), query.limit)
    if is_gateway(pool):
        async with pool.transaction(str(query.tenant_id)) as reader:
            rows = await reader.fetch(sql, *args)
        return _entities(rows)
    async with tenant_connection(pool, str(query.tenant_id)) as conn:
        rows = await conn.fetch(sql, *args)
        return _entities(rows)


def _entities(rows: list[Any]) -> list[EntityResult]:
    return [
        EntityResult(
            entity_id=row["entity_id"],
            name=row["name"],
            entity_type=row["entity_type"],
            metadata=dict(row["metadata"]) if row["metadata"] else {},
        )
        for row in rows
    ]


async def verify_answer(
    pool: asyncpg.Pool | SqlGateway, *, judge: object | None, query: VerifyAnswerQuery
) -> VerifyAnswerResult:
    """Verify an LLM-generated answer's claims against cited chunks."""
    from corpus_kb.domain.models import ClaimVerdict

    if not query.chunk_ids or judge is None:
        return VerifyAnswerResult(verdicts=[], groundedness=0.0, abstained=True)
    args = (str(query.tenant_id), [str(cid) for cid in query.chunk_ids])
    if is_gateway(pool):
        # The gateway arm carries the explicit array cast (DuckDB binds a
        # bare untyped ANY list loosely); Postgres keeps its shipped SQL.
        async with pool.transaction(str(query.tenant_id)) as reader:
            rows = await reader.fetch(_VERIFY_CHUNKS_SQL_GATEWAY, *args)
    else:
        async with tenant_connection(pool, str(query.tenant_id)) as conn:
            rows = await conn.fetch(_VERIFY_CHUNKS_SQL, *args)
    cited_texts = [row["text"] for row in rows]
    if not cited_texts:
        return VerifyAnswerResult(verdicts=[], groundedness=0.0, abstained=True)
    claims = cast("OllamaJudge", judge).decompose(query.answer)
    if not claims:
        return VerifyAnswerResult(verdicts=[], groundedness=0.0, abstained=True)
    verdicts: list[ClaimVerdict] = []
    for claim in claims:
        label, confidence = cast("OllamaJudge", judge).entail(claim, cited_texts)
        verdicts.append(
            ClaimVerdict(
                claim=claim,
                label=label,
                confidence=confidence,
                cited_chunk_ids=query.chunk_ids,
            )
        )
    entailed = sum(1 for v in verdicts if v.label == "entailed")
    return VerifyAnswerResult(
        verdicts=verdicts,
        groundedness=entailed / len(verdicts),
        abstained=False,
    )
