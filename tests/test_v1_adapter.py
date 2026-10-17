"""OpenAI-compatible /v1 adapter tests (UX program Wave 2).

Offline by construction: the chat route is tested through the house seam
(``tools.ask_tools.ask`` is imported INSIDE the handler body, so a
monkeypatched stub replaces the whole retrieval + generation stack), and
the embeddings route through ``corpus_kb.rag.create_embedder`` (imported
inside the body too). No Postgres, no Ollama.

Wire-format assertions are exact on purpose: chat frontends break on
malformed SSE frames, so every ``data:`` payload must parse as JSON (or
be the literal ``[DONE]`` sentinel), and the concatenated deltas must
equal the non-stream content.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.testclient import TestClient

import corpus_kb.rag as rag_module
import corpus_kb.tools.ask_tools as ask_tools
from corpus_kb.api.http import create_http_app

_ANSWER = "Startup works in stages. [1]"
_SOURCES = [
    {
        "n": 1,
        "chunk_id": "11111111-1111-1111-1111-111111111111",
        "doc_id": "22222222-2222-2222-2222-222222222222",
        "source": "docs/startup.md",
        "text": "Startup works in stages.",
        "score": 0.9,
        "chunk_index": 3,
    },
    {
        "n": 2,
        "chunk_id": "33333333-3333-3333-3333-333333333333",
        "doc_id": "22222222-2222-2222-2222-222222222222",
        "source": "docs/other.md",
        "text": "More evidence.",
        "score": 0.8,
    },
]
_CHAT_BODY = {
    "model": "corpus-kb-custom",
    "messages": [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "how does startup work?"},
    ],
}


def _envelope(
    status: str = "success",
    answer: str = _ANSWER,
    sources: list[dict[str, Any]] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """The ask tool's uniform envelope (ask.composer.AnswerBundle shape)."""
    return {
        "status": status,
        "result": {
            "answer": answer,
            "sentences": [{"text": answer, "citations": [1]}],
            "sources": _SOURCES if sources is None else sources,
            "verify": {"groundedness": 0.0, "verdicts": [], "abstained": True},
            "model": "qwen3:4b",
            "note": note,
        },
        "degraded_excluded": {},
    }


def _stub_ask(monkeypatch: Any, envelope: dict[str, Any], captured: dict[str, Any]) -> None:
    async def stub(query: str, **kwargs: object) -> dict[str, Any]:
        captured["query"] = query
        captured["kwargs"] = kwargs
        return envelope

    monkeypatch.setattr(ask_tools, "ask", stub)


@pytest.fixture
def gate_on(monkeypatch: Any) -> None:
    monkeypatch.delenv("CORPUS_KB_OPENAI_COMPAT", raising=False)


def _client() -> TestClient:
    return TestClient(create_http_app())


def _sse_payloads(raw: str) -> list[str]:
    """The ``data:`` payload strings, in order (SSE frames are \\n\\n-split)."""
    payloads: list[str] = []
    for frame in raw.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        assert frame.startswith("data: "), f"malformed SSE frame: {frame!r}"
        payloads.append(frame[len("data: ") :])
    return payloads


def _chunk_frames(payloads: list[str]) -> list[dict[str, Any]]:
    """Every payload except the trailing ``[DONE]`` sentinel, JSON-parsed."""
    assert payloads[-1] == "[DONE]", f"stream must end with [DONE], got {payloads[-1]!r}"
    frames = [json.loads(payload) for payload in payloads[:-1]]
    for frame in frames:
        assert frame["object"] == "chat.completion.chunk"
    return frames


# ---------------------------------------------------------------------------
# POST /v1/chat/completions — non-streaming
# ---------------------------------------------------------------------------


