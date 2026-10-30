"""Shared helpers for the research projectors (todo-11 (c)).

Every session opens through the storage seam: ``resolve_store`` in the
projector constructors, :func:`tenant_scope` in the module functions —
tenant GUC + one transaction on Postgres, the gateway's BEGIN..COMMIT
scope on DuckDB. SQL that diverges per engine is declared inline (the
``ANY`` list-membership form only).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from corpus_kb.contracts.model import DEFAULT_TENANT_ID
from corpus_kb.projections.event_reader import DomainNotification
from corpus_kb.research.conn import Store, dialect_of, tenant_scope

logger = logging.getLogger(__name__)

RESEARCH_PROJECTION_NAME = "ResearchProjection"

DEFAULT_TENANT = UUID(DEFAULT_TENANT_ID)


def event_payload(notification: DomainNotification) -> dict[str, Any]:
    """Decoded event attrs as a plain dict, with aggregate_id attached."""
    payload = {
        key: value for key, value in vars(notification.event).items() if not key.startswith("_")
    }
    payload["aggregate_id"] = notification.originator_id
    return payload


def require_tenant(payload: dict[str, Any], notification: DomainNotification) -> UUID:
    """TENANT CONTRACT (r8): research events carry tenant_id; assert presence."""
    tenant = payload.get("tenant_id")
    if tenant is None:
        raise ValueError(
            f"event {notification.topic} missing tenant_id "
            f"(notification_id={notification.notification_id})"
        )
    return UUID(str(tenant))


async def store_turn_texts(
    store: Store,
    tenant_id: UUID,
    texts_by_sha: dict[str, str],
    media_type: str = "text",
) -> int:
    """Write turn texts ONCE to the immutable content-addressed store.

    Ingest-owned (NOT a read model): excluded from the rebuild drop-set.
    Idempotent by PK; existing entries are never rewritten (immutability).
    """
    if not texts_by_sha:
        return 0
    written = 0
    async with tenant_scope(store, tenant_id) as conn:
        for text_sha, text in texts_by_sha.items():
            result = await conn.execute(
                """
                INSERT INTO research_transcript_text (tenant_id, text_sha256, text, media_type)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (tenant_id, text_sha256) DO NOTHING
                """,
                str(tenant_id),
                text_sha,
                text,
                media_type,
            )
            written += 0 if str(result).endswith("0") else 1
    return written


_LOOKUP_SHA_ANY_SQL = """
            SELECT text_sha256, text FROM research_transcript_text
            WHERE tenant_id = $1 AND text_sha256 = ANY($2)
            """

_LOOKUP_SHA_LIST_SQL = """
            SELECT text_sha256, text FROM research_transcript_text
            WHERE tenant_id = $1 AND list_contains($2::VARCHAR[], text_sha256)
            """


async def lookup_turn_texts(store: Store, tenant_id: UUID, shas: list[str]) -> dict[str, str]:
    """Resolve text_sha256 -> text for projection-side caching (read-only)."""
    if not shas:
        return {}
    sql = _LOOKUP_SHA_LIST_SQL if dialect_of(store).is_duckdb else _LOOKUP_SHA_ANY_SQL
    async with tenant_scope(store, tenant_id) as conn:
        rows = await conn.fetch(sql, str(tenant_id), shas)
    return {row["text_sha256"]: row["text"] for row in rows}


async def upsert_speaker(conn: Any, tenant_id: UUID, doc_id: UUID, turn: dict[str, Any]) -> None:
    await conn.execute(
        """
        INSERT INTO research_speakers (tenant_id, doc_id, raw_label, role, role_basis)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (tenant_id, doc_id, raw_label) DO UPDATE SET
            role = EXCLUDED.role,
            role_basis = EXCLUDED.role_basis
        """,
        str(tenant_id),
        str(doc_id),
        str(turn.get("speaker", "unknown")),
        str(turn.get("role", "unknown")),
        turn.get("role_basis"),
    )


async def upsert_unit(
    conn: Any,
    tenant_id: UUID,
    doc_id: UUID,
    project_id: str | None,
    turn: dict[str, Any],
    texts: dict[str, str],
) -> None:
    sha = str(turn["text_sha256"])
    await conn.execute(
        """
        INSERT INTO research_units
        (tenant_id, doc_id, project_id, speaker_id, seq, t_start, t_end,
         text, text_sha256, role_in_exchange, is_codable, turn_type,
         start_char, end_char)
        VALUES ($1, $2, $3,
                (SELECT speaker_id FROM research_speakers
                 WHERE tenant_id = $1 AND doc_id = $2 AND raw_label = $4),
                $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
        ON CONFLICT (tenant_id, doc_id, seq) DO NOTHING
        """,
        str(tenant_id),
        str(doc_id),
        project_id,
        str(turn.get("speaker", "unknown")),
        int(turn["seq"]),
        turn.get("t_start"),
        turn.get("t_end"),
        texts.get(sha),
        sha,
        str(turn.get("role_in_exchange", "other")),
        bool(turn.get("is_codable", True)),
        turn.get("turn_type"),
        turn.get("start_char"),
        turn.get("end_char"),
    )


# List-membership over BIGINT ids: asyncpg binds the list for ``ANY``;
# DuckDB has no ``= ANY``, so the twin casts the list and uses the engine's
# ``list_contains`` (the rag/parent_expand precedent).
_SEQ_ANY_SQL = "seq = ANY($3)"
_SEQ_LIST_SQL = "list_contains($3::BIGINT[], seq)"
_UNIT_ANY_SQL = "unit_id = ANY($3)"
_UNIT_LIST_SQL = "list_contains($3::BIGINT[], unit_id)"


async def unit_ids_by_seqs(
    conn: Any, tenant_id: UUID, doc_id: UUID, seqs: Any, duck: bool
) -> list[int]:
    """Resolve turn seqs to unit ids, ascending by seq."""
    rows = await conn.fetch(
        f"""
        SELECT unit_id FROM research_units
        WHERE tenant_id = $1 AND doc_id = $2 AND {_SEQ_LIST_SQL if duck else _SEQ_ANY_SQL}
        ORDER BY seq
        """,
        str(tenant_id),
        str(doc_id),
        [int(s) for s in seqs],
    )
    return [int(r["unit_id"]) for r in rows]


async def unit_texts_by_ids(
    conn: Any, tenant_id: UUID, doc_id: UUID, unit_ids: list[int], duck: bool
) -> dict[int, Any]:
    """Read unit texts for the given ids (question/qa cache resolution)."""
    rows = await conn.fetch(
        f"""
        SELECT unit_id, text, role_in_exchange FROM research_units
        WHERE tenant_id = $1 AND doc_id = $2 AND {_UNIT_LIST_SQL if duck else _UNIT_ANY_SQL}
        """,
        str(tenant_id),
        str(doc_id),
        unit_ids,
    )
    return {int(r["unit_id"]): r["text"] for r in rows}


def opt_str(value: Any) -> str | None:
    """None-safe str coercion (the projectors' shared row-value shape)."""
    return None if value is None else str(value)
