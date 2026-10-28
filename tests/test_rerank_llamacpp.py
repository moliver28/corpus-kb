"""LlamaCppReranker tests against a stub /v1/rerank HTTP server (offline).

Ollama 0.31.1 cannot serve rerankers in any mode (no rerank endpoint,
generate refused on reranker GGUFs — verified live, see rag/reranker.py).
LlamaCppReranker is the true-score path: one batched POST per score()
call to a rank-capable server (llama-server exposes /v1/rerank and
/health), graded floats back, aligned to input order by index.

The stub here speaks the real wire contract: request body
``{model, query, documents}``, response ``{"results": [{"index",
"relevance_score"}]}``. Fail-open, truncation, the neutral-0.5 honesty
counter, and the LRU cache are pinned below, mirroring the
test_reranker_qwen3.py patterns.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from corpus_kb._setup import doctor_reranker
from corpus_kb._setup.doctor_reranker import reranker_backend_check
from corpus_kb._setup.doctor_research import STATUS_OK, STATUS_WARN
from corpus_kb.rag.rerank_llamacpp import LlamaCppReranker
from corpus_kb.rag.rerank_settings import (
    DEFAULT_RERANK_LLAMACPP_URL,
    RERANK_CONFIG_DEFAULTS,
    load_rerank_settings,
)
from corpus_kb.rag.reranker import OllamaReranker, build_reranker

Responder = Callable[[dict[str, object]], tuple[int, object]]


def _rank_response(body: dict[str, object]) -> tuple[int, object]:
    """Canned 200: descending graded scores over the received documents."""
    documents = body.get("documents")
    count = len(documents) if isinstance(documents, list) else 0
    results = [{"index": i, "relevance_score": round(1.0 - i * 0.25, 4)} for i in range(count)]
    return 200, {"results": results}


class _StubRerankServer(ThreadingHTTPServer):
    """One-thread-per-request HTTP stub with a swappable /v1/rerank responder."""

    tagged_model = "qwen3-reranker:4b"

    def __init__(self, responder: Responder) -> None:
        super().__init__(("127.0.0.1", 0), _StubHandler)
        self.responder = responder
        self.requests: list[dict[str, object]] = []

    def handle_error(self, request: object, client_address: object) -> None:
        """Timed-out clients vanish mid-response; that noise is expected."""
        pass


class _StubHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server = _stub_server(self.server)
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        elif self.path == "/api/tags":
            self._send(200, {"models": [{"name": server.tagged_model}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        server = _stub_server(self.server)
        length = int(self.headers.get("content-length", "0"))
        try:
            body: dict[str, object] = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send(400, {"error": "bad json"})
            return
        if self.path == "/v1/rerank":
            server.requests.append(body)
            status, payload = server.responder(body)
            self._send(status, payload)
        else:
            self._send(404, {"error": "not found"})

    def _send(self, status: int, payload: object) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def _stub_server(server: object) -> _StubRerankServer:
    """The handler-side view of the stub (adds responder/tagged_model)."""
    assert isinstance(server, _StubRerankServer)
    return server


@pytest.fixture
def stub_server():
    """A running stub whose default responder echoes graded scores."""
    server = _StubRerankServer(_rank_response)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _reranker(server: _StubRerankServer, **overrides: object) -> LlamaCppReranker:
    block: dict[str, object] = {
        "enabled": True,
        "backend": "llamacpp",
        "llamacpp_url": f"http://127.0.0.1:{server.server_address[1]}",
        **overrides,
    }
    return LlamaCppReranker({"reranking": block})


def _stub_url(server: _StubRerankServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ---------------------------------------------------------------------------
# Wire contract: request shape, response mapping, order alignment
# ---------------------------------------------------------------------------


def test_score_maps_response_scores_back_to_input_order(stub_server) -> None:
    # The stub answers in RANK order (best first, index 1 = the last doc);
    # the client must align by index, never by response position.
    stub_server.responder = lambda body: (
        200,
        {
            "results": [
                {"index": 1, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.2},
            ]
        },
    )
    reranker = _reranker(stub_server)
    scores = reranker.score("capital of France?", ["mitochondria text", "paris text"])
    assert scores == [0.2, 0.9]
    assert reranker.last_not_evaluable == 0
    assert reranker.rerank_mode == "cross-encoder"


def test_request_carries_model_query_documents(stub_server) -> None:
    reranker = _reranker(stub_server, model="bge-reranker-v2-m3-Q8_0.gguf")
    reranker.score("q", ["a", "b"])
    request = stub_server.requests[0]
    assert set(request) == {"model", "query", "documents"}
    assert request["model"] == "bge-reranker-v2-m3-Q8_0.gguf"
    assert request["query"] == "q"
    assert request["documents"] == ["a", "b"]


def test_empty_texts_sends_nothing(stub_server) -> None:
    reranker = _reranker(stub_server)
    assert reranker.score("q", []) == []
    assert stub_server.requests == []


# ---------------------------------------------------------------------------
# Truncation, cache, honesty counter
# ---------------------------------------------------------------------------


def test_documents_truncated_at_max_pair_tokens_times_four(stub_server) -> None:
    reranker = _reranker(stub_server, max_pair_tokens=8)  # 8 * 4 = 32 chars
    reranker.score("q", ["x" * 100])
    assert stub_server.requests[0]["documents"] == ["x" * 32]


def test_cache_hit_skips_the_second_request(stub_server) -> None:
    reranker = _reranker(stub_server)
    first = reranker.score("q", ["doc"])
    second = reranker.score("q", ["doc"])
    assert first == second
    assert len(stub_server.requests) == 1


def test_distinct_queries_bypass_the_cache(stub_server) -> None:
    reranker = _reranker(stub_server)
    reranker.score("q1", ["doc"])
    reranker.score("q2", ["doc"])
    assert len(stub_server.requests) == 2


def test_missing_entries_score_neutral_and_counted(stub_server, caplog) -> None:
    stub_server.responder = lambda body: (
        200,
        {"results": [{"index": 0, "relevance_score": 0.8}]},  # 1 of 2 docs
    )
    reranker = _reranker(stub_server)
    with caplog.at_level("WARNING"):
        scores = reranker.score("q", ["covered", "missing"])
    assert scores == [0.8, 0.5]
    assert reranker.last_not_evaluable == 1
    assert "unscored" in caplog.text
    assert "neutral 0.5" in caplog.text


def test_malformed_entries_count_as_missing(stub_server) -> None:
    stub_server.responder = lambda body: (
        200,
        {
            "results": [
                {"index": 5, "relevance_score": 0.9},  # out of range
                {"index": 1, "relevance_score": "high"},  # non-numeric
                "garbled",  # not an object
            ]
        },
    )
    reranker = _reranker(stub_server)
    scores = reranker.score("q", ["a", "b"])
    assert scores == [0.5, 0.5]
    assert reranker.last_not_evaluable == 2


def test_wrong_shape_200_body_scores_all_neutral(stub_server) -> None:
    stub_server.responder = lambda body: (200, {"unexpected": True})
    reranker = _reranker(stub_server)
    scores = reranker.score("q", ["a", "b"])
    assert scores == [0.5, 0.5]
    assert reranker.last_not_evaluable == 2


# ---------------------------------------------------------------------------
# Fail-open contract: never raise into search
# ---------------------------------------------------------------------------


def test_connection_refused_returns_none() -> None:
    reranker = LlamaCppReranker(
        {
            "reranking": {
                "enabled": True,
                "backend": "llamacpp",
                "llamacpp_url": f"http://127.0.0.1:{_free_port()}",
            }
        }
    )
    assert reranker.score("q", ["doc"]) is None


def test_http_500_returns_none(stub_server, caplog) -> None:
    stub_server.responder = lambda body: (500, {"error": "model not loaded"})
    reranker = _reranker(stub_server)
    with caplog.at_level("WARNING"):
        assert reranker.score("q", ["doc"]) is None
    assert "falling back to RRF order" in caplog.text


def test_slow_server_times_out_and_returns_none(stub_server) -> None:
    def slow(body: dict[str, object]) -> tuple[int, object]:
        time.sleep(1.5)
        return _rank_response(body)

    stub_server.responder = slow
    reranker = _reranker(stub_server, timeout_seconds=0.3)
    assert reranker.score("q", ["doc"]) is None


# ---------------------------------------------------------------------------
# Selection + settings wiring
# ---------------------------------------------------------------------------


def test_build_reranker_selects_llamacpp_backend(stub_server) -> None:
    built = build_reranker(
        {
            "reranking": {
                "enabled": True,
                "backend": "llamacpp",
                "llamacpp_url": _stub_url(stub_server),
            }
        }
    )
    assert isinstance(built, LlamaCppReranker)
    assert built.base_url == _stub_url(stub_server)
    assert built.model == "qwen3-reranker:4b"


def test_build_reranker_generative_values_keep_ollama_path() -> None:
    assert isinstance(build_reranker({"reranking": {"backend": None}}), OllamaReranker)
    assert isinstance(build_reranker({"reranking": {"backend": "generative"}}), OllamaReranker)
    assert build_reranker({"reranking": {"enabled": False, "backend": "llamacpp"}}) is None


def test_llamacpp_url_settings_defaults_and_overrides() -> None:
    assert DEFAULT_RERANK_LLAMACPP_URL == "http://localhost:8080"
    assert RERANK_CONFIG_DEFAULTS["llamacpp_url"] == "http://localhost:8080"
    assert load_rerank_settings({}).llamacpp_url == "http://localhost:8080"
    loaded = load_rerank_settings({"reranking": {"llamacpp_url": "http://gpu-box:8081"}})
    assert loaded.llamacpp_url == "http://gpu-box:8081"


def test_llamacpp_url_lockstep_across_the_three_config_locations() -> None:
    """Same key, same value: code default, root YAML, packaged YAML."""
    from pathlib import Path

    import yaml

    from corpus_kb.config import get_default_config

    assert get_default_config()["reranking"]["llamacpp_url"] == "http://localhost:8080"
    repo = Path(__file__).resolve().parent.parent
    for rel in ("config.yaml", Path("src") / "corpus_kb" / "config.yaml"):
        data = yaml.safe_load((repo / rel).read_text(encoding="utf-8"))
        assert data["reranking"]["llamacpp_url"] == "http://localhost:8080", rel


# ---------------------------------------------------------------------------
# Doctor: reranker backend check (stub server, no Postgres)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_doctor_llamacpp_backend_healthy(stub_server) -> None:
    config: dict[str, object] = {
        "reranking": {
            "enabled": True,
            "backend": "llamacpp",
            "llamacpp_url": _stub_url(stub_server),
        }
    }
    check = await reranker_backend_check(config)
    assert check.status == STATUS_OK
    assert check.name == "reranker backend"
    assert "/health" in check.detail
    assert "/v1/rerank" in check.detail


@pytest.mark.asyncio
async def test_doctor_llamacpp_backend_down_warns() -> None:
    config: dict[str, object] = {
        "reranking": {
            "enabled": True,
            "backend": "llamacpp",
            "llamacpp_url": f"http://127.0.0.1:{_free_port()}",
        }
    }
    check = await reranker_backend_check(config)
    assert check.status == STATUS_WARN
    assert "not reachable" in check.detail
    assert "llama-server" in check.fix


@pytest.mark.asyncio
async def test_doctor_generative_model_present_ok_with_honest_note(stub_server) -> None:
    stub_server.tagged_model = "qwen3:4b"
    config: dict[str, object] = {
        "reranking": {
            "enabled": True,
            "backend": None,
            "model": "qwen3:4b",
            "base_url": _stub_url(stub_server),
        }
    }
    check = await reranker_backend_check(config)
    assert check.status == STATUS_OK
    assert "generative-binary" in check.detail
    assert "Ollama 0.31.1" in check.detail
    assert "docs/ADMIN.md" in check.detail


@pytest.mark.asyncio
async def test_doctor_generative_model_missing_warns(stub_server) -> None:
    stub_server.tagged_model = "other-model"
    config: dict[str, object] = {
        "reranking": {
            "enabled": True,
            "backend": "generative",
            "model": "qwen3-reranker:4b",
            "base_url": _stub_url(stub_server),
        }
    }
    check = await reranker_backend_check(config)
    assert check.status == STATUS_WARN
    assert "not found in Ollama" in check.detail


@pytest.mark.asyncio
async def test_doctor_disabled_reranking_reports_ok() -> None:
    check = await reranker_backend_check({"reranking": {"enabled": False}})
    assert check.status == STATUS_OK
    assert "disabled" in check.detail


# ---------------------------------------------------------------------------
# Doctor: rerank sanity pair (functional probe behind a healthy /health)
# ---------------------------------------------------------------------------


def _doctor_config(server: _StubRerankServer, **overrides: object) -> dict[str, object]:
    """Doctor config block pointing at the stub's llama-server surface."""
    return {
        "reranking": {
            "enabled": True,
            "backend": "llamacpp",
            "llamacpp_url": _stub_url(server),
            **overrides,
        }
    }


