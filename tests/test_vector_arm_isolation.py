"""Vector-arm failure isolation (transaction savepoints) tests.

A failed SQL statement inside a Postgres transaction aborts the whole
transaction. Only ROLLBACK (to a savepoint) restores it. Without a
savepoint, one broken vector arm (say, ``pgml.embed`` missing on a
no-pgml host) poisons the handler's shared transaction, and the lexical
arm then dies with ``InFailedSQLTransactionError`` — hybrid search loses
BOTH arms instead of degrading to lexical-only.

Two layers:

* offline — a fake connection models the abort semantics honestly: a
  failed statement poisons the transaction; every later statement is
  rejected until a rollback (to savepoint) happens.
* live (``requires_postgres``) — the real pgml arm fails against a real
  server, and the lexical query must still execute on the same
  connection.
"""

from __future__ import annotations

from typing import cast
from uuid import uuid4

import asyncpg
import pytest

from corpus_kb.domain.models import DEFAULT_TENANT_ID, SearchQuery
from corpus_kb.handlers.query_handler import QueryHandler
from corpus_kb.handlers.query_vector_arms import (
    DEDUP_PREDICATE,
    SELECT_LIST,
    ollama_vector_arm,
    pgml_vector_arm,
)

# The Wave-5 live error text (asyncpg spells it SQL, not Sql).
_ABORTED_TEXT = "current transaction is aborted, commands ignored until end of transaction block"


# ============================================================================
# Fakes: a conn that models Postgres transaction + abort semantics
# ============================================================================


class _IsolationConn:
    """Asyncpg stand-in with honest transaction-abort modeling.

    * ``transaction()`` nesting: first open block is BEGIN; blocks inside
      it are SAVEPOINTs (the asyncpg nested-transaction behavior).
    * A scripted failure inside any open block poisons the transaction.
    * A poisoned transaction rejects every later statement with
      ``InFailedSQLTransactionError`` — the statement never runs.
    * ``__aexit__`` with an exception: ROLLBACK (TO SAVEPOINT when nested)
      and the poison is undone back to the block's entry state.
    * ``__aexit__`` clean: COMMIT, or RELEASE SAVEPOINT when nested.

    ``sql`` holds EXECUTED statements only; ``rejected`` holds statements
    the aborted transaction refused; ``tx_events`` is the event log
    (begin/savepoint/rollback_to/release/commit/rollback).
    """

    def __init__(
        self,
        fail_on: str | None = "pgml.embed",
        arm_error: Exception | None = None,
        vector_rows: list[dict[str, object]] | None = None,
        fts_rows: list[dict[str, object]] | None = None,
        fused_rows: list[dict[str, object]] | None = None,
        probe_fails: bool = False,
    ) -> None:
        self.sql: list[str] = []
        self.rejected: list[str] = []
        self.tx_events: list[str] = []
        self.fail_on = fail_on
        self.arm_error = arm_error or asyncpg.UndefinedFunctionError(
            "function pgml.embed(text, text[]) does not exist"
        )
        self.vector_rows = vector_rows or []
        self.fts_rows = fts_rows or []
        self.fused_rows = fused_rows or []
        self.probe_fails = probe_fails
        self._depth = 0
        self._aborted = False

    # -- statement plumbing -------------------------------------------------

    def _gate(self, sql: str) -> None:
        """Reject statements once the transaction is aborted (Postgres rule)."""
        if self._aborted:
            self.rejected.append(sql)
            raise asyncpg.InFailedSQLTransactionError(_ABORTED_TEXT)

    def _fail_statement(self, exc: Exception) -> None:
        """A failed statement poisons the open transaction (Postgres rule)."""
        if self._depth > 0:
            self._aborted = True
        raise exc

    def _maybe_fail(self, sql: str) -> None:
        if self.fail_on is not None and self.fail_on in sql:
            self._fail_statement(self.arm_error)

    async def execute(self, sql: str, *args: object) -> str:
        self._gate(sql)
        self.sql.append(sql)
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        self._gate(sql)
        self.sql.append(sql)
        self._maybe_fail(sql)
        if "plainto_tsquery" in sql:
            return list(self.fts_rows)
        if "rrf_fusion" in sql:
            return list(self.fused_rows)
        if "pgml.embed" in sql or "<=>" in sql:
            return list(self.vector_rows)
        return []

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        self._gate(sql)
        self.sql.append(sql)
        self._maybe_fail(sql)
        if "pg_extension" in sql:
            return None  # unknown pgvector version: skip the SET LOCAL block
        if "AS candidates" in sql:
            if self.probe_fails:
                self._fail_statement(
                    asyncpg.UndefinedFunctionError("operator does not exist (probe)")
                )
            return None
        return None

    def transaction(self) -> object:
        conn = self
        nested = self._depth > 0
        snapshot = self._aborted

        class _Tx:
            async def __aenter__(self) -> _IsolationConn:
                conn.tx_events.append("savepoint" if nested else "begin")
                conn._depth += 1
                return conn

            async def __aexit__(self, extype: object, ex: object, tb: object) -> bool:
                conn._depth -= 1
                if extype is not None:
                    conn.tx_events.append("rollback_to" if nested else "rollback")
                    conn._aborted = snapshot
                    return False
                conn.tx_events.append("release" if nested else "commit")
                return False

        return _Tx()


