"""SearchBackend seam + engine-honesty envelope keys (storage portability §3.3, §6).

OFFLINE: a recording connection captures the SQL order and canned rows
(the test_search_honesty pattern); no Postgres, no Ollama. Pins:

* the seam — ``PostgresSearchBackend`` satisfies the one-method
  ``SearchBackend`` protocol, and ``QueryHandler(search_backend=None)``
  defaults to it, so every existing constructor keeps working;
* the pure move — the arms still run on the handler's transaction
  connection in the same order (dense vector, degraded probe, lexical),
  and the fusion consumes the outcome's raw arm rows;
* guaranteed keys — every PG search envelope carries ``vector_index_mode``
  and ``lexical_index_mode`` with the Postgres engine values, on success
  and on ``degraded_empty`` alike;
* note-conditional shape — ``vector_index_note`` / ``lexical_index_note``
  appear in the payload only when set (the embedding_mode/embedding_note
  contract shape); the PG path emits no notes (both modes are defaults).
"""

from __future__ import annotations

import re
from typing import cast

import pytest

from corpus_kb.domain.models import DEFAULT_TENANT_ID, SearchQuery
from corpus_kb.handlers.query_handler import QueryHandler
from corpus_kb.handlers.query_integrity import (
    PG_LEXICAL_INDEX_MODE,
    PG_VECTOR_INDEX_MODE,
    REASON_NO_EMBEDDER,
    envelope_to_payload,
    finalize_envelope,
)
from corpus_kb.handlers.query_search_pg import PostgresSearchBackend
from corpus_kb.storage.search_backend import SearchBackend, SearchOutcome, SearchRequest

# ============================================================================
# Fakes (recording conn/pool — same shape as test_search_honesty)
# ============================================================================


class _RecordingConn:
    """Asyncpg-connection stand-in: records SQL, returns canned arm rows."""

    def __init__(self, vector_rows: list[dict[str, object]] | None = None) -> None:
        self.sql: list[str] = []
        self.vector_rows = vector_rows or []

    async def execute(self, sql: str, *args: object) -> str:
        self.sql.append(sql)
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        self.sql.append(sql)
        if "pgml.embed" in sql:
            return list(self.vector_rows)
        return []  # lexical arm + selectivity probe reads: no matches

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        self.sql.append(sql)
        if "pg_extension" in sql:
            return {"extversion": "0.8.2"}
        return None

    def transaction(self):
        outer = self

        class _Tx:
            async def __aenter__(self) -> _RecordingConn:
                outer.sql.append("BEGIN")
                return outer

            async def __aexit__(self, *exc: object) -> bool:
                outer.sql.append("COMMIT")
                return False

        return _Tx()


class _RecordingPool:
    def __init__(self, conn: _RecordingConn) -> None:
        self._conn = conn

    def acquire(self):
        outer = self

        class _Acquire:
            async def __aenter__(self) -> _RecordingConn:
                return outer._conn

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Acquire()


_PGML_CONFIG: dict[str, object] = {
    "embedding": {"provider": "pgml", "model": "qwen3-embedding:4b"},
    "retrieval": {"exclude_degraded": True},
    "search": {"reranker": "none"},
    "reranking": {"enabled": False},
}


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


# ============================================================================
# The seam: protocol conformance + the QueryHandler default
# ============================================================================


def test_pg_backend_satisfies_search_backend_protocol() -> None:
    backend = PostgresSearchBackend(config=dict(_PGML_CONFIG))
    # runtime_checkable: method presence on the one-method protocol.
    assert isinstance(backend, SearchBackend)


def test_request_and_outcome_carry_the_arm_contract() -> None:
    """The dataclasses expose exactly the fields the split needs (§3.3)."""
    request_fields = set(SearchRequest.__dataclass_fields__)
    assert {
        "conn",
        "query",
        "semantic_query",
        "dynamic_predicates",
        "degraded_pred",
        "over_retrieve_n",
        "reasons",
    } == request_fields
    outcome_fields = set(SearchOutcome.__dataclass_fields__)
    assert {
        "vector_rows",
        "lexical_rows",
        "excluded_count",
        "vector_index_mode",
        "lexical_index_mode",
        "vector_index_note",
        "lexical_index_note",
    } == outcome_fields


def test_query_handler_defaults_to_the_pg_backend() -> None:
    """search_backend=None builds PostgresSearchBackend from the same config."""
    handler = QueryHandler(
        cast("object", _RecordingPool(_RecordingConn())),
        embedder=None,
        reranker=None,
        config=dict(_PGML_CONFIG),
    )
    assert isinstance(handler._search_backend, PostgresSearchBackend)


# ============================================================================
# The pure move: same arms, same order, fusion stays in the handler
# ============================================================================