@pytest.mark.asyncio
async def test_doctor_sanity_pair_passes_and_sends_configured_model(stub_server) -> None:
    check = await reranker_backend_check(
        _doctor_config(stub_server, model="bge-reranker-v2-m3-Q8_0.gguf")
    )
    assert check.status == STATUS_OK
    assert "sanity" in check.detail
    request = stub_server.requests[0]
    assert request["model"] == "bge-reranker-v2-m3-Q8_0.gguf"
    assert request["query"] == doctor_reranker.SANITY_QUERY
    assert len(request["documents"]) == 2


@pytest.mark.asyncio
async def test_doctor_sanity_pair_inverted_scores_warn(stub_server) -> None:
    # Rank-order body (best first): the unrelated text outranks the topic text.
    stub_server.responder = lambda body: (
        200,
        {
            "results": [
                {"index": 1, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.2},
            ]
        },
    )
    check = await reranker_backend_check(_doctor_config(stub_server))
    assert check.status == STATUS_WARN
    assert "gave the relevant text a lower score" in check.detail
    assert "fused RRF order" in check.detail
    assert "classifier tensor" in check.fix


@pytest.mark.asyncio
async def test_doctor_sanity_pair_near_zero_scores_warn(stub_server) -> None:
    stub_server.responder = lambda body: (
        200,
        {
            "results": [
                {"index": 0, "relevance_score": 4.6e-23},
                {"index": 1, "relevance_score": 4.5e-23},
            ]
        },
    )
    check = await reranker_backend_check(_doctor_config(stub_server))
    assert check.status == STATUS_WARN
    assert "near-zero scores" in check.detail
    assert "classifier tensor" in check.fix


