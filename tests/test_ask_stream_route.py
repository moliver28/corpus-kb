"""POST /api/ask with ``stream: true`` — the SPA's SSE transport variant.

Offline by construction: the route is tested through the house seam
(``tools.ask_tools.ask_stream`` is imported INSIDE the handler body, so a
monkeypatched stub replaces the whole stack). What the tests pin:

  * content frames carry the grounded sentences in order;
  * the ``ask.envelope`` frame carries the EXACT envelope payload the
    non-streaming surface returns (byte-parity on the settle receipt);
  * the stream ends with ``[DONE]`` and honest statuses ride the envelope
    (degraded never becomes a fake completion or a hidden error).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.testclient import TestClient

import corpus_kb.tools.ask_tools as ask_tools
from corpus_kb.api import research_http
from corpus_kb.api.http import create_http_app
from corpus_kb.ask.composer import (
    NO_EVIDENCE,
    STATUS_DEGRADED,
    STATUS_EMPTY,
    STATUS_SUCCESS,
    AnswerBundle,
)
from corpus_kb.ask.streaming import AskStreamDelta, AskStreamFinal


class _FakePool:
    async def close(self) -> None:  # pragma: no cover - interface parity
        return None


_SOURCES = [
    {
        "n": 1,
        "chunk_id": "11111111-1111-1111-1111-111111111111",
        "doc_id": "22222222-2222-2222-2222-222222222222",
        "source": "docs/startup.md",
        "text": "Startup works in stages.",
        "score": 0.9,
        "chunk_index": 3,
    }
]


def _bundle(
    status: str = STATUS_SUCCESS,
    answer: str = "Startup works in stages. [1]",
    note: str | None = None,
) -> AnswerBundle:
    """The terminal bundle for one status (verify abstains; no judge)."""
    if status == STATUS_EMPTY:
        return AnswerBundle(
            STATUS_EMPTY, "", [], [], {"abstained": True}, "qwen3:4b", NO_EVIDENCE, {}
        )
    if status == STATUS_DEGRADED:
        return AnswerBundle(
            STATUS_DEGRADED, "", [], _SOURCES, {"abstained": True}, "qwen3:4b", note, {}
        )
    return AnswerBundle(
        STATUS_SUCCESS,
        answer,
        [{"text": answer, "citations": [1]}],
        _SOURCES,
        {"abstained": True},
        "qwen3:4b",
        None,
        {},
    )


def _stub_ask_stream(monkeypatch: Any, events: list[Any], captured: dict[str, Any]) -> None:
    async def stub(query: str, **kwargs: object) -> Any:
        captured["query"] = query
        captured["kwargs"] = kwargs
        for event in events:
            yield event

    monkeypatch.setattr(ask_tools, "ask_stream", stub)


def _payloads(raw: str) -> list[dict[str, Any] | str]:
    """Every ``data:`` payload in order (JSON frames; the literal [DONE])."""
    out: list[dict[str, Any] | str] = []
    for frame in raw.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        assert frame.startswith("data: "), f"malformed SSE frame: {frame!r}"
        payload = frame[len("data: ") :]
        out.append(payload if payload == "[DONE]" else json.loads(payload))
    return out


@pytest.fixture
def gate_on(monkeypatch: Any) -> None:
    monkeypatch.delenv("CORPUS_KB_OPENAI_COMPAT", raising=False)
    monkeypatch.setattr(research_http, "_pool", _FakePool())


def test_stream_frames_carry_deltas_then_the_exact_envelope(
    gate_on: None, monkeypatch: Any
) -> None:
    captured: dict[str, Any] = {}
    bundle = _bundle()
    _stub_ask_stream(monkeypatch, [AskStreamDelta(bundle.answer), AskStreamFinal(bundle)], captured)

    response = TestClient(create_http_app()).post(
        "/api/ask", json={"query": "how does startup work?", "stream": True}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    payloads = _payloads(response.text)
    assert payloads[-1] == "[DONE]"
    chunk = payloads[0]
    assert isinstance(chunk, dict) and chunk["object"] == "chat.completion.chunk"
    assert chunk["choices"][0]["delta"] == {"content": bundle.answer}
    envelope_frame = payloads[1]
    assert isinstance(envelope_frame, dict) and envelope_frame["object"] == "ask.envelope"
    assert envelope_frame["envelope"] == bundle.to_payload(), "the settle receipt is the envelope"
    stop = payloads[2]
    assert isinstance(stop, dict) and stop["choices"][0]["finish_reason"] == "stop"
    # The tool seam got the query and the shared route pool, as non-stream does.
    assert captured["query"] == "how does startup work?"
    assert captured["kwargs"].get("pg_pool") is not None


def test_stream_degraded_rides_the_envelope_never_an_error_frame(
    gate_on: None, monkeypatch: Any
) -> None:
    note = "generation unavailable; returning evidence only"
    _stub_ask_stream(monkeypatch, [AskStreamFinal(_bundle(status=STATUS_DEGRADED, note=note))], {})

    raw = TestClient(create_http_app()).post("/api/ask", json={"query": "q", "stream": True}).text

    payloads = _payloads(raw)
    assert payloads[-1] == "[DONE]"
    envelope_frame = next(
        p for p in payloads if isinstance(p, dict) and p["object"] == "ask.envelope"
    )
    envelope = envelope_frame["envelope"]
    assert envelope["status"] == STATUS_DEGRADED
    assert envelope["result"]["note"] == note
    assert envelope["result"]["sources"], "the degraded envelope still ships the evidence"
    assert not any(isinstance(p, dict) and "error" in p for p in payloads)


def test_stream_empty_carries_the_honest_empty_envelope(gate_on: None, monkeypatch: Any) -> None:
    _stub_ask_stream(monkeypatch, [AskStreamFinal(_bundle(status=STATUS_EMPTY))], {})

    raw = TestClient(create_http_app()).post("/api/ask", json={"query": "q", "stream": True}).text

    payloads = _payloads(raw)
    envelope_frame = next(
        p for p in payloads if isinstance(p, dict) and p["object"] == "ask.envelope"
    )
    assert isinstance(envelope_frame, dict)
    assert envelope_frame["envelope"]["status"] == STATUS_EMPTY
    assert not any(
        isinstance(p, dict)
        and p.get("object") == "chat.completion.chunk"
        and p["choices"][0]["delta"].get("content")
        for p in payloads
    ), "no evidence means no content frames, only the honest envelope"


def test_stream_without_query_still_answers_the_validation_error(gate_on: None) -> None:
    response = TestClient(create_http_app()).post("/api/ask", json={"stream": True})

    assert response.status_code == 400
    assert response.json()["error_type"] == "validation_error"
