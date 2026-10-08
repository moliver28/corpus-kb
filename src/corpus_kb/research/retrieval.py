"""Parent-child retrieval over the research read models (todo 12, v5 §6).

Hybrid dense + lexical over retrieval CHILDREN (v5 §5.2: units of kind
answer/question, exchanges of kind qa), fused with the house RRF function
(``corpus.rrf_fusion``, k=60 — migration 006 precedent), optionally
cross-encoder reranked, then mapped to PARENTS (the exchange, or the
sliding window for facilitator-less sources): search children, return the
parent exchange with the question above and the answer highlighted.

Every query is scoped by tenant (RLS) AND ``project_id``; optional filters:
``source_type`` (documents.source_type), ``speaker_role``, ``doc_ids``,
``topic_id``. Guide-item scoping is DEFERRED (r9: no filter over an
unpopulated column).

QUERY-side embeddings route through ``instruct()`` (Wave-1 Embedder
Protocol): the instruction prefix applies to the query text only; children
were embedded RAW + deterministic-prefix at projection time.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, replace
from typing import cast
from uuid import UUID, uuid5

import asyncpg

from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.research.chunking import CHILD_QA, meeting_windows, unit_kind
from corpus_kb.storage.tenant_conn import tenant_connection

logger = logging.getLogger(__name__)

RRF_K = 60
CANDIDATE_MULTIPLIER = 8
RERANK_MIN_CANDIDATES = 30
_RRF_NAMESPACE = UUID("6f6f6f6f-6f6f-6f6f-6f6f-6f6f6f6f6f6f")

# Assignment-confidence floors (todo-18 notebook filter): --min-confidence X
# admits X and every STRONGER level.
CONFIDENCE_FLOORS: dict[str, tuple[str, ...]] = {
    "high": ("high",),
    "medium": ("high", "medium"),
    "low": ("high", "medium", "low"),
}


@dataclass(frozen=True)
class ResearchQuery:
    """A project-scoped retrieval query (tenant + project mandatory)."""

    query: str
    tenant_id: UUID
    project_id: UUID
    k: int = 10
    source_type: str | None = None
    speaker_role: str | None = None
    doc_ids: tuple[UUID, ...] | None = None
    topic_id: int | None = None
    code_id: str | None = None
    min_confidence: str | None = None
    review_status: str | None = None
    include_exchange_children: bool = True
    rerank: bool = True


@dataclass(frozen=True)
class Citation:
    """One parent result: question above, answer highlighted."""

    doc_id: UUID
    doc_title: str
    project_name: str | None
    exchange_seq: int | None
    question_text: str | None
    answer_text: str
    answer_highlight: tuple[int, int]
    matched_kind: str
    score: float
    unit_seqs: tuple[int, ...] = ()

    def highlighted_answer(self) -> str:
        """Answer with the highlighted span bracketed (surface rendering)."""
        start, end = self.answer_highlight
        return (
            self.answer_text[:start]
            + "["
            + self.answer_text[start:end]
            + "]"
            + self.answer_text[end:]
        )


class _Sql:
    """Numbered-parameter builder: every value gets a distinct placeholder."""

    def __init__(self, tenant_id: str, project_id: str) -> None:
        self.values: list[object] = [tenant_id, project_id]

    def p(self, value: object) -> str:
        self.values.append(value)
        return f"${len(self.values)}"


@dataclass
class _Child:
    key: str
    kind: str
    doc_id: str
    unit_id: int | None = None
    exchange_id: int | None = None
    seq: int = 0
    text: str = ""
    score: float = 0.0


def _child_uuid(key: str) -> str:
    return str(uuid5(_RRF_NAMESPACE, f"corpus-research:{key}"))


async def research_search(
    pool: asyncpg.Pool,
    embedder: ResearchEmbedder,
    q: ResearchQuery,
    reranker: object | None = None,
) -> list[Citation]:
    """Search retrieval children, return parent citations (small-to-big)."""
    from corpus_kb.rag.embedder import instruct

    depth = max(q.k * CANDIDATE_MULTIPLIER, RERANK_MIN_CANDIDATES)
    query_vector: list[float] | None = await embedder.embed_cached(q.tenant_id, instruct(q.query))

    async with tenant_connection(pool, q.tenant_id) as conn:
        dense_units, fts_units = await _unit_arms(conn, query_vector, q, depth)
        dense_ex: list[dict[str, object]] = []
        fts_ex: list[dict[str, object]] = []
        if q.include_exchange_children:
            dense_ex, fts_ex = await _exchange_arms(conn, query_vector, q, depth)

        units_fused = await _rrf(conn, dense_units, fts_units, depth)
        exch_fused = await _rrf(conn, dense_ex, fts_ex, depth)
        fused = await _rrf(conn, units_fused, exch_fused, depth)

        children = _children_from_fused(fused)
        windows = await _meeting_windows(conn, children)
        parents = _to_parents(children, windows)
        citations = await _citations(conn, parents, windows)

    if reranker is not None and q.rerank and citations:
        citations = await _rerank(q.query, citations, reranker)
    return citations[: q.k]


def _assignment_predicate(sql: _Sql, q: ResearchQuery) -> list[str]:
    """WHERE fragments for the assignment-grain filters (code/confidence/status)."""
    conditions: list[str] = []
    if q.code_id:
        conditions.append(f"ra.code_id = {sql.p(str(q.code_id))}")
    if q.min_confidence:
        levels = CONFIDENCE_FLOORS[q.min_confidence]
        placeholders = ", ".join(sql.p(level) for level in levels)
        conditions.append(f"ra.confidence IN ({placeholders})")
    if q.review_status:
        conditions.append(f"ra.status = {sql.p(str(q.review_status))}")
    return conditions


def _unit_filters(sql: _Sql, q: ResearchQuery) -> str:
    frag = ""
    if q.source_type:
        frag += f" AND d.source_type = {sql.p(q.source_type)}"
    if q.speaker_role:
        frag += f" AND s.role = {sql.p(q.speaker_role)}"
    if q.doc_ids:
        frag += f" AND u.doc_id = ANY({sql.p([str(d) for d in q.doc_ids])}::uuid[])"
    if q.topic_id is not None:
        frag += (
            " AND u.exchange_id IN (SELECT exchange_id FROM research_exchanges "
            f"WHERE tenant_id = u.tenant_id AND topic_id = {sql.p(int(q.topic_id))})"
        )
    assignment = _assignment_predicate(sql, q)
    if assignment:
        frag += (
            " AND u.unit_id IN (SELECT ra.unit_id FROM research_assignments ra "
            f"WHERE ra.tenant_id = u.tenant_id AND {' AND '.join(assignment)})"
        )
    return frag


def _exchange_filters(sql: _Sql, q: ResearchQuery) -> str:
    frag = ""
    if q.source_type:
        frag += f" AND d.source_type = {sql.p(q.source_type)}"
    if q.speaker_role:
        frag += (
            " AND EXISTS (SELECT 1 FROM research_units u2 "
            "JOIN research_speakers s2 ON s2.speaker_id = u2.speaker_id "
            f"WHERE u2.unit_id = ANY(e.a_unit_ids) AND s2.role = {sql.p(q.speaker_role)})"
        )
    if q.doc_ids:
        frag += f" AND e.doc_id = ANY({sql.p([str(d) for d in q.doc_ids])}::uuid[])"
    if q.topic_id is not None:
        frag += f" AND e.topic_id = {sql.p(int(q.topic_id))}"
    assignment = _assignment_predicate(sql, q)
    if assignment:
        frag += (
            " AND EXISTS (SELECT 1 FROM research_assignments ra "
            "WHERE ra.tenant_id = e.tenant_id AND ra.unit_id = ANY(e.a_unit_ids) "
            f"AND {' AND '.join(assignment)})"
        )
    return frag


async def _unit_arms(
    conn: asyncpg.Connection,
    query_vector: list[float] | None,
    q: ResearchQuery,
    depth: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    dense_sql = _Sql(str(q.tenant_id), str(q.project_id))
    filters = _unit_filters(dense_sql, q)
    dense: list[dict[str, object]] = []
    if query_vector is not None:
        vec = dense_sql.p(str(query_vector))
        limit = dense_sql.p(depth)
        rows = await conn.fetch(
            f"""
            SELECT u.unit_id, u.doc_id, u.exchange_id, u.seq, u.text, u.role_in_exchange,
                   1 - (u.embedding::halfvec(1024) <=> {vec}::halfvec(1024)) AS score
            FROM research_units u
            JOIN documents d ON d.doc_id = u.doc_id
            LEFT JOIN research_speakers s ON s.speaker_id = u.speaker_id
            WHERE u.tenant_id = $1 AND d.project_id = $2
              AND u.embedding IS NOT NULL{filters}
            ORDER BY u.embedding::halfvec(1024) <=> {vec}::halfvec(1024)
            LIMIT {limit}
            """,
            *dense_sql.values,
        )
        dense = [_unit_payload(r) for r in rows]

    fts_sql = _Sql(str(q.tenant_id), str(q.project_id))
    fts_filters = _unit_filters(fts_sql, q)
    tsq = fts_sql.p(q.query)
    limit = fts_sql.p(depth)
    rows = await conn.fetch(
        f"""
        SELECT u.unit_id, u.doc_id, u.exchange_id, u.seq, u.text, u.role_in_exchange,
               ts_rank(to_tsvector('english', u.text), plainto_tsquery('english', {tsq})) AS score
        FROM research_units u
        JOIN documents d ON d.doc_id = u.doc_id
        LEFT JOIN research_speakers s ON s.speaker_id = u.speaker_id
        WHERE u.tenant_id = $1 AND d.project_id = $2{fts_filters}
          AND to_tsvector('english', u.text) @@ plainto_tsquery('english', {tsq})
        ORDER BY score DESC
        LIMIT {limit}
        """,
        *fts_sql.values,
    )
    return dense, [_unit_payload(r) for r in rows]


async def _exchange_arms(
    conn: asyncpg.Connection,
    query_vector: list[float] | None,
    q: ResearchQuery,
    depth: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    dense_sql = _Sql(str(q.tenant_id), str(q.project_id))
    filters = _exchange_filters(dense_sql, q)
    dense: list[dict[str, object]] = []
    if query_vector is not None:
        vec = dense_sql.p(str(query_vector))
        limit = dense_sql.p(depth)
        rows = await conn.fetch(
            f"""
            SELECT e.exchange_id, e.doc_id, e.seq, coalesce(e.qa_text, '') AS text,
                   1 - (e.embedding::halfvec(1024) <=> {vec}::halfvec(1024)) AS score
            FROM research_exchanges e
            JOIN documents d ON d.doc_id = e.doc_id
            WHERE e.tenant_id = $1 AND d.project_id = $2
              AND e.embedding IS NOT NULL{filters}
            ORDER BY e.embedding::halfvec(1024) <=> {vec}::halfvec(1024)
            LIMIT {limit}
            """,
            *dense_sql.values,
        )
        dense = [_exchange_payload(r) for r in rows]

    fts_sql = _Sql(str(q.tenant_id), str(q.project_id))
    fts_filters = _exchange_filters(fts_sql, q)
    tsq = fts_sql.p(q.query)
    limit = fts_sql.p(depth)
    rows = await conn.fetch(
        f"""
        SELECT e.exchange_id, e.doc_id, e.seq, coalesce(e.qa_text, '') AS text,
               ts_rank(to_tsvector('english', coalesce(e.qa_text, '')),
                       plainto_tsquery('english', {tsq})) AS score
        FROM research_exchanges e
        JOIN documents d ON d.doc_id = e.doc_id
        WHERE e.tenant_id = $1 AND d.project_id = $2 AND coalesce(e.qa_text, '') <> ''{fts_filters}
          AND to_tsvector('english', coalesce(e.qa_text, ''))
              @@ plainto_tsquery('english', {tsq})
        ORDER BY score DESC
        LIMIT {limit}
        """,
        *fts_sql.values,
    )
    return dense, [_exchange_payload(r) for r in rows]


def _unit_payload(row: asyncpg.Record) -> dict[str, object]:
    unit_id = int(row["unit_id"])
    exchange_id = row["exchange_id"]
    payload: dict[str, object] = {
        "chunk_id": _child_uuid(f"unit:{unit_id}"),
        "text": str(row["text"]),
        "source": "",
        "doc_id": str(row["doc_id"]),
        "score": float(row["score"]),
        "_key": f"unit:{unit_id}",
        "_kind": unit_kind(row["role_in_exchange"]),
        "_unit_id": unit_id,
        "_exchange_id": None if exchange_id is None else int(exchange_id),
        "_seq": int(row["seq"]),
        "_doc_id": str(row["doc_id"]),
    }
    return payload


def _exchange_payload(row: asyncpg.Record) -> dict[str, object]:
    exchange_id = int(row["exchange_id"])
    payload: dict[str, object] = {
        "chunk_id": _child_uuid(f"exchange:{exchange_id}"),
        "text": str(row["text"]),
        "source": "",
        "doc_id": str(row["doc_id"]),
        "score": float(row["score"]),
        "_key": f"exchange:{exchange_id}",
        "_kind": CHILD_QA,
        "_unit_id": None,
        "_exchange_id": exchange_id,
        "_seq": int(row["seq"]),
        "_doc_id": str(row["doc_id"]),
    }
    return payload


async def _rrf(
    conn: asyncpg.Connection,
    dense: list[dict[str, object]],
    lexical: list[dict[str, object]],
    depth: int,
) -> list[dict[str, object]]:
    if not dense and not lexical:
        return []
    try:
        rows = await conn.fetch(
            "SELECT chunk_id, text, source, doc_id, score "
            "FROM corpus.rrf_fusion($1::jsonb, $2::jsonb, $3, $4)",
            json.dumps(dense),
            json.dumps(lexical),
            depth,
            RRF_K,
        )
    except asyncpg.UndefinedFunctionError as exc:
        raise RuntimeError(
            "corpus.rrf_fusion() is not installed — apply migration 006 "
            "(corpus_kb/migrations/006_rrf_fusion.sql)"
        ) from exc
    by_uuid = {str(row["chunk_id"]): row for row in dense + lexical}
    fused: list[dict[str, object]] = []
    for row in rows:
        item: dict[str, object] = {
            "chunk_id": str(row["chunk_id"]),
            "text": row["text"],
            "source": "",
            "doc_id": str(row["doc_id"]),
            "score": float(row["score"]),
        }
        meta = by_uuid.get(str(row["chunk_id"]))
        if meta is not None:
            for name in ("_key", "_kind", "_unit_id", "_exchange_id", "_seq", "_doc_id"):
                if name in meta:
                    item[name] = meta[name]
        fused.append(item)
    return fused


def _children_from_fused(fused: list[dict[str, object]]) -> list[_Child]:
    children: list[_Child] = []
    for row in fused:
        if "_key" not in row:
            continue
        children.append(
            _Child(
                key=str(row["_key"]),
                kind=str(row.get("_kind", "")),
                doc_id=str(row.get("_doc_id", row["doc_id"])),
                unit_id=row.get("_unit_id"),
                exchange_id=row.get("_exchange_id"),
                seq=int(row.get("_seq", 0)),
                text=str(row["text"]),
                score=float(row["score"]),
            )
        )
    return children


async def _meeting_windows(
    conn: asyncpg.Connection, children: list[_Child]
) -> dict[str, dict[int, str]]:
    """Window parents for exchange-less candidate units: window key -> {seq: text}."""
    doc_ids = sorted({c.doc_id for c in children if c.exchange_id is None})
    if not doc_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT u.doc_id, u.seq, u.text FROM research_units u
        WHERE u.doc_id = ANY($1::uuid[])
        ORDER BY u.doc_id, u.seq
        """,
        doc_ids,
    )
    by_doc: dict[str, dict[int, str]] = {}
    for row in rows:
        by_doc.setdefault(str(row["doc_id"]), {})[int(row["seq"])] = str(row["text"])
    windows: dict[str, dict[int, str]] = {}
    for doc_id, texts in by_doc.items():
        for window in meeting_windows(texts.keys()):
            windows[f"window:{doc_id}:{window.seq}"] = {seq: texts[seq] for seq in window.seqs}
    return windows


