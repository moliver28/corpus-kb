"""The ``corpus-kb query`` / ``verify-answer`` CLI legs: honest exits.

Offline by construction: the pool and the shared tool functions
(``tools.query_tools``) are faked at the same seams the other surfaces
use, so the exit codes, the human rendering, and the ``--json`` envelope
are exercised end-to-end without a database.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_kb.cli import app
from corpus_kb.errors import CorpusError

runner = CliRunner()

DOC_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"

_CFG = {"embedding": {"model": "nomic-embed-text", "dimensions": 768}}

_QUERY = {
    "status": "success",
    "result": {
        "route": "semantic",
        "confidence": 0.9,
        "fallback": False,
        "results": [{"doc_id": DOC_ID, "text": "startup\nbehavior", "score": 0.9}],
    },
}

_VERIFY = {
    "status": "success",
    "result": {
        "groundedness": 0.75,
        "abstained": False,
        "verdicts": [{"claim": "the server starts", "verdict": "supported"}],
    },
}


class _FakePool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _RecordingTool:
    def __init__(self, envelope: dict[str, object], error: Exception | None = None) -> None:
        self.envelope = envelope
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        self.calls.append({"args": args, **kwargs})
        if self.error is not None:
            raise self.error
        return self.envelope


def _patch_world(
    monkeypatch: pytest.MonkeyPatch,
    fn_name: str,
    fake: _RecordingTool,
) -> _FakePool:
    pool = _FakePool()

    async def _open(_cfg: dict[str, object]) -> _FakePool:
        return pool

    def _cfg() -> dict[str, object]:
        return dict(_CFG)

    monkeypatch.setattr("corpus_kb.cli_query._open_pool", _open)
    monkeypatch.setattr("corpus_kb.cli_query._load_config", _cfg)
    monkeypatch.setattr(f"corpus_kb.tools.query_tools.{fn_name}", fake)
    return pool


def test_query_json_is_the_tool_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_QUERY)
    pool = _patch_world(monkeypatch, "query", fake)
    result = runner.invoke(app, ["query", "how does startup work?", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _QUERY
    call = fake.calls[0]
    assert call["args"][0] == "how does startup work?"
    assert call["config"] == dict(_CFG)
    assert pool.closed is True


def test_query_human_output_ranks_results(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_QUERY)
    _patch_world(monkeypatch, "query", fake)
    result = runner.invoke(app, ["query", "how does startup work?"])
    assert result.exit_code == 0, result.output
    assert "route: semantic" in result.output
    assert f"1. {DOC_ID} :: startup behavior" in result.output


def test_query_empty_prints_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool({"status": "success", "result": {"route": "semantic", "results": []}})
    _patch_world(monkeypatch, "query", fake)
    result = runner.invoke(app, ["query", "anything"])
    assert result.exit_code == 0, result.output
    assert "no results" in result.output


def test_query_k_reaches_the_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_QUERY)
    _patch_world(monkeypatch, "query", fake)
    result = runner.invoke(app, ["query", "anything", "--k", "3"])
    assert result.exit_code == 0, result.output
    assert fake.calls[0]["k"] == 3


def test_query_error_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool({}, error=CorpusError("the database is down"))
    _patch_world(monkeypatch, "query", fake)
    result = runner.invoke(app, ["query", "anything"])
    assert result.exit_code == 1
    assert "ERROR: the database is down" in result.output


def test_verify_json_is_the_tool_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_VERIFY)
    pool = _patch_world(monkeypatch, "verify_answer", fake)
    result = runner.invoke(app, ["verify-answer", "the server starts", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _VERIFY
    call = fake.calls[0]
    assert call["args"][1] == "the server starts"
    assert call["args"][0] is pool
    assert call["chunk_ids"] == []


def test_verify_chunk_ids_reach_the_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_VERIFY)
    _patch_world(monkeypatch, "verify_answer", fake)
    result = runner.invoke(
        app,
        [
            "verify-answer",
            "the server starts",
            "--chunk-id",
            "11111111-1111-1111-1111-111111111111",
            "--chunk-id",
            "22222222-2222-2222-2222-222222222222",
        ],
    )
    assert result.exit_code == 0, result.output
    assert fake.calls[0]["chunk_ids"] == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ]


def test_verify_human_output_shows_groundedness(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_VERIFY)
    _patch_world(monkeypatch, "verify_answer", fake)
    result = runner.invoke(app, ["verify-answer", "the server starts"])
    assert result.exit_code == 0, result.output
    assert "groundedness: 0.75" in result.output
    assert "supported" in result.output


def test_verify_abstention_still_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(
        {"status": "success", "result": {"groundedness": 0.0, "abstained": True, "verdicts": []}}
    )
    _patch_world(monkeypatch, "verify_answer", fake)
    result = runner.invoke(app, ["verify-answer", "unverifiable"])
    assert result.exit_code == 0, result.output
    assert "abstained: True" in result.output


def test_help_documents_exit_codes() -> None:
    for command in ("query", "verify-answer"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0
        flat = " ".join(result.output.split())
        assert "1 failed" in flat
        assert "3 the database is unreachable" in flat
