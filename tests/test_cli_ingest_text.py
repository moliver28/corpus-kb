"""The ``corpus-kb ingest-text`` CLI leg: honest exits, envelope fidelity.

Offline by construction: the pool and the shared pipeline function
(``tools.ingest_common.ingest_text``) are faked at the same seams the
other surfaces use, so the exit codes, the human rendering, the stdin arm,
and the ``--json`` envelope are exercised end-to-end without a database.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_kb.cli import app
from corpus_kb.errors import CorpusError

runner = CliRunner()

_ANSI = re.compile(r"\[[0-9;]*m")


def _flat_help(output: str) -> str:
    """Rich help output with ANSI escapes stripped and whitespace joined."""
    return " ".join(_ANSI.sub("", output).split())


_CFG = {"embedding": {"model": "nomic-embed-text", "dimensions": 768}}

_SUCCESS = {
    "status": "success",
    "document_id": "doc-1",
    "path": "raw_text",
    "source_type": "text",
    "size_bytes": 11,
    "chunk_count": 2,
    "entity_count": 1,
    "relation_count": 0,
    "pg_chunk_count": 2,
    "pg_vector_count": 2,
    "degraded": False,
    "extractor_id": "regex",
    "extraction": {},
    "entities": [],
    "errors": [],
    "run_id": "run-1",
    "embedding_mode": "pooled",
}


class _FakePool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _RecordingIngest:
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
    fake: _RecordingIngest,
) -> _FakePool:
    pool = _FakePool()

    async def _open(_cfg: dict[str, object]) -> _FakePool:
        return pool

    def _cfg() -> dict[str, object]:
        return dict(_CFG)

    monkeypatch.setattr("corpus_kb.cli.cli_ingest_text._open_pool", _open)
    monkeypatch.setattr("corpus_kb.cli.cli_ingest_text._load_config", _cfg)
    monkeypatch.setattr("corpus_kb.tools.ingest_common.ingest_text", fake)
    return pool


def test_json_envelope_is_the_pipeline_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingIngest(_SUCCESS)
    pool = _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["ingest-text", "hello world", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _SUCCESS
    call = fake.calls[0]
    assert call["args"][0] == "hello world"
    assert call["args"][1] is pool
    assert call["source"] == "raw_text"
    assert call["source_type"] == "text"
    assert call["config"] == dict(_CFG)
    assert pool.closed is True


def test_human_output_summarizes_the_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingIngest(_SUCCESS)
    _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["ingest-text", "hello world"])
    assert result.exit_code == 0, result.output
    assert "ingested doc-1" in result.output
    assert "2 chunks" in result.output
    assert "1 entities" in result.output


def test_source_flags_reach_the_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingIngest(_SUCCESS)
    _patch_world(monkeypatch, fake)
    result = runner.invoke(
        app,
        ["ingest-text", "hello", "--source", "notes", "--source-type", "markdown"],
    )
    assert result.exit_code == 0, result.output
    assert fake.calls[0]["source"] == "notes"
    assert fake.calls[0]["source_type"] == "markdown"


def test_stdin_arm_reads_the_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingIngest(_SUCCESS)
    _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["ingest-text", "-"], input="piped text")
    assert result.exit_code == 0, result.output
    assert fake.calls[0]["args"][0] == "piped text"


def test_error_envelope_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingIngest({"status": "error", "message": "the pipeline refused"})
    _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["ingest-text", "hello"])
    assert result.exit_code == 1
    assert "ingest-text: error" in result.output


def test_corpus_error_exits_one_and_closes_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingIngest({}, error=CorpusError("the database is down"))
    pool = _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["ingest-text", "hello"])
    assert result.exit_code == 1
    assert "ERROR: the database is down" in result.output
    assert pool.closed is True


def test_help_documents_exit_codes() -> None:
    result = runner.invoke(app, ["ingest-text", "--help"])
    assert result.exit_code == 0
    flat = _flat_help(result.output)
    assert "0 ingested" in flat
    assert "1 failed" in flat
    assert "3 the database is unreachable" in flat
