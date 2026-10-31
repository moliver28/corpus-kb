"""Stage 3 degraded-policy + search-honesty tests (OFFLINE).

No Postgres, no Ollama: a recording fake connection captures the SQL the
handler builds (same pattern as test_vector_search_ordering) and fake
embedders drive the zero-query guard. The degraded COLUMNS themselves come
from migration 022 (a parallel stage); these tests only verify the SQL and
envelope contracts around them.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from corpus_kb._setup.validate_configs import (
    RUNTIME_CONFIG_KEYS,
    load_yaml,
    validate_runtime_config,
)
from corpus_kb.domain.models import SearchQuery, SearchResult, SearchSimilarQuery
from corpus_kb.handlers.query_handler import QueryHandler
from corpus_kb.handlers.query_integrity import (
    REASON_FILTER_EMPTY,
    REASON_ZERO_QUERY,
    REEMBED_REMEDY,
    STATUS_DEGRADED_EMPTY,
    STATUS_SUCCESS,
    degraded_candidate_predicate,
    envelope_to_payload,
    finalize_envelope,
    is_all_zero,
)

ROOT = Path(__file__).resolve().parent.parent


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


# ============================================================================
# Fakes (recording conn/pool, stub embedders)
# ============================================================================


class _RecordingConn:
    """Asyncpg-connection stand-in: records SQL, returns canned rows."""

    def __init__(
        self,
        extversion: str | None = "0.8.2",
        fail_vector_fetch: bool = False,
        vector_rows: list[dict[str, object]] | None = None,
        degraded_probe_count: int | None = None,
    ) -> None:
        self.sql: list[str] = []
        self.extversion = extversion
        self.fail_vector_fetch = fail_vector_fetch
        self.vector_rows = vector_rows or []
        self.degraded_probe_count = degraded_probe_count

    async def execute(self, sql: str, *args: object) -> str:
        self.sql.append(sql)
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        self.sql.append(sql)
        if self.fail_vector_fetch and "pgml.embed" in sql:
            raise RuntimeError("undefined column: cv.degraded (pre-migration stand-in)")
        if "pgml.embed" in sql or "<=>" in sql:
            return self.vector_rows
        return []  # FTS arm: no matches

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        self.sql.append(sql)
        if "pg_extension" in sql:
            return None if self.extversion is None else {"extversion": self.extversion}
        if "AS candidates" in sql:
            # The M2 degraded-filter count probe. None (default) = probe not
            # scripted: the handler treats it as "no rows excluded".
            if self.degraded_probe_count is None:
                return None
            return {
                "candidates": float(self.degraded_probe_count),
            }
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


class _ZeroEmbedder:
    """Embedder stub whose every vector is the all-zero degraded marker."""

    dimensions = 8
    model = "zero-stub"

    def embed(self, text: str) -> list[float]:
        return [0.0] * self.dimensions

    def embed_matryoshka(self, text: str, dim: int) -> list[float]:
        return [0.0] * dim


class _RealEmbedder:
    """Embedder stub with stable non-zero vectors."""

    dimensions = 8
    model = "real-stub"

    def embed(self, text: str) -> list[float]:
        return [0.1] * self.dimensions

    def embed_matryoshka(self, text: str, dim: int) -> list[float]:
        return [0.2] * dim


def _handler(
    pool: _RecordingPool,
    config: dict[str, object],
    embedder: object | None = None,
) -> QueryHandler:
    return QueryHandler(
        cast("object", pool),
        embedder=cast("object", embedder),
        reranker=None,
        config=config,
    )


_PGML_CONFIG: dict[str, object] = {
    "embedding": {"provider": "pgml", "model": "qwen3-embedding:4b"},
    "retrieval": {"exclude_degraded": True},
    "search": {"reranker": "none"},
    "reranking": {"enabled": False},
}

_OLLAMA_CONFIG: dict[str, object] = {
    "embedding": {"provider": "ollama", "model": "stub", "dimensions": 8},
    "retrieval": {"exclude_degraded": True},
    "search": {"reranker": "none"},
    "reranking": {"enabled": False},
}


def _sqls(conn: _RecordingConn, needle: str) -> list[str]:
    return [_norm(s) for s in conn.sql if needle in s]


def _vector_sqls(conn: _RecordingConn) -> list[str]:
    return [
        s
        for s in (_norm(x) for x in conn.sql)
        if "chunks_vectors" in s
        and "to_tsvector" not in s
        and "pg_extension" not in s
        and "AS candidates" not in s  # the M2 count probe is not a vector arm
    ]


# ============================================================================
# Pure helpers (handlers/query_integrity.py)
# ============================================================================


def test_is_all_zero() -> None:
    assert is_all_zero([0.0, 0.0])
    assert is_all_zero([])
    assert not is_all_zero([0.0, 0.1])
    assert not is_all_zero(None)


def test_degraded_candidate_predicate_gated() -> None:
    assert degraded_candidate_predicate(True) == "AND cv.degraded = FALSE"
    assert degraded_candidate_predicate(True, alias="cand") == "AND cand.degraded = FALSE"
    assert degraded_candidate_predicate(False) == ""


def test_finalize_envelope_success_with_results() -> None:
    result = SearchResult(chunk_id=uuid4(), text="t", score=1.0, source="s", doc_id=uuid4())
    envelope = finalize_envelope([result], ["zero-query-embedding"])
    assert envelope.status == STATUS_SUCCESS
    assert envelope.remedy is None
    # count = rows the degraded filter excluded (probe-backed); an arm
    # impairment with results is recorded in reasons with count 0.
    assert envelope.degraded_excluded.count == 0
    assert envelope.degraded_excluded.reasons == ["zero-query-embedding"]


def test_finalize_envelope_count_carries_probe_excluded_rows() -> None:
    """count semantics: the M2 probe's excluded-row count, not len(reasons)."""
    envelope = finalize_envelope([], [REASON_ZERO_QUERY], excluded_count=7)
    assert envelope.status == STATUS_DEGRADED_EMPTY
    assert envelope.degraded_excluded.count == 7
    assert envelope.degraded_excluded.reasons == [REASON_ZERO_QUERY]
    assert envelope.remedy == REEMBED_REMEDY