class _IsolationPool:
    def __init__(self, conn: _IsolationConn) -> None:
        self._conn = conn

    def acquire(self):
        outer = self

        class _Acquire:
            async def __aenter__(self) -> _IsolationConn:
                return outer._conn

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Acquire()


class _RealEmbedder:
    """Embedder stub with stable non-zero vectors."""

    dimensions = 8
    model = "real-stub"

    def embed(self, text: str) -> list[float]:
        return [0.1] * self.dimensions

    def embed_matryoshka(self, text: str, dim: int) -> list[float]:
        return [0.2] * dim


_PGML_CONFIG: dict[str, object] = {
    "embedding": {"provider": "pgml", "model": "nomic-embed-text"},
    "search": {"exclude_degraded": True, "reranker": "none"},
}

_OLLAMA_CONFIG: dict[str, object] = {
    "embedding": {"provider": "ollama", "model": "stub", "dimensions": 8},
    "search": {"exclude_degraded": True, "reranker": "none"},
}


def _fts_row() -> dict[str, object]:
    return {
        "chunk_id": str(uuid4()),
        "text": "lexical match body",
        "source": "stub",
        "doc_id": str(uuid4()),
        "score": 0.07,
    }


def _handler(conn: _IsolationConn, config: dict[str, object], embedder: object = None) -> object:
    return QueryHandler(
        cast("object", _IsolationPool(conn)),
        embedder=cast("object", embedder),
        reranker=None,
        config=config,
    )


# ============================================================================
# Unit: each arm isolates its own failure
# ============================================================================


@pytest.mark.asyncio
async def test_pgml_arm_failure_rolls_back_and_frees_connection() -> None:
    """pgml arm: failure rolls the arm's block back, connection stays usable."""
    conn = _IsolationConn(fail_on="pgml.embed", fts_rows=[_fts_row()])
    rows, reason = await pgml_vector_arm(
        cast("asyncpg.Connection", conn),
        model="nomic-embed-text",
        semantic_query="housing",
        tenant_id=str(DEFAULT_TENANT_ID),
        k=5,
        vector_candidates=0,
        dynamic_predicates=[],
        degraded_pred="",
        exact_threshold=0.05,
    )
    assert rows == []
    assert reason is not None and reason.startswith("vector-arm-error")
    # The arm ran under its own transaction block and rolled it back.
    assert conn.tx_events == ["begin", "rollback"]
    # The same connection still executes the lexical arm's query.
    assert await conn.fetch("SELECT plainto_tsquery('english', $1)", "housing") != []
    assert conn.rejected == []