@pytest.mark.asyncio
async def test_doctor_sanity_pair_timeout_degrades_not_crashes(stub_server, monkeypatch) -> None:
    def slow(body: dict[str, object]) -> tuple[int, object]:
        time.sleep(0.5)
        return _rank_response(body)

    stub_server.responder = slow
    monkeypatch.setattr(doctor_reranker, "SANITY_TIMEOUT_SECONDS", 0.2)
    check = await reranker_backend_check(_doctor_config(stub_server))
    assert check.status == STATUS_WARN
    assert "rerank probe failed" in check.detail


@pytest.mark.asyncio
async def test_doctor_sanity_pair_http_500_degrades(stub_server) -> None:
    stub_server.responder = lambda body: (500, {"error": "no rerank route"})
    check = await reranker_backend_check(_doctor_config(stub_server))
    assert check.status == STATUS_WARN
    assert "answered HTTP 500" in check.detail


@pytest.mark.asyncio
async def test_doctor_sanity_pair_unreadable_answer_degrades(stub_server) -> None:
    stub_server.responder = lambda body: (200, {"unexpected": True})
    check = await reranker_backend_check(_doctor_config(stub_server))
    assert check.status == STATUS_WARN
    assert "no usable scores" in check.detail


@pytest.mark.asyncio
async def test_doctor_generative_backend_skips_the_sanity_pair(stub_server) -> None:
    stub_server.tagged_model = "qwen3:4b"
    check = await reranker_backend_check(
        {
            "reranking": {
                "enabled": True,
                "backend": None,
                "model": "qwen3:4b",
                "base_url": _stub_url(stub_server),
            }
        }
    )
    assert check.status == STATUS_OK
    assert stub_server.requests == []
