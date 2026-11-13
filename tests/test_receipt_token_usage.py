"""Receipt round-trips for per-run token accounting (issue #198).

A run whose embedder reports its spend carries the typed ``token_usage``
section on the receipt; a run whose embedder reports nothing keeps the
byte-identical envelope. Covers the corpus pipeline (the ChunkSink path)
and the research transcript path (the UnitSink path). All backends are
faked at the house seams, so the tests never need a live Ollama.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from ollama._types import EmbedResponse

import corpus_kb.handlers.research_handler as research_handler_module
import corpus_kb.rag.embedder as embedder_module
import corpus_kb.rag.embedder_selection as embedder_selection_module
import corpus_kb.tools.ingest_common as ingest_common_module
from corpus_kb.rag.embedder import FakeEmbedder, OllamaEmbedder
from corpus_kb.research.dynamic_ingest import IngestDecision
from corpus_kb.research.transcript_parser import parse_transcript_text
from corpus_kb.tools.ingest_common import run_pipeline

_DIMS = 768

TRANSCRIPT = (
    "Moderator: How did the team handle the release process last quarter?\n"
    "Alice: We shipped weekly cuts and rolled back twice."
)


def _ollama_config() -> dict[str, object]:
    return {
        "graph": {"extract_entities": False},
        "ingest": {"chunking": {"enabled": False}},
        "embedding": {
            "provider": "ollama",
            "model": "nomic-embed-text",
            "dimensions": _DIMS,
            "base_url": "http://localhost:11434",
            "batch_size": 32,
            "late_chunking": False,
        },
    }


def _reporting_embedder(config: dict[str, object], prompt_tokens: int) -> OllamaEmbedder:
    embedder = OllamaEmbedder(config)

    def _respond(**kwargs: object) -> EmbedResponse:
        inputs = cast("list[str]", kwargs["input"])
        return EmbedResponse(
            embeddings=[[0.5] * _DIMS for _ in inputs], prompt_eval_count=prompt_tokens
        )

    embedder._client.embed = MagicMock(side_effect=_respond)
    return embedder


# ---------------------------------------------------------------------------
# Corpus pipeline (ChunkSink path)
# ---------------------------------------------------------------------------


class _LockConn:
    """Advisory-lock connection: try-lock always answers True."""

    async def fetchval(self, sql: str, *args: Any) -> bool:
        return True

    async def execute(self, sql: str, *args: Any) -> str:
        return "OK"


class _FakePool:
    def __init__(self) -> None:
        self.conn = _LockConn()

    def acquire(self) -> _FakePool:
        return self

    async def __aenter__(self) -> _LockConn:
        return self.conn

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def close(self) -> None:
        return None


class _InsertingStore:
    """Minimal ingest store: every chunk counts as newly inserted, so the
    embed stage actually runs."""

    def __init__(self, pool: Any, tenant_id: str = "t") -> None:
        del pool, tenant_id

    async def unchanged_doc_id(self, source: str, file_hash: str) -> str | None:
        return None

    async def begin_run(self, document: Any, file_hash: str) -> tuple[str, str]:
        return "doc-1", "run-1"

    async def store_chunks(self, chunks: list[Any]) -> set[str]:
        return {str(c.chunk_id) for c in chunks}

    async def store_vectors(
        self,
        chunks: list[Any],
        embedding_model: str,
        dimensions: int = _DIMS,
        vectors_1024: dict[str, list[float]] | None = None,
    ) -> int:
        return len(chunks)

    async def store_entities(self, entities: list[Any]) -> int:
        return 0

    async def store_relations(
        self, relations: list[Any], model_version: Any = None, prompt_version: Any = None
    ) -> int:
        return 0

    async def update_document(self, document: Any, file_hash: str) -> None:
        return None

    async def finish_run(
        self, run_id: str, file_hash: str, stages: dict[str, object], errors: list[str]
    ) -> None:
        return None

    async def fail_run(self, run_id: str, error: str) -> None:
        return None


async def test_corpus_receipt_reports_embed_token_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One reporting embedder: the receipt carries the typed usage section."""
    config = _ollama_config()
    embedder = _reporting_embedder(config, 37)
    monkeypatch.setattr(ingest_common_module, "PostgresIngestStore", _InsertingStore)
    monkeypatch.setattr(
        embedder_selection_module, "create_embedder", lambda cfg, pool=None: embedder
    )

    result = await run_pipeline(
        "Alpha beta gamma. Delta epsilon zeta.", "text", "receipt-doc.txt", config, _FakePool()
    )

    assert result["status"] == "success", result
    assert result["token_usage"]["embed"]["provider"] == "ollama"
    assert result["token_usage"]["embed"]["embedded_texts"] == result["chunk_count"]
    assert result["token_usage"]["embed"]["cached_texts"] == 0
    assert result["token_usage"]["embed"]["prompt_tokens"] == 37