def test_finalize_envelope_empty_with_reasons_is_degraded_empty() -> None:
    envelope = finalize_envelope([], [REASON_ZERO_QUERY])
    assert envelope.status == STATUS_DEGRADED_EMPTY
    remedy = envelope.remedy
    assert remedy == REEMBED_REMEDY
    assert remedy is not None and "corpus-kb ingest --reembed" in remedy
    assert envelope.degraded_excluded.reasons == [REASON_ZERO_QUERY]
    # No probe ran for this reason shape -> the filter excluded nothing.
    assert envelope.degraded_excluded.count == 0


def test_finalize_envelope_empty_without_reasons_is_success() -> None:
    """Healthy corpus + junk query = plain empty success, NOT degraded_empty."""
    envelope = finalize_envelope([], [])
    assert envelope.status == STATUS_SUCCESS
    assert envelope.remedy is None
    assert envelope.degraded_excluded.count == 0


def test_envelope_to_payload_shape() -> None:
    envelope = finalize_envelope([], [REASON_ZERO_QUERY], excluded_count=4)
    payload = envelope_to_payload(envelope)
    # Storage-portability §6: the engine index modes are guaranteed keys.
    assert set(payload) == {
        "status",
        "result",
        "degraded_excluded",
        "remedy",
        "vector_index_mode",
        "lexical_index_mode",
        "meta_hits",
    }
    assert payload["status"] == STATUS_DEGRADED_EMPTY
    assert payload["degraded_excluded"] == {"count": 4, "reasons": [REASON_ZERO_QUERY]}
    healthy = envelope_to_payload(finalize_envelope([], []))
    assert set(healthy) == {
        "status",
        "result",
        "degraded_excluded",
        "vector_index_mode",
        "lexical_index_mode",
        "meta_hits",
    }
    assert healthy["degraded_excluded"] == {"count": 0, "reasons": []}
    # Postgres defaults on the legacy constructor path; the conditional
    # notes are absent (the embedding_mode/embedding_note contract shape).
    assert healthy["vector_index_mode"] == "hnsw_ann"
    assert healthy["lexical_index_mode"] == "gin_auto"
    assert "vector_index_note" not in healthy
    assert "lexical_index_note" not in healthy


# ============================================================================
# Degraded filter in candidate CTEs (SQL construction, recording conn)
# ============================================================================


@pytest.mark.asyncio
async def test_pgml_candidate_cte_excludes_degraded() -> None:
    conn = _RecordingConn()
    await _handler(_RecordingPool(conn), dict(_PGML_CONFIG)).handle_search(
        SearchQuery(query="housing policy", k=5)
    )
    vector_sqls = _vector_sqls(conn)
    assert vector_sqls, "pgml vector arm did not run"
    assert all("cv.degraded = FALSE" in s for s in vector_sqls), vector_sqls
    # FTS arm: no degraded predicate (it reads chunks, not chunks_vectors).
    fts = _sqls(conn, "plainto_tsquery")
    assert fts and all("degraded" not in s for s in fts)


@pytest.mark.asyncio
async def test_flag_off_keeps_candidate_sql_unfiltered() -> None:
    config = {
        "embedding": {"provider": "pgml", "model": "qwen3-embedding:4b"},
        "retrieval": {"exclude_degraded": False},
    }
    conn = _RecordingConn()
    await _handler(_RecordingPool(conn), config).handle_search(
        SearchQuery(query="housing policy", k=5)
    )
    vector_sqls = _vector_sqls(conn)
    assert vector_sqls
    assert all("degraded" not in s for s in vector_sqls)


