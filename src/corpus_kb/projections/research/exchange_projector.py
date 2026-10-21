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

from corpus_kb.projections._gateway import resolve_gateway
from corpus_kb.projections.research._common import (
    event_payload,
    lookup_turn_texts,
    opt_str,
    require_tenant,
    unit_ids_by_seqs,
    unit_texts_by_ids,
    upsert_speaker,
    upsert_unit,
)
from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.projections.research._embed_writes import (
    apply_unit_embeddings,
    embed_exchange_texts,
    turn_prefixes,
)
from corpus_kb.research.chunking import build_context_prefix
from corpus_kb.research.conn import Store, dialect_of, json_cast

logger = logging.getLogger(__name__)


class ExchangeProjector:
    """Projects Document transcript events into the exchange read models."""

    def __init__(self, pool: Store, embedder: ResearchEmbedder, gateway: Any = None) -> None:
        self._store = resolve_gateway(pool, gateway)
        self._duck = dialect_of(self._store).is_duckdb
        self._embedder = embedder

    async def on_ingested(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        metadata = payload.get("metadata", {}) or {}
        # The document leg runs INSIDE this façade (before speaker/unit/
        # exchange stages) so the FK chain documents -> speakers -> units
        # never races the legacy DocumentsProjection loop.
        metadata_cast = json_cast(dialect_of(self._store), "$8")
        async with self._store.transaction(str(tenant_id)) as conn:
            await conn.execute(
                f"""
                INSERT INTO documents
                (doc_id, tenant_id, source, source_type, file_size, file_hash,
                 language, metadata, project_id, title, source_path, source_hash,
                 parser_name, parser_version, doc_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, {metadata_cast}, $9, $10, $13, $14, $11, $12, 1)
                ON CONFLICT (tenant_id, source) DO UPDATE SET
                    source_type = EXCLUDED.source_type,
                    file_hash = COALESCE(EXCLUDED.file_hash, documents.file_hash),
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
                opt_str(payload.get("file_hash")),
                opt_str(payload.get("language")),
                json.dumps(metadata),
                opt_str(metadata.get("project_id")),
                opt_str(metadata.get("title")),
                opt_str(metadata.get("parser_name")),
                opt_str(metadata.get("parser_version")),
                str(payload.get("source", "")),
                opt_str(payload.get("file_hash")),
            )

    async def on_turns_parsed(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        doc_id = UUID(str(payload["aggregate_id"]))
        turns = payload.get("turns", [])

        shas = [str(t["text_sha256"]) for t in turns]
        texts = await lookup_turn_texts(self._store, tenant_id, shas)

        async with self._store.transaction(str(tenant_id)) as conn:
            # The Ingested stage stored project_id on the documents row;
            # TurnsParsed carries no metadata, so resolve it there — together
            # with the deterministic-prefix context (todo-12, v5 5.2).
            doc = await conn.fetchrow(
                """
                SELECT d.project_id, d.title, d.source_type, p.name AS project_name
                FROM documents d
                LEFT JOIN research_projects p ON p.project_id = d.project_id
                WHERE d.doc_id = $1
                """,
                str(doc_id),
            )
            pseudonyms = {
                row["raw_label"]: row["pseudonym"]
                for row in await conn.fetch(
                    "SELECT raw_label, pseudonym FROM research_speakers "
                    "WHERE tenant_id = $1 AND doc_id = $2",
                    str(tenant_id),
                    str(doc_id),
                )
            }
            project_id = opt_str(doc["project_id"]) if doc else None
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
        prefixes = turn_prefixes(doc, turns, pseudonyms)
        await apply_unit_embeddings(self._store, self._embedder, tenant_id, doc_id, texts, prefixes)

    async def on_exchanges_linked(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        doc_id = UUID(str(payload["aggregate_id"]))
        exchanges = payload.get("exchanges", [])

        embed_texts: dict[int, str] = {}
        unit_update_sql = (
            """
                        UPDATE research_units SET exchange_id = $4
                        WHERE tenant_id = $1 AND doc_id = $2
                          AND list_contains($3::BIGINT[], unit_id)
                        """
            if self._duck
            else """
                        UPDATE research_units SET exchange_id = $4
                        WHERE tenant_id = $1 AND doc_id = $2
                          AND unit_id = ANY($3::bigint[])
                        """
        )
        async with self._store.transaction(str(tenant_id)) as conn:
            doc = await conn.fetchrow(
                """
                SELECT d.title, d.source_type, p.name AS project_name
                FROM documents d
                LEFT JOIN research_projects p ON p.project_id = d.project_id
                WHERE d.doc_id = $1
                """,
                str(doc_id),
            )
            prefix = build_context_prefix(
                project=opt_str(doc["project_name"]) if doc else None,
                doc_title=opt_str(doc["title"]) if doc else None,
                source_type=opt_str(doc["source_type"]) if doc else None,
            )
            for exchange in exchanges:
                seq = int(exchange["seq"])
                q_ids = await unit_ids_by_seqs(
                    conn, tenant_id, doc_id, exchange.get("q_unit_seqs", []), self._duck
                )
                a_ids = await unit_ids_by_seqs(
                    conn, tenant_id, doc_id, exchange.get("a_unit_seqs", []), self._duck
                )
                question_text, qa_text = await self._cached_texts(
                    conn, tenant_id, doc_id, q_ids, a_ids
                )
                row = await conn.fetchrow(
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
                    RETURNING exchange_id
                    """,
                    str(tenant_id),
                    str(doc_id),
                    seq,
                    q_ids,
                    a_ids,
                    exchange.get("topic_id"),
                    str(exchange.get("link_method", "adjacency")),
                    opt_str(exchange.get("link_confidence")),
                    exchange.get("link_score"),
                    question_text,
                    qa_text,
                    opt_str(exchange.get("stance")),
                    opt_str(exchange.get("term_origin")),
                )
                exchange_id: int | None = None
                if row is not None:
                    exchange_id = int(row["exchange_id"])
                    # Small-to-big parent link: children resolve to this
                    # exchange directly at query time.
                    await conn.execute(
                        unit_update_sql,
                        str(tenant_id),
                        str(doc_id),
                        q_ids + a_ids,
                        exchange_id,
                    )
                if qa_text and exchange_id is not None:
                    embed_texts[exchange_id] = prefix + "\n" + qa_text
        if embed_texts:
            await embed_exchange_texts(self._store, self._embedder, tenant_id, doc_id, embed_texts)

    async def _cached_texts(
        self,
        conn: Any,
        tenant_id: UUID,
        doc_id: UUID,
        q_ids: list[int],
        a_ids: list[int],
    ) -> tuple[str | None, str | None]:
        if not q_ids and not a_ids:
            return None, None
        by_id = await unit_texts_by_ids(conn, tenant_id, doc_id, q_ids + a_ids, self._duck)
        question = " ".join(str(by_id[i]) for i in q_ids if by_id.get(i))
        answers = " ".join(str(by_id[i]) for i in a_ids if by_id.get(i))
        qa = f"Q: {question} A: {answers}" if question or answers else None
        return question or None, qa
