"""Project exemplar mining for ``customized`` extraction mode.

Mines high-confidence stored relations (chunk-linked, confidence-scored
— migration 008) into plain-dict LangExtract exemplars. This is the
bootstrapping ladder: the project's own best extractions teach the next
pass. When the W3 resolve flow lands, human-confirmed decisions upgrade
the same mining query's WHERE clause — no caller changes.

Names are located in the chunk text by literal search; pairs whose
mention cannot be located are skipped (a wrong char interval is worse
than a missing exemplar). Output shape matches the ``extra_examples``
contract on ``LangExtractExtractor``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_MIN_CONFIDENCE = 0.8

_MINE_SQL = """
SELECT c.text AS chunk_text,
       r.relation_type,
       r.confidence,
       es.name AS source_name,
       es.entity_type AS source_type,
       et.name AS target_name,
       et.entity_type AS target_type
FROM relations r
JOIN chunks c ON c.chunk_id = r.chunk_id AND c.tenant_id = r.tenant_id
JOIN entities es ON es.entity_id = r.source_entity_id
JOIN entities et ON et.entity_id = r.target_entity_id
WHERE r.tenant_id = $1::uuid AND r.confidence >= $2
ORDER BY r.confidence DESC, r.relation_id
LIMIT $3
"""


async def mine_exemplars(
    pool: asyncpg.Pool,
    tenant_id: str,
    limit: int = 8,
    min_confidence: float = _MIN_CONFIDENCE,
) -> list[dict[str, object]]:
    """High-confidence relation exemplars as LangExtract example dicts."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant_id)
        rows = await conn.fetch(_MINE_SQL, tenant_id, min_confidence, limit)
    exemplars: list[dict[str, object]] = []
    for row in rows:
        example = _to_example(row)
        if example is not None:
            exemplars.append(example)
    return exemplars


def _to_example(row: Any) -> dict[str, object] | None:
    text = row["chunk_text"] or ""
    source_span = _locate(text, row["source_name"])
    target_span = _locate(text, row["target_name"])
    if source_span is None or target_span is None:
        return None
    confidence = float(row["confidence"]) if row["confidence"] is not None else 0.9
    return {
        "text": text,
        "extractions": [
            {
                "extraction_class": row["source_type"],
                "extraction_text": row["source_name"],
                "start_pos": source_span[0],
                "end_pos": source_span[1],
            },
            {
                "extraction_class": row["target_type"],
                "extraction_text": row["target_name"],
                "start_pos": target_span[0],
                "end_pos": target_span[1],
            },
            {
                "extraction_class": row["relation_type"],
                "extraction_text": text[source_span[1] : target_span[0]].strip() or "relates to",
                "attributes": {
                    "subject": row["source_name"],
                    "object": row["target_name"],
                    "confidence": confidence,
                },
            },
        ],
    }


def _locate(text: str, name: object) -> tuple[int, int] | None:
    if not isinstance(name, str) or not name:
        return None
    start = text.find(name)
    if start < 0:
        start = text.lower().find(name.lower())
    if start < 0:
        return None
    return start, start + len(name)
