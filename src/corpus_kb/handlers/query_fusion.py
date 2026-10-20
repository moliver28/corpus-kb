"""Pure-Python RRF fusion — an exact port of corpus.rrf_fusion.

The source of truth is the SQL function (migrations/024_rrf_fusion_tiebreak.sql,
which replaced 006's tie order). This module mirrors it line for line:
rank is the 1-based position in each leg (``WITH ORDINALITY``), each position
contributes ``1.0 / (rrf_k + rank)`` in double precision (SQL ``float8``,
default ``rrf_k = 60``), the fused score sums both legs per chunk (dense leg
first — the SQL ``UNION ALL`` order inside ``SUM``), metadata follows the
``attrs`` rule (lexical/FTS side wins on overlap, last position wins inside
a leg), and the output is the migration-024 total order fscore DESC then
chunk id ASC (zero-drift rule 9) — never inherited from input order.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class RowLike(Protocol):
    """Row surface both ``asyncpg.Record`` and plain dicts satisfy."""

    def __getitem__(self, key: str) -> object: ...


@dataclass(frozen=True)
class FusedRow:
    """One fused hit — the five columns corpus.rrf_fusion returns."""

    chunk_id: UUID
    text: str
    source: str
    doc_id: UUID
    score: float


def _as_uuid(value: object) -> UUID:
    """SQL ``::uuid`` cast: accept a UUID or its canonical string form."""
    return value if isinstance(value, UUID) else UUID(str(value))


def _as_text(value: object) -> str:
    return value if isinstance(value, str) else "" if value is None else str(value)


def rrf_fuse(
    lexical: Sequence[RowLike],
    dense: Sequence[RowLike],
    *,
    k: int,
    rrf_k: int = 60,
) -> list[FusedRow]:
    """Fuse the two retrieval legs exactly as corpus.rrf_fusion does.

    ``k`` caps the output after ordering (SQL ``LIMIT``; negative means no
    cap, as in Postgres). ``rrf_k`` is the rank smoothing constant (SQL
    default 60). Total order: fscore DESC, then chunk id ASC.
    """
    scores: dict[UUID, float] = {}
    attrs: dict[UUID, tuple[str, str, UUID]] = {}
    # Dense leg first, so a chunk present in both legs sums dense + lexical
    # contributions in the SQL UNION ALL order. Every pass overwrites the
    # attrs entry, so the later (lexical) leg wins and, inside one leg, the
    # last position wins — exactly SQL ``ORDER BY cid, side ASC, ord DESC``.
    for rows in (dense, lexical):
        for rank, row in enumerate(rows, start=1):
            cid = _as_uuid(row["chunk_id"])
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank)
            attrs[cid] = (_as_text(row["text"]), _as_text(row["source"]), _as_uuid(row["doc_id"]))
    fused = [
        FusedRow(chunk_id=cid, text=text, source=source, doc_id=doc_id, score=scores[cid])
        for cid, (text, source, doc_id) in attrs.items()
    ]
    # cid is a UUID: Python compares it by integer value, the same byte
    # order Postgres uses for ``cid ASC``. The key is total, so input order
    # never leaks into the output.
    fused.sort(key=lambda row: (-row.score, row.chunk_id))
    return fused[:k] if k >= 0 else fused
