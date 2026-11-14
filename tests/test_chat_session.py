"""Chat session tests (Phase-2 Wave 7, work-order step 2).

A FakeAgentClient drives the rung-agnostic turn loop: delta-to-envelope
ordering, tool activity, interrupt-and-drain, the single-flight conflict,
the TTL close, the env refusal, and the resume passthrough. No live
Claude, no network. The lazy-import law gets its own subprocess check:
importing the chat package must never import ``claude_agent_sdk``.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from types import SimpleNamespace

import pytest

from corpus_kb.chat import claude_rung
from corpus_kb.chat.events import ChatActivity, ChatDelta, ChatEnvelope, ChatWarning
from corpus_kb.chat.session import ChatManager, ChatSession
from corpus_kb.chat.settings import ChatSettings, read_chat_settings
from corpus_kb.errors import ConfigError, ConflictError


class FakeAgentClient:
    """The AgentClient Protocol with a scripted message stream."""

    def __init__(self, script: list[object]) -> None:
        self.script = script
        self.queries: list[str] = []
        self.interrupts = 0
        self.disconnects = 0
        self.closed = False

    async def connect(self) -> None:
        return None

    async def query(self, text: str) -> None:
        self.queries.append(text)

    def receive_response(self) -> object:
        return self._replay()

    async def _replay(self) -> object:
        for message in self.script:
            yield message

    async def interrupt(self) -> None:
        self.interrupts += 1

    async def disconnect(self) -> None:
        self.disconnects += 1
        self.closed = True


def _stream_event(text: str) -> object:
    return SimpleNamespace(
        event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}
    )


def _tool_use() -> object:
    return SimpleNamespace(content=[SimpleNamespace(name="search", id="t1", input={"query": "q"})])


def _tool_result(count: int) -> object:
    contract = '{"results": [' + ",".join(["{}"] * count) + '], "degraded": false, "note": null}'
    return SimpleNamespace(
        content=[SimpleNamespace(tool_use_id="t1", content=contract, is_error=False)]
    )


def _result(**overrides: object) -> object:
    base: dict[str, object] = {
        "num_turns": 2,
        "duration_ms": 1500,
        "session_id": "sid-1",
        "is_error": False,
        "terminal_reason": "completed",
        "total_cost_usd": 0.01,
        "model_usage": {"claude-sonnet-5": {}},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _claude_session(
    script: list[object], ttl_s: int = 1800, session_id: str = "sid-1"
) -> ChatSession:
    return ChatSession(
        session_id=session_id,
        backend="claude-agent-sdk",
        client=FakeAgentClient(script),
        turn_stream=claude_rung._turn_stream([]),
        ttl_s=ttl_s,
    )


def _events(session: ChatSession, text: str) -> list[object]:
    async def _run() -> list[object]:
        return [event async for event in session.send(text)]

    return asyncio.run(_run())


class TestClaudeTurnMapping:
    def test_delta_then_activity_then_envelope(self) -> None:
        script = [
            _stream_event("Hello "),
            _stream_event("world"),
            _tool_use(),
            _tool_result(2),
            _result(),
        ]
        events = _events(_claude_session(script), "question")
        assert isinstance(events[0], ChatDelta)
        assert events[0].text == "Hello "
        assert [e.text for e in events if isinstance(e, ChatDelta)] == ["Hello ", "world"]
        activity = [e for e in events if isinstance(e, ChatActivity)]
        assert activity[0].summary == "Searching the corpus..."
        assert activity[1].summary == "Read 2 chunks"
        assert activity[1].chunk_count == 2
        envelope = events[-1]
        assert isinstance(envelope, ChatEnvelope)
        assert envelope.session_id == "sid-1"
        assert envelope.backend == "claude-agent-sdk"
        assert envelope.num_turns == 2
        assert envelope.model == "claude-sonnet-5"
        assert envelope.is_error is False
        assert len(envelope.citations) == 2

    def test_stderr_drains_verbatim_before_envelope(self) -> None:
        script = [_stream_event("Hi"), _result()]
        session = ChatSession(
            session_id="sid-1",
            backend="claude-agent-sdk",
            client=FakeAgentClient(script),
            turn_stream=claude_rung._turn_stream(["Your login expires in 3 days"]),
            ttl_s=1800,
        )
        events = _events(session, "q")
        warning = [e for e in events if isinstance(e, ChatWarning)]
        assert [w.text for w in warning] == ["Your login expires in 3 days"]
        assert events.index(warning[0]) < len(events) - 1

    def test_aborted_turn_still_yields_receipt(self) -> None:
        script = [_stream_event("part"), _result(terminal_reason="aborted_streaming")]
        events = _events(_claude_session(script), "q")
        assert isinstance(events[-1], ChatEnvelope)
        assert events[-1].terminal_reason == "aborted_streaming"

    def test_interrupt_drains_pending_receipt(self) -> None:
        script = [_stream_event("part")]
        session = _claude_session(script)
        gen = session.send("q")

        async def _scenario() -> None:
            await gen.__anext__()
            await session.interrupt()
            rest = [event async for event in gen]
            client = session._client
            assert isinstance(client, FakeAgentClient)
            assert client.interrupts == 1
            assert not rest  # fake script held no ResultMessage; nothing pending

        asyncio.run(_scenario())


class TestSessionDiscipline:
    def test_second_send_while_busy_conflicts(self) -> None:
        release = asyncio.Event()

        async def slow_turn(
            client: object, text: str, history: list[dict[str, str]], k: int | None
        ) -> object:
            yield ChatDelta("a")
            await release.wait()
            yield ChatEnvelope(
                session_id="s",
                backend="claude-agent-sdk",
                num_turns=1,
                duration_ms=1,
                is_error=False,
            )

        session = ChatSession("s", "claude-agent-sdk", FakeAgentClient([]), slow_turn, 1800)

        async def _scenario() -> None:
            gen = session.send("first")
            first = await gen.__anext__()
            assert isinstance(first, ChatDelta)
            gen2 = session.send("second")
            with pytest.raises(ConflictError):
                await gen2.__anext__()
            release.set()
            rest = [event async for event in gen]
            assert isinstance(rest[-1], ChatEnvelope)

        asyncio.run(_scenario())

    def test_ttl_expiry_and_manager_sweep(self) -> None:
        session = _claude_session([], ttl_s=10)
        assert session.expired(session.last_activity + 11) is True
        assert session.expired(session.last_activity + 5) is False
        manager = ChatManager(max_sessions=1, ttl_s=10)
        asyncio.run(manager.create(session))
        closed = asyncio.run(manager.sweep())
        assert closed == []
        assert asyncio.run(manager.sweep()) == []  # nothing expired yet (sweep just ran)
        client = session._client
        assert isinstance(client, FakeAgentClient)
        assert not client.closed

    def test_ttl_sweep_closes_idle_session(self) -> None:
        session = _claude_session([], ttl_s=10)
        session.last_activity -= 20
        manager = ChatManager(max_sessions=1, ttl_s=10)
        asyncio.run(manager.create(session))
        closed = asyncio.run(manager.sweep())
        assert closed == ["sid-1"]
        client = session._client
        assert isinstance(client, FakeAgentClient)
        assert client.closed is True
        assert manager.get("sid-1") is None

    def test_max_sessions_conflict(self) -> None:
        manager = ChatManager(max_sessions=1, ttl_s=1800)
        asyncio.run(manager.create(_claude_session([])))
        with pytest.raises(ConflictError):
            asyncio.run(manager.create(_claude_session([], session_id="sid-2")))

    def test_create_with_live_id_answers_the_live_session(self) -> None:
        # One owner resuming their own live session (the page reload):
        # create answers the live session and disconnects the duplicate
        # spawn, instead of fighting the cap.
        manager = ChatManager(max_sessions=1, ttl_s=1800)
        live = _claude_session([])
        asyncio.run(manager.create(live))
        duplicate = _claude_session([])  # same id: a resume of the live one
        answered = asyncio.run(manager.create(duplicate))
        assert answered is live
        assert manager.get("sid-1") is live
        dup_client = duplicate._client
        assert isinstance(dup_client, FakeAgentClient)
        assert dup_client.closed is True  # the duplicate spawn disconnected
        live_client = live._client
        assert isinstance(live_client, FakeAgentClient)
        assert live_client.closed is False

    def test_aclose_is_idempotent(self) -> None:
        # The SSE pump and the disconnect handler can both close in the
        # same race; the second close must not re-disconnect.
        session = _claude_session([])
        asyncio.run(session.aclose())
        asyncio.run(session.aclose())
        client = session._client
        assert isinstance(client, FakeAgentClient)
        assert client.closed is True
        assert client.disconnects == 1

    def test_close_by_id(self) -> None:
        manager = ChatManager(max_sessions=1, ttl_s=1800)
        session = _claude_session([])
        asyncio.run(manager.create(session))
        assert asyncio.run(manager.close("sid-1")) is True
        assert asyncio.run(manager.close("sid-1")) is False


class TestClaudeRungGuards:
    def test_env_refusal_lists_the_variable(self) -> None:
        text = claude_rung.env_refusal({"ANTHROPIC_API_KEY": "k"})
        assert text is not None
        assert "ANTHROPIC_API_KEY" in text
        assert "anthropic-api or ollama" in text

    def test_clean_env_passes(self) -> None:
        assert claude_rung.env_refusal({}) is None

    def test_build_session_refuses_precedence_trap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
            asyncio.run(claude_rung.build_session(_settings(), {}, pg_pool=None, resume=None))

    def test_build_options_resume_and_fresh_identity(self) -> None:
        options = claude_rung._build_options(
            _settings(),
            {},
            pg_pool=None,
            on_stderr=lambda line: None,
            session_id="00000000-0000-0000-0000-000000000001",
            resume="sid-9",
        )
        assert options.resume == "sid-9"
        assert options.session_id is None
        assert options.strict_mcp_config is True
        assert options.setting_sources == []
        assert list(options.mcp_servers) == ["corpus"]
        assert options.allowed_tools == ["mcp__corpus__search"]
        assert "Bash" in options.disallowed_tools
        fresh = claude_rung._build_options(
            _settings(),
            {},
            pg_pool=None,
            on_stderr=lambda line: None,
            session_id="00000000-0000-0000-0000-000000000002",
            resume=None,
        )
        assert fresh.resume is None
        assert fresh.session_id == "00000000-0000-0000-0000-000000000002"

    def test_deny_backstop_allows_only_the_corpus_read(self) -> None:
        allowed = asyncio.run(claude_rung._deny_backstop("mcp__corpus__search", {"query": "x"}))
        denied = asyncio.run(claude_rung._deny_backstop("Bash", {"command": "rm"}))
        assert allowed.behavior == "allow"
        assert denied.behavior == "deny"

    def test_sdk_not_imported_by_package_import(self) -> None:
        code = (
            "import sys;"
            "import corpus_kb.chat, corpus_kb.chat.session, corpus_kb.chat.events,"
            " corpus_kb.chat.settings, corpus_kb.chat.agent_tools;"
            "assert 'claude_agent_sdk' not in sys.modules, 'SDK leaked at import'"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def _settings() -> ChatSettings:
    return read_chat_settings({})