@pytest.mark.asyncio
async def test_ollama_exact_and_matryoshka_ctes_exclude_degraded() -> None:
    conn = _RecordingConn()
    await _handler(_RecordingPool(conn), dict(_OLLAMA_CONFIG), _RealEmbedder()).handle_search(
        SearchQuery(query="housing policy", k=5)
    )
    vector_sqls = _vector_sqls(conn)
    assert vector_sqls
    assert all("cv.degraded = FALSE" in s for s in vector_sqls)


@pytest.mark.asyncio
async def test_matryoshka_candidate_cte_excludes_degraded() -> None:
    config = {
        "embedding": {"provider": "ollama", "model": "stub", "dimensions": 8},
        "retrieval": {"exclude_degraded": True, "matryoshka_enabled": True},
    }
    conn = _RecordingConn()
    await _handler(_RecordingPool(conn), config, _RealEmbedder()).handle_search(
        SearchQuery(query="housing policy", k=5)
    )
    matryoshka_sqls = [s for s in _vector_sqls(conn) if "vector_1024" in s]
    assert matryoshka_sqls
    assert all("cv.degraded = FALSE" in s for s in matryoshka_sqls)


@pytest.mark.asyncio
async def test_search_similar_filters_candidates_never_seed() -> None:
    conn = _RecordingConn()
    handler = _handler(_RecordingPool(conn), dict(_OLLAMA_CONFIG), _RealEmbedder())
    await handler.handle_search_similar(SearchSimilarQuery(chunk_id=uuid4(), k=5))
    similar_sqls = _sqls(conn, "candidate_chunk_id")
    assert len(similar_sqls) == 1
    sql = similar_sqls[0]
    # Exactly ONE degraded predicate: on the candidate WHERE clause.
    assert sql.count("cv.degraded = FALSE") == 1
    # The seed subqueries stay unfiltered — seed documents stay discoverable.
    assert sql.count("SELECT embedding FROM chunks_vectors WHERE chunk_id = $1") == 2
    seed_fragments = sql.split("SELECT embedding FROM chunks_vectors WHERE chunk_id = $1")[1:]
    assert all(not frag.startswith(") AND cv.degraded") for frag in seed_fragments)


@pytest.mark.asyncio
async def test_selectivity_probe_matches_candidate_universe() -> None:
    """The U21 probe counts the SAME rows the candidate CTE will scan."""
    config = {
        "embedding": {"provider": "pgml", "model": "qwen3-embedding:4b"},
        "retrieval": {"exclude_degraded": True},
    }
    conn = _RecordingConn()
    from corpus_kb.rag.self_query import ParsedQuery, Predicate

    class _StubParser:
        def parse(self, query: str) -> ParsedQuery:
            return ParsedQuery(
                semantic_query=query,
                predicates=[Predicate(target="documents.source_type", op="=", value="markdown")],
            )

    handler = QueryHandler(
        cast("object", _RecordingPool(conn)),
        embedder=None,
        reranker=None,
        self_query_parser=_StubParser(),
        config=config,
    )
    await handler.handle_search(SearchQuery(query="housing", k=5, self_query=True))
    probes = _sqls(conn, "count(*) FILTER")
    assert probes, "selectivity probe did not run"
    assert all("cv.degraded = FALSE" in s for s in probes)


# ============================================================================
# Zero-query-embedding guard + honest fallbacks
# ============================================================================


@pytest.mark.asyncio
async def test_zero_query_embedding_abstains_vector_arm() -> None:
    conn = _RecordingConn()
    envelope = await _handler(
        _RecordingPool(conn), dict(_OLLAMA_CONFIG), _ZeroEmbedder()
    ).handle_search_with_meta(SearchQuery(query="anything", k=5))
    # Vector arm never issued (no cosine comparison against chunks_vectors).
    assert not _vector_sqls(conn)
    # Lexical arm still ran.
    assert _sqls(conn, "plainto_tsquery")
    assert envelope.degraded_excluded.reasons == [REASON_ZERO_QUERY]


@pytest.mark.asyncio
async def test_vector_arm_error_surfaces_in_envelope() -> None:
    """Pre-migration (undefined column) must surface, not silently fall back."""
    conn = _RecordingConn(fail_vector_fetch=True)
    envelope = await _handler(_RecordingPool(conn), dict(_PGML_CONFIG)).handle_search_with_meta(
        SearchQuery(query="housing", k=5)
    )
    reasons = envelope.degraded_excluded.reasons
    assert any(r.startswith("vector-arm-error") for r in reasons)
    # The error came from the degraded predicate on a vector fetch (the
    # recorded SQL names the candidate CTE), not from the lexical arm.
    assert any("candidate" in s for s in _vector_sqls(conn))
    # An arm error is an impairment, not an exclusion: count stays 0 (the
    # probe never ran) and the reason carries the honesty.
    assert envelope.degraded_excluded.count == 0


