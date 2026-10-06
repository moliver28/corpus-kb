"""Wire speaker_roles.classify_role into chunk_status.speaker_role.

chunk_status carries a `speaker_role` column (migration 011) that nothing has
ever written: no production ingest path attaches a speaker label to a chunk
today, so this only activates for corpora that DO carry one, via
`chunks.metadata->>'speaker'`. Corpora without a speaker field are untouched
(the query that drives this returns nothing for them), keeping the module
inert rather than broken for non-transcript sources.

Kept as its own module (not folded into pooling.py, already at the 250-line
soft limit) following the same DB-facing-wiring split as
floors_calibration.py, keyword_governance_report.py, and saturation_query.py.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.coding.speaker_roles import classify_role
from corpus_kb.storage.tenant_conn import tenant_connection


async def classify_speaker_roles(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    roster: set[str] | None = None,
    vendor_threshold: int = 8,
) -> dict[str, Any]:
    """Classify and persist chunk_status.speaker_role for every speaker-tagged chunk.

    `doc_count` (how many distinct documents carry each exact speaker label)
    is computed corpus-wide for this tenant, matching classify_role's contract.
    `roster` defaults to empty: this tenant carries no participant-roster
    table today, so every chunk resolves to "moderator" or "unknown" rather
    than "participant" until a roster is threaded in -- a correct, inert
    degradation, not a broken one.

    Only touches chunks that already have a chunk_status row (written by
    reconcile_coverage's backfill), so this should run after pooling's
    coverage reconciliation for this tenant.
    """
    roster = roster or set()

    async with tenant_connection(pool, tenant_id) as conn:
        doc_counts = await conn.fetch(
            """
            SELECT metadata->>'speaker' AS speaker, COUNT(DISTINCT doc_id) AS doc_count
            FROM chunks
            WHERE tenant_id=$1 AND metadata->>'speaker' IS NOT NULL
            GROUP BY metadata->>'speaker'
            """,
            tenant_id,
        )
        doc_count_by_speaker = {row["speaker"]: row["doc_count"] for row in doc_counts}

        chunk_rows = await conn.fetch(
            """
            SELECT chunk_id, metadata->>'speaker' AS speaker
            FROM chunks
            WHERE tenant_id=$1 AND metadata->>'speaker' IS NOT NULL
            """,
            tenant_id,
        )

        role_counts: dict[str, int] = {"moderator": 0, "participant": 0, "unknown": 0}
        for row in chunk_rows:
            speaker = row["speaker"]
            role = classify_role(
                speaker,
                doc_count=doc_count_by_speaker.get(speaker, 0),
                roster=roster,
                vendor_threshold=vendor_threshold,
            )
            role_counts[role] += 1
            await conn.execute(
                "UPDATE chunk_status SET speaker_role=$1 WHERE chunk_id=$2 AND tenant_id=$3",
                role,
                row["chunk_id"],
                tenant_id,
            )

    return {
        "chunks_classified": len(chunk_rows),
        "distinct_speakers": len(doc_count_by_speaker),
        "role_counts": role_counts,
    }
