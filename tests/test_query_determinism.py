"""Zero-drift rule 9: query results are deterministically ordered.

Two layers are pinned here:

* Unit: the query handler fuses its two arms with the pure Python port
  (handlers/query_fusion.py, an exact mirror of corpus.rrf_fusion
  migration 024). Stub arm rows with TIED fused scores must come out
  ordered by score DESC then chunk id ASC — the same output for every
  equivalent input. research/retrieval still calls the SQL function; its
  stub keeps the same contract.
* SQL shape: every score-ordered ORDER BY across the query-handler modules
  (query_vector_arms.py + query_handler.py + query_crud.py — the Wave-3
  split of the pre-2026-10 single query_handler.py; query_fusion.py carries
  no SQL, its Python sort is pinned by tests/test_query_fusion.py and by
  the total-order unit below) and research/retrieval.py carries the
  chunk-id tiebreaker, and migration 024 pins the same order inside
  corpus.rrf_fusion.

All offline: no Postgres, no Ollama.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import cast
from uuid import UUID, uuid5

import pytest

from corpus_kb.domain.models import DEFAULT_TENANT_ID, SearchQuery
from corpus_kb.errors import MigrationMissingError
from corpus_kb.handlers.query_fusion import rrf_fuse
from corpus_kb.handlers.query_handler import QueryHandler
from corpus_kb.research.retrieval import _rrf
from corpus_kb.research.search_settings import missing_rrf_fusion_error

ROOT = Path(__file__).resolve().parent.parent

# Fixed namespace so the tiebreaker keys are stable across runs.
_DET_NAMESPACE = UUID("6f6f6f6f-6f6f-6f6f-6f6f-6f6f6f6f6f6f")


def _det_uuid(name: str) -> str:
    return str(uuid5(_DET_NAMESPACE, name))


# ============================================================================
# Part (a): stubbed conn, tied scores, shuffled input order
# ============================================================================


class _ScriptedConn:
    """Asyncpg stand-in: canned ARM rows, records SQL.

    The handler fuses in Python (handlers/query_fusion.py) — no
    corpus.rrf_fusion SQL is issued, so this stub scripts the vector and
    FTS arm outputs and the fusion runs for real over them.
    """

    def __init__(
        self,
        vector_rows: list[dict[str, object]] | None = None,
        fts_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.sql: list[str] = []
        self.vector_rows = vector_rows or []
        self.fts_rows = fts_rows or []

    async def execute(self, sql: str, *args: object) -> str:
        self.sql.append(sql)
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        self.sql.append(sql)
        if "pgml.embed" in sql:
            return list(self.vector_rows)
        if "plainto_tsquery" in sql:
            return list(self.fts_rows)
        return []

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        self.sql.append(sql)
        if "pg_extension" in sql:
            return {"extversion": "0.8.2"}
        return None

    def transaction(self):
        outer = self

        class _Tx:
            async def __aenter__(self) -> _ScriptedConn:
                outer.sql.append("BEGIN")
                return outer

            async def __aexit__(self, *exc: object) -> bool:
                outer.sql.append("COMMIT")
                return False

        return _Tx()


class _ScriptedPool:
    def __init__(self, conn: _ScriptedConn) -> None:
        self._conn = conn

    def acquire(self):
        outer = self

        class _Acquire:
            async def __aenter__(self) -> _ScriptedConn:
                return outer._conn

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Acquire()


_CONFIG: dict[str, object] = {
    "embedding": {"provider": "pgml", "model": "nomic-embed-text"},
    "search": {"exclude_degraded": True, "reranker": "none"},
}


def _fused_row(chunk_id: str, score: float) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "text": f"text for {chunk_id[:8]}",
        "source": "stub",
        "doc_id": _det_uuid("doc"),
        "score": score,
    }


def _arm_row(chunk_id: str, score: float = 0.5) -> dict[str, object]:
    """One vector/FTS arm row (the fusion input shape the arms SELECT)."""
    return {
        "chunk_id": chunk_id,
        "text": f"text for {chunk_id[:8]}",
        "source": "stub",
        "doc_id": _det_uuid("doc"),
        "score": score,
    }


@pytest.mark.asyncio
async def test_handler_orders_tied_fused_rows_by_chunk_id() -> None:
    """Tied fused scores come out ordered by chunk id ASC.

    The vector hit and the rank-1 FTS hit each score 1/(rrf_k + 1) — a tie
    the pure fusion must break by chunk id, never by arm output order."""
    ids = [_det_uuid(f"chunk-{i}") for i in range(3)]
    conn = _ScriptedConn(
        vector_rows=[_arm_row(ids[2])],
        fts_rows=[_arm_row(ids[0]), _arm_row(ids[1])],
    )
    handler = QueryHandler(
        cast("object", _ScriptedPool(conn)), embedder=None, reranker=None, config=dict(_CONFIG)
    )
    envelope = await handler.handle_search_with_meta(
        SearchQuery(query="housing", tenant_id=DEFAULT_TENANT_ID, k=3)
    )
    # ids[2] (vector rank 1) ties ids[0] (FTS rank 1) at 1/(rrf_k+1);
    # ids[1] (FTS rank 2) trails. Chunk id ASC decides the tie band.
    assert [str(r.chunk_id) for r in envelope.results] == [*sorted([ids[0], ids[2]]), ids[1]]


@pytest.mark.asyncio
async def test_handler_orders_mixed_scores_then_chunk_id() -> None:
    """Score DESC wins first; chunk id breaks the ties inside a score band.

    ids[2] is hit by BOTH arms (contributions sum per migration 024) and
    lands above the 1/(rrf_k+1) tie band of ids[3] and ids[0]."""
    ids = [_det_uuid(f"chunk-{i}") for i in range(4)]
    conn = _ScriptedConn(
        vector_rows=[_arm_row(ids[3]), _arm_row(ids[2])],
        fts_rows=[_arm_row(ids[0]), _arm_row(ids[2])],
    )
    handler = QueryHandler(
        cast("object", _ScriptedPool(conn)), embedder=None, reranker=None, config=dict(_CONFIG)
    )
    envelope = await handler.handle_search_with_meta(
        SearchQuery(query="housing", tenant_id=DEFAULT_TENANT_ID, k=4)
    )
    assert [str(r.chunk_id) for r in envelope.results] == [ids[2], *sorted([ids[0], ids[3]])]


def test_query_fusion_total_order_pin() -> None:
    """query_fusion.rrf_fuse (the handler's fusion port) returns the
    migration-024 total order — fscore DESC, then chunk id ASC — including
    the tie band, whatever the leg argument order."""
    ids = [_det_uuid(f"pin-{i}") for i in range(3)]
    dense = [
        {"chunk_id": cid, "text": "d", "source": "s", "doc_id": _det_uuid("doc"), "score": 0.1}
        for cid in ids
    ]
    lexical = [
        {"chunk_id": cid, "text": "l", "source": "s", "doc_id": _det_uuid("doc"), "score": 0.2}
        for cid in reversed(ids)
    ]
    for legs in ((lexical, dense), (dense, lexical)):
        fused = rrf_fuse(*legs, k=10)
        keys = [(-r.score, r.chunk_id) for r in fused]
        assert keys == sorted(keys)  # total order: fscore DESC, cid ASC
        assert len({r.chunk_id for r in fused}) == len(fused)


def test_rrf_orders_tied_rows_by_chunk_id() -> None:
    """research/retrieval._rrf: same contract, two input orders, one output."""

    class _FusionConn:
        def __init__(self, rows: list[dict[str, object]]) -> None:
            self.rows = rows

        async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
            return list(self.rows)

    ids = [_det_uuid(f"unit-{i}") for i in range(3)]
    dense = [
        {"chunk_id": cid, "text": "t", "source": "", "doc_id": "d", "score": 0.02} for cid in ids
    ]

    def run(order: list[int]) -> list[tuple[float, str]]:
        conn = _FusionConn([_fused_row(ids[i], 0.02) for i in order])
        fused = asyncio.run(_rrf(conn, dense, [], 100, 42))
        return [(float(item["score"]), str(item["chunk_id"])) for item in fused]

    assert run([2, 0, 1]) == run([1, 2, 0]) == [(0.02, cid) for cid in sorted(ids)]


# ============================================================================
# Part (b): SQL-shape audit — every score ORDER BY carries the tiebreaker
# ============================================================================


def _desc_order_by_clauses(path: Path) -> list[str]:
    """Whitespace-normalized score-ordered ORDER BY clauses.

    A clause starts at a line beginning with ``ORDER BY <expr> DESC,`` — the
    comma is the tiebreaker separator, so a bare (un-tiebroken) ``... DESC``
    never starts a clause and the exact-count assertions below fail instead.
    The inner ANN scans order by ascending distance, carry no DESC keyword,
    and are never collected. A clause runs to its trailing ``ASC`` key,
    absorbing continuation lines (the matryoshka arm wraps one line).
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    clauses: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("ORDER BY") and " DESC," in stripped:
            clause = stripped
            while not clause.endswith("ASC") and i + 1 < len(lines):
                i += 1
                clause = f"{clause} {lines[i].strip()}"
            clauses.append(re.sub(r"\s+", " ", clause))
        i += 1
    return clauses


def test_every_score_order_by_in_query_handler_carries_tiebreaker() -> None:
    # Wave-3 split: the single pre-split query_handler.py is now several
    # modules; the audit covers ALL of them so the invariant set is
    # unchanged (same 7 clauses, same tiebreaker regex). query_fusion.py is
    # scanned too — it carries no SQL ORDER BY (the fusion is a Python sort,
    # pinned by tests/test_query_fusion.py and the total-order unit above).
    clauses: list[str] = []
    for rel in (
        "src/corpus_kb/handlers/query_vector_arms.py",
        "src/corpus_kb/handlers/query_handler.py",
        "src/corpus_kb/handlers/query_crud.py",
        "src/corpus_kb/handlers/query_fusion.py",
    ):
        clauses.extend(_desc_order_by_clauses(ROOT / rel))
    assert len(clauses) == 7, clauses  # 5 vector arms + FTS + search_similar
    for clause in clauses:
        assert re.fullmatch(r"ORDER BY .+ DESC, (chunks\.chunk_id|c\.chunk_id) ASC", clause), clause


def test_every_score_order_by_in_retrieval_carries_tiebreaker() -> None:
    clauses = _desc_order_by_clauses(ROOT / "src/corpus_kb/research/retrieval.py")
    assert len(clauses) == 6, clauses  # 2 dense arms x 2 + 2 FTS arms
    keys = r"(candidate\.unit_id|candidate\.exchange_id|u\.unit_id|e\.exchange_id)"
    for clause in clauses:
        assert re.fullmatch(rf"ORDER BY .+ DESC, {keys} ASC", clause), clause


def _asc_order_by_clauses(path: Path) -> list[str]:
    """Whitespace-normalized ascending score-ordered ORDER BY clauses.

    The analytics_sql twin of :func:`_desc_order_by_clauses`: a clause starts
    at a line beginning with ``ORDER BY candidate.dist + 0`` (the exact ANN
    re-order outside the materialized CTE) and runs to its trailing ``ASC``
    key, absorbing continuation lines. The inner ANN scans (plain
    ``ORDER BY <vector> <=>``) never start a clause and are not collected.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    clauses: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("ORDER BY candidate.dist + 0"):
            clause = stripped
            while not clause.endswith("ASC") and i + 1 < len(lines):
                i += 1
                clause = f"{clause} {lines[i].strip()}"
            clauses.append(re.sub(r"\s+", " ", clause))
        i += 1
    return clauses


def test_context_proximity_order_carries_tiebreaker() -> None:
    """The /api/search/context distance ordering also ties (chunk n-1 vs n+1)
    and must carry the chunk-id tiebreaker. (handle_search_context lives in
    handlers/query_crud.py since the Wave-3 split.)"""
    source = (ROOT / "src/corpus_kb/handlers/query_crud.py").read_text(encoding="utf-8")
    assert re.search(
        r"ORDER BY ABS\(c\.chunk_index - \(\s*"
        r"SELECT chunk_index FROM chunks WHERE chunk_id = \$1\s*\)\) ASC, c\.chunk_id ASC",
        source,
    )


def test_every_score_order_by_in_analytics_sql_carries_tiebreaker() -> None:
    """The analytics KNN reads (research/analytics_sql.py) pin the same
    contract: exact distance ordering first, stable id ASC as the tiebreaker.
    Four unit-id reads and one exchange-id read exist; a new score ORDER BY
    without the id key fails here."""
    clauses = _asc_order_by_clauses(ROOT / "src/corpus_kb/research/analytics_sql.py")
    assert len(clauses) == 5, clauses  # 4 unit-id reads + 1 exchange-id read
    ids = r"(candidate\.unit_id|candidate\.exchange_id)"
    for clause in clauses:
        assert re.fullmatch(rf"ORDER BY candidate\.dist \+ 0, {ids} ASC", clause), clause


def test_migration_024_pins_fusion_tie_order() -> None:
    sql = (ROOT / "src/corpus_kb/migrations/024_rrf_fusion_tiebreak.sql").read_text(
        encoding="utf-8"
    )
    assert "ORDER BY fused.fscore DESC, a.cid ASC" in sql
    assert "CREATE OR REPLACE FUNCTION corpus.rrf_fusion" in sql
    # Migration history is append-only: 006 must keep its original body.
    sql_006 = (ROOT / "src/corpus_kb/migrations/006_rrf_fusion.sql").read_text(encoding="utf-8")
    assert "ORDER BY fused.fscore DESC, fs.first_appearance ASC" in sql_006


# ============================================================================
# Shared fusion-missing helper
# ============================================================================


def test_missing_rrf_fusion_error_is_shared_and_chained() -> None:
    cause = ValueError("undefined function")
    err = missing_rrf_fusion_error(cause)
    assert isinstance(err, MigrationMissingError)
    assert err.__cause__ is cause
    assert "corpus.rrf_fusion() is not installed" in str(err)
    assert "scripts/migrate.py" not in str(err)
    assert "corpus-kb setup" in err.remediation
    # Single source: the one remaining SQL fusion call site delegates and
    # never restates the message. The query handler fuses in Python
    # (handlers/query_fusion.py) — it has no fusion SQL left to fail, and
    # its source must not carry the helper (dead import) either.
    source = (ROOT / "src/corpus_kb/research/retrieval.py").read_text(encoding="utf-8")
    assert "missing_rrf_fusion_error(exc)" in source
    assert "is not installed" not in source
    handler_source = (ROOT / "src/corpus_kb/handlers/query_handler.py").read_text(encoding="utf-8")
    assert "missing_rrf_fusion_error" not in handler_source
    # No fusion SQL is issued from the handler anymore (the docstring may
    # still NAME the migration it mirrors).
    assert "FROM corpus.rrf_fusion" not in handler_source