@pytest.mark.asyncio
async def test_ollama_without_embedder_is_recorded() -> None:
    conn = _RecordingConn()
    handler = _handler(_RecordingPool(conn), dict(_OLLAMA_CONFIG), None)
    envelope = await handler.handle_search_with_meta(SearchQuery(query="housing", k=5))
    assert "no-embedder" in envelope.degraded_excluded.reasons


@pytest.mark.asyncio
async def test_all_candidates_excluded_is_degraded_empty_with_remedy() -> None:
    conn = _RecordingConn(fail_vector_fetch=True)  # arm errors + FTS finds nothing
    envelope = await _handler(_RecordingPool(conn), dict(_PGML_CONFIG)).handle_search_with_meta(
        SearchQuery(query="housing", k=5)
    )
    assert envelope.status == STATUS_DEGRADED_EMPTY
    assert envelope.remedy is not None
    assert "Start Ollama" in envelope.remedy and "corpus-kb ingest --reembed" in envelope.remedy
    payload = envelope_to_payload(envelope)
    assert payload["status"] == STATUS_DEGRADED_EMPTY


@pytest.mark.asyncio
async def test_filter_active_reason_when_arm_runs_clean_and_empty() -> None:
    """Empty arm + probe says the filter removed rows -> degraded_empty, count N."""
    conn = _RecordingConn(degraded_probe_count=3)  # clean run, filter removed 3 candidates
    envelope = await _handler(_RecordingPool(conn), dict(_PGML_CONFIG)).handle_search_with_meta(
        SearchQuery(query="housing", k=5)
    )
    assert envelope.degraded_excluded.reasons == [REASON_FILTER_EMPTY]
    assert envelope.status == STATUS_DEGRADED_EMPTY
    assert envelope.degraded_excluded.count == 3
    assert envelope.remedy is not None
    # The disambiguation probe itself runs WITHOUT the degraded predicate
    # (it counts what the filter took out).
    probes = _sqls(conn, "AS candidates")
    assert len(probes) == 1
    assert "cv.degraded = FALSE" not in probes[0]


@pytest.mark.asyncio
async def test_legitimately_empty_search_is_plain_success_no_remedy() -> None:
    """Empty arm + probe says 0 excluded (nothing existed) -> success, no reasons."""
    conn = _RecordingConn(degraded_probe_count=0)  # empty tenant / filters match nothing
    envelope = await _handler(_RecordingPool(conn), dict(_PGML_CONFIG)).handle_search_with_meta(
        SearchQuery(query="housing", k=5)
    )
    assert envelope.status == STATUS_SUCCESS
    assert envelope.degraded_excluded.reasons == []
    assert envelope.degraded_excluded.count == 0
    assert envelope.remedy is None
    # The probe still ran (one cheap COUNT) to earn that honesty.
    assert len(_sqls(conn, "AS candidates")) == 1


@pytest.mark.asyncio
async def test_handle_search_returns_plain_list_backcompat() -> None:
    conn = _RecordingConn()
    results = await _handler(_RecordingPool(conn), dict(_PGML_CONFIG)).handle_search(
        SearchQuery(query="housing", k=5)
    )
    assert results == []


# ============================================================================
# build_embedding_input + reembed helper (tools/ingest_common.py)
# ============================================================================


def test_build_embedding_input_blurb_and_plain() -> None:
    from corpus_kb.tools.ingest_common import build_embedding_input

    assert build_embedding_input("body", "summary") == "summary\n\nbody"
    assert build_embedding_input("body", None) == "body"
    assert build_embedding_input("body", "") == "body"


def test_pipeline_uses_build_embedding_input() -> None:
    """Single-sourcing guard: the pipeline embeds the SAME bytes a reembed would.

    The embed call lives in _run_locked_stages (ingest_common) and the
    repair site in reembed_degraded (ingest_reembed) since the Wave 3B
    split; check BOTH modules so the guard survives further restructures.
    """
    from corpus_kb.tools import ingest_common, ingest_reembed

    source = inspect.getsource(ingest_common) + inspect.getsource(ingest_reembed)
    assert "build_embedding_input(" in source
    assert source.count("build_embedding_input(") >= 2  # pipeline site + reembed site


