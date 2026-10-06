"""Exchange projector — document enrichment, speakers, units, exchanges.

Internal ordering rule (todo-11 (c)): document/speaker/unit rows land BEFORE
exchange rows, and the exchange projector reads units back to resolve
q/a seq -> unit_id and to cache question_text/qa_text. Link corrections are
NEW ExchangesLinked events projected as updates, never rewrites of history.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.projections.research._common import (
    event_payload,
    lookup_turn_texts,
    require_tenant,
    upsert_speaker,
    upsert_unit,
)
from corpus_kb.projections.research._embed import ResearchEmbedder, apply_unit_embeddings
from corpus_kb.storage.tenant_conn import tenant_connection

logger = logging.getLogger(__name__)


class ExchangeProjector:
    """Projects Document transcript events into the exchange read models."""

    def __init__(self, pool: asyncpg.Pool, embedder: ResearchEmbedder) -> None:
        self._pool = pool
        self._embedder = embedder

    async def on_ingested(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        metadata = payload.get("metadata", {}) or {}
        # The document leg runs INSIDE this façade (before speaker/unit/
        # exchange stages) so the FK chain documents -> speakers -> units
        # never races the legacy DocumentsProjection loop.
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO documents
                (doc_id, tenant_id, source, source_type, file_size, file_hash,
                 language, metadata, project_id, title, source_path, source_hash,
                 parser_name, parser_version, doc_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $13, $14, $11, $12, 1)
                ON CONFLICT (tenant_id, source) DO UPDATE SET
                    source_type = EXCLUDED.source_type,
                    file_hash = EXCLUDED.file_hash,
                    metadata = EXCLUDED.metadata,
                    project_id = COALESCE(EXCLUDED.project_id, documents.project_id),
                    title = COALESCE(EXCLUDED.title, documents.title),
                    source_hash = COALESCE(EXCLUDED.source_hash, documents.source_hash),
                    parser_name = COALESCE(EXCLUDED.parser_name, documents.parser_name),
                    parser_version = COALESCE(EXCLUDED.parser_version, documents.parser_version),
                    updated_at = NOW()
                """,
                str(payload["aggregate_id"]),
                str(tenant_id),
                str(payload.get("source", "")),
                str(payload.get("source_type", "text")),
                payload.get("file_size"),
                _opt_str(payload.get("file_hash")),
                _opt_str(payload.get("language")),
                json.dumps(metadata),
                _opt_str(metadata.get("project_id")),
                _opt_str(metadata.get("title")),
                _opt_str(metadata.get("parser_name")),
                _opt_str(metadata.get("parser_version")),
                str(payload.get("source", "")),
                _opt_str(payload.get("file_hash")),
            )

    async def on_turns_parsed(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        doc_id = UUID(str(payload["aggregate_id"]))
        turns = payload.get("turns", [])

        shas = [str(t["text_sha256"]) for t in turns]
        texts = await lookup_turn_texts(self._pool, tenant_id, shas)

        async with tenant_connection(self._pool, tenant_id) as conn:
            # The Ingested stage stored project_id on the documents row;
            # TurnsParsed carries no metadata, so resolve it there.
            project_id = _opt_str(
                await conn.fetchval(
                    "SELECT project_id FROM documents WHERE doc_id = $1", str(doc_id)
                )
            )
            for turn in turns:
                await upsert_speaker(conn, tenant_id, doc_id, turn)
                if str(turn["text_sha256"]) not in texts:
                    logger.warning(
                        "TurnsParsed references unknown text_sha256 %s (doc %s seq %s); "
                        "skipping unit — the content-addressed store is ingest-owned",
                        turn["text_sha256"],
                        doc_id,
                        turn.get("seq"),
                    )
                    continue
                await upsert_unit(conn, tenant_id, doc_id, project_id, turn, texts)
        await apply_unit_embeddings(self._pool, self._embedder, tenant_id, doc_id, texts)

    async def on_exchanges_linked(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        doc_id = UUID(str(payload["aggregate_id"]))
        exchanges = payload.get("exchanges", [])

        async with tenant_connection(self._pool, tenant_id) as conn:
            for exchange in exchanges:
                seq = int(exchange["seq"])
                q_ids = await self._unit_ids(
                    conn, tenant_id, doc_id, exchange.get("q_unit_seqs", [])
                )
                a_ids = await self._unit_ids(
                    conn, tenant_id, doc_id, exchange.get("a_unit_seqs", [])
                )
                question_text, qa_text = await self._cached_texts(
                    conn, tenant_id, doc_id, q_ids, a_ids
                )
                await conn.execute(
                    """
                    INSERT INTO research_exchanges
                    (tenant_id, doc_id, seq, q_unit_ids, a_unit_ids, topic_id,
                     link_method, link_confidence, link_score, question_text, qa_text,
                     stance, term_origin)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                    ON CONFLICT (tenant_id, doc_id, seq) DO UPDATE SET
                        q_unit_ids = EXCLUDED.q_unit_ids,
                        a_unit_ids = EXCLUDED.a_unit_ids,
                        topic_id = EXCLUDED.topic_id,
                        link_method = EXCLUDED.link_method,
                        link_confidence = EXCLUDED.link_confidence,
                        link_score = EXCLUDED.link_score,
                        question_text = EXCLUDED.question_text,
                        qa_text = EXCLUDED.qa_text,
                        stance = EXCLUDED.stance,
                        term_origin = EXCLUDED.term_origin
                    """,
                    str(tenant_id),
                    str(doc_id),
                    seq,
                    q_ids,
                    a_ids,
                    exchange.get("topic_id"),
                    str(exchange.get("link_method", "adjacency")),
                    _opt_str(exchange.get("link_confidence")),
                    exchange.get("link_score"),
                    question_text,
                    qa_text,
                    _opt_str(exchange.get("stance")),
                    _opt_str(exchange.get("term_origin")),
                )

    async def _unit_ids(
        self, conn: asyncpg.Connection, tenant_id: UUID, doc_id: UUID, seqs: Any
    ) -> list[int]:
        rows = await conn.fetch(
            """
            SELECT unit_id FROM research_units
            WHERE tenant_id = $1 AND doc_id = $2 AND seq = ANY($3)
            ORDER BY seq
            """,
            str(tenant_id),
            str(doc_id),
            [int(s) for s in seqs],
        )
        return [int(r["unit_id"]) for r in rows]

    async def _cached_texts(
        self,
        conn: asyncpg.Connection,
        tenant_id: UUID,
        doc_id: UUID,
        q_ids: list[int],
        a_ids: list[int],
    ) -> tuple[str | None, str | None]:
        if not q_ids and not a_ids:
            return None, None
        rows = await conn.fetch(
            """
            SELECT unit_id, text, role_in_exchange FROM research_units
            WHERE tenant_id = $1 AND doc_id = $2 AND unit_id = ANY($3)
            """,
            str(tenant_id),
            str(doc_id),
            q_ids + a_ids,
        )
        by_id = {int(r["unit_id"]): r["text"] for r in rows}
        question = " ".join(by_id[i] for i in q_ids if by_id.get(i))
        answers = " ".join(by_id[i] for i in a_ids if by_id.get(i))
        qa = f"Q: {question} A: {answers}" if question or answers else None
        return question or None, qa


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)
