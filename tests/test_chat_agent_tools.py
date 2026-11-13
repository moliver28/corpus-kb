"""Corpus agent-tool tests (Phase-2 Wave 7, work-order step 3).

The tool resolves against a stubbed ``tools/search_tools.search`` (the
routes_ask monkeypatch pattern): passthrough of query/k/pool, the
read-only annotation, the output contract, and the honest ``is_error``
tool result when the search path raises a validation error.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from corpus_kb.chat.agent_tools import (
    build_corpus_tool,
    citations_from_contract,
)
from corpus_kb.errors import ValidationError


class _RecordingSearch:
    def __init__(self, payload: dict[str, object] | Exception) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    async def __call__(
        self,
        pg_pool: object,
        query: str,
        config: dict[str, object] | None = None,
        tenant_id: str = "t",
        k: int | None = None,
        source_type: str | None = None,
        self_query: bool | None = None,
    ) -> dict[str, object]:
        self.calls.append({"pool": pg_pool, "query": query, "config": config, "k": k})
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _payload() -> dict[str, object]:
    return {
        "status": "success",
        "result": [
            {
                "chunk_id": "c-1",
                "doc_id": "d-1",
                "source": "report.pdf",
                "file_path": "/docs/report.pdf",
                "text": "Budget line [1].",
                "score": 0.9,
            }
        ],
        "degraded_excluded": {"count": 1, "reasons": ["zero-vector"]},
        "remedy": "Re-ingest the degraded document.",
    }


def _tool(monkeypatch: pytest.MonkeyPatch, payload: dict[str, object] | Exception) -> tuple:
    recorder = _RecordingSearch(payload)

    async def stub(*args: Any, **kwargs: Any) -> dict[str, object]:
        query = args[1] if len(args) > 1 else kwargs.get("query", "")
        return await recorder(args[0], str(query), config=kwargs.get("config"), k=kwargs.get("k"))

    monkeypatch.setattr("corpus_kb.tools.search_tools.search", stub)
    return build_corpus_tool(pg_pool="POOL", config={"x": 1}), recorder


def _handler_result(tool: object, args: dict[str, object]) -> dict[str, object]:
    async def _run() -> dict[str, object]:
        handler = tool.handler
        return await handler(args)

    import asyncio

    return asyncio.run(_run())


class TestCorpusTool:
    def test_passthrough_query_k_pool_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tool, recorder = _tool(monkeypatch, _payload())
        result = _handler_result(tool, {"query": "budget", "k": 3})
        call = recorder.calls[0]
        assert call["query"] == "budget"
        assert call["k"] == 3
        assert call["pool"] == "POOL"
        assert call["config"] == {"x": 1}
        contract = json.loads(result["content"][0]["text"])
        assert contract["results"][0]["chunk_id"] == "c-1"

    def test_output_contract_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tool, _ = _tool(monkeypatch, _payload())
        contract = json.loads(_handler_result(tool, {"query": "budget"})["content"][0]["text"])
        row = contract["results"][0]
        assert set(row) >= {"chunk_id", "doc_id", "source", "file_path", "text", "score"}
        assert contract["degraded"] is True
        assert contract["note"] == "Re-ingest the degraded document."

    def test_missing_k_passes_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tool, recorder = _tool(monkeypatch, _payload())
        _handler_result(tool, {"query": "budget"})
        assert recorder.calls[0]["k"] is None

    def test_validation_error_is_error_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tool, _ = _tool(monkeypatch, ValidationError("k must be at least 1."))
        result = _handler_result(tool, {"query": "budget", "k": 0})
        assert result.get("is_error") is True
        assert "k must be at least 1." in result["content"][0]["text"]

    def test_read_only_annotation_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tool, _ = _tool(monkeypatch, _payload())
        annotations = tool.annotations
        assert (
            getattr(annotations, "readOnlyHint", getattr(annotations, "read_only_hint", None))
            is True
        )

    def test_tool_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tool, _ = _tool(monkeypatch, _payload())
        assert tool.name == "search"
        schema = tool.input_schema
        assert schema["required"] == ["query"]
        assert schema["properties"]["k"]["minimum"] == 1


class TestCitations:
    def test_citations_from_contract(self) -> None:
        contract = json.loads(
            '{"results": [{"chunk_id": "c-1", "doc_id": "d-1",'
            ' "source": "report.pdf", "file_path": "/docs/report.pdf",'
            ' "text": "Budget line [1].", "score": 0.9}], "degraded": true,'
            ' "note": null}'
        )
        citations = citations_from_contract(contract)
        assert len(citations) == 1
        assert citations[0].chunk_id == "c-1"
        assert citations[0].document_id == "d-1"
        assert citations[0].title == "report.pdf"
        assert citations[0].source_path == "/docs/report.pdf"
        assert citations[0].score == pytest.approx(0.9)

    def test_citations_empty_on_bad_contract(self) -> None:
        assert citations_from_contract({"results": "nope"}) == []