class _FakeConn:
    """Minimal asyncpg conn for reembed_degraded: scripted fetchval/fetch.

    ``transaction()`` records BEGIN/COMMIT markers in the SQL log so tests
    can pin that the tenant set_config is issued INSIDE the transaction (a
    LOCAL GUC on an autocommit connection evaporates with its statement).
    """

    def __init__(
        self,
        degraded_columns_present: int,
        candidate_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.sql: list[str] = []
        self._column_count = degraded_columns_present
        self.candidate_rows = candidate_rows or []

    async def execute(self, sql: str, *args: object) -> str:
        self.sql.append(sql)
        return "OK"

    async def fetchval(self, sql: str, *args: object) -> object:
        self.sql.append(sql)
        if "information_schema" in sql:
            return self._column_count
        return None

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        self.sql.append(sql)
        return self.candidate_rows

    def transaction(self):
        outer = self

        class _Tx:
            async def __aenter__(self) -> _FakeConn:
                outer.sql.append("BEGIN")
                return outer

            async def __aexit__(self, *exc: object) -> bool:
                outer.sql.append("COMMIT")
                return False

        return _Tx()


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self):
        outer = self

        class _Acquire:
            async def __aenter__(self) -> _FakeConn:
                return outer._conn

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Acquire()


def _candidate_row() -> dict[str, object]:
    return {
        "chunk_id": str(uuid4()),
        "doc_id": str(uuid4()),
        "text": "body text",
        "context_blurb": "a blurb",
        "was_stale": False,
    }


@pytest.mark.asyncio
async def test_reembed_unavailable_pre_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_kb.tools import ingest_common

    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    summary = await ingest_common.reembed_degraded(
        cast("object", _FakePool(_FakeConn(degraded_columns_present=1))),
        {"embedding": {"provider": "pgml"}},
    )
    assert summary["status"] == "unavailable"
    assert "migration 022" in str(summary["reason"])
    assert summary["scanned"] == 0


@pytest.mark.asyncio
async def test_reembed_repairs_and_clears_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_kb.tools import ingest_common, ingest_reembed

    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    row = _candidate_row()
    conn = _FakeConn(degraded_columns_present=2, candidate_rows=[row])
    captured: dict[str, object] = {}

    async def fake_embed_chunks(
        chunks: list,
        config: dict,
        pool: object = None,
        text_override: dict | None = None,
        document_text: str | None = None,
        meta: dict | None = None,
    ):
        captured["override"] = text_override
        for chunk in chunks:
            chunk.embedding = [0.5] * 4
        return False, None

    monkeypatch.setattr(ingest_reembed, "embed_chunks", fake_embed_chunks)
    summary = await ingest_common.reembed_degraded(
        cast("object", _FakePool(conn)),
        {"embedding": {"provider": "pgml", "dimensions": 4}},
    )
    assert summary == {"status": "success", "scanned": 1, "fixed": 1, "failed": 0}
    # The embedding input came from build_embedding_input (blurb + text).
    assert captured["override"] == {row["chunk_id"]: "a blurb\n\nbody text"}
    updates = [s for s in (_norm(x) for x in conn.sql) if s.startswith("UPDATE chunks")]
    assert len(updates) == 2  # chunks_vectors + chunks flag clears
    assert all("degraded = FALSE" in s for s in updates)


@pytest.mark.asyncio
async def test_reembed_counts_zero_output_as_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_kb.tools import ingest_common, ingest_reembed

    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    conn = _FakeConn(degraded_columns_present=2, candidate_rows=[_candidate_row()])

    async def fake_embed_chunks(
        chunks: list,
        config: dict,
        pool: object = None,
        text_override: dict | None = None,
        document_text: str | None = None,
        meta: dict | None = None,
    ):
        for chunk in chunks:
            chunk.embedding = [0.0] * 4
        return True, "Connection failed: pgml returned zero vectors"

    monkeypatch.setattr(ingest_reembed, "embed_chunks", fake_embed_chunks)
    summary = await ingest_common.reembed_degraded(
        cast("object", _FakePool(conn)),
        {"embedding": {"provider": "pgml", "dimensions": 4}},
    )
    assert summary["failed"] == 1
    assert summary["fixed"] == 0
    assert summary["degraded"] is True
    updates = [s for s in (_norm(x) for x in conn.sql) if s.startswith("UPDATE chunks")]
    assert updates == []  # a zero-vector "repair" must never clear the flag


@pytest.mark.asyncio
async def test_reembed_no_candidates_is_empty_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_kb.tools import ingest_common

    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    summary = await ingest_common.reembed_degraded(
        cast("object", _FakePool(_FakeConn(degraded_columns_present=2))),
        {"embedding": {"provider": "pgml"}},
    )
    assert summary == {"status": "success", "scanned": 0, "fixed": 0, "failed": 0}