def test_chat_completion_shape(gate_on: None, monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _stub_ask(monkeypatch, _envelope(), captured)

    response = _client().post("/v1/chat/completions", json=_CHAT_BODY)

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"].startswith("chatcmpl-")
    assert payload["object"] == "chat.completion"
    assert isinstance(payload["created"], int)
    assert payload["model"] == "corpus-kb-custom"
    assert "usage" not in payload, "token counts are not tracked; usage must be omitted"
    choice = payload["choices"][0]
    assert choice["index"] == 0
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["role"] == "assistant"
    content = choice["message"]["content"]
    assert content.startswith(_ANSWER)
    assert "\n\nSources:\n1. docs/startup.md — chunk 3\n2. docs/other.md" in content
    # The shared layer got the LAST user message and the loaded config.
    assert captured["query"] == "how does startup work?"
    assert isinstance(captured["kwargs"].get("config"), dict)


def test_chat_completion_defaults_model_from_config(gate_on: None, monkeypatch: Any) -> None:
    _stub_ask(monkeypatch, _envelope(), {})

    body = {"messages": [{"role": "user", "content": "q"}]}
    response = _client().post("/v1/chat/completions", json=body)

    payload = response.json()
    from corpus_kb.config import load_config

    expected = load_config()["llm"]["model"]
    assert payload["model"] == expected


def test_chat_empty_status_is_an_honest_completion(gate_on: None, monkeypatch: Any) -> None:
    _stub_ask(monkeypatch, _envelope(status="empty", answer="", sources=[]), {})

    response = _client().post("/v1/chat/completions", json=_CHAT_BODY)

    assert response.status_code == 200
    content = response.json()["choices"][0]["message"]["content"]
    assert "no evidence" in content.lower()
    assert "Sources:" not in content


def test_chat_degraded_status_maps_to_503_openai_error(gate_on: None, monkeypatch: Any) -> None:
    note = "generation unavailable; returning evidence only"
    _stub_ask(monkeypatch, _envelope(status="degraded", answer="", note=note), {})

    response = _client().post("/v1/chat/completions", json=_CHAT_BODY)

    assert response.status_code == 503
    error = response.json()["error"]
    assert "generation unavailable" in error["message"]
    assert error["type"] == "service_unavailable"
    assert "code" in error


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"messages": [{"role": "user", "content": " "}]}, 400),
        ({"messages": [{"role": "assistant", "content": "hi"}]}, 400),
        ({}, 422),
        ({"messages": "not-a-list"}, 422),
        ({"messages": [{"role": "user"}]}, 422),
    ],
    ids=(
        "blank-query",
        "no-user-message",
        "empty-body",
        "messages-not-a-list",
        "message-missing-content",
    ),
)
def test_chat_malformed_bodies_answer_openai_errors(
    gate_on: None, monkeypatch: Any, body: dict[str, Any], status: int
) -> None:
    _stub_ask(monkeypatch, _envelope(), {})

    response = _client().post("/v1/chat/completions", json=body)

    assert response.status_code == status
    error = response.json()["error"]
    assert set(error) == {"message", "type", "code"}
    assert error["type"] == "invalid_request_error"


