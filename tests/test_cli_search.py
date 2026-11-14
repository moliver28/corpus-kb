"""The ``corpus-kb search`` CLI leg: honest exit codes and envelope fidelity.

Offline by construction: the pool and the ONE shared handler
(``tools.search_tools.search``) are faked at the same seams the other
surfaces use, so the exit codes, the human rendering, and the ``--json``
envelope are exercised end-to-end without a database.
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

DOC_ID = "00000000-0000-0000-0000-0000000000a1"
CHUNK_ID = "00000000-0000-0000-0000-0000000000b2"

_CFG = {"embedding": {"model": "nomic-embed-text", "dimensions": 768}}

_HIT = {
    "chunk_id": CHUNK_ID,
    "text": "startup behavior\nspans two lines",
    "score": 0.87,
    "source": "notes.md",
    "doc_id": DOC_ID,
    "file_path": None,
    "start_line": None,
    "end_line": None,
    "chunk_index": None,
    "heading_path": None,
}

_SUCCESS = {
    "status": "success",
    "result": [_HIT],
    "degraded_excluded": {"count": 0, "reasons": []},
}

_DEGRADED = {
    "status": "degraded_empty",
    "result": [],
    "degraded_excluded": {"count": 3, "reasons": ["ollama returned zero vectors"]},
    "remedy": "Run corpus-kb ingest-reembed once the provider is back.",
}


class _RecordingSearch:
    """Canned envelopes + the call args, for pass-through assertions."""

    def __init__(self, envelope: dict[str, object], error: Exception | None = None) -> None:
        self.envelope = envelope
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, pool: Any, query: str, **kwargs: Any) -> dict[str, object]:
        self.calls.append({"pool": pool, "query": query, **kwargs})
        if self.error is not None:
            raise self.error
        return self.envelope


def _patch_world(monkeypatch: pytest.MonkeyPatch, fake_search: _RecordingSearch) -> FakePool:
    pool = FakePool()

    async def _open(_cfg: dict[str, object]) -> FakePool:
        return pool

    def _cfg() -> dict[str, object]:
        return dict(_CFG)

    monkeypatch.setattr("corpus_kb.cli.cli_search._open_pool", _open)
    monkeypatch.setattr("corpus_kb.cli.cli_search._load_config", _cfg)
    monkeypatch.setattr("corpus_kb.tools.search_tools.search", fake_search)
    return pool


def test_json_envelope_is_the_handler_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _RecordingSearch(_SUCCESS)
    _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["search", "startup", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _SUCCESS


def test_human_output_ranks_hits_with_flat_snippets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_text = " ".join(["word"] * 60)
    hit = dict(_HIT, text=f"first line\nsecond line {long_text}")
    fake = _RecordingSearch(dict(_SUCCESS, result=[hit]))
    _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["search", "startup"])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert len(lines) == 1
    rank, sep, rest = lines[0].partition(". ")
    assert (rank, sep) == ("1", ". ")
    assert rest.startswith("[0.87] ")
    # The document's source (its readable name) prints beside the id.
    assert rest.startswith(f"[0.87] notes.md ({DOC_ID}) :: first line second line ")
    snippet = rest.split(" :: ", 1)[1]
    assert "\n" not in snippet
    assert len(snippet) <= 160
    assert snippet.endswith("...")


def test_hit_without_source_falls_back_to_file_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hit = dict(_HIT, source="", file_path="transcripts/interview01.docx")
    fake = _RecordingSearch(dict(_SUCCESS, result=[hit]))
    _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["search", "startup"])
    assert result.exit_code == 0, result.output
    assert f"[0.87] transcripts/interview01.docx ({DOC_ID}) ::" in result.output


def test_hit_without_any_label_prints_the_bare_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hit = dict(_HIT, source="", file_path=None)
    fake = _RecordingSearch(dict(_SUCCESS, result=[hit]))
    _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["search", "startup"])
    assert result.exit_code == 0, result.output
    assert f"[0.87] {DOC_ID} ::" in result.output


def test_empty_result_prints_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingSearch(dict(_SUCCESS, result=[]))
    _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["search", "startup"])
    assert result.exit_code == 0, result.output
    assert "no results" in result.output


def test_degraded_empty_prints_remedy_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _RecordingSearch(_DEGRADED)
    _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["search", "startup"])
    assert result.exit_code == 0, result.output
    assert "degraded_empty" in result.output
    assert "ingest-reembed" in result.output


def test_error_envelope_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingSearch({"status": "error", "result": [], "degraded_excluded": {}})
    _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["search", "startup"])
    assert result.exit_code == 1
    assert "search: error" in result.output
    assert "no results" not in result.output


def test_corpus_error_exits_one_and_closes_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _RecordingSearch({}, error=CorpusError("the database is down"))
    pool = _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["search", "startup"])
    assert result.exit_code == 1
    assert "ERROR: the database is down" in result.output
    assert pool.closed is True


def test_k_and_source_type_reach_the_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _RecordingSearch(_SUCCESS)
    pool = _patch_world(monkeypatch, fake)
    result = runner.invoke(app, ["search", "startup", "--k", "3", "--source-type", "transcript"])
    assert result.exit_code == 0, result.output
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["query"] == "startup"
    assert call["k"] == 3
    assert call["source_type"] == "transcript"
    assert call["config"] == dict(_CFG)
    assert call["pool"] is pool
    assert pool.closed is True


def test_search_help_documents_exit_codes() -> None:
    result = runner.invoke(app, ["search", "--help"])
    assert result.exit_code == 0
    assert "0 answered" in result.output
    assert "1 the search failed" in result.output
    assert "3 the database is unreachable" in result.output


# ============================================================================
# Issue #182 read pair: search-similar and search-context (same file's
# plumbing; the tool layer is faked at the same seam).
# ============================================================================


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


def _patch_read_world(
    monkeypatch: pytest.MonkeyPatch,
    fn_name: str,
    fake: _RecordingTool,
) -> FakePool:
    """Same seam patch as _patch_world, for the two read commands."""
    pool = FakePool()

    async def _open(_cfg: dict[str, object]) -> FakePool:
        return pool

    def _cfg() -> dict[str, object]:
        return dict(_CFG)

    monkeypatch.setattr("corpus_kb.cli.cli_search._open_pool", _open)
    monkeypatch.setattr("corpus_kb.cli.cli_search._load_config", _cfg)
    monkeypatch.setattr(f"corpus_kb.tools.search_tools.{fn_name}", fake)
    return pool


def test_search_similar_json_is_the_tool_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    envelope = {"status": "success", "result": [_HIT]}
    fake = _RecordingTool(envelope)
    pool = _patch_read_world(monkeypatch, "search_similar", fake)
    result = runner.invoke(app, ["search-similar", CHUNK_ID, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == envelope
    call = fake.calls[0]
    assert call["args"][1] == CHUNK_ID
    assert call["k"] == 10
    assert call["config"] == dict(_CFG)
    assert call["args"][0] is pool
    assert pool.closed is True


def test_search_similar_passes_k_and_lists_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    envelope = {"status": "success", "result": [_HIT]}
    fake = _RecordingTool(envelope)
    _patch_read_world(monkeypatch, "search_similar", fake)
    result = runner.invoke(app, ["search-similar", CHUNK_ID, "--k", "3"])
    assert result.exit_code == 0, result.output
    assert fake.calls[0]["k"] == 3
    assert "no results" not in result.output
    assert "[0.87]" in result.output
    assert DOC_ID in result.output


def test_search_similar_error_envelope_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool({"status": "error", "result": []})
    _patch_read_world(monkeypatch, "search_similar", fake)
    result = runner.invoke(app, ["search-similar", CHUNK_ID])
    assert result.exit_code == 1
    assert "search-similar: error" in result.output


def test_search_similar_corpus_error_exits_one_and_closes_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _RecordingTool({}, error=CorpusError("the database is down"))
    pool = _patch_read_world(monkeypatch, "search_similar", fake)
    result = runner.invoke(app, ["search-similar", CHUNK_ID])
    assert result.exit_code == 1
    assert "ERROR: the database is down" in result.output
    assert pool.closed is True


def test_search_context_json_is_the_tool_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    envelope = {"status": "success", "result": [_HIT]}
    fake = _RecordingTool(envelope)
    pool = _patch_read_world(monkeypatch, "search_context", fake)
    result = runner.invoke(app, ["search-context", "startup", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == envelope
    call = fake.calls[0]
    assert call["args"][1] == "startup"
    assert call["k"] == 5
    assert call["config"] == dict(_CFG)
    assert call["args"][0] is pool
    assert pool.closed is True


def test_search_context_empty_still_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool({"status": "success", "result": []})
    _patch_read_world(monkeypatch, "search_context", fake)
    result = runner.invoke(app, ["search-context", "ghost"])
    assert result.exit_code == 0, result.output
    assert "no results" in result.output


def test_search_context_corpus_error_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool({}, error=CorpusError("the database is down"))
    pool = _patch_read_world(monkeypatch, "search_context", fake)
    result = runner.invoke(app, ["search-context", "startup"])
    assert result.exit_code == 1
    assert "ERROR: the database is down" in result.output
    assert pool.closed is True


def test_search_read_helps_document_exit_codes() -> None:
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    for command in ("search-similar", "search-context"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0
        flat = " ".join(ansi.sub("", result.output).split())
        assert "0 answered" in flat
        assert "1 failed" in flat
        assert "3 the database is unreachable" in flat