@pytest.mark.asyncio
async def test_reembed_sets_tenant_guc_inside_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M1: set_config is_local only survives INSIDE a transaction.

    On an autocommit connection the GUC dies with its statement, so the
    scan/UPDATEs would run GUC-less and match zero rows under FORCE RLS —
    while the summary still claimed success.
    """
    from corpus_kb.tools import ingest_common, ingest_reembed

    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    conn = _FakeConn(degraded_columns_present=2, candidate_rows=[_candidate_row()])

    async def fake_embed_chunks(
        chunks: list,
        config: dict,
        pool: object = None,
        text_override: dict | None = None,
        document_text: str | None = None,
        meta: dict | None = None,
    ):
        for chunk in chunks:
            chunk.embedding = [0.5] * 4
        return False, None

    monkeypatch.setattr(ingest_reembed, "embed_chunks", fake_embed_chunks)
    await ingest_common.reembed_degraded(
        cast("object", _FakePool(conn)),
        {"embedding": {"provider": "pgml", "dimensions": 4}},
    )
    normed = [_norm(s) for s in conn.sql]
    # Two transactions (scan + write window); the tenant set_config must be
    # the FIRST statement inside each, never before BEGIN.
    begins = [i for i, s in enumerate(normed) if s == "BEGIN"]
    assert len(begins) == 2, normed
    set_configs = [i for i, s in enumerate(normed) if "set_config" in s]
    assert len(set_configs) == 2
    for begin_idx, guc_idx in zip(begins, set_configs, strict=True):
        assert begin_idx < guc_idx, normed
        # The GUC immediately opens the transaction's work, before any read.
        assert guc_idx == begin_idx + 1


@pytest.mark.asyncio
async def test_reembed_updates_pin_tenant_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two UPDATEs carry an explicit tenant_id predicate (defense in depth)."""
    from corpus_kb.tools import ingest_common, ingest_reembed

    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    conn = _FakeConn(degraded_columns_present=2, candidate_rows=[_candidate_row()])

    async def fake_embed_chunks(
        chunks: list,
        config: dict,
        pool: object = None,
        text_override: dict | None = None,
        document_text: str | None = None,
        meta: dict | None = None,
    ):
        for chunk in chunks:
            chunk.embedding = [0.5] * 4
        return False, None

    monkeypatch.setattr(ingest_reembed, "embed_chunks", fake_embed_chunks)
    await ingest_common.reembed_degraded(
        cast("object", _FakePool(conn)),
        {"embedding": {"provider": "pgml", "dimensions": 4}},
    )
    updates = [s for s in (_norm(x) for x in conn.sql) if s.startswith("UPDATE chunks")]
    assert len(updates) == 2
    assert all("AND tenant_id = $" in s for s in updates), updates


# ============================================================================
# Doctor additions (offline, scripted conn)
# ============================================================================


class _DoctorConn:
    """Scripted fetchval queue for the doctor DB checks."""

    def __init__(self, fetchvals: list[object]) -> None:
        self._fetchvals = list(fetchvals)
        self.sql: list[str] = []

    async def execute(self, sql: str, *args: object) -> str:
        self.sql.append(sql)
        return "OK"

    async def fetchval(self, sql: str, *args: object) -> object:
        self.sql.append(sql)
        return self._fetchvals.pop(0) if self._fetchvals else 0

    def transaction(self):
        outer = self

        class _Tx:
            async def __aenter__(self) -> _DoctorConn:
                return outer

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Tx()


@pytest.mark.asyncio
async def test_doctor_zero_vector_check_warns_with_reembed_fix() -> None:
    from corpus_kb._setup import doctor_research as dr

    warn = await dr._zero_vector_check(cast("object", _DoctorConn([7])))
    assert warn.status == dr.STATUS_WARN
    assert "7" in warn.detail
    assert "corpus-kb ingest --reembed" in warn.fix
    ok = await dr._zero_vector_check(cast("object", _DoctorConn([0])))
    assert ok.status == dr.STATUS_OK


@pytest.mark.asyncio
async def test_doctor_degraded_check_guards_missing_columns() -> None:
    from corpus_kb._setup import doctor_research as dr

    absent = await dr._degraded_flag_check(cast("object", _DoctorConn([1])))
    assert absent.status == dr.STATUS_WARN
    assert "migration 022 not applied" in absent.detail
    degraded = await dr._degraded_flag_check(cast("object", _DoctorConn([2, 3, 4])))
    assert degraded.status == dr.STATUS_WARN
    assert "3 vector row(s) + 4 chunk row(s)" in degraded.detail
    clean = await dr._degraded_flag_check(cast("object", _DoctorConn([2, 0, 0])))
    assert clean.status == dr.STATUS_OK


@pytest.mark.asyncio
async def test_doctor_ingest_runs_check_regclass_guarded() -> None:
    from corpus_kb._setup import doctor_research as dr

    absent = await dr._ingest_runs_check(cast("object", _DoctorConn([None])))
    assert absent.status == dr.STATUS_WARN
    assert "to_regclass" not in absent.detail and "ingest_runs table absent" in absent.detail
    incomplete = await dr._ingest_runs_check(cast("object", _DoctorConn(["rel", 2])))
    assert incomplete.status == dr.STATUS_WARN
    assert "never finished" in incomplete.detail
    clean = await dr._ingest_runs_check(cast("object", _DoctorConn(["rel", 0])))
    assert clean.status == dr.STATUS_OK


