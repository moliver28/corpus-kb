"""Shared unit/exchange filter compiler (issue 232; storage portability §13).

One filter compiler (:func:`_row_filters`) serves both retrieval grains:
the unit grain (``research_units``) and the exchange grain
(``research_exchanges``). Every place the two former twins differed rides
in :class:`_Grain` as data or as a one-line fragment: the row alias, the
speaker/topic/assignment shapes, and the stable id column. The fragment
lambdas emit the exact former byte forms, and the ``_Sql`` parameter
order stays the contract: source type, speaker role, doc ids, topic,
then assignment ($1 tenant, $2 project, filters $3+).

This module is the family leaf: it imports nothing from its siblings.
``ResearchQuery`` lives here (it is the compiler's input), and both
``retrieval.py`` and ``retrieval_arms.py`` take it from this module.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from corpus_kb.research.chunking import CHILD_QA, unit_kind


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


# Assignment-confidence floors (todo-18 notebook filter): --min-confidence X
# admits X and every STRONGER level.
CONFIDENCE_FLOORS: dict[str, tuple[str, ...]] = {
    "high": ("high",),
    "medium": ("high", "medium"),
    "low": ("high", "medium", "low"),
}


class _Sql:
    """Numbered-parameter builder: every value gets a distinct placeholder."""

    def __init__(self, tenant_id: str, project_id: str) -> None:
        self.values: list[object] = [tenant_id, project_id]

    def p(self, value: object) -> str:
        self.values.append(value)
        return f"${len(self.values)}"


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


@dataclass(frozen=True)
class _Grain:
    """Per-grain SQL pieces for the shared filter compiler and arm builder.

    One builder serves both grains; every former twin difference rides here
    as data or as a one-line fragment. The fragment lambdas must keep the
    shared ``_Sql`` parameter order: source type, speaker role, doc ids,
    topic, then assignment.
    """

    alias: str  # row alias in the arms' SQL: "u" / "e"
    table: str  # FROM target: "research_units u" / "research_exchanges e"
    joins: str  # document (and speaker) joins; the count probe reuses them
    cols: str  # arm SELECT list; the FTS arm reuses it
    outer_cols: str  # CTE outer SELECT list (candidate-qualified)
    row_id: str  # stable tiebreak column: "unit_id" / "exchange_id"
    embedding: str  # dense vector column
    text_expr: str  # FTS text expression
    fts_guard: str  # extra FTS WHERE guard ('' for units)
    key: str  # child key prefix: "unit" / "exchange"
    speaker_role: Callable[[_Sql, str, bool], str]
    topic: Callable[[_Sql, int], str]
    assignment: Callable[[bool, str], str]
    kind: Callable[[Mapping[str, Any]], str]
    unit_of: Callable[[int, Mapping[str, Any]], int | None]
    exchange_of: Callable[[int, Mapping[str, Any]], int | None]


def _a_unit_member(duck: bool, alias: str, unit: str) -> str:
    """Answer-unit membership: list_contains on DuckDB, ``= ANY`` on Postgres."""
    return (
        f"list_contains({alias}.a_unit_ids, {unit})"
        if duck
        else f"{unit} = ANY({alias}.a_unit_ids)"
    )


_UNIT_GRAIN = _Grain(
    alias="u",
    table="research_units u",
    joins=(
        "JOIN documents d ON d.doc_id = u.doc_id "
        "LEFT JOIN research_speakers s ON s.speaker_id = u.speaker_id"
    ),
    cols="u.unit_id, u.doc_id, u.exchange_id, u.seq, u.text, u.role_in_exchange",
    outer_cols=(
        "candidate.unit_id, candidate.doc_id, candidate.exchange_id, "
        "candidate.seq, candidate.text, candidate.role_in_exchange"
    ),
    row_id="unit_id",
    embedding="u.embedding",
    text_expr="u.text",
    fts_guard="",
    key="unit",
    speaker_role=lambda sql, role, duck: f" AND s.role = {sql.p(role)}",
    topic=lambda sql, tid: (
        " AND u.exchange_id IN (SELECT exchange_id FROM research_exchanges "
        f"WHERE tenant_id = u.tenant_id AND topic_id = {sql.p(tid)})"
    ),
    assignment=lambda duck, cond: (
        " AND u.unit_id IN (SELECT ra.unit_id FROM research_assignments ra "
        f"WHERE ra.tenant_id = u.tenant_id AND {cond})"
    ),
    kind=lambda row: unit_kind(row["role_in_exchange"]),
    unit_of=lambda rid, row: rid,
    exchange_of=lambda rid, row: None if row["exchange_id"] is None else int(row["exchange_id"]),
)

_EXCHANGE_GRAIN = _Grain(
    alias="e",
    table="research_exchanges e",
    joins="JOIN documents d ON d.doc_id = e.doc_id",
    cols="e.exchange_id, e.doc_id, e.seq, coalesce(e.qa_text, '') AS text",
    outer_cols="candidate.exchange_id, candidate.doc_id, candidate.seq, candidate.text",
    row_id="exchange_id",
    embedding="e.embedding",
    text_expr="coalesce(e.qa_text, '')",
    fts_guard=" AND coalesce(e.qa_text, '') <> ''",
    key="exchange",
    speaker_role=lambda sql, role, duck: (
        " AND EXISTS (SELECT 1 FROM research_units u2 "
        "JOIN research_speakers s2 ON s2.speaker_id = u2.speaker_id "
        f"WHERE {_a_unit_member(duck, 'e', 'u2.unit_id')} "
        f"AND s2.role = {sql.p(role)})"
    ),
    topic=lambda sql, tid: f" AND e.topic_id = {sql.p(tid)}",
    assignment=lambda duck, cond: (
        " AND EXISTS (SELECT 1 FROM research_assignments ra "
        f"WHERE ra.tenant_id = e.tenant_id AND {_a_unit_member(duck, 'e', 'ra.unit_id')} "
        f"AND {cond})"
    ),
    kind=lambda row: CHILD_QA,
    unit_of=lambda rid, row: None,
    exchange_of=lambda rid, row: rid,
)


def _row_filters(sql: _Sql, q: ResearchQuery, grain: _Grain, duck: bool = False) -> str:
    """WHERE fragments for one grain; the ``_Sql`` parameter order is contract."""
    frag = ""
    if q.source_type:
        frag += f" AND d.source_type = {sql.p(q.source_type)}"
    if q.speaker_role:
        frag += grain.speaker_role(sql, q.speaker_role, duck)
    if q.doc_ids:
        ids = sql.p([str(d) for d in q.doc_ids])
        col = f"{grain.alias}.doc_id"
        # List membership: asyncpg's ANY on Postgres (byte-identical), the
        # engine's list_contains on DuckDB (no ``= ANY`` there).
        frag += (
            f" AND list_contains({ids}::VARCHAR[], {col})"
            if duck
            else f" AND {col} = ANY({ids}::uuid[])"
        )
    if q.topic_id is not None:
        frag += grain.topic(sql, int(q.topic_id))
    assignment = _assignment_predicate(sql, q)
    if assignment:
        frag += grain.assignment(duck, " AND ".join(assignment))
    return frag


def _unit_filters(sql: _Sql, q: ResearchQuery, duck: bool = False) -> str:
    """Unit-grain filter fragments (the DuckDB twin binds this name)."""
    return _row_filters(sql, q, _UNIT_GRAIN, duck)


def _exchange_filters(sql: _Sql, q: ResearchQuery, duck: bool = False) -> str:
    """Exchange-grain filter fragments (same binding as _unit_filters)."""
    return _row_filters(sql, q, _EXCHANGE_GRAIN, duck)
