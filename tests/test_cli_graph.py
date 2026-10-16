"""The ``corpus-kb`` graph/documents CLI legs: honest exits, envelope fidelity.

Offline by construction: the pool and the shared tool functions
(``tools.graph_tools``, ``tools.ingest_tools``) are faked at the same
seams the other surfaces use, so the exit codes, the human rendering, and
the ``--json`` envelope are exercised end-to-end without a database.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_kb.cli import app
from corpus_kb.errors import CorpusError

runner = CliRunner()

ENTITY_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
RELATION_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
DOC_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"

_CFG = {"embedding": {"model": "nomic-embed-text", "dimensions": 768}}

_ENTITY = {"status": "success", "entity_id": ENTITY_ID, "name": "parity", "entity_type": "concept"}
_RELATION = {"status": "success", "relation_id": RELATION_ID, "relation_type": "related_to"}
_LIST = {
    "status": "success",
    "result": [
        {
            "doc_id": DOC_ID,
            "source": "notes.md",
            "source_type": "markdown",
            "chunk_count": 4,
            "created_at": "2026-10-13T00:00:00Z",
        }
    ],
}
_DELETE = {"status": "success", "document_id": DOC_ID}


class _FakePool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


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
    module: str,
    fn_name: str,
    fake: _RecordingTool,
) -> _FakePool:
    pool = _FakePool()

    async def _open(_cfg: dict[str, object]) -> _FakePool:
        return pool

    def _cfg() -> dict[str, object]:
        return dict(_CFG)

    monkeypatch.setattr("corpus_kb.cli_graph._open_pool", _open)
    monkeypatch.setattr("corpus_kb.cli_graph._load_config", _cfg)
    monkeypatch.setattr(f"{module}.{fn_name}", fake)
    return pool


def test_add_entity_json_is_the_tool_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_ENTITY)
    pool = _patch_world(monkeypatch, "corpus_kb.tools.graph_tools", "add_entity", fake)
    result = runner.invoke(app, ["add-entity", "parity", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _ENTITY
    call = fake.calls[0]
    assert call["args"][1] == "parity"
    assert call["entity_type"] == "concept"
    assert call["config"] == dict(_CFG)
    assert call["args"][0] is pool
    assert pool.closed is True


def test_add_entity_human_output(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(dict(_ENTITY, entity_type="person"))
    _patch_world(monkeypatch, "corpus_kb.tools.graph_tools", "add_entity", fake)
    result = runner.invoke(app, ["add-entity", "parity", "--entity-type", "person"])
    assert result.exit_code == 0, result.output
    assert f"entity {ENTITY_ID} created (person)" in result.output


def test_add_entity_error_envelope_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool({"status": "error", "message": "no graph store"})
    _patch_world(monkeypatch, "corpus_kb.tools.graph_tools", "add_entity", fake)
    result = runner.invoke(app, ["add-entity", "parity"])
    assert result.exit_code == 1
    assert "graph: error" in result.output


def test_add_relation_passes_type_and_weight(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(dict(_RELATION, relation_type="cites"))
    _patch_world(monkeypatch, "corpus_kb.tools.graph_tools", "add_relation", fake)
    result = runner.invoke(
        app,
        ["add-relation", ENTITY_ID, RELATION_ID, "--relation-type", "cites", "--weight", "0.5"],
    )
    assert result.exit_code == 0, result.output
    call = fake.calls[0]
    assert call["args"][1:3] == (ENTITY_ID, RELATION_ID)
    assert call["relation_type"] == "cites"
    assert call["weight"] == 0.5
    assert f"relation {RELATION_ID} created (cites)" in result.output


def test_corpus_error_exits_one_and_closes_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool({}, error=CorpusError("the database is down"))
    pool = _patch_world(monkeypatch, "corpus_kb.tools.graph_tools", "add_relation", fake)
    result = runner.invoke(app, ["add-relation", ENTITY_ID, RELATION_ID])
    assert result.exit_code == 1
    assert "ERROR: the database is down" in result.output
    assert pool.closed is True


def test_list_documents_json_is_the_tool_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_LIST)
    _patch_world(monkeypatch, "corpus_kb.tools.ingest_tools", "list_documents", fake)
    result = runner.invoke(app, ["list-documents", "--limit", "5", "--offset", "2", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _LIST
    call = fake.calls[0]
    assert call["limit"] == 5
    assert call["offset"] == 2


def test_list_documents_empty_prints_no_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool({"status": "success", "result": []})
    _patch_world(monkeypatch, "corpus_kb.tools.ingest_tools", "list_documents", fake)
    result = runner.invoke(app, ["list-documents"])
    assert result.exit_code == 0, result.output
    assert "no documents" in result.output


def test_list_documents_human_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_LIST)
    _patch_world(monkeypatch, "corpus_kb.tools.ingest_tools", "list_documents", fake)
    result = runner.invoke(app, ["list-documents"])
    assert result.exit_code == 0, result.output
    assert DOC_ID in result.output
    assert "markdown" in result.output
    assert "4 chunks" in result.output
    assert "notes.md" in result.output


def test_delete_document_json_and_idempotent_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_DELETE)
    pool = _patch_world(monkeypatch, "corpus_kb.tools.ingest_tools", "delete_document", fake)
    result = runner.invoke(app, ["delete-document", DOC_ID, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _DELETE
    assert fake.calls[0]["args"][0] == DOC_ID
    assert pool.closed is True


def test_delete_document_error_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool({"status": "error", "message": "could not delete"})
    _patch_world(monkeypatch, "corpus_kb.tools.ingest_tools", "delete_document", fake)
    result = runner.invoke(app, ["delete-document", DOC_ID])
    assert result.exit_code == 1
    assert "graph: error" in result.output
    assert "could not delete" in result.output


def test_help_documents_exit_codes() -> None:
    for command in ("add-entity", "add-relation", "list-documents", "delete-document"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0
        flat = " ".join(result.output.split())
        assert "0" in flat
        assert "1 failed" in flat
        assert "3 the database is unreachable" in flat
