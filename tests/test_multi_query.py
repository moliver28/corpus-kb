"""Issue #136 multi-query retrieval: rewriter, fan-out, fusion, honesty.

Layers pinned here:

* Settings: the ``retrieval.multi_query`` block loader (typed reads,
  loud failures on bad values).
* Rewriter: the pure functions (SHA256 cache key, stable seed, rewrite
  vetting) and the fail-open client contract with a stubbed chat edge.
* Pipeline: flag OFF keeps the single-query run and the exact envelope
  shape; flag ON fans out one backend run per rewrite and fuses all legs
  through the N-leg RRF port; a down rewriter degrades to the
  single-query path with the honest note; a score reranker sees the
  ORIGINAL query and the FUSED list (rerank after fusion).
* Wiring: the QueryHandler constructor builds the rewriter only when the
  flag is on; the ask receipt rides ``result`` only when rewrites ran.

All offline: no Postgres, no Ollama.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid5

import httpx
import pytest

from corpus_kb.ask.composer import AnswerBundle
from corpus_kb.domain.models import DEFAULT_TENANT_ID, SearchQuery, SearchResult
from corpus_kb.handlers import query_handler as query_handler_module
from corpus_kb.handlers import query_multiquery as query_multiquery_module
from corpus_kb.handlers.query_integrity import (
    REASON_MULTI_QUERY_UNAVAILABLE,
    envelope_to_payload,
    finalize_envelope,
)
from corpus_kb.handlers.query_pipeline import search_with_meta
from corpus_kb.handlers.query_rewrites import (
    QueryRewriter,
    clean_rewrites,
    rewrite_cache_key,
    rewrite_seed,
)
from corpus_kb.rag.rerank_settings import RerankSettings
from corpus_kb.research.multi_query_settings import (
    MultiQuerySettings,
    load_multi_query_settings,
)
from corpus_kb.research.retrieval_settings import RetrievalSettings
from corpus_kb.research.search_settings import HnswSettings
from corpus_kb.storage.search_backend import SearchOutcome

_NS = UUID("6f6f6f6f-6f6f-6f6f-6f6f-6f6f6f6f6f6f")


def _cid(name: str) -> str:
    return str(uuid5(_NS, name))


def _row(chunk_id: str, text: str | None = None) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "text": text or f"t-{chunk_id[:8]}",
        "source": "stub",
        "doc_id": str(uuid5(_NS, "doc")),
        "score": 0.5,
    }


# ============================================================================
# Settings loader
# ============================================================================


def test_settings_default_off_with_sibling_defaults() -> None:
    settings = load_multi_query_settings({})
    assert settings.enabled is False
    assert settings.variants == 3
    assert settings.model == "qwen3:4b"
    assert settings.base_url == "http://localhost:11434"
    assert settings.timeout_seconds == 8.0


def test_settings_read_the_block_and_fill_defaults() -> None:
    config = {"retrieval": {"multi_query": {"enabled": True, "variants": 5}}}
    settings = load_multi_query_settings(config)
    assert settings.enabled is True
    assert settings.variants == 5
    assert settings.model == "qwen3:4b"


@pytest.mark.parametrize("bad", [0, 11, "3", True, None])
def test_settings_reject_bad_variants(bad: object) -> None:
    config: dict[str, Any] = {"retrieval": {"multi_query": {"variants": bad}}}
    with pytest.raises(ValueError):
        load_multi_query_settings(config)


def test_settings_reject_bad_enabled_and_timeout() -> None:
    with pytest.raises(ValueError):
        load_multi_query_settings({"retrieval": {"multi_query": {"enabled": "yes"}}})
    with pytest.raises(ValueError):
        load_multi_query_settings({"retrieval": {"multi_query": {"timeout_seconds": 0}}})


# ============================================================================
# Rewriter pure functions + fail-open client
# ============================================================================


def test_cache_key_and_seed_are_stable_pure_functions() -> None:
    one = rewrite_cache_key("housing", "qwen3:4b", 3)
    two = rewrite_cache_key("housing", "qwen3:4b", 3)
    assert one == two
    assert rewrite_cache_key("housing", "qwen3:4b", 4) != one
    assert rewrite_seed(one) == rewrite_seed(one)
    assert isinstance(rewrite_seed(one), int)


def test_clean_rewrites_parses_and_caps() -> None:
    rewrites, ok = clean_rewrites('{"rewrites": ["a", "b", "c"]}', 2, "q")
    assert rewrites == ["a", "b"]
    assert ok is True


def test_clean_rewrites_drops_original_and_duplicates() -> None:
    rewrites, ok = clean_rewrites('{"rewrites": ["Housing", "a", "A", "a "]}', 5, "housing")
    assert rewrites == ["a"]
    assert ok is True


def test_clean_rewrites_empty_or_broken_reply_degrades() -> None:
    assert clean_rewrites('{"rewrites": []}', 3, "q") == ([], False)
    assert clean_rewrites("no json here", 3, "q") == ([], False)
    assert clean_rewrites('["a list"]', 3, "q") == ([], False)


class _StubRewriter(QueryRewriter):
    """QueryRewriter with the chat edge canned; failures injectable."""

    def __init__(self, replies: list[Any], variants: int = 3) -> None:
        super().__init__(
            model="stub", base_url="http://localhost:11434", timeout_seconds=1, variants=variants
        )
        self.replies = list(replies)
        self.chat_calls = 0

    def _client_chat(self, query: str, cache_key: str) -> str:
        self.chat_calls += 1
        reply = self.replies[min(self.chat_calls - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return str(reply)


def test_rewriter_caches_by_query_so_two_calls_hit_the_llm_once() -> None:
    stub = _StubRewriter(['{"rewrites": ["r1", "r2"]}'])
    first = stub.rewrites("housing")
    second = stub.rewrites("housing")
    assert first == ["r1", "r2"]
    assert second == first
    assert stub.chat_calls == 1
    assert stub.rewrites("other query") is not None
    assert stub.chat_calls == 2


def test_rewriter_fails_open_on_network_and_unusable_replies() -> None:
    down = _StubRewriter([httpx.ConnectError("down")])
    assert down.rewrites("housing") is None
    empty = _StubRewriter(['{"rewrites": []}'])
    assert empty.rewrites("housing") is None


# ============================================================================
# Pipeline: fake pool + fake backend over search_with_meta
# ============================================================================


class _FakeConn:
    async def execute(self, sql: str, *args: object) -> str:
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object]:
        return {"extversion": "0.8.2"}

    def transaction(self) -> _FakeTx:
        return _FakeTx(self)


class _FakeTx:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self.conn

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakePool:
    def __init__(self) -> None:
        self.conn = _FakeConn()

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.conn)


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self.conn

    async def __aexit__(self, *exc: object) -> bool:
        return False


# The pool seam: the fake stand-in typed as the asyncpg pool (the same
# cast idiom the determinism tests use for the scripted pool).
def _fake_pool() -> Any:
    return _FakePool()


class _FakeBackend:
    """Arm rows keyed by the semantic query; records the leg queries."""

    def __init__(
        self,
        rows_by_query: dict[str, tuple[list[dict[str, object]], list[dict[str, object]]]],
    ) -> None:
        self.rows = rows_by_query
        self.calls: list[str] = []

    async def search(self, request: Any) -> SearchOutcome:
        self.calls.append(request.semantic_query)
        vec, lex = self.rows.get(request.semantic_query, ([], []))
        return SearchOutcome(
            vector_rows=list(vec),
            lexical_rows=list(lex),
            excluded_count=0,
            vector_index_mode="hnsw_ann",
            lexical_index_mode="gin_auto",
        )


FLAG_OFF_KEYS = {
    "status",
    "result",
    "degraded_excluded",
    "vector_index_mode",
    "lexical_index_mode",
    "meta_hits",
}


async def _run_search(
    backend: _FakeBackend,
    *,
    rewriter: QueryRewriter | None = None,
    multi: MultiQuerySettings | None = None,
    reranker: object | None = None,
    rerank_settings: RerankSettings | None = None,
) -> tuple[Any, dict[str, Any]]:
    envelope = await search_with_meta(
        pool=_fake_pool(),
        search_backend=backend,
        reranker=reranker,
        rerank_settings=rerank_settings or RerankSettings(),
        retrieval_settings=RetrievalSettings(expand_to_parent=False),
        hnsw_settings=HnswSettings(),
        exclude_degraded=True,
        self_query_parser=None,
        self_query_enabled=False,
        query=SearchQuery(query="housing", tenant_id=DEFAULT_TENANT_ID, k=5),
        multi_query_settings=multi or MultiQuerySettings(),
        rewriter=rewriter,
    )
    return envelope, envelope_to_payload(envelope)


async def test_flag_off_is_the_single_query_pipeline() -> None:
    """OFF: one backend run, exact envelope key-set, migration-024 order."""
    c0, c1, c2 = _cid("off0"), _cid("off1"), _cid("off2")
    backend = _FakeBackend({"housing": ([_row(c2)], [_row(c0), _row(c1)])})
    envelope, payload = await _run_search(backend)
    assert backend.calls == ["housing"]
    assert set(payload) == FLAG_OFF_KEYS
    # c2 and c0 tie at 1/(rrf_k+1); chunk id ASC decides; c1 trails at 1/62.
    assert [str(r.chunk_id) for r in envelope.results] == [*sorted([c0, c2], key=UUID), c1]


async def test_flag_off_omits_multi_query_params_entirely() -> None:
    """The no-rewriter call shape (older callers) returns the same envelope."""
    c0, c2 = _cid("om0"), _cid("om2")
    rows: dict[str, tuple[list[dict[str, object]], list[dict[str, object]]]] = {
        "housing": ([_row(c2)], [_row(c0)])
    }
    with_params, _ = await _run_search(_FakeBackend(dict(rows)))
    envelope = await search_with_meta(
        pool=_fake_pool(),
        search_backend=_FakeBackend(dict(rows)),
        reranker=None,
        rerank_settings=RerankSettings(),
        retrieval_settings=RetrievalSettings(expand_to_parent=False),
        hnsw_settings=HnswSettings(),
        exclude_degraded=True,
        self_query_parser=None,
        self_query_enabled=False,
        query=SearchQuery(query="housing", tenant_id=DEFAULT_TENANT_ID, k=5),
    )
    assert [r.model_dump() for r in envelope.results] == [
        r.model_dump() for r in with_params.results
    ]
    assert envelope.query_rewrites is None


async def test_flag_on_fans_out_and_fuses_all_legs() -> None:
    """ON: one backend run per rewrite, all legs fused, receipt attached."""
    c0, c2, c3, c4 = _cid("on0"), _cid("on2"), _cid("on3"), _cid("on4")
    backend = _FakeBackend(
        {
            "housing": ([_row(c2)], [_row(c0)]),
            "housing affordability": ([_row(c3)], []),
            "rent trends": ([], [_row(c4)]),
        }
    )
    stub = _StubRewriter(['{"rewrites": ["housing affordability", "rent trends"]}'])
    envelope, payload = await _run_search(
        backend, rewriter=stub, multi=MultiQuerySettings(enabled=True)
    )
    assert backend.calls == ["housing", "housing affordability", "rent trends"]
    assert payload["query_rewrites"] == ["housing affordability", "rent trends"]
    # Every leg's rank-1 hit reaches the fused list; all four tie at
    # 1/(rrf_k+1), so the migration-024 total order is chunk id ASC.
    ids = [str(r.chunk_id) for r in envelope.results]
    assert sorted(ids, key=UUID) == sorted([c0, c2, c3, c4], key=UUID)
    assert ids == sorted(ids, key=UUID)
    assert all(abs(r.score - 1.0 / 61) < 1e-12 for r in envelope.results)
    assert stub.chat_calls == 1


async def test_flag_on_is_deterministic_across_runs() -> None:
    """Same query twice (fresh stub, same inputs): identical output."""
    c0, c2, c3 = _cid("det0"), _cid("det2"), _cid("det3")
    rows: dict[str, tuple[list[dict[str, object]], list[dict[str, object]]]] = {
        "housing": ([_row(c2)], [_row(c0)]),
        "housing costs": ([_row(c3)], []),
    }

    async def one_run() -> tuple[Any, dict[str, Any]]:
        stub = _StubRewriter(['{"rewrites": ["housing costs"]}'])
        return await _run_search(
            _FakeBackend(dict(rows)), rewriter=stub, multi=MultiQuerySettings(enabled=True)
        )

    one_env, one_payload = await one_run()
    two_env, two_payload = await one_run()
    assert one_payload["query_rewrites"] == two_payload["query_rewrites"] == ["housing costs"]
    assert [(str(r.chunk_id), r.score) for r in one_env.results] == [
        (str(r.chunk_id), r.score) for r in two_env.results
    ]


async def test_down_rewriter_degrades_to_single_query_with_note() -> None:
    """LLM down: single-query results, the honest reason, no receipt key."""
    c0, c2 = _cid("dn0"), _cid("dn2")
    rows: dict[str, tuple[list[dict[str, object]], list[dict[str, object]]]] = {
        "housing": ([_row(c2)], [_row(c0)])
    }
    off_envelope, _ = await _run_search(_FakeBackend(dict(rows)))
    stub = _StubRewriter([httpx.ConnectError("down")])
    envelope, payload = await _run_search(
        _FakeBackend(dict(rows)), rewriter=stub, multi=MultiQuerySettings(enabled=True)
    )
    assert envelope.degraded_excluded.reasons == [REASON_MULTI_QUERY_UNAVAILABLE]
    assert "query_rewrites" not in payload
    assert [r.model_dump() for r in envelope.results] == [
        r.model_dump() for r in off_envelope.results
    ]


class _ScoringReranker:
    """The ``score`` seam: records what the rerank stage saw."""

    model = "stub-reranker"
    rerank_mode = "stub"

    def __init__(self, scores_by_text: dict[str, float]) -> None:
        self.scores = scores_by_text
        self.queries: list[str] = []
        self.batches: list[list[str]] = []

    def score(self, query: str, texts: list[str]) -> list[float]:
        self.queries.append(query)
        self.batches.append(list(texts))
        return [self.scores[t] for t in texts]


async def test_reranker_runs_after_fusion_on_the_original_query() -> None:
    """Rewrites -> fusion -> reranker: the reranker grades the FUSED list
    against the ORIGINAL query, and rewrite-only hits are in the batch."""
    c_a, c_b = _cid("rr-a"), _cid("rr-b")
    text_a, text_b = "alpha housing text", "beta affordability text"
    backend = _FakeBackend(
        {
            "housing": ([_row(c_a, text_a)], []),
            "housing prices": ([_row(c_b, text_b)], []),
        }
    )
    stub = _StubRewriter(['{"rewrites": ["housing prices"]}'])
    reranker = _ScoringReranker({text_a: 0.9, text_b: 0.5})
    envelope, payload = await _run_search(
        backend,
        rewriter=stub,
        multi=MultiQuerySettings(enabled=True),
        reranker=reranker,
        rerank_settings=RerankSettings(calibration="none"),
    )
    assert reranker.queries == ["housing"]
    assert set(reranker.batches[0]) == {text_a, text_b}
    assert [str(r.chunk_id) for r in envelope.results] == [c_a, c_b]
    assert [r.score for r in envelope.results] == [0.9, 0.5]
    assert payload["query_rewrites"] == ["housing prices"]


# ============================================================================
# Handler wiring + ask receipt
# ============================================================================


async def test_handler_builds_rewriter_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The QueryHandler constructor wires the rewriter behind the flag."""
    config_on: dict[str, Any] = {
        "retrieval": {
            "multi_query": {
                "enabled": True,
                "variants": 2,
                "model": "stub",
                "base_url": "http://localhost:11434",
                "timeout_seconds": 1,
            },
            "expand_to_parent": False,
        },
        "reranking": {"enabled": False},
    }
    seen_kwargs: dict[str, Any] = {}

    def stub_factory(**kwargs: Any) -> _StubRewriter:
        seen_kwargs.update(kwargs)
        return _StubRewriter(['{"rewrites": ["housing costs"]}'], variants=kwargs["variants"])

    monkeypatch.setattr(query_multiquery_module, "QueryRewriter", stub_factory)
    backend = _FakeBackend({"housing": ([], []), "housing costs": ([_row(_cid("hw1"))], [])})
    handler = query_handler_module.QueryHandler(
        _FakePool(), embedder=None, reranker=None, config=config_on, search_backend=backend
    )
    assert seen_kwargs == {
        "model": "stub",
        "base_url": "http://localhost:11434",
        "timeout_seconds": 1,
        "variants": 2,
    }
    envelope = await handler.handle_search_with_meta(
        SearchQuery(query="housing", tenant_id=DEFAULT_TENANT_ID, k=5)
    )
    assert backend.calls == ["housing", "housing costs"]
    assert envelope.query_rewrites == ["housing costs"]

    config_off: dict[str, Any] = {"retrieval": {"expand_to_parent": False}}
    backend_off = _FakeBackend({})
    handler_off = query_handler_module.QueryHandler(
        _FakePool(), embedder=None, reranker=None, config=config_off, search_backend=backend_off
    )
    envelope_off = await handler_off.handle_search_with_meta(
        SearchQuery(query="housing", tenant_id=DEFAULT_TENANT_ID, k=5)
    )
    assert backend_off.calls == ["housing"]
    assert envelope_off.query_rewrites is None


def test_envelope_carries_query_rewrites_only_when_set() -> None:
    result = SearchResult(
        chunk_id=UUID(_cid("env")), text="t", score=1.0, source="s", doc_id=UUID(_cid("doc"))
    )
    with_rewrites = finalize_envelope([result], [], query_rewrites=["r1"])
    assert envelope_to_payload(with_rewrites)["query_rewrites"] == ["r1"]
    without = finalize_envelope([result], [])
    assert "query_rewrites" not in envelope_to_payload(without)


def test_ask_bundle_emits_query_rewrites_only_when_set() -> None:
    bundle = AnswerBundle("success", "a", [], [], {}, "m", None, {}, ["r1"])
    assert bundle.to_payload()["result"]["query_rewrites"] == ["r1"]
    plain = AnswerBundle("success", "a", [], [], {}, "m", None, {})
    assert "query_rewrites" not in plain.to_payload()["result"]
