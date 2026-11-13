"""Chat route tests (Phase-2 Wave 7, work-order step 5).

Starlette TestClient over the four chat routes with a scripted fake
session: the full frame sequence, JSON/stream receipt parity, the
chat.error taxonomy triple, the disabled-feature typed error,
list_sessions pagination, and the disconnect close (interrupt + aclose).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

import corpus_kb.chat as chat_core
from corpus_kb.api import routes_chat
from corpus_kb.chat.events import ChatActivity, ChatDelta, ChatEnvelope, ChatWarning
from corpus_kb.chat.session import ChatManager, ChatSession
from corpus_kb.chat.settings import read_chat_settings
from corpus_kb.errors import ERROR_MAP


class FakeClient:
    def __init__(self) -> None:
        self.interrupts = 0
        self.closed = False

    async def connect(self) -> None:
        return None

    async def query(self, prompt: str) -> None:
        return None

    def receive_response(self) -> AsyncGenerator[object, None]:
        raise AssertionError("the scripted turn stream replaces the client stream")
        yield None  # pragma: no cover

    async def interrupt(self) -> None:
        self.interrupts += 1

    async def disconnect(self) -> None:
        self.closed = True


def _scripted_turn(script: list[object]) -> Any:
    async def turn(
        client: object, text: str, history: list[dict[str, str]], k: int | None
    ) -> AsyncGenerator[object, None]:
        for event in script:
            yield event

    return turn


def _script(raise_exc: Exception | None = None) -> list[object]:
    delta = ChatDelta("Hello world")
    activity = ChatActivity("search", "Read 3 chunks", 3)
    warning = ChatWarning("Your login expires in 3 days")
    envelope = ChatEnvelope(
        session_id="s1",
        backend="claude-agent-sdk",
        num_turns=2,
        duration_ms=1200,
        is_error=False,
        terminal_reason="completed",
        total_cost_usd=0.02,
        model_usage={"claude-sonnet-5": {}},
        model="claude-sonnet-5",
    )
    return [delta, activity, warning, envelope]


def _envelope() -> ChatEnvelope:
    return ChatEnvelope(
        session_id="s1",
        backend="claude-agent-sdk",
        num_turns=2,
        duration_ms=1200,
        is_error=False,
        terminal_reason="completed",
        total_cost_usd=0.02,
        model_usage={"claude-sonnet-5": {}},
        model="claude-sonnet-5",
    )


def _session(script: list[object] | None = None, raise_exc: Exception | None = None) -> ChatSession:
    events = script if script is not None else _script()

    async def turn(
        client: object, text: str, history: list[dict[str, str]], k: int | None
    ) -> AsyncGenerator[object, None]:
        for event in events:
            yield event
        if raise_exc is not None:
            raise raise_exc

    return ChatSession("s1", "claude-agent-sdk", FakeClient(), turn, 1800)


def _app() -> Starlette:
    return Starlette(
        routes=[
            Route("/api/chat/sessions", routes_chat.create_chat_session, methods=["POST"]),
            Route("/api/chat/sessions", routes_chat.list_chat_sessions, methods=["GET"]),
            Route(
                "/api/chat/sessions/{sid}/messages",
                routes_chat.send_chat_message,
                methods=["POST"],
            ),
            Route("/api/chat/status", routes_chat.chat_status, methods=["GET"]),
        ]
    )


def _install(monkeypatch: pytest.MonkeyPatch, session: ChatSession | None) -> None:
    manager = ChatManager(max_sessions=4, ttl_s=1800)
    if session is not None:
        manager.create(session)
    monkeypatch.setattr(chat_core, "_MANAGER", manager)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_app())


class TestFrameSequence:
    def test_full_stream_order(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch, _session())
        response = client.post("/api/chat/sessions/s1/messages", json={"text": "q", "stream": True})
        assert response.status_code == 200
        body = response.text
        objects = [
            line.removeprefix("data: ").split('"object": "')[1].split('"')[0]
            for line in body.splitlines()
            if line.startswith("data: ") and '"object":' in line
        ]
        assert objects[0] == "chat.completion.chunk"
        assert objects[-1] == "chat.envelope"
        assert body.rstrip().endswith("data: [DONE]")
        assert objects.count("chat.envelope") == 1
        assert "chat.activity" in objects
        assert "chat.warning" in objects

    def test_json_stream_receipt_parity(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, _session())
        streamed = client.post("/api/chat/sessions/s1/messages", json={"text": "q", "stream": True})
        plain = client.post("/api/chat/sessions/s1/messages", json={"text": "q"})
        assert plain.status_code == 200
        receipt = plain.json()["result"]
        for line in streamed.text.splitlines():
            if "chat.envelope" in line:
                framed = line.removeprefix("data: ")
                import json

                assert json.loads(framed)["envelope"] == receipt
                break
        else:
            pytest.fail("no envelope frame")


class TestErrorFrames:
    def test_error_frame_carries_taxonomy_triple(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from corpus_kb.errors import ConflictError

        conflict = ConflictError("One turn runs at a time.")
        _install(monkeypatch, _session(raise_exc=conflict))
        response = client.post("/api/chat/sessions/s1/messages", json={"text": "q", "stream": True})
        import json

        error_frames = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ") and "chat.error" in line
        ]
        assert len(error_frames) == 1
        frame = error_frames[0]
        expected = ERROR_MAP["conflict"]
        assert frame["status"] == expected[0]
        assert frame["error_type"] == "conflict"
        assert response.text.rstrip().endswith("data: [DONE]")

    def test_disabled_feature_typed_error(self, client: TestClient) -> None:
        response = client.post("/api/chat/sessions", json={})
        assert response.status_code == 400
        assert response.json()["error_type"] == "config_error"

    def test_unknown_session_404(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch, _session())
        response = client.post("/api/chat/sessions/zz/messages", json={"text": "q"})
        assert response.status_code == 404
        assert response.json()["error_type"] == "not_found"

    def test_empty_text_validation_error(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, _session())
        response = client.post("/api/chat/sessions/s1/messages", json={"text": "  "})
        assert response.status_code == 400
        assert response.json()["error_type"] == "validation_error"


class TestSessionsListing:
    def test_pagination_rides_the_canonical_rule(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        seen: dict[str, object] = {}

        async def fake_list(
            config: object, limit: int | None = None, offset: int = 0
        ) -> list[dict[str, object]]:
            seen["limit"] = limit
            seen["offset"] = offset
            rows = [
                {"session_id": f"s{i}", "summary": f"t{i}", "last_modified": i} for i in range(3)
            ]
            return rows[offset : offset + (limit or len(rows))]

        monkeypatch.setattr("corpus_kb.chat.claude_rung.list_transcripts", fake_list)
        response = client.get("/api/chat/sessions?limit=2&offset=1")
        assert response.status_code == 200
        result = response.json()["result"]
        assert seen["limit"] == 2
        assert seen["offset"] == 1
        assert [row["session_id"] for row in result["sessions"]] == ["s1", "s2"]
        assert result["limit"] == 2
        assert result["offset"] == 1

    def test_negative_limit_is_typed_error(self, client: TestClient) -> None:
        response = client.get("/api/chat/sessions?limit=-3")
        assert response.status_code == 400
        assert response.json()["error_type"] == "validation_error"


class TestStatusRoute:
    def test_status_carries_disabled_truth(self, client: TestClient) -> None:
        response = client.get("/api/chat/status")
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["enabled"] is False
        assert result["backend"] == "claude-agent-sdk"
        assert len(result["checks"]) >= 6


class TestDisconnect:
    def test_disconnect_interrupts_and_closes(self) -> None:
        session = _session()
        client = session._client
        assert isinstance(client, FakeClient)
        events = session.send("q")
        framer = routes_chat.ChatFramer("")

        async def scenario() -> None:
            agen = routes_chat._chat_sse(events, session, framer)
            first = await agen.__anext__()
            assert "chat.completion.chunk" in first
            await agen.aclose()  # the client hung up mid-turn
            for _ in range(50):
                await asyncio.sleep(0.01)
                if client.closed:
                    break
            assert client.interrupts == 1
            assert client.closed is True

        asyncio.run(scenario())

    def test_create_session_returns_ids(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_create(
            config: object, pool: object, resume: str | None = None
        ) -> ChatSession:
            settings = read_chat_settings({})
            return ChatSession(
                "sid-new",
                "claude-agent-sdk",
                FakeClient(),
                _scripted_turn([]),
                settings.session_ttl_s,
            )

        monkeypatch.setattr(chat_core, "create_session", fake_create)
        monkeypatch.setattr(chat_core, "_MANAGER", ChatManager(4, 1800))
        monkeypatch.setattr(routes_chat, "_pool", _fake_pool)
        monkeypatch.setattr(
            "corpus_kb.config.load_config",
            lambda path=None: {**_enabled_config()},
        )
        response = client.post("/api/chat/sessions", json={})
        assert response.status_code == 200
        assert response.json()["result"]["session_id"] == "sid-new"

    def test_create_conflict_over_cap(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_create(
            config: object, pool: object, resume: str | None = None
        ) -> ChatSession:
            settings = read_chat_settings({})
            manager = chat_core.get_manager()
            assert isinstance(manager, ChatManager)
            return manager.create(
                ChatSession(
                    "sid-x",
                    "claude-agent-sdk",
                    FakeClient(),
                    _scripted_turn([]),
                    settings.session_ttl_s,
                )
            )

        monkeypatch.setattr(chat_core, "create_session", fake_create)
        tight = ChatManager(max_sessions=1, ttl_s=1800)
        monkeypatch.setattr(chat_core, "_MANAGER", tight)
        monkeypatch.setattr(routes_chat, "_pool", _fake_pool)
        monkeypatch.setattr(
            "corpus_kb.config.load_config",
            lambda path=None: {**_enabled_config()},
        )
        first = client.post("/api/chat/sessions", json={})
        second = client.post("/api/chat/sessions", json={})
        assert first.status_code == 200
        assert second.status_code == 409


async def _fake_pool() -> object:
    return None


def _enabled_config() -> dict[str, object]:
    from corpus_kb.config import get_default_config

    config = dict(get_default_config())
    chat = dict(config["chat"])
    assert isinstance(chat, dict)
    chat["enabled"] = True
    config["chat"] = chat
    return config