def test_chat_non_json_body_is_400(gate_on: None, monkeypatch: Any) -> None:
    _stub_ask(monkeypatch, _envelope(), {})

    response = _client().post(
        "/v1/chat/completions", content=b"not json", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"


# ---------------------------------------------------------------------------
# POST /v1/chat/completions — SSE streaming
# ---------------------------------------------------------------------------


def test_chat_stream_frames_are_valid_openai_chunks(gate_on: None, monkeypatch: Any) -> None:
    _stub_ask(monkeypatch, _envelope(), {})

    stream_response = _client().post("/v1/chat/completions", json={**_CHAT_BODY, "stream": True})
    assert stream_response.status_code == 200
    assert stream_response.headers["content-type"].startswith("text/event-stream")

    payloads = _sse_payloads(stream_response.text)
    frames = _chunk_frames(payloads)
    assert frames, "at least one content frame expected"

    contents = [frame["choices"][0]["delta"].get("content", "") for frame in frames]
    final = frames[-1]
    assert final["choices"][0]["finish_reason"] == "stop"
    assert final["choices"][0]["delta"] == {}
    for frame in frames[:-1]:
        assert frame["choices"][0]["finish_reason"] is None
        assert frame["choices"][0]["delta"]["content"]
    ids = {frame["id"] for frame in frames}
    models = {frame["model"] for frame in frames}
    assert len(ids) == 1, "all frames of one stream share one completion id"
    assert ids.pop().startswith("chatcmpl-")
    assert models == {"corpus-kb-custom"}

    # The concatenated deltas must equal the non-stream content exactly.
    _stub_ask(monkeypatch, _envelope(), {})
    plain_response = _client().post("/v1/chat/completions", json=_CHAT_BODY)
    plain_content = plain_response.json()["choices"][0]["message"]["content"]
    assert "".join(contents) == plain_content
    assert plain_content.startswith(_ANSWER)


# ---------------------------------------------------------------------------
# POST /v1/embeddings
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    """Config-wired embedder stub; records its batch, returns tiny vectors."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [[float(len(text)), 1.0] for text in texts]


def _stub_embedder(monkeypatch: Any) -> _FakeEmbedder:
    fake = _FakeEmbedder()
    monkeypatch.setattr(rag_module, "create_embedder", lambda cfg, pool=None: fake)
    return fake


def test_embeddings_two_indexed_entries(gate_on: None, monkeypatch: Any) -> None:
    fake = _stub_embedder(monkeypatch)
    texts = ["alpha", "beta"]

    response = _client().post("/v1/embeddings", json={"input": texts})

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    assert "usage" not in payload
    assert len(payload["data"]) == 2
    for index, entry in enumerate(payload["data"]):
        assert entry["object"] == "embedding"
        assert entry["index"] == index
        assert entry["embedding"] == [float(len(texts[index])), 1.0]
    assert fake.batches == [texts]
    from corpus_kb.config import load_config

    assert payload["model"] == load_config()["embedding"]["model"]


def test_embeddings_single_string_and_model_echo(gate_on: None, monkeypatch: Any) -> None:
    _stub_embedder(monkeypatch)

    response = _client().post("/v1/embeddings", json={"input": "one text", "model": "custom-embed"})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["data"]) == 1
    assert payload["model"] == "custom-embed"


@pytest.mark.parametrize(
    "body", [{"input": []}, {"input": 123}], ids=("empty-list", "input-not-a-string")
)
def test_embeddings_malformed_bodies(gate_on: None, monkeypatch: Any, body: dict[str, Any]) -> None:
    _stub_embedder(monkeypatch)

    response = _client().post("/v1/embeddings", json=body)

    assert response.status_code in (400, 422)
    assert response.json()["error"]["type"] in ("invalid_request_error",)


# ---------------------------------------------------------------------------
# GET /v1/models
# ---------------------------------------------------------------------------


def test_models_lists_embedding_and_answer_models(gate_on: None) -> None:
    from corpus_kb.config import load_config

    response = _client().get("/v1/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    cfg = load_config()
    assert payload["data"] == [
        {"id": cfg["embedding"]["model"], "object": "model"},
        {"id": cfg["llm"]["model"], "object": "model"},
    ]


# ---------------------------------------------------------------------------
# The server.openai_compat gate
# ---------------------------------------------------------------------------


def test_gate_off_unregisters_v1_routes(monkeypatch: Any) -> None:
    monkeypatch.setenv("CORPUS_KB_OPENAI_COMPAT", "false")

    app = create_http_app()
    assert all(not getattr(route, "path", "").startswith("/v1/") for route in app.routes)

    client = TestClient(app)
    for method, path in (
        ("POST", "/v1/chat/completions"),
        ("POST", "/v1/embeddings"),
        ("GET", "/v1/models"),
    ):
        body: dict[str, Any] | None = {"messages": []} if method == "POST" else None
        response = client.request(method, path, json=body)
        assert response.status_code == 404, path


def test_gate_on_registers_all_three_routes(gate_on: None) -> None:
    paths = {getattr(route, "path", "") for route in create_http_app().routes}
    assert {"/v1/chat/completions", "/v1/embeddings", "/v1/models"} <= paths


def test_v1_routes_are_not_parity_surfaces(gate_on: None) -> None:
    """The /v1 adapter is outside the operation catalogue (research_http precedent)."""
    from corpus_kb.api.routes_v1 import v1_routes

    assert all(route.path.startswith("/v1/") for route in v1_routes())
