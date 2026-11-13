"""Child-level citation rendering for the expansion-off path (issue 201).

``research_search`` maps fused children to PARENT citations by default
(``retrieval.expand_to_parent: true``). This module is the opt-out render:
one citation per fused child, no parent collapsing, no sibling stitching.
The row shape is unchanged — the same :class:`Citation` contract — so
surfaces and snapshots see one envelope either way. The render lives in
its own module to keep ``citations.py`` inside the 250-line rule.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from corpus_kb.research.citations import Citation, _Child
from corpus_kb.research.conn import SqlConn, ids_any

__all__ = ["_child_citations"]


async def _child_citations(
    conn: SqlConn, children: list[_Child], duck: bool = False
) -> list[Citation]:
    """Render one citation per retrieved child (expansion off; issue 201).

    The child text is the answer; the highlight covers it. A child with an
    exchange link keeps the question above and the exchange seq. A plain
    unit child reports its own seq. Order is the fused order (score DESC,
    chunk id ASC; zero-drift rule 9), so repeated calls agree.
    """
    if not children:
        return []
    doc_ids = sorted({child.doc_id for child in children})
    doc_rows = await conn.fetch(
        f"""
        SELECT d.doc_id, d.title, p.name AS project_name
        FROM documents d LEFT JOIN research_projects p ON p.project_id = d.project_id
        WHERE {ids_any("$1", "d.doc_id", duck, "uuid")}
        """,
        doc_ids,
    )
    docs = {str(r["doc_id"]): (str(r["title"] or ""), r["project_name"]) for r in doc_rows}
    exchange_ids = sorted({int(c.exchange_id) for c in children if c.exchange_id is not None})
    exchanges: dict[int, Any] = {}
    if exchange_ids:
        rows = await conn.fetch(
            "SELECT exchange_id, seq, question_text FROM research_exchanges "
            f"WHERE {ids_any('$1', 'exchange_id', duck, 'bigint')}",
            exchange_ids,
        )
        for row in rows:
            exchanges[int(row["exchange_id"])] = row
    citations: list[Citation] = []
    for child in children:
        title, project_name = docs.get(child.doc_id, ("", None))
        exchange_row = None if child.exchange_id is None else exchanges.get(int(child.exchange_id))
        citations.append(
            Citation(
                doc_id=UUID(child.doc_id),
                doc_title=title,
                project_name=None if project_name is None else str(project_name),
                exchange_seq=None if exchange_row is None else int(exchange_row["seq"]),
                question_text=None if exchange_row is None else exchange_row["question_text"],
                answer_text=child.text,
                answer_highlight=(0, len(child.text)),
                matched_kind=child.kind,
                score=child.score,
                unit_seqs=() if child.unit_id is None else (child.seq,),
            )
        )
    return citations
