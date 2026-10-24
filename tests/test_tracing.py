"""UX program Wave 4: OpenTelemetry tracing (OFFLINE tests).

Two tiers:

* Always-run (no SDK — ``opentelemetry-api`` is a core no-op tracer):
  the disabled setup path, the typed dependency error through the import
  seam, the no-op span helpers, and the envelope key-set pins that MUST
  hold with tracing off (mirrors test_search_honesty).
* SDK tier (``pytest.importorskip("opentelemetry.sdk")``): the real span
  tree through the REAL QueryHandler over the recording fake pool (same
  fakes as test_search_honesty), the ingest tree over a fake store, the
  route.decide child, and the ask composer stages. CI (3 OSes, no
  ``[otel]`` extra) skips these; a local venv with the extra runs them.
"""

from __future__ import annotations

import re
from typing import cast
from uuid import uuid4

import pytest

from corpus_kb.config import get_default_config
from corpus_kb.domain.models import RoutedQuery, SearchQuery, SearchResult
from corpus_kb.errors import DependencyMissingError
from corpus_kb.handlers.query_handler import QueryHandler
from corpus_kb.handlers.query_integrity import (
    REASON_ZERO_QUERY,
    SearchEnvelope,
    envelope_to_payload,
    finalize_envelope,
)
from corpus_kb.utils import tracing
from corpus_kb.utils.tracing import (
    ATTR_ROUTE,
    SPAN_ASK_GENERATE,
    SPAN_ASK_RETRIEVE,
    SPAN_ASK_VERIFY,
    SPAN_CHUNK,
    SPAN_EMBED,
    SPAN_EXTRACT,
    SPAN_INGEST,
    SPAN_PARTITION,
    SPAN_QUERY,
    SPAN_RETRIEVE_DEGRADED_PROBE,
    SPAN_RETRIEVE_FTS,
    SPAN_RETRIEVE_VECTOR,
    SPAN_ROUTE_DECIDE,
    SPAN_RRF_FUSION,
    SPAN_STORE,
    OtelDependencyError,
    current_trace_id,
    setup_tracing,
    span,
)

HEX32 = re.compile(r"[0-9a-f]{32}")

# ============================================================================
# Always-run tier (no SDK; the no-op opentelemetry-api tracer)
# ============================================================================


def test_setup_tracing_disabled_returns_false() -> None:
    assert setup_tracing(get_default_config()) is False


def test_setup_tracing_empty_config_is_disabled() -> None:
    assert setup_tracing({}) is False


def test_current_trace_id_is_none_outside_a_span() -> None:
    assert current_trace_id() is None


def test_span_helper_is_a_noop_without_a_recording_span() -> None:
    with span(SPAN_QUERY), span(SPAN_RETRIEVE_VECTOR, **{"corpus_kb.row_count": 3}):
        pass


def test_envelope_has_no_trace_id_when_tracing_off() -> None:
    """The pinned off-path key-set: identical to the honesty-test literal."""
    payload = envelope_to_payload(finalize_envelope([], []))
    assert set(payload) == {
        "status",
        "result",
        "degraded_excluded",
        "vector_index_mode",
        "lexical_index_mode",
        "meta_hits",
    }
    degraded = envelope_to_payload(finalize_envelope([], [REASON_ZERO_QUERY], excluded_count=4))
    assert set(degraded) == {
        "status",
        "result",
        "degraded_excluded",
        "remedy",
        "vector_index_mode",
        "lexical_index_mode",
        "meta_hits",
    }


def test_missing_sdk_raises_the_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enabled + a missing extra = OtelDependencyError through the seam."""

    def _missing(name: str) -> object:
        raise ModuleNotFoundError(f"No module named '{name}'")

    monkeypatch.setattr(tracing, "_import_module", _missing)
    monkeypatch.setattr(tracing, "_provider_set", False)
    cfg = get_default_config()
    obs = cast("dict[str, object]", cfg["observability"])
    obs["enabled"] = True
    with pytest.raises(OtelDependencyError) as excinfo:
        setup_tracing(cfg)
    assert excinfo.value.remediation == "pip install 'corpus-kb[otel]'"
    assert isinstance(excinfo.value, DependencyMissingError)


def test_setup_tracing_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second enabled call returns True without touching the SDK again."""
    monkeypatch.setattr(tracing, "_provider_set", True)
    cfg = get_default_config()
    obs = cast("dict[str, object]", cfg["observability"])
    obs["enabled"] = True
    assert setup_tracing(cfg) is True