async def test_corpus_receipt_omits_usage_when_backend_reports_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-reporting embedder keeps the envelope byte-identical: no key."""
    config = _ollama_config()
    monkeypatch.setattr(ingest_common_module, "PostgresIngestStore", _InsertingStore)
    monkeypatch.setattr(
        embedder_selection_module,
        "create_embedder",
        lambda cfg, pool=None: FakeEmbedder(cfg),
    )

    result = await run_pipeline(
        "Alpha beta gamma. Delta epsilon zeta.", "text", "plain-doc.txt", config, _FakePool()
    )

    assert result["status"] == "success", result
    assert "token_usage" not in result


# ---------------------------------------------------------------------------
# Research transcript path (UnitSink path)
# ---------------------------------------------------------------------------


class _NoopSink:
    def __init__(self, store: Any, tenant_id: Any) -> None:
        del store, tenant_id

    async def store_turn_texts(self, texts: dict[str, str], media_type: str) -> int:
        del texts, media_type
        return 0

    async def record_ledger(self, *args: Any) -> None:
        return None


class _RecordingApp:
    def __init__(self) -> None:
        self.saved: list[Any] = []

    def save(self, aggregate: Any) -> None:
        self.saved.append(aggregate)


async def test_research_receipt_reports_link_embed_usage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The transcript receipt sums the cross-ref link embeds (one file, one tally)."""
    rh = research_handler_module

    async def fake_plan(pool: Any, tenant_id: Any, path: Any, force: bool = False) -> Any:
        del pool, tenant_id, force
        return IngestDecision(path, "sha256abc", skipped=False, source_type="txt")

    async def fake_last_doc(pool: Any, tenant_id: Any, path: Any) -> None:
        del pool, tenant_id, path
        return None

    async def fake_source_doc(pool: Any, tenant_id: Any, source: str) -> None:
        del pool, tenant_id, source
        return None

    app = _RecordingApp()
    monkeypatch.setattr(rh, "plan_file", fake_plan)
    monkeypatch.setattr(rh, "ledger_last_doc_id", fake_last_doc)
    monkeypatch.setattr(rh, "documents_source_doc_id", fake_source_doc)
    monkeypatch.setattr(rh, "parse_transcript", lambda path: parse_transcript_text(TRANSCRIPT))
    monkeypatch.setattr(rh, "UnitSink", _NoopSink)
    monkeypatch.setattr(rh.ResearchHandler, "app", property(lambda self: app))

    config: dict[str, object] = {
        "embedding": {
            "provider": "ollama",
            "model": "nomic-embed-text",
            "dimensions": _DIMS,
            "base_url": "http://localhost:11434",
            "batch_size": 32,
        }
    }
    embedder = _reporting_embedder(config, 11)
    monkeypatch.setattr(embedder_module, "create_embedder", lambda cfg=None: embedder)

    path = tmp_path / "session.txt"
    path.write_text(TRANSCRIPT, encoding="utf-8")
    handler = rh.ResearchHandler(cast("Any", object()))

    result = await handler.ingest_transcript(UUID(int=2), str(path), force=True)

    assert result["status"] == "success", result
    assert result["token_usage"]["embed"] == {
        "provider": "ollama",
        "embedded_texts": 2,
        "cached_texts": 0,
        "prompt_tokens": 22,
    }


async def test_research_receipt_omits_usage_without_a_reporting_embedder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """An injected plain embed fn reports nothing: the receipt has no key."""
    rh = research_handler_module

    async def fake_plan(pool: Any, tenant_id: Any, path: Any, force: bool = False) -> Any:
        del pool, tenant_id, force
        return IngestDecision(path, "sha256abc", skipped=False, source_type="txt")

    async def fake_last_doc(pool: Any, tenant_id: Any, path: Any) -> None:
        del pool, tenant_id, path
        return None

    async def fake_source_doc(pool: Any, tenant_id: Any, source: str) -> None:
        del pool, tenant_id, source
        return None

    app = _RecordingApp()
    monkeypatch.setattr(rh, "plan_file", fake_plan)
    monkeypatch.setattr(rh, "ledger_last_doc_id", fake_last_doc)
    monkeypatch.setattr(rh, "documents_source_doc_id", fake_source_doc)
    monkeypatch.setattr(rh, "parse_transcript", lambda path: parse_transcript_text(TRANSCRIPT))
    monkeypatch.setattr(rh, "UnitSink", _NoopSink)
    monkeypatch.setattr(rh.ResearchHandler, "app", property(lambda self: app))

    def _plain_embed(text: str) -> list[float]:
        return [0.5] * _DIMS

    handler = rh.ResearchHandler(cast("Any", object()), embed_fn=_plain_embed)

    path = tmp_path / "session.txt"
    path.write_text(TRANSCRIPT, encoding="utf-8")

    result = await handler.ingest_transcript(uuid4(), str(path), force=True)

    assert result["status"] == "success", result
    assert "token_usage" not in result