@pytest.mark.asyncio
async def test_ollama_arm_failure_rolls_back_and_frees_connection() -> None:
    """ollama arm: same contract, same isolation (symmetry)."""
    conn = _IsolationConn(fail_on="<=>", fts_rows=[_fts_row()])
    rows, reason = await ollama_vector_arm(
        cast("asyncpg.Connection", conn),
        embedder=cast("object", _RealEmbedder()),
        semantic_query="housing",
        tenant_id=str(DEFAULT_TENANT_ID),
        k=5,
        vector_candidates=0,
        dynamic_predicates=[],
        degraded_pred="",
        matryoshka_enabled=False,
        matryoshka_dim=4,
        candidate_multiplier=8,
        exact_threshold=0.05,
    )
    assert rows == []
    assert reason is not None and reason.startswith("vector-arm-error")
    assert conn.tx_events == ["begin", "rollback"]
    assert await conn.fetch("SELECT plainto_tsquery('english', $1)", "housing") != []
    assert conn.rejected == []


# ============================================================================
# Orchestrator: one dead arm must not kill the lexical arm
# ============================================================================


@pytest.mark.asyncio
async def test_pgml_arm_failure_degrades_to_lexical_results() -> None:
    """THE Wave-5 repro: dead pgml arm, lexical arm must still answer."""
    fts = _fts_row()
    conn = _IsolationConn(fail_on="pgml.embed", fts_rows=[fts], fused_rows=[dict(fts)])
    envelope = await _handler(conn, dict(_PGML_CONFIG)).handle_search_with_meta(
        SearchQuery(query="housing", tenant_id=DEFAULT_TENANT_ID, k=5)
    )
    # The lexical query EXECUTED (it was never swallowed by an aborted
    # transaction) and its results came back through the fusion.
    assert any("plainto_tsquery" in s for s in conn.sql)
    assert conn.rejected == []
    assert len(envelope.results) == 1
    assert envelope.status == "success"
    # The dead arm is still recorded honestly in the envelope.
    assert any(r.startswith("vector-arm-error") for r in envelope.degraded_excluded.reasons)


@pytest.mark.asyncio
async def test_pgml_arm_savepoint_sits_inside_handler_transaction() -> None:
    """Event order pins the savepoint under the handler's BEGIN.

    A failed block rolls back TO the savepoint (no release — the block
    failed); the outer transaction then commits with the lexical results.
    """
    fts = _fts_row()
    conn = _IsolationConn(fail_on="pgml.embed", fts_rows=[fts], fused_rows=[dict(fts)])
    await _handler(conn, dict(_PGML_CONFIG)).handle_search_with_meta(
        SearchQuery(query="housing", tenant_id=DEFAULT_TENANT_ID, k=5)
    )
    assert conn.tx_events == ["begin", "savepoint", "rollback_to", "commit"]


@pytest.mark.asyncio
async def test_clean_arm_block_releases_its_savepoint() -> None:
    """The healthy path only pays savepoint + release inside the BEGIN."""
    vec = _fts_row()  # any row shape works; the arm just hands it to fusion
    fts = _fts_row()
    conn = _IsolationConn(
        fail_on=None, vector_rows=[dict(vec)], fts_rows=[fts], fused_rows=[dict(fts)]
    )
    envelope = await _handler(conn, dict(_PGML_CONFIG)).handle_search_with_meta(
        SearchQuery(query="housing", tenant_id=DEFAULT_TENANT_ID, k=5)
    )
    assert conn.tx_events == ["begin", "savepoint", "release", "commit"]
    assert envelope.status == "success"


