"""The tags/metadata read CLI legs (issue #182): honest exits, envelope fidelity.

Offline by construction: the pool and the ONE shared tool functions
(``tools.tag_tools``) are faked at the same seams the other surfaces use,
so the exit codes, the human rendering, and the ``--json`` envelope are
exercised end-to-end without a database.
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

DOC_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"

_CFG = {"embedding": {"model": "nomic-embed-text", "dimensions": 768}}

_TAGS = {
    "status": "success",
    "result": [{"name": "outage", "color": "#ff0000", "description": "incident notes"}],
}

_METADATA = {
    "status": "success",
    "result": [{"key": "owner", "value": "dana", "doc_id": DOC_ID}],
}


def _flat_help(output: str) -> str:
    """Rich help output with ANSI escapes stripped and whitespace joined."""
    return " ".join(_ANSI.sub("", output).split())


class _RecordingTool:
    """Canned envelope + the call args, for pass-through assertions."""

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
) -> FakePool:
    pool = FakePool()

    async def _open(_cfg: dict[str, object]) -> FakePool:
        return pool

    def _cfg() -> dict[str, object]:
        return dict(_CFG)

    monkeypatch.setattr("corpus_kb.cli.cli_tags._open_pool", _open)
    monkeypatch.setattr("corpus_kb.cli.cli_tags._load_config", _cfg)
    monkeypatch.setattr(f"corpus_kb.tools.tag_tools.{fn_name}", fake)
    return pool


def test_get_document_tags_json_is_the_tool_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_TAGS)
    pool = _patch_world(monkeypatch, "get_document_tags", fake)
    result = runner.invoke(app, ["get-document-tags", DOC_ID, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _TAGS
    call = fake.calls[0]
    assert call["args"][1] == DOC_ID
    assert call["args"][0] is pool
    assert pool.closed is True


def test_get_document_tags_human_rows_and_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_TAGS)
    _patch_world(monkeypatch, "get_document_tags", fake)
    result = runner.invoke(app, ["get-document-tags", DOC_ID])
    assert result.exit_code == 0, result.output
    assert "outage" in result.output

    empty = _RecordingTool({"status": "success", "result": []})
    _patch_world(monkeypatch, "get_document_tags", empty)
    result = runner.invoke(app, ["get-document-tags", DOC_ID])
    assert result.exit_code == 0, result.output
    assert "no results" in result.output


def test_get_document_tags_error_envelope_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool({"status": "error", "result": []})
    _patch_world(monkeypatch, "get_document_tags", fake)
    result = runner.invoke(app, ["get-document-tags", DOC_ID])
    assert result.exit_code == 1
    assert "get-document-tags: error" in result.output


def test_get_document_tags_corpus_error_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool({}, error=CorpusError("the database is down"))
    pool = _patch_world(monkeypatch, "get_document_tags", fake)
    result = runner.invoke(app, ["get-document-tags", DOC_ID])
    assert result.exit_code == 1
    assert "ERROR: the database is down" in result.output
    assert pool.closed is True


def test_get_metadata_json_is_the_tool_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_METADATA)
    pool = _patch_world(monkeypatch, "get_metadata", fake)
    result = runner.invoke(app, ["get-metadata", "--key", "owner", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _METADATA
    call = fake.calls[0]
    assert call["key"] == "owner"
    assert call["doc_id"] is None
    assert call["args"][0] is pool
    assert pool.closed is True


def test_get_metadata_passes_key_and_doc_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_METADATA)
    _patch_world(monkeypatch, "get_metadata", fake)
    result = runner.invoke(app, ["get-metadata", "--key", "owner", "--doc-id", DOC_ID])
    assert result.exit_code == 0, result.output
    assert fake.calls[0]["key"] == "owner"
    assert fake.calls[0]["doc_id"] == DOC_ID
    assert "dana" in result.output


def test_get_metadata_corpus_error_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool({}, error=CorpusError("the database is down"))
    pool = _patch_world(monkeypatch, "get_metadata", fake)
    result = runner.invoke(app, ["get-metadata"])
    assert result.exit_code == 1
    assert "ERROR: the database is down" in result.output
    assert pool.closed is True


def test_read_helps_document_exit_codes() -> None:
    for command in ("get-document-tags", "get-metadata"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0
        flat = _flat_help(result.output)
        assert "0 answered" in flat
        assert "1 failed" in flat
        assert "3 the database is unreachable" in flat
