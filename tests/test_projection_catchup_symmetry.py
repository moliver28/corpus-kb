"""Zero-drift projection catch-up: ONE post-command contract, every surface.

Every event-writing research tool (``research_ingest_transcript``,
``research_ingest``, ``coding_run``, ``inductive_run``, ``codebook_promote``)
advances ALL read models (research + documents + embeds) through
``projections.catchup.advance_all_projections``. The CLI shares those tool
functions and never catches up on its own — no double catch-up, and MCP/HTTP
callers land the same projections the CLI always did.

Offline: the domain edge (run_deductive / run_inductive / promote_proposal /
handler ingests) and every projection ``catch_up`` are stubbed at the same
seams the real wiring uses.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from typer.testing import CliRunner

import corpus_kb.handlers.research_handler as research_handler_module
import corpus_kb.tools.research_tools as research_tools
from corpus_kb.cli import app
from corpus_kb.projections.documents_projection import DocumentsProjection
from corpus_kb.projections.embed_projection import EmbedChunksProjection
from corpus_kb.projections.research_projection import ResearchProjection
from corpus_kb.tools.research_tools import (
    codebook_promote,
    coding_run,
    inductive_run,
    research_ingest,
    research_ingest_transcript,
)

runner = CliRunner()
TENANT = "00000000-0000-0000-0000-000000000001"


class _CatchUpRecorder:
    """Class-level catch_up spy: order + count per projection."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = self

        async def research(self: Any, reader: Any) -> int:
            recorder.calls.append("research")
            return 0

        async def documents(self: Any, reader: Any, tenant_id: UUID) -> int:
            recorder.calls.append("documents")
            return 0

        async def embeds(self: Any, reader: Any, tenant_id: UUID) -> int:
            recorder.calls.append("embeds")
            return 0

        monkeypatch.setattr(ResearchProjection, "catch_up", research)
        monkeypatch.setattr(DocumentsProjection, "catch_up", documents)
        monkeypatch.setattr(EmbedChunksProjection, "catch_up", embeds)


class _FakePool:
    async def close(self) -> None:
        return None


def _install_domain_stubs(monkeypatch: pytest.MonkeyPatch) -> _CatchUpRecorder:
    """Stub the events app accessor + embedder factory; return the spy."""
    recorder = _CatchUpRecorder()
    recorder.install(monkeypatch)
    fake_app = SimpleNamespace(
        mapper=object(),
        recorder=SimpleNamespace(events_table_name="events"),
    )
    monkeypatch.setattr(
        research_handler_module, "get_research_handler", lambda pool: SimpleNamespace(app=fake_app)
    )
    monkeypatch.setattr("corpus_kb.rag.create_embedder", lambda cfg, pool=None: object())
    return recorder


# ---------------------------------------------------------------------------
# The shared tool functions advance ALL projections
# ---------------------------------------------------------------------------


def test_research_ingest_transcript_advances_all_projections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _install_domain_stubs(monkeypatch)
    transcript_calls: list[tuple[Any, ...]] = []

    class _Handler:
        @property
        def app(self) -> Any:
            return SimpleNamespace(
                mapper=object(), recorder=SimpleNamespace(events_table_name="events")
            )

        async def ingest_transcript(self, *args: Any, **kwargs: Any) -> dict[str, object]:
            transcript_calls.append(args)
            return {"status": "success"}

    fake_handler = _Handler()
    # research_tools binds get_research_handler at import time; the catch-up
    # helper imports it lazily from the handler module. Stub BOTH seams.
    monkeypatch.setattr(research_tools, "get_research_handler", lambda pool: fake_handler)
    monkeypatch.setattr(research_handler_module, "get_research_handler", lambda pool: fake_handler)

    result = asyncio.run(
        research_ingest_transcript("call.txt", _FakePool(), tenant_id=TENANT)  # type: ignore[arg-type]
    )

    assert result == {"status": "success"}
    assert recorder.calls == ["research", "documents", "embeds"]
    assert len(transcript_calls) == 1