@pytest.mark.asyncio
async def test_doctor_db_checks_append_stage3_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """db_checks carries the three new checks after the DLQ block."""
    from corpus_kb._setup import doctor_research as dr

    # Deterministic: _events_head needs the eventsourcing app; the queue
    # below is scripted for db_checks' own reads only.
    async def no_head(conn: object) -> int | None:
        return None

    monkeypatch.setattr(dr, "_events_head", no_head)

    class _FullConn(_DoctorConn):
        async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
            self.sql.append(sql)
            return [
                {"relname": t}
                for t in (
                    "code_registry",
                    "codebook_versions",
                    "code_keywords",
                    "chunk_signals",
                    "chunk_keyword_hits",
                    "answer_ctx_vectors",
                    "batch_status",
                    "chunk_codes",
                    "final_codes",
                    "chunk_status",
                    "doc_extraction_audit",
                    "coding_gate_decisions",
                    "research_projects",
                    "research_speakers",
                    "research_transcript_text",
                    "research_units",
                    "research_exchanges",
                    "research_assignments",
                    "research_signals",
                    "research_runs",
                    "research_reviews",
                    "ingested_files",
                    "embedding_cache",
                    "research_observations",
                    "research_proposed_codes",
                    "research_noise_queue",
                    "research_keyword_hits",
                )
            ]

        async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
            self.sql.append(sql)
            return {"last_sequence": 0, "checkpoint_timestamp": None}

    # fetchval order: dlq, versions, runs, assignments | zero-vectors,
    # degraded-columns, to_regclass, running-count.
    conn = _FullConn([0, 0, 0, 0, 0, 2, 0, 0])
    checks = await dr.db_checks(cast("object", conn), {})
    names = [c.name for c in checks]
    assert names[-3:] == ["zero-vector chunks", "degraded flags", "ingest runs ledger"]
    # The stage-3 reads ran under the tenant GUC (FORCE RLS safety).
    guc_count = sum(1 for s in conn.sql if "app.current_tenant_id" in s)
    assert guc_count >= 2


# ============================================================================
# Config key validation (validate_configs.py runtime keys)
# ============================================================================


def test_runtime_config_keys_declare_stage3_key() -> None:
    # Config v2 moved the stage-3 keys under retrieval.* / reranking.*; the
    # legacy search.* spellings are LOADER aliases, not shipped-YAML keys.
    # The website-ingestion feature added the web.* fetch/crawl keys.
    # W4C config-truth added extraction.backend (the preferred backend key).
    # The storage-portability program added database.backend, graph.max_hops,
    # and the two DuckDB-side retrieval knobs (docs/research/
    # storage-portability-program.md §3).
    assert {
        "extraction.backend": str,
        "embedding.mrl_trained": bool,
        "embedding.late_chunking": bool,
        "embedding.query_instruction": bool,
        "storage.vector_type": str,
        "ingest.chunking.enabled": bool,
        "ingest.chunking.child_tokens": int,
        "ingest.chunking.chunk_tokens": int,
        "ingest.chunking.parent_tokens": int,
        "ingest.chunking.overlap_tokens": int,
        "ingest.chunking.respect_boundaries": list,
        "ingest.contextualize.enabled": bool,
        "ingest.contextualize.backend": str,
        "ingest.contextualize.model": str,
        "ingest.contextualize.prompt_profile": str,
        "ingest.contextualize.max_context_tokens": int,
        "ingest.contextualize.document_first_prompt": bool,
        "ingest.contextualize.apply_to": list,
        "retrieval.mode": str,
        # Storage-portability keys: the DuckDB-side search knobs. Postgres
        # ignores both (validate_configs pins the enum rules separately).
        "retrieval.vector_index": str,
        "retrieval.fts_rebuild_debounce_s": float,
        "retrieval.vector_candidates": int,
        "retrieval.lexical_candidates": int,
        "retrieval.rrf_k": int,
        "retrieval.expand_to_parent": bool,
        "retrieval.hnsw.m": int,
        "retrieval.hnsw.ef_construction": int,
        "reranking.enabled": bool,
        "reranking.return_top_k": int,
        # backend is the null-or-enum union rule in _reranking_block_errors,
        # deliberately not in the single-type map (null is the default).
        "reranking.llamacpp_url": str,
        "ingestion.default": str,
        "ingestion.strategies": dict,
        "ingestion.concurrency": int,
        "web.user_agent": str,
        "web.timeout_seconds": int,
        "web.max_bytes": int,
        "web.respect_robots": bool,
        "web.max_pages": int,
        "web.max_depth": int,
        "web.delay_seconds": float,
        "web.allow_private_addresses": bool,
        # Wave 4 topics layer (min_topic_size is the str|int union rule in
        # _topics_block_errors, deliberately not in the single-type map).
        "topics.enabled": bool,
        "topics.method": str,
        "topics.llm_reduction": bool,
        "topics.llm": str,
        "topics.llm_base_url": str,
        # UI SPA program Task 3 (the lifecycle SPA's runtime-tunable block).
        "ui.spa.poll_ms": int,
        "ui.spa.request_timeout_s": float,
        "ui.spa.max_upload_mb": int,
        # UX Wave 6 notebooks console (the [notebook] extra's flag + folder).
        "notebooks.enabled": bool,
        "notebooks.directory": str,
        # UX Wave 2: the /v1 OpenAI-compatibility gate (api/routes_v1.py).
        "server.openai_compat": bool,
        # Storage-portability program §3: the backend switch and the graph
        # traversal depth cap (enum/range rules live in _database_block_errors
        # and _max_hops_errors, outside the single-type map).
        "database.backend": str,
        "graph.max_hops": int,
        # UX Wave 4 tracing (utils/tracing.py is the typed reader).
        "observability.enabled": bool,
        "observability.otlp_endpoint": str,
        "observability.service_name": str,
        # Textbook-ingestion program Wave 1: the textbook gate + parser,
        # the queue worker knobs, the graph write policy, and the DuckDB
        # graph traversal engine (parser/mode/engine enums live in the
        # cross-field rules; poll_seconds and friends in _queue_block_errors).
        "textbook.min_pages": int,
        "textbook.window_pages": int,
        "textbook.parser": str,
        "textbook.cache_dir": str,
        # Textbook hardening: optional isolation, timeout, and cache-cap knobs.
        "textbook.window_subprocess": bool,
        "textbook.window_timeout_s": float,
        "textbook.cache_max_mb": int,
        # Rich-structure flag (textbook program W1-B).
        "textbook.structured_output": bool,
        "queue.autorun": bool,
        "queue.poll_seconds": int,
        "queue.stale_lease_seconds": int,
        "queue.max_attempts": int,
        "graph_policy.mode": str,
        "graph.duckdb_graph_backend": str,
    } == RUNTIME_CONFIG_KEYS