def _to_parents(
    children: list[_Child], windows: dict[str, dict[int, str]]
) -> list[tuple[str, _Child, float]]:
    """First-seen (best fused score) child wins per parent; order preserved."""
    parents: list[tuple[str, _Child, float]] = []
    seen: set[str] = set()
    for child in children:
        parent_key = _parent_key(child, windows)
        if parent_key is None or parent_key in seen:
            continue
        seen.add(parent_key)
        parents.append((parent_key, child, child.score))
    return parents


def _parent_key(child: _Child, windows: dict[str, dict[int, str]]) -> str | None:
    if child.exchange_id is not None:
        return f"exchange:{child.exchange_id}"
    for window_key, members in windows.items():
        if window_key.startswith(f"window:{child.doc_id}:") and child.seq in members:
            return window_key
    return None


async def _citations(
    conn: asyncpg.Connection,
    parents: list[tuple[str, _Child, float]],
    windows: dict[str, dict[int, str]],
) -> list[Citation]:
    doc_ids = sorted({child.doc_id for _, child, _ in parents})
    if not doc_ids:
        return []
    doc_rows = await conn.fetch(
        """
        SELECT d.doc_id, d.title, p.name AS project_name
        FROM documents d LEFT JOIN research_projects p ON p.project_id = d.project_id
        WHERE d.doc_id = ANY($1::uuid[])
        """,
        doc_ids,
    )
    docs = {str(r["doc_id"]): (str(r["title"] or ""), r["project_name"]) for r in doc_rows}

    exchange_ids = [int(key.split(":")[1]) for key, _, _ in parents if key.startswith("exchange:")]
    exchange_rows: dict[int, asyncpg.Record] = {}
    unit_ids: list[int] = []
    if exchange_ids:
        for row in await conn.fetch(
            """
            SELECT exchange_id, doc_id, seq, question_text, q_unit_ids, a_unit_ids
            FROM research_exchanges WHERE exchange_id = ANY($1::bigint[])
            """,
            exchange_ids,
        ):
            exchange_rows[int(row["exchange_id"])] = row
            unit_ids.extend(int(u) for u in list(row["a_unit_ids"]))
    unit_rows: dict[int, asyncpg.Record] = {}
    if unit_ids:
        for row in await conn.fetch(
            "SELECT unit_id, seq, text FROM research_units WHERE unit_id = ANY($1::bigint[])",
            unit_ids,
        ):
            unit_rows[int(row["unit_id"])] = row

    citations: list[Citation] = []
    for parent_key, matched, score in parents:
        title, project_name = docs.get(matched.doc_id, ("", None))
        if parent_key.startswith("exchange:"):
            row = exchange_rows.get(int(parent_key.split(":")[1]))
            if row is None:
                continue
            answer_ids = [int(u) for u in row["a_unit_ids"]]
            answers = sorted(
                (unit_rows[u] for u in answer_ids if u in unit_rows),
                key=lambda r: int(r["seq"]),
            )
            answer_text = " ".join(str(r["text"]) for r in answers)
            citations.append(
                Citation(
                    doc_id=UUID(matched.doc_id),
                    doc_title=title,
                    project_name=None if project_name is None else str(project_name),
                    exchange_seq=int(row["seq"]),
                    question_text=row["question_text"],
                    answer_text=answer_text,
                    answer_highlight=_highlight(matched, answer_text),
                    matched_kind=matched.kind,
                    score=score,
                    unit_seqs=tuple(int(r["seq"]) for r in answers),
                )
            )
        else:
            members = windows.get(parent_key, {})
            window_text = " ".join(members[seq] for seq in sorted(members))
            start = window_text.find(matched.text)
            citations.append(
                Citation(
                    doc_id=UUID(matched.doc_id),
                    doc_title=title,
                    project_name=None if project_name is None else str(project_name),
                    exchange_seq=None,
                    question_text=None,
                    answer_text=window_text,
                    answer_highlight=(start, start + len(matched.text))
                    if start >= 0
                    else (0, len(window_text)),
                    matched_kind=matched.kind,
                    score=score,
                    unit_seqs=tuple(sorted(members)),
                )
            )
    return citations


def _highlight(matched: _Child, answer_text: str) -> tuple[int, int]:
    """Highlight the matched answer unit; qa/question matches highlight the
    whole answer (v5 §15 citation contract: the answer is the evidence)."""
    start = answer_text.find(matched.text) if matched.unit_id is not None else -1
    if start >= 0:
        return (start, start + len(matched.text))
    return (0, len(answer_text))


async def _rerank(query: str, citations: list[Citation], reranker: object) -> list[Citation]:
    score = getattr(reranker, "score", None)
    if not callable(score):
        return citations
    texts = [
        f"{c.question_text}\n{c.answer_text}" if c.question_text else c.answer_text
        for c in citations
    ]
    raw = await asyncio.to_thread(score, query, texts)
    if not raw:
        return citations
    scores = [float(value) for value in cast("list[float]", raw)]
    lo, hi = (min(scores), max(scores)) if scores else (0.0, 1.0)
    span = (hi - lo) or 1.0
    ranked = sorted(zip(citations, scores, strict=True), key=lambda pair: pair[1], reverse=True)
    return [replace(c, score=(s - lo) / span) for c, s in ranked]
