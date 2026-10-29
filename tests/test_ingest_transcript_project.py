"""ResearchHandler.ingest_transcript: the best-effort project link.

The handler link (migration 036) fires after the run "starts" on the
research path and must never fail ingest: a raising store, a missing
ledger, or an absent project column all reduce to a warning. The DB
layer is stubbed at the handler's port seams (same shape as
tests/test_research_ingestion_e2e.py's _RecordingStore).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from corpus_kb.research.dynamic_ingest import IngestDecision
from corpus_kb.research.transcript_parser import parse_transcript_text


class _SpyProjectStore:
    """PostgresIngestStore stand-in: records set_run_project calls."""

    last: _SpyProjectStore | None = None
    raise_on_link = False

    def __init__(self, pool: object, tenant_id: str = "") -> None:
        del pool, tenant_id
        self.calls: list[tuple[str, str]] = []
        _SpyProjectStore.last = self

    async def set_run_project(self, run_id: str, project_id: str) -> None:
        if type(self).raise_on_link:
            raise RuntimeError("link boom")
        self.calls.append((run_id, project_id))


class _RecordingApp:
    def __init__(self) -> None:
        self.saved: list[object] = []

    def save(self, aggregate: object) -> None:
        self.saved.append(aggregate)


def _wire_handler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[object, _RecordingApp]:
    import corpus_kb.handlers.research_handler as rh

    async def fake_plan(pool: object, tenant_id: object, path: Path, force: bool = False) -> Any:
        del pool, tenant_id, force
        return IngestDecision(path, "sha256abc", skipped=False, source_type="txt")

    class _NoopSink:
        def __init__(self, store: object, tenant_id: object) -> None:
            del store, tenant_id

        async def store_turn_texts(self, texts: dict[str, str], media_type: str) -> int:
            del texts, media_type
            return 0

        async def record_ledger(self, *args: object) -> None:
            return None

    app = _RecordingApp()
    monkeypatch.setattr(rh, "plan_file", fake_plan)
    monkeypatch.setattr(
        rh, "parse_transcript", lambda path: parse_transcript_text("Moderator: Hello.\nAlice: Hi.")
    )
    monkeypatch.setattr(rh, "UnitSink", _NoopSink)
    monkeypatch.setattr(rh, "PostgresIngestStore", _SpyProjectStore)
    monkeypatch.setattr(rh.ResearchHandler, "app", property(lambda self: app))
    return tmp_path / "session.txt", app


@pytest.mark.asyncio
async def test_ingest_transcript_links_the_run_to_the_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from corpus_kb.handlers.research_handler import ResearchHandler

    path, _ = _wire_handler(monkeypatch, tmp_path)
    path.write_text("Moderator: Hello.\nAlice: Hi.", encoding="utf-8")
    project = uuid4()
    handler = ResearchHandler(cast("Any", object()))
    result = await handler.ingest_transcript(uuid4(), str(path), project_id=project, force=True)
    assert result["status"] == "success"
    spy = _SpyProjectStore.last
    assert spy is not None and len(spy.calls) == 1
    run_id, linked_project = spy.calls[0]
    assert linked_project == str(project)
    assert run_id  # the path's run identity (the canonical document id)


@pytest.mark.asyncio
async def test_ingest_transcript_survives_a_failed_project_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from corpus_kb.handlers.research_handler import ResearchHandler

    path, _ = _wire_handler(monkeypatch, tmp_path)
    path.write_text("Moderator: Hello.\nAlice: Hi.", encoding="utf-8")
    _SpyProjectStore.raise_on_link = True
    try:
        handler = ResearchHandler(cast("Any", object()))
        result = await handler.ingest_transcript(uuid4(), str(path), project_id=uuid4(), force=True)
        assert result["status"] == "success"
    finally:
        _SpyProjectStore.raise_on_link = False


@pytest.mark.asyncio
async def test_ingest_transcript_without_project_skips_the_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from corpus_kb.handlers.research_handler import ResearchHandler

    path, _ = _wire_handler(monkeypatch, tmp_path)
    path.write_text("Moderator: Hello.\nAlice: Hi.", encoding="utf-8")
    handler = ResearchHandler(cast("Any", object()))
    result = await handler.ingest_transcript(uuid4(), str(path), force=True)
    assert result["status"] == "success"
    spy = _SpyProjectStore.last
    assert spy is not None and spy.calls == []
