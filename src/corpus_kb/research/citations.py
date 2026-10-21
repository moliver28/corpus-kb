"""Citation rendering for the research parent-child retrieval (todo 12).

Split from ``retrieval.py`` (storage portability program §13: split, don't
grow). Owns the parent mapping — meeting windows for exchange-less units —
and the citation contract: the question above, the answer highlighted, the
matched kind carried through. The retrieval engine calls these after the
fusion; the surface layers only ever see :class:`Citation`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from corpus_kb.research.chunking import meeting_windows
from corpus_kb.research.conn import SqlConn, ids_any


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


__all__ = ["Citation", "_Child", "_citations", "_meeting_windows", "_to_parents"]


async def _meeting_windows(
    conn: SqlConn, children: list[_Child], duck: bool = False
) -> dict[str, dict[int, str]]:
    """Window parents for exchange-less candidate units: window key -> {seq: text}."""
    doc_ids = sorted({c.doc_id for c in children if c.exchange_id is None})
    if not doc_ids:
        return {}
    rows = await conn.fetch(
        f"""
        SELECT u.doc_id, u.seq, u.text FROM research_units u
        WHERE {ids_any("$1", "u.doc_id", duck, "uuid")}
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


async def _citation_lookup_rows(
    conn: SqlConn, parents: list[tuple[str, _Child, float]], duck: bool = False
) -> tuple[
    dict[str, tuple[str, object]],
    dict[int, Any],
    dict[int, Any],
]:
    """Fetch the doc/exchange/answer-unit rows the citations render from."""
    doc_ids = sorted({child.doc_id for _, child, _ in parents})
    doc_rows = await conn.fetch(
        f"""
        SELECT d.doc_id, d.title, p.name AS project_name
        FROM documents d LEFT JOIN research_projects p ON p.project_id = d.project_id
        WHERE {ids_any("$1", "d.doc_id", duck, "uuid")}
        """,
        doc_ids,
    )
    docs = {str(r["doc_id"]): (str(r["title"] or ""), r["project_name"]) for r in doc_rows}

    exchange_ids = [int(key.split(":")[1]) for key, _, _ in parents if key.startswith("exchange:")]
    exchange_rows: dict[int, Any] = {}
    unit_ids: list[int] = []
    if exchange_ids:
        for row in await conn.fetch(
            f"""
            SELECT exchange_id, doc_id, seq, question_text, q_unit_ids, a_unit_ids
            FROM research_exchanges WHERE {ids_any("$1", "exchange_id", duck, "bigint")}
            """,
            exchange_ids,
        ):
            exchange_rows[int(row["exchange_id"])] = row
            unit_ids.extend(int(u) for u in list(row["a_unit_ids"]))
    unit_rows: dict[int, Any] = {}
    if unit_ids:
        for row in await conn.fetch(
            "SELECT unit_id, seq, text FROM research_units "
            f"WHERE {ids_any('$1', 'unit_id', duck, 'bigint')}",
            unit_ids,
        ):
            unit_rows[int(row["unit_id"])] = row
    return docs, exchange_rows, unit_rows


def _exchange_citation(
    parent_key: str,
    matched: _Child,
    score: float,
    row: Any,
    unit_rows: dict[int, Any],
    title: str,
    project_name: object,
) -> Citation:
    """One exchange-shaped citation: question + seq-ordered answer text."""
    answer_ids = [int(u) for u in row["a_unit_ids"]]
    answers = sorted(
        (unit_rows[u] for u in answer_ids if u in unit_rows),
        key=lambda r: int(r["seq"]),
    )
    answer_text = " ".join(str(r["text"]) for r in answers)
    return Citation(
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


def _window_citation(
    parent_key: str,
    matched: _Child,
    score: float,
    windows: dict[str, dict[int, str]],
    title: str,
    project_name: object,
) -> Citation:
    """One window-shaped citation: the chunk window with the match outlined."""
    members = windows.get(parent_key, {})
    window_text = " ".join(members[seq] for seq in sorted(members))
    start = window_text.find(matched.text)
    return Citation(
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


async def _citations(
    conn: SqlConn,
    parents: list[tuple[str, _Child, float]],
    windows: dict[str, dict[int, str]],
    duck: bool = False,
) -> list[Citation]:
    doc_ids = sorted({child.doc_id for _, child, _ in parents})
    if not doc_ids:
        return []
    docs, exchange_rows, unit_rows = await _citation_lookup_rows(conn, parents, duck)

    citations: list[Citation] = []
    for parent_key, matched, score in parents:
        title, project_name = docs.get(matched.doc_id, ("", None))
        if parent_key.startswith("exchange:"):
            row = exchange_rows.get(int(parent_key.split(":")[1]))
            if row is None:
                continue
            citations.append(
                _exchange_citation(parent_key, matched, score, row, unit_rows, title, project_name)
            )
        else:
            citations.append(
                _window_citation(parent_key, matched, score, windows, title, project_name)
            )
    return citations


def _highlight(matched: _Child, answer_text: str) -> tuple[int, int]:
    """Highlight the matched answer unit; qa/question matches highlight the
    whole answer (v5 §15 citation contract: the answer is the evidence)."""
    start = answer_text.find(matched.text) if matched.unit_id is not None else -1
    if start >= 0:
        return (start, start + len(matched.text))
    return (0, len(answer_text))