def test_shutdown_tracing_without_setup_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "_provider_set", False)
    tracing.shutdown_tracing()  # must not raise


# ============================================================================
# Fakes (recording conn/pool — the test_search_honesty pattern)
# ============================================================================


class _RecordingConn:
    """Asyncpg-connection stand-in: records SQL, returns canned rows."""

    def __init__(self, extversion: str | None = "0.8.2") -> None:
        self.sql: list[str] = []
        self.extversion = extversion

    async def execute(self, sql: str, *args: object) -> str:
        self.sql.append(sql)
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        self.sql.append(sql)
        return []  # vector arm, FTS arm, rrf_fusion: all empty here

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        self.sql.append(sql)
        if "pg_extension" in sql:
            return None if self.extversion is None else {"extversion": self.extversion}
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


def _query_handler() -> QueryHandler:
    return QueryHandler(
        cast("object", _RecordingPool(_RecordingConn())),
        embedder=None,
        reranker=None,
        config=dict(_PGML_CONFIG),
    )


class _OneResultHandler:
    """The retrieval seam with exactly one evidence chunk (composer fakes)."""

    async def handle_search_with_meta(self, query: object) -> SearchEnvelope:
        result = SearchResult(
            chunk_id=uuid4(),
            text="alpha text",
            score=0.9,
            source="doc.txt",
            doc_id=uuid4(),
        )
        return finalize_envelope([result], [])


class _CitedLlm:
    model = "fake-model"

    async def chat(
        self, messages: list[dict[str, str]], model: str | None = None, options: object = None
    ) -> dict[str, object]:
        return {"message": {"content": "Alpha holds [1]."}}


class _BrokenLlm:
    model = "fake-model"

    async def chat(
        self, messages: list[dict[str, str]], model: str | None = None, options: object = None
    ) -> dict[str, object]:
        return {"error": "down", "model": self.model}


# ============================================================================
# SDK tier (skipped unless opentelemetry.sdk is installed)
# ============================================================================


@pytest.fixture(scope="module")
def otel_exporter() -> object:
    """One SDK TracerProvider + InMemorySpanExporter for the whole module.

    ``set_tracer_provider`` installs once per process; tests filter the
    accumulating exporter by span name.
    """
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    otel_trace.set_tracer_provider(provider)
    return exporter


def _span_names(exporter: object) -> set[str]:
    return {s.name for s in exporter.get_finished_spans()}


@pytest.mark.asyncio
async def test_query_span_tree_and_envelope_trace_id(otel_exporter: object) -> None:
    """The REAL handler emits the query tree; the envelope carries the id."""
    envelope = await _query_handler().handle_search_with_meta(
        SearchQuery(query="housing policy", k=3)
    )
    payload = envelope_to_payload(envelope)
    trace_id = payload["trace_id"]
    assert isinstance(trace_id, str)
    assert HEX32.fullmatch(trace_id)
    assert trace_id == envelope.trace_id
    expected = {
        SPAN_QUERY,
        SPAN_RETRIEVE_VECTOR,
        SPAN_RETRIEVE_DEGRADED_PROBE,
        SPAN_RETRIEVE_FTS,
        SPAN_RRF_FUSION,
    }
    assert expected <= _span_names(otel_exporter)


@pytest.mark.asyncio
async def test_query_trace_id_matches_the_span_tree(otel_exporter: object) -> None:
    """The envelope id equals the corpus_kb.query root span's trace id."""
    envelope = await _query_handler().handle_search_with_meta(SearchQuery(query="another", k=2))
    roots = [s for s in otel_exporter.get_finished_spans() if s.name == SPAN_QUERY]
    assert roots, "corpus_kb.query span not exported"
    ctx = roots[-1].get_span_context()
    assert format(ctx.trace_id, "032x") == envelope.trace_id