_INGESTION_KEYS: dict[str, object] = {"ingestion": {"default": "default", "strategies": {}}}
_EMBEDDING_KEYS: dict[str, object] = {"embedding": {"mrl_trained": False, "late_chunking": True}}
_EXTRACTION_KEYS: dict[str, object] = {"extraction": {"backend": "pgml"}}
_WEB_KEYS: dict[str, object] = {
    "web": {
        "user_agent": "corpus-kb/0.1 (+https://github.com/moliver28/corpus-kb)",
        "timeout_seconds": 30,
        "max_bytes": 5242880,
        "respect_robots": True,
        "max_pages": 5,
        "max_depth": 2,
        "delay_seconds": 1.0,
        "allow_private_addresses": False,
    }
}


def test_validate_runtime_config_accepts_bool() -> None:
    # Base: the shipped YAML (it carries every RUNTIME_CONFIG_KEY), with the
    # stage-3 flags flipped through their canonical retrieval.* home.
    config = cast(dict[str, object], load_yaml(ROOT / "config.yaml"))
    cast(dict[str, object], config["retrieval"]).update(
        {
            "exclude_degraded": True,
            "qa_context_in_fts": False,
            "matryoshka_enabled": False,
        }
    )
    assert validate_runtime_config(config) == []
    config_off = cast(dict[str, object], load_yaml(ROOT / "config.yaml"))
    cast(dict[str, object], config_off["retrieval"]).update(
        {
            "exclude_degraded": False,
            "qa_context_in_fts": False,
            "matryoshka_enabled": False,
        }
    )
    assert validate_runtime_config(config_off) == []


def test_validate_runtime_config_rejects_missing_and_mistyped() -> None:
    missing = validate_runtime_config({"retrieval": {"mode": "hybrid_rrf"}})
    assert any("missing required key" in e for e in missing)
    string_val = validate_runtime_config({"reranking": {"enabled": "true"}})
    assert any("must be bool" in e for e in string_val)
    no_keys = validate_runtime_config({})
    assert any("missing required key" in e for e in no_keys)


def test_shipped_config_yamls_carry_exclude_degraded() -> None:
    for rel in ("config.yaml", "src/corpus_kb/config.yaml"):
        cfg = load_yaml(ROOT / rel)
        errors = validate_runtime_config(cfg)
        assert errors == [], f"{rel}: {errors}"
        retrieval = cfg["retrieval"]
        assert retrieval["exclude_degraded"] is True
