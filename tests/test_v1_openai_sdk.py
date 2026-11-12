"""The /v1 SSE stream consumed by the REAL OpenAI SDK (issue #131).

The acceptance rule for streaming is "test with the real client, not
hand-rolled SSE parsing": the SDK's own SSE decoder and chunk models read
the frames the route emits, over an in-process ASGI transport — no
network, no Ollama. The ask seams are monkeypatched exactly like
``tests/test_v1_adapter.py``. Skips honestly when the ``openai`` package
is absent (the judge's SDK is optional at install time).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import corpus_kb.tools.ask_tools as ask_tools
from corpus_kb.api.http import create_http_app
from corpus_kb.ask.composer import STATUS_DEGRADED, STATUS_SUCCESS, AnswerBundle
from corpus_kb.ask.streaming import AskStreamDelta, AskStreamFinal

openai = pytest.importorskip("openai")

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
    }
]
_MESSAGES = [{"role": "user", "content": "how does startup work?"}]


def _success_events() -> list[Any]:
    bundle = AnswerBundle(
        STATUS_SUCCESS,
        _ANSWER,
        [{"text": _ANSWER, "citations": [1]}],
        _SOURCES,
        {"abstained": True},
        "qwen3:4b",
        None,
        {},
    )
    return [AskStreamDelta(_ANSWER), AskStreamFinal(bundle)]


def _stub_ask_stream(monkeypatch: Any, events: list[Any]) -> None:
    async def stub(query: str, **kwargs: object) -> Any:
        for event in events:
            yield event

    monkeypatch.setattr(ask_tools, "ask_stream", stub)


def _sdk_client(monkeypatch: Any) -> Any:
    """A real AsyncOpenAI client over the app's ASGI transport (in-process)."""
    monkeypatch.delenv("CORPUS_KB_OPENAI_COMPAT", raising=False)
    app = create_http_app()
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    return openai.AsyncOpenAI(api_key="test-key", base_url="http://testserver/v1", http_client=http)


async def test_openai_sdk_streams_the_answer_and_finishes_clean(
    monkeypatch: Any,
) -> None:
    _stub_ask_stream(monkeypatch, _success_events())
    client = _sdk_client(monkeypatch)

    stream = await client.chat.completions.create(
        model="corpus-kb", messages=_MESSAGES, stream=True
    )
    pieces: list[str] = []
    finish: str | None = None
    async for chunk in stream:
        assert chunk.object == "chat.completion.chunk"
        for choice in chunk.choices:
            if choice.delta.content:
                pieces.append(choice.delta.content)
            if choice.finish_reason is not None:
                finish = choice.finish_reason

    assert "".join(pieces).startswith(_ANSWER)
    assert "Sources:" in "".join(pieces), "the SDK client receives the portable sources block"
    assert finish == "stop"


async def test_openai_sdk_mid_stream_failure_raises_api_error(monkeypatch: Any) -> None:
    """The degraded-after-content error frame makes the real SDK raise."""
    bundle = AnswerBundle(
        STATUS_DEGRADED,
        "",
        [],
        _SOURCES,
        {"abstained": True},
        "qwen3:4b",
        "generation unavailable",
        {},
    )
    _stub_ask_stream(monkeypatch, [AskStreamDelta(_ANSWER), AskStreamFinal(bundle)])
    client = _sdk_client(monkeypatch)

    stream = await client.chat.completions.create(
        model="corpus-kb", messages=_MESSAGES, stream=True
    )
    with pytest.raises(openai.APIError):
        async for _chunk in stream:
            pass


async def test_openai_sdk_unique_completion_ids_per_stream(monkeypatch: Any) -> None:
    _stub_ask_stream(monkeypatch, _success_events())
    client = _sdk_client(monkeypatch)

    ids: set[str] = set()
    for _ in range(2):
        stream = await client.chat.completions.create(
            model="corpus-kb", messages=_MESSAGES, stream=True
        )
        async for chunk in stream:
            ids.add(chunk.id)
    assert len(ids) == 2, "two streams are two completions (fresh uuid per stream)"
    assert all(str(completion_id).startswith("chatcmpl-") for completion_id in ids)
