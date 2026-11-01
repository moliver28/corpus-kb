"""Focused stage-helper tests for the extracted locked-stage functions.

Covers the diff-coverage gaps from the IngestDocument threading: the
additive LlamaIndex mirror (success and failure), the document-commit
failure branch, the contextual-blurbs loop, and the ``rag_backend`` call
inside the locked stages.
"""

from __future__ import annotations

from typing import Any

import pytest

from corpus_kb.tools.ingest_common import build_document
from corpus_kb.tools.pipeline.document import IngestDocument
from corpus_kb.tools.pipeline.prepare import _prepare_chunks_for_store
from corpus_kb.tools.pipeline.produce import _commit_document, _mirror_rag_backend
from corpus_kb.utils.models import Chunk


class _RecordingRagBackend:
    """RagBackend double that records ingest calls (and can fail)."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []
        self._fail_with = fail_with

    async def ingest(self, document_id: str, chunks: list[dict[str, Any]]) -> None:
        if self._fail_with is not None:
            raise self._fail_with
        self.calls.append((document_id, chunks))


class _FailingCommitSink:
    """IngestSink double whose update_document always fails."""

    async def update_document(self, document: Any, file_hash: str) -> None:
        raise RuntimeError("write refused")


def _doc_with_chunks() -> IngestDocument:
    chunks = [
        Chunk(document_id="doc-1", text="alpha", source_type="text"),
        Chunk(document_id="doc-1", text="beta", source_type="text"),
    ]
    return IngestDocument(
        document=build_document("book.pdf", "text", "alpha beta"),
        text="alpha beta",
        file_hash="h" * 64,
        source_type="text",
        path="book.pdf",
        is_file=False,
        chunks=chunks,
    )


@pytest.mark.asyncio
async def test_mirror_rag_backend_sends_chunk_payloads() -> None:
    """The additive mirror forwards the doc id and the chunk payloads."""
    doc = _doc_with_chunks()
    backend = _RecordingRagBackend()
    errors: list[str] = []

    await _mirror_rag_backend(backend, doc, errors)

    assert errors == []
    assert len(backend.calls) == 1
    document_id, payloads = backend.calls[0]
    assert document_id == doc.document.document_id
    assert [payload["text"] for payload in payloads] == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_mirror_rag_backend_appends_error_on_failure() -> None:
    """A failed mirror degrades honestly: one log line, one stage error."""
    doc = _doc_with_chunks()
    backend = _RecordingRagBackend(fail_with=ValueError("mirror down"))
    errors: list[str] = []

    await _mirror_rag_backend(backend, doc, errors)

    assert errors == ["LlamaIndexError: mirror down"]
    assert backend.calls == []


@pytest.mark.asyncio
async def test_commit_document_records_write_failure() -> None:
    """A failed document refresh appends PostgresWriteError and moves on."""
    doc = _doc_with_chunks()
    sink = _FailingCommitSink()
    errors: list[str] = []

    await _commit_document(sink, doc, errors)

    assert doc.document.chunk_count == len(doc.chunks)
    assert errors == ["PostgresWriteError: write refused"]


def test_prepare_assigns_contextual_blurbs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contextual mode fills each chunk's context_blurb from the generator."""

    class _FakeContextGenerator:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config

        def generate_blurbs(self, text: str, chunks: list[str]) -> list[str]:
            return [f"blurb for {chunk}" for chunk in chunks]

    monkeypatch.setattr("corpus_kb.rag.contextualizer.ContextGenerator", _FakeContextGenerator)
    doc = _doc_with_chunks()
    config: dict[str, Any] = {"ingest": {"contextualize": {"enabled": True}}}
    errors: list[str] = []

    _prepare_chunks_for_store(doc, config, "doc-1", errors)

    assert errors == []
    assert doc.document.document_id == "doc-1"
    assert doc.chunks[0].context_blurb == "blurb for alpha"
    assert doc.chunks[1].context_blurb == "blurb for beta"


class _StubIngestStore:
    """Minimal store double for one locked-stages run (mirror focus)."""

    def __init__(self, pool: Any, tenant_id: str) -> None:
        self.tenant_id = tenant_id

    async def unchanged_doc_id(self, source: str, file_hash: str) -> str | None:
        return None

    async def begin_run(self, document: Any, file_hash: str) -> tuple[str, str]:
        return "doc-x", "run-x"

    async def store_chunks(self, chunks: list[Chunk]) -> set[str]:
        return {str(chunk.chunk_id) for chunk in chunks}

    async def count_chunks_for_document(self, doc_id: str) -> int:
        return 2

    async def store_vectors(self, chunks: Any, embedding_model: str, **kwargs: Any) -> int:
        return 0

    async def update_document(self, document: Any, file_hash: str) -> None:
        return None

    async def finish_run(self, run_id: str, file_hash: str, stages: Any, errors: Any) -> None:
        return None

    async def store_entities(self, entities: Any) -> dict[str, str]:
        return {}

    async def store_relations(self, relations: Any, **kwargs: Any) -> int:
        return 0


@pytest.mark.asyncio
async def test_locked_stages_mirror_rag_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The locked stages mirror chunks into a provided rag_backend."""

    async def fake_embed(
        chunks: list[Chunk],
        config: dict[str, Any],
        pool: object = None,
        text_override: dict[str, str] | None = None,
        document_text: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        return False, None

    monkeypatch.setattr("corpus_kb.tools.ingest_common.embed_chunks", fake_embed)
    monkeypatch.setattr("corpus_kb.tools.ingest_common.PostgresIngestStore", _StubIngestStore)
    from corpus_kb.tools.pipeline.stages import _run_locked_stages

    doc = _doc_with_chunks()
    backend = _RecordingRagBackend()

    result = await _run_locked_stages(
        document=doc.document,
        chunks=doc.chunks,
        file_hash=doc.file_hash,
        text=doc.text,
        source_type=doc.source_type,
        path=doc.path,
        config={"graph": {"extract_entities": False}, "ingest": {"chunking": {"enabled": False}}},
        pg_pool=None,
        tenant_id="00000000-0000-0000-0000-000000000003",
        rag_backend=backend,
        is_file=False,
    )

    assert result["status"] == "success"
    assert len(backend.calls) == 1
    assert backend.calls[0][0] == "doc-x"