@pytest.mark.asyncio
async def test_pg_search_runs_arms_on_the_request_connection_in_order() -> None:
    conn = _RecordingConn()
    backend = PostgresSearchBackend(config=dict(_PGML_CONFIG))
    reasons: list[str] = []
    outcome = await backend.search(
        SearchRequest(
            conn=cast("object", conn),
            query=SearchQuery(query="housing", tenant_id=DEFAULT_TENANT_ID, k=3),
            semantic_query="housing",
            dynamic_predicates=[],
            degraded_pred="AND cv.degraded = FALSE",
            over_retrieve_n=0,
            reasons=reasons,
        )
    )
    normed = [_norm(s) for s in conn.sql]
    vector_idx = next(i for i, s in enumerate(normed) if "pgml.embed" in s)
    lexical_idx = next(i for i, s in enumerate(normed) if "plainto_tsquery" in s)
    assert vector_idx < lexical_idx  # dense arm before the lexical arm
    # The outcome hands the raw arm rows to the handler-side fusion, and the
    # degraded-probe count rides along (0: the probe found nothing to count).
    assert outcome.vector_rows == []
    assert outcome.lexical_rows == []
    assert outcome.excluded_count == 0


@pytest.mark.asyncio
async def test_handler_fuses_the_backend_outcome_into_results() -> None:
    """End-to-end: the handler consumes the outcome's arm rows (fusion pin)."""
    from uuid import uuid4

    hit = {
        "chunk_id": str(uuid4()),
        "text": "lexical match body",
        "source": "stub",
        "doc_id": str(uuid4()),
        "score": 0.42,
    }
    conn = _RecordingConn(vector_rows=[hit])
    handler = QueryHandler(
        cast("object", _RecordingPool(conn)),
        embedder=None,
        reranker=None,
        config=dict(_PGML_CONFIG),
    )
    envelope = await handler.handle_search_with_meta(
        SearchQuery(query="housing", tenant_id=DEFAULT_TENANT_ID, k=3)
    )
    assert [r.text for r in envelope.results] == ["lexical match body"]


# ============================================================================
# Guaranteed honesty keys (PG path emits today's engine values)
# ============================================================================


@pytest.mark.asyncio
async def test_pg_path_envelope_carries_engine_modes() -> None:
    conn = _RecordingConn()
    handler = QueryHandler(
        cast("object", _RecordingPool(conn)),
        embedder=None,
        reranker=None,
        config=dict(_PGML_CONFIG),
    )
    envelope = await handler.handle_search_with_meta(
        SearchQuery(query="housing", tenant_id=DEFAULT_TENANT_ID, k=3)
    )
    assert envelope.vector_index_mode == PG_VECTOR_INDEX_MODE == "hnsw_ann"
    assert envelope.lexical_index_mode == PG_LEXICAL_INDEX_MODE == "gin_auto"
    # Both modes are the engine defaults: no conditional note on this path.
    assert envelope.vector_index_note is None
    assert envelope.lexical_index_note is None


@pytest.mark.asyncio
async def test_pg_path_payload_guaranteed_keys_survive_degraded_empty() -> None:
    """The modes are guaranteed keys on EVERY outcome, degraded_empty too."""
    conn = _RecordingConn()
    handler = QueryHandler(
        cast("object", _RecordingPool(conn)),
        embedder=None,
        reranker=None,
        config={**_PGML_CONFIG, "embedding": {"provider": "ollama", "model": "stub"}},
    )
    envelope = await handler.handle_search_with_meta(
        SearchQuery(query="housing", tenant_id=DEFAULT_TENANT_ID, k=3)
    )
    assert envelope.status == "degraded_empty"  # no embedder wired: honest failure
    assert REASON_NO_EMBEDDER in envelope.degraded_excluded.reasons
    payload = envelope_to_payload(envelope)
    assert payload["vector_index_mode"] == "hnsw_ann"
    assert payload["lexical_index_mode"] == "gin_auto"
    assert "vector_index_note" not in payload
    assert "lexical_index_note" not in payload


def test_note_conditional_shape_follows_embedding_note_contract() -> None:
    """Notes ride the payload only when set; modes are always present."""
    exact_scan_note = "Exact scan is in use. Results are exact; large corpora will be slower."
    rebuild_note = "The text index is being rebuilt. Recent chunks may be missing from results."
    base_keys = {
        "status",
        "result",
        "degraded_excluded",
        "vector_index_mode",
        "lexical_index_mode",
    }
    healthy = finalize_envelope([], [])
    assert set(envelope_to_payload(healthy)) == base_keys

    noted = finalize_envelope([], [], vector_index_note=exact_scan_note)
    payload = envelope_to_payload(noted)
    assert payload["vector_index_note"] == exact_scan_note
    assert "lexical_index_note" not in payload  # only the set note appears

    lexical_noted = finalize_envelope([], [], lexical_index_note=rebuild_note)
    lpayload = envelope_to_payload(lexical_noted)
    assert "vector_index_note" not in lpayload
    assert set(lpayload) == base_keys | {"lexical_index_note"}