def test_research_ingest_advances_all_projections(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _install_domain_stubs(monkeypatch)
    ingest_calls: list[tuple[Any, ...]] = []

    class _Handler:
        @property
        def app(self) -> Any:
            return SimpleNamespace(
                mapper=object(), recorder=SimpleNamespace(events_table_name="events")
            )

        async def ingest_path(self, *args: Any, **kwargs: Any) -> dict[str, object]:
            ingest_calls.append(args)
            return {"status": "success"}

    fake_handler = _Handler()
    monkeypatch.setattr(research_tools, "get_research_handler", lambda pool: fake_handler)
    monkeypatch.setattr(research_handler_module, "get_research_handler", lambda pool: fake_handler)

    result = asyncio.run(research_ingest("drop/", _FakePool(), tenant_id=TENANT))  # type: ignore[arg-type]

    assert result == {"status": "success"}
    assert recorder.calls == ["research", "documents", "embeds"]
    assert len(ingest_calls) == 1


def test_coding_run_advances_all_projections_not_research_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old tool caught up ONLY research; MCP/HTTP callers silently missed
    the documents + embeds projections. Now all three land, once."""
    recorder = _install_domain_stubs(monkeypatch)

    async def fake_run_deductive(
        pool: Any, tenant: UUID, cvid: UUID, project_id: Any = None, cal_alpha: float = 0.1
    ) -> dict[str, object]:
        return {"status": "success"}

    monkeypatch.setattr("corpus_kb.research.deductive_run.run_deductive", fake_run_deductive)

    summary = asyncio.run(
        coding_run(_FakePool(), str(uuid4()), tenant_id=TENANT)  # type: ignore[arg-type]
    )

    assert summary == {"status": "success"}
    assert recorder.calls == ["research", "documents", "embeds"]


def test_inductive_run_advances_all_projections(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _install_domain_stubs(monkeypatch)

    async def fake_run_inductive(
        pool: Any,
        tenant: UUID,
        project_id: Any = None,
        llm: Any = None,
        embedder: Any = None,
        cfg: Any = None,
    ) -> dict[str, object]:
        return {"status": "success"}

    monkeypatch.setattr("corpus_kb.research.inductive_run.run_inductive", fake_run_inductive)

    summary = asyncio.run(
        inductive_run(_FakePool(), tenant_id=TENANT)  # type: ignore[arg-type]
    )

    assert summary == {"status": "success"}
    assert recorder.calls == ["research", "documents", "embeds"]


def test_codebook_promote_advances_all_projections(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _install_domain_stubs(monkeypatch)

    async def fake_promote_proposal(
        pool: Any, tenant: UUID, *args: Any, **kwargs: Any
    ) -> dict[str, object]:
        return {"status": "promoted"}

    monkeypatch.setattr("corpus_kb.research.promote_code.promote_proposal", fake_promote_proposal)

    result = asyncio.run(
        codebook_promote(
            _FakePool(),
            1,
            "code",
            "definition",
            tenant_id=TENANT,  # type: ignore[arg-type]
        )
    )

    assert result == {"status": "promoted"}
    assert recorder.calls == ["research", "documents", "embeds"]


# ---------------------------------------------------------------------------
# The CLI delegates to the shared tools and never catches up itself
# ---------------------------------------------------------------------------


def _stub_cli_pool(monkeypatch: pytest.MonkeyPatch) -> _FakePool:
    pool = _FakePool()

    async def fake_create_pool(_conn_str: str) -> _FakePool:
        return pool

    monkeypatch.setattr("asyncpg.create_pool", fake_create_pool)
    return pool


def test_cli_research_ingest_delegates_and_skips_own_catchup(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _CatchUpRecorder()
    recorder.install(monkeypatch)
    pool = _stub_cli_pool(monkeypatch)
    tool_calls: list[dict[str, object]] = []

    async def fake_research_ingest(
        path: str,
        pg_pool: Any,
        project_id: Any = None,
        tenant_id: str = TENANT,
        force: bool = False,
    ) -> dict[str, object]:
        tool_calls.append({"path": path, "pool": pg_pool, "force": force})
        return {"status": "success"}

    monkeypatch.setattr(research_tools, "research_ingest", fake_research_ingest)
    target = tmp_path / "call.txt"
    target.write_text("moderator: hello", encoding="utf-8")

    result = runner.invoke(app, ["research", "ingest", str(target), "--force"])

    assert result.exit_code == 0, result.output
    assert tool_calls == [{"path": str(target), "pool": pool, "force": True}]
    assert recorder.calls == []  # the tool layer owns catch-up; CLI adds none


def test_cli_coding_run_delegates_to_shared_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _CatchUpRecorder()
    recorder.install(monkeypatch)
    _stub_cli_pool(monkeypatch)
    calls: list[dict[str, object]] = []

    async def fake_coding_run(
        pool: Any,
        codebook_version_id: str,
        project_id: Any = None,
        tenant_id: str = TENANT,
        cal_alpha: float = 0.1,
    ) -> dict[str, object]:
        calls.append({"cvid": codebook_version_id, "alpha": cal_alpha, "tenant": tenant_id})
        return {"status": "success"}

    monkeypatch.setattr(research_tools, "coding_run", fake_coding_run)
    cvid = str(uuid4())

    result = runner.invoke(app, ["coding", "run", "--codebook-version-id", cvid, "--alpha", "0.2"])

    assert result.exit_code == 0, result.output
    assert calls == [{"cvid": cvid, "alpha": 0.2, "tenant": TENANT}]
    assert recorder.calls == []


def test_cli_codebook_promote_delegates_exit_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _CatchUpRecorder()
    recorder.install(monkeypatch)
    _stub_cli_pool(monkeypatch)

    async def fake_promote(*args: Any, **kwargs: Any) -> dict[str, object]:
        return {"status": "duplicate_blocked"}

    monkeypatch.setattr(research_tools, "codebook_promote", fake_promote)

    result = runner.invoke(
        app,
        [
            "codebook",
            "promote",
            "--proposed-id",
            "3",
            "--name",
            "code",
            "--definition",
            "definition",
        ],
    )

    assert result.exit_code == 3, result.output  # duplicate_blocked maps to 3
    assert recorder.calls == []
