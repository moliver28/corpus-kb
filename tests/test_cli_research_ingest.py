"""The research ingest CLI leg: ``--validate`` reaches the shared tool layer.

Offline by construction: the pool seam (``asyncpg.create_pool``) and the
shared tools (``tools.research_tools.research_ingest`` /
``research_ingest_transcript``) are faked, so the flag wiring is exercised
end-to-end without a database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_kb.cli import app

runner = CliRunner()


class _RecordingTool:
    """Stand-in for a ``tools.research_tools`` function; records kwargs."""

    def __init__(self, envelope: dict[str, object]) -> None:
        self.envelope = envelope
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        self.calls.append({"args": args, **kwargs})
        return self.envelope


def _patch_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakePool:
        async def close(self) -> None:
            return None

    async def fake_create_pool(_conn_str: str) -> _FakePool:
        return _FakePool()

    monkeypatch.setattr("asyncpg.create_pool", fake_create_pool)


def _transcript(tmp_path: Path) -> str:
    target = tmp_path / "call.txt"
    target.write_text("moderator: hello", encoding="utf-8")
    return str(target)


def test_research_ingest_validate_reaches_the_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _RecordingTool({"status": "success"})
    monkeypatch.setattr("corpus_kb.tools.research_tools.research_ingest", tool)
    _patch_pool(monkeypatch)
    result = runner.invoke(
        app, ["research", "ingest", _transcript(tmp_path), "--validate", "enforce"]
    )
    assert result.exit_code == 0, result.output
    assert tool.calls[0]["validate"] == "enforce"


def test_research_ingest_validate_omitted_keeps_the_off_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _RecordingTool({"status": "success"})
    monkeypatch.setattr("corpus_kb.tools.research_tools.research_ingest", tool)
    _patch_pool(monkeypatch)
    result = runner.invoke(app, ["research", "ingest", _transcript(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "validate" not in tool.calls[0]


def test_research_ingest_transcript_validate_reaches_the_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ingest-transcript command shares the research_ingest call."""
    tool = _RecordingTool({"status": "success"})
    monkeypatch.setattr("corpus_kb.tools.research_tools.research_ingest", tool)
    _patch_pool(monkeypatch)
    result = runner.invoke(
        app, ["research", "ingest-transcript", _transcript(tmp_path), "--validate", "advisory"]
    )
    assert result.exit_code == 0, result.output
    assert tool.calls[0]["validate"] == "advisory"


def test_research_watch_validate_reaches_the_per_file_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The --watch arm hands the flag to every per-file transcript ingest."""
    tool = _RecordingTool({"status": "success"})
    monkeypatch.setattr("corpus_kb.tools.research_tools.research_ingest_transcript", tool)
    _patch_pool(monkeypatch)
    target = Path(_transcript(tmp_path))

    async def fake_watch(pool: Any, tenant: Any, path: Any, interval: Any, ingest_one: Any) -> int:
        await ingest_one(target)
        return 0

    monkeypatch.setattr("corpus_kb.research.dynamic_ingest.watch", fake_watch)
    result = runner.invoke(app, ["research", "ingest", str(target), "--watch", "--validate", "off"])
    assert result.exit_code == 0, result.output
    assert tool.calls[0]["validate"] == "off"