@pytest.mark.asyncio
async def test_route_decide_span_records_choice(otel_exporter: object) -> None:
    from corpus_kb.handlers.router_handler import RouterHandler

    class _Embedder:
        def embed(self, text: str) -> list[float]:
            return [0.1, 0.2]

    class _QH:
        async def handle_search(self, query: object) -> list[object]:
            return []

    class _Graph:
        async def handle_search_graph(self, *args: object, **kwargs: object) -> list[object]:
            return []

    class _Versioning:
        async def handle_query_document_stats(self, *args: object) -> dict[str, object]:
            return {}

    router = RouterHandler(_QH(), _Graph(), _Versioning(), _Embedder(), {})
    routed = await router.handle_routed_query(RoutedQuery(tenant_id=uuid4(), query="what?", k=3))
    assert routed.decision.route == "semantic"
    assert SPAN_ROUTE_DECIDE in _span_names(otel_exporter)
    decide = next(s for s in otel_exporter.get_finished_spans() if s.name == SPAN_ROUTE_DECIDE)
    attrs = dict(decide.attributes or {})
    assert attrs.get(ATTR_ROUTE) == "semantic"


@pytest.mark.asyncio
async def test_ask_composer_stage_spans(otel_exporter: object) -> None:
    from corpus_kb.ask.composer import STATUS_SUCCESS, compose_answer

    bundle = await compose_answer("q", _OneResultHandler(), _CitedLlm())
    assert bundle.status == STATUS_SUCCESS
    expected = {SPAN_ASK_RETRIEVE, SPAN_ASK_GENERATE, SPAN_ASK_VERIFY}
    assert expected <= _span_names(otel_exporter)


@pytest.mark.asyncio
async def test_ask_degraded_still_spans_generate(otel_exporter: object) -> None:
    from corpus_kb.ask.composer import STATUS_DEGRADED, compose_answer

    bundle = await compose_answer("q", _OneResultHandler(), _BrokenLlm())
    assert bundle.status == STATUS_DEGRADED
    assert SPAN_ASK_GENERATE in _span_names(otel_exporter)


class _LockConn:
    async def fetchval(self, sql: str, *args: object) -> bool:
        return True

    async def execute(self, sql: str, *args: object) -> str:
        return "OK"


class _LockPool:
    def acquire(self):
        class _Acquire:
            async def __aenter__(self) -> _LockConn:
                return _LockConn()

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Acquire()


class _FakeStore:
    """PostgresIngestStore stand-in: the whole write layer is fake."""

    def __init__(self, pool: object, tenant_id: str) -> None:
        pass

    async def unchanged_doc_id(self, path: str, file_hash: str) -> None:
        return None

    async def begin_run(self, document: object, file_hash: str) -> tuple[str, str]:
        return "doc-1", "run-1"

    async def store_chunks(self, chunks: list[object]) -> set[str]:
        return {str(c.chunk_id) for c in chunks}

    async def store_vectors(
        self,
        chunks: list[object],
        model: str,
        dimensions: int | None = None,
        vectors_1024: object | None = None,
    ) -> int:
        return len(chunks)

    async def store_entities(self, entities: list[object]) -> None:
        return None

    async def store_relations(
        self, relations: list[object], model_version: object = None, prompt_version: object = None
    ) -> None:
        return None

    async def update_document(self, document: object, file_hash: str) -> None:
        return None

    async def finish_run(
        self, run_id: str, file_hash: str, stages: object, errors: list[str]
    ) -> None:
        return None

    async def fail_run(self, run_id: str, error: str) -> None:
        return None


@pytest.mark.asyncio
async def test_ingest_span_tree(otel_exporter: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """The REAL run_pipeline emits partition/chunk/embed/extract/store."""
    from corpus_kb.tools import ingest_common

    async def _fake_embed(
        chunks: list[object],
        config: dict[str, object],
        pool: object = None,
        text_override: object = None,
        document_text: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> tuple[bool, str | None]:
        return False, None

    monkeypatch.setattr(ingest_common, "PostgresIngestStore", _FakeStore)
    monkeypatch.setattr(ingest_common, "embed_chunks", _fake_embed)
    config: dict[str, object] = {
        "graph": {"extract_entities": False},
        "ingest": {"chunking": {"enabled": False}},
    }
    result = await ingest_common.run_pipeline(
        "alpha text. beta text.", "text", "raw:test", config, _LockPool()
    )
    assert result["status"] == "success"
    expected = {SPAN_INGEST, SPAN_PARTITION, SPAN_CHUNK, SPAN_EMBED, SPAN_STORE, SPAN_EXTRACT}
    assert expected <= _span_names(otel_exporter)
