"""The ``corpus-kb ask`` CLI leg: honest exits.

Offline by construction: the pool and the shared tool function
(``tools.ask_tools.ask``) are faked at the same seams the other surfaces
use, so the exit codes, the human rendering, and the ``--json`` envelope
are exercised end-to-end without a database or Ollama.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_kb.cli import app
from corpus_kb.errors import CorpusError
from tests.cli_fakes import FakePool

runner = CliRunner()

_ANSI = re.compile(r"\[[0-9;]*m")


def _flat_help(output: str) -> str:
    """Rich help output with ANSI escapes stripped and whitespace joined."""
    return " ".join(_ANSI.sub("", output).split())


_CFG = {"embedding": {"model": "nomic-embed-text", "dimensions": 768}}

_ASK = {
    "status": "success",
    "result": {
        "answer": "Startup order is documented [1].",
        "sentences": [{"text": "Startup order is documented [1].", "citations": [1]}],
        "sources": [
            {
                "n": 1,
                "chunk_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "doc_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
                "source": "runbook.md",
                "text": "The startup order is documented.",
                "score": 0.9,
                "file_path": None,
                "heading_path": [],
            }
        ],
        "verify": {"groundedness": 0.0, "verdicts": [], "abstained": True},
        "model": "fake-model",
        "note": None,
    },
    "degraded_excluded": {"count": 0, "reasons": []},
}


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


def _patch_world(monkeypatch: pytest.MonkeyPatch, fake: _RecordingTool) -> FakePool:
    pool = FakePool()

    async def _open(_cfg: dict[str, object]) -> FakePool:
        return pool

    def _cfg() -> dict[str, object]:
        return dict(_CFG)

    monkeypatch.setattr("corpus_kb.cli.cli_ask._open_pool", _open)
    monkeypatch.setattr("corpus_kb.cli.cli_ask._load_config", _cfg)
    monkeypatch.setattr("corpus_kb.tools.ask_tools.ask", fake)
    return pool


def test_ask_json_is_the_tool_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_ASK)
    pool = _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["ask", "what is the startup order?", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _ASK
    call = fake.calls[0]
    assert call["args"][0] == "what is the startup order?"
    assert call["config"] == dict(_CFG)
    assert call["pg_pool"] is pool
    assert pool.closed is True


def test_ask_human_output_shows_sentences_and_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _RecordingTool(_ASK)
    _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["ask", "what is the startup order?"])
    assert result.exit_code == 0, result.output
    flat = _flat_help(result.output)
    assert "Startup order is documented [1]. [1]" in flat
    assert "sources:" in flat
    assert "[1] runbook.md (score 0.9)" in flat


def test_ask_k_reaches_the_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_ASK)
    _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["ask", "anything", "--k", "3"])
    assert result.exit_code == 0, result.output
    assert fake.calls[0]["k"] == 3


def test_ask_empty_status_still_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(
        {
            "status": "empty",
            "result": {
                "answer": "",
                "sentences": [],
                "sources": [],
                "verify": {"groundedness": 0.0, "verdicts": [], "abstained": True},
                "model": "fake-model",
                "note": "no evidence matched the question",
            },
            "degraded_excluded": {"count": 0, "reasons": []},
        }
    )
    _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["ask", "anything"])
    assert result.exit_code == 0, result.output
    assert "no evidence matched the question" in result.output
    assert "no citable answer" in result.output


def test_ask_degraded_status_prints_the_note_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _RecordingTool(
        {
            "status": "degraded",
            "result": {
                "answer": "",
                "sentences": [],
                "sources": [{"n": 1, "source": "a.txt", "text": "x", "score": 0.5}],
                "verify": {"groundedness": 0.0, "verdicts": [], "abstained": True},
                "model": "fake-model",
                "note": "generation unavailable; returning evidence only",
            },
            "degraded_excluded": {"count": 2, "reasons": ["zero-query-embedding"]},
        }
    )
    _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["ask", "anything", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "degraded"


def test_ask_error_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool({}, error=CorpusError("the database is down"))
    _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["ask", "anything"])
    assert result.exit_code == 1
    assert "ERROR: the database is down" in result.output


def test_ask_error_status_envelope_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool({"status": "error", "error": "boom", "error_type": "internal_error"})
    _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["ask", "anything"])
    assert result.exit_code == 1


def test_ask_human_output_shows_groundedness_when_not_abstained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.loads(json.dumps(_ASK))
    payload["result"]["verify"] = {"groundedness": 0.5, "verdicts": [], "abstained": False}
    fake = _RecordingTool(payload)
    _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["ask", "anything"])
    assert result.exit_code == 0, result.output
    assert "groundedness: 0.5" in result.output


def test_help_documents_exit_codes() -> None:
    result = runner.invoke(app, ["ask", "--help"])
    assert result.exit_code == 0
    flat = _flat_help(result.output)
    assert "1 failed" in flat
    assert "3 the database is unreachable" in flat