@pytest.mark.asyncio
async def test_ollama_arm_failure_degrades_to_lexical_results() -> None:
    """Same repro on the ollama arm: a broken vector read keeps FTS alive."""
    fts = _fts_row()
    conn = _IsolationConn(fail_on="<=>", fts_rows=[fts], fused_rows=[dict(fts)])
    envelope = await _handler(conn, dict(_OLLAMA_CONFIG), _RealEmbedder()).handle_search_with_meta(
        SearchQuery(query="housing", tenant_id=DEFAULT_TENANT_ID, k=5)
    )
    assert any("plainto_tsquery" in s for s in conn.sql)
    assert conn.rejected == []
    assert len(envelope.results) == 1
    assert any(r.startswith("vector-arm-error") for r in envelope.degraded_excluded.reasons)


@pytest.mark.asyncio
async def test_degraded_probe_failure_does_not_poison_lexical_arm() -> None:
    """The M2 count probe is fail-open by contract; a savepoint enforces it.

    A clean-but-empty vector arm runs, the probe's statement fails, and the
    lexical arm must still run on the same connection.
    """
    fts = _fts_row()
    conn = _IsolationConn(
        fail_on=None,  # vector arm runs clean (and returns no rows)
        vector_rows=[],
        fts_rows=[fts],
        fused_rows=[dict(fts)],
        probe_fails=True,
    )
    envelope = await _handler(conn, dict(_PGML_CONFIG)).handle_search_with_meta(
        SearchQuery(query="housing", tenant_id=DEFAULT_TENANT_ID, k=5)
    )
    assert any("plainto_tsquery" in s for s in conn.sql)
    assert conn.rejected == []
    assert len(envelope.results) == 1
    # The probe failed; it cost only its annotation, nothing else.
    assert envelope.degraded_excluded.count == 0


# ============================================================================
# Live pin (requires Postgres with the base schema and NO pgml)
# ============================================================================


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_live_pgml_failure_keeps_lexical_arm_alive(pg_pool) -> None:
    """Real-server pin of the Wave-5 fix.

    On a no-pgml host the pgml arm's SQL fails (no ``pgml`` schema). The
    savepoint must roll that back so the REAL lexical query still executes
    on the same connection inside the same transaction.
    """
    pgml_present = await pg_pool.fetchval(
        "SELECT to_regprocedure('pgml.embed(text, text[])') IS NOT NULL"
    )
    if pgml_present:
        pytest.skip("pgml is installed here: the arm cannot fail")
    schema_ready = await pg_pool.fetchval(
        """
        SELECT to_regclass('chunks') IS NOT NULL
           AND to_regclass('documents') IS NOT NULL
           AND to_regclass('chunks_vectors') IS NOT NULL
           AND EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'chunks' AND column_name = 'tombstoned_at')
        """
    )
    if not schema_ready:
        pytest.skip("base schema missing")

    tenant = str(DEFAULT_TENANT_ID)
    async with pg_pool.acquire() as conn, conn.transaction():
        rows, reason = await pgml_vector_arm(
            cast("asyncpg.Connection", conn),
            model="nomic-embed-text",
            semantic_query="housing",
            tenant_id=tenant,
            k=5,
            vector_candidates=0,
            dynamic_predicates=[],
            degraded_pred="",
            exact_threshold=0.05,
        )
        assert rows == []
        assert reason is not None and reason.startswith("vector-arm-error")
        # The REAL lexical query (same shape as the handler's FTS arm) on
        # the SAME connection: it must execute, not be rejected by an
        # aborted transaction.
        fts_rows = await conn.fetch(
            f"""
            SELECT {SELECT_LIST},
                   ts_rank(to_tsvector('english', chunks.text),
                           plainto_tsquery('english', $1)) AS score
            FROM chunks
            JOIN documents ON chunks.doc_id = documents.doc_id
            WHERE chunks.tenant_id = $2 {DEDUP_PREDICATE}
              AND to_tsvector('english', chunks.text) @@ plainto_tsquery('english', $1)
            ORDER BY score DESC, chunks.chunk_id ASC
            LIMIT $3
            """,
            "housing",
            tenant,
            10,
        )
        assert isinstance(fts_rows, list)
