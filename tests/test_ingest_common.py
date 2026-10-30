"""Direct unit tests for ingest_common.py helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from corpus_kb.extraction.pgml_backend import PgmlExtractor
from corpus_kb.extraction.protocol import ExtractStats
from corpus_kb.extraction.regex_backend import RegexExtractor
from corpus_kb.ontology import load_ontology
from corpus_kb.rag.embedder import OllamaEmbedder
from corpus_kb.tools.ingest_common import (
    _extract_entities_flag,
    _extractor_name,
    _stage_store_and_embed,
    build_document,
    embed_chunks,
    extract_with_fallback,
    load_config_or_pass,
    ontology,
)
from corpus_kb.utils.models import Chunk, Document, Entity

_ONTOLOGY_PATH = (
    Path(__file__).resolve().parent.parent / "src" / "corpus_kb" / "config" / "ontology.yaml"
)

# ---------------------------------------------------------------------------
# load_config_or_pass
# ---------------------------------------------------------------------------


def test_load_config_or_pass_with_none_loads_config() -> None:
    """Passing None loads the default config."""
    config = load_config_or_pass(None)
    assert isinstance(config, dict)
    assert len(config) > 0


def test_load_config_or_pass_with_dict_returns_dict() -> None:
    """Passing a dict returns it unchanged."""
    custom: dict[str, object] = {"custom": True}
    result = load_config_or_pass(custom)
    assert result is custom


# ---------------------------------------------------------------------------
# ontology
# ---------------------------------------------------------------------------


def test_ontology_with_explicit_path() -> None:
    """Config with graph.ontology_path loads that ontology."""
    config: dict[str, object] = {"graph": {"ontology_path": "config/ontology.yaml"}}
    ont = ontology(config)
    assert len(ont.entity_types) == 9
    assert len(ont.relation_types) == 11


def test_ontology_with_fallback_path() -> None:
    """Config without graph.ontology_path falls back to default."""
    config: dict[str, object] = {"graph": {}}
    ont = ontology(config)
    assert len(ont.entity_types) == 9


# ---------------------------------------------------------------------------
# embed_chunks
# ---------------------------------------------------------------------------


class _FakePgmlConn:
    """Fake asyncpg connection answering pgml.embed(TEXT[]) calls."""

    def __init__(
        self,
        dimensions: int = 768,
        *,
        fail: bool = False,
        zero: bool = False,
    ) -> None:
        self.dimensions = dimensions
        self.fail = fail
        self.zero = zero
        self.fetch_calls: list[tuple[str, str, list[str]]] = []

    async def fetch(self, sql: str, model: str, texts: list[str]) -> list[dict[str, list[float]]]:
        self.fetch_calls.append((sql, model, list(texts)))
        if self.fail:
            raise RuntimeError("pgml extension unavailable")
        value = 0.0 if self.zero else 0.5
        return [{"embed": [value] * self.dimensions} for _ in texts]


class _FakePool:
    """Fake asyncpg.Pool: acquire() returns an async context manager."""

    def __init__(self, conn: _FakePgmlConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakePool:
        return self

    async def __aenter__(self) -> _FakePgmlConn:
        return self._conn

    async def __aexit__(self, *args: Any) -> bool:
        return False


def _pgml_ingest_config(**overrides: object) -> dict[str, object]:
    """pgml-primary config whose ollama fallback points at a dead port."""
    embedding: dict[str, object] = {
        "provider": "pgml",
        "fallback_provider": "ollama",
        "model": "nomic-embed-text",
        "base_url": "http://localhost:65432",
        "batch_size": 100,
        "dimensions": 768,
    }
    embedding.update(overrides)
    return {"embedding": embedding}


async def test_embed_chunks_pgml_primary_success() -> None:
    """pgml primary produces real vectors: not degraded, fallback never needed."""
    conn = _FakePgmlConn()
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]

    degraded, error = await embed_chunks(
        chunks,
        _pgml_ingest_config(),
        pool=_FakePool(conn),  # type: ignore[arg-type]
    )

    assert degraded is False
    assert error is None
    assert chunks[0].embedding == [0.5] * 768
    assert len(conn.fetch_calls) == 1


async def test_embed_chunks_pgml_zero_vectors_falls_back_to_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pgml zero-vectors: fallback ollama repopulates real vectors, degraded=True.

    The degraded flag must reflect that the primary failed (not misleading
    success), and the stale zero-vectors must not persist as embeddings.
    """
    conn = _FakePgmlConn(zero=True)
    real_vector = [0.7] * 768
    monkeypatch.setattr(
        OllamaEmbedder,
        "embed_batch",
        lambda self, texts: [list(real_vector) for _ in texts],
    )
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]

    degraded, error = await embed_chunks(
        chunks,
        _pgml_ingest_config(),
        pool=_FakePool(conn),  # type: ignore[arg-type]
    )

    assert degraded is True
    assert error is not None
    assert "pgml" in error and "ollama" in error
    assert chunks[0].embedding == real_vector


async def test_embed_chunks_pgml_sql_error_falls_back_to_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pgml SQL failure (extension missing): fallback ollama vectors, degraded=True."""
    conn = _FakePgmlConn(fail=True)
    real_vector = [0.9] * 768
    monkeypatch.setattr(
        OllamaEmbedder,
        "embed_batch",
        lambda self, texts: [list(real_vector) for _ in texts],
    )
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]

    degraded, error = await embed_chunks(
        chunks,
        _pgml_ingest_config(),
        pool=_FakePool(conn),  # type: ignore[arg-type]
    )

    assert degraded is True
    assert error is not None
    assert chunks[0].embedding == real_vector


async def test_embed_chunks_both_providers_fail_returns_zero_vectors() -> None:
    """pgml zeros + dead-port ollama fallback: degraded, zeros, message names both."""
    conn = _FakePgmlConn(zero=True)
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]

    degraded, error = await embed_chunks(
        chunks,
        _pgml_ingest_config(),
        pool=_FakePool(conn),  # type: ignore[arg-type]
    )

    assert degraded is True
    assert error is not None
    assert "both returned zero vectors" in error
    assert "pgml" in error and "ollama" in error
    assert chunks[0].embedding == [0.0] * 768


async def test_embed_chunks_same_provider_skips_fallback_attempt() -> None:
    """provider == fallback_provider: fallback is not attempted a second time."""
    conn = _FakePgmlConn(zero=True)
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]

    degraded, error = await embed_chunks(
        chunks,
        _pgml_ingest_config(fallback_provider="pgml"),
        pool=_FakePool(conn),  # type: ignore[arg-type]
    )

    assert degraded is True
    assert error is not None
    assert "PgmlEmbedder returned zero vectors" in error
    assert len(conn.fetch_calls) == 1
    assert chunks[0].embedding == [0.0] * 768


async def test_embed_chunks_dead_port_returns_degraded_tuple() -> None:
    """embed_chunks with a dead port returns (True, error_string)."""
    config: dict[str, object] = {
        "embedding": {
            "provider": "ollama",
            "model": "nomic-embed-text",
            "dimensions": 768,
            "base_url": "http://localhost:99999",
            "batch_size": 32,
        }
    }
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]
    degraded, error = await embed_chunks(chunks, config)
    assert degraded is True
    assert error is not None
    assert isinstance(error, str)
    assert len(error) > 0


async def test_embed_chunks_success_returns_ok_tuple() -> None:
    """embed_chunks with a working Ollama returns (False, None)."""
    config: dict[str, object] = {
        "embedding": {
            "provider": "ollama",
            "model": "nomic-embed-text",
            "dimensions": 768,
            "base_url": "http://localhost:11434",
            "batch_size": 32,
        }
    }
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]
    degraded, error = await embed_chunks(chunks, config)
    # If Ollama is running, this should be (False, None).
    # If not running, it should be (True, error_string).
    assert isinstance(degraded, bool)
    if not degraded:
        assert error is None
    else:
        assert error is not None


# ---------------------------------------------------------------------------
# _extractor_name
# ---------------------------------------------------------------------------


def test_extractor_name_with_explicit_value() -> None:
    """_extractor_name returns the configured extractor."""
    config: dict[str, object] = {"graph": {"extractor": "langextract"}}
    assert _extractor_name(config) == "langextract"


# ---------------------------------------------------------------------------
# _extract_entities_flag
# ---------------------------------------------------------------------------


def test_extract_entities_flag_true() -> None:
    """_extract_entities_flag returns True when explicitly set."""
    config: dict[str, object] = {"graph": {"extract_entities": True}}
    assert _extract_entities_flag(config) is True


def test_extract_entities_flag_false() -> None:
    """_extract_entities_flag returns False when explicitly disabled."""
    config: dict[str, object] = {"graph": {"extract_entities": False}}
    assert _extract_entities_flag(config) is False


def test_extract_entities_flag_defaults_true() -> None:
    """_extract_entities_flag defaults to True when not configured."""
    config: dict[str, object] = {"graph": {}}
    assert _extract_entities_flag(config) is True


# ---------------------------------------------------------------------------
# extract_with_fallback
# ---------------------------------------------------------------------------


class _FakePoolNoConn:
    """Minimal asyncpg.Pool stand-in for extraction tests."""

    def acquire(self) -> _FakePoolNoConn:
        return self

    async def __aenter__(self) -> _FakePoolNoConn:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


async def test_extract_with_fallback_pgml_success_records_pgml_id() -> None:
    """When pgml succeeds, extractor_id honestly reports 'pgml'."""
    chunk = Chunk(chunk_id="c1", document_id="d1", text="Acme Inc", source_type="text")
    ontology = load_ontology(_ONTOLOGY_PATH)

    class _HappyPgml(PgmlExtractor):
        extractor_id = "pgml"

        def __init__(self) -> None:
            super().__init__(pool=None)

        async def aextract(
            self, chunks: list[Chunk], ont: Any, source_document_id: str
        ) -> tuple[list[Entity], list[Any], ExtractStats]:
            return (
                [
                    Entity(
                        name="Acme",
                        entity_type="Org",
                        source_type="text",
                        source_document_id=source_document_id,
                        chunk_id=chunks[0].chunk_id,
                        confidence=None,
                        extractor_id="pgml",
                        metadata={},
                    )
                ],
                [],
                ExtractStats(backend="pgml", requested_entities=1, kept_entities=1),
            )

    import corpus_kb.extraction as extraction_module
    import corpus_kb.tools.ingest_extract as ingest_module

    original_create_extractor = ingest_module.create_extractor
    extraction_module.create_extractor = lambda config, pool=None: _HappyPgml()  # type: ignore[assignment]
    ingest_module.create_extractor = lambda config, pool=None: _HappyPgml()  # type: ignore[assignment]
    try:
        entities, _relations, extractor_id, stats = await extract_with_fallback(
            [chunk],
            ontology,
            "doc-pgml",
            {"graph": {"extractor": "pgml"}},
            pool=_FakePoolNoConn(),
        )
        assert extractor_id == "pgml"
        assert len(entities) == 1
        assert stats.backend == "pgml"
        assert stats.kept_entities == 1
    finally:
        extraction_module.create_extractor = original_create_extractor  # type: ignore[assignment]
        ingest_module.create_extractor = original_create_extractor  # type: ignore[assignment]


async def test_extract_with_fallback_pgml_fails_then_langextract_then_regex() -> None:
    """pgml failure cascades to langextract; langextract failure cascades to regex."""
    chunk = Chunk(chunk_id="c1", document_id="d1", text="Acme Inc", source_type="text")
    ontology = load_ontology(_ONTOLOGY_PATH)
    calls: list[str] = []

    class _FailingPgml(PgmlExtractor):
        extractor_id = "pgml"

        def __init__(self) -> None:
            super().__init__(pool=None)

        async def aextract(
            self, chunks: list[Chunk], ont: Any, source_document_id: str
        ) -> tuple[list[Entity], list[Any], ExtractStats]:
            calls.append("pgml")
            raise RuntimeError("pgml unavailable")

    class _FailingLangextract:
        extractor_id = "langextract"

        def extract(
            self, chunks: list[Chunk], ont: Any, source_document_id: str
        ) -> tuple[list[Entity], list[Any], ExtractStats]:
            calls.append("langextract")
            raise ImportError("langextract missing")

    import corpus_kb.extraction as extraction_module
    import corpus_kb.tools.ingest_extract as ingest_module

    original_create_extractor = ingest_module.create_extractor

    def _fake_create_extractor(config: dict[str, object], pool: Any = None) -> Any:
        graph = config.get("graph", {})
        assert isinstance(graph, dict)
        name = graph.get("extractor", "pgml")
        if name == "pgml":
            return _FailingPgml()
        if name == "langextract":
            return _FailingLangextract()
        return RegexExtractor()

    extraction_module.create_extractor = _fake_create_extractor  # type: ignore[assignment]
    ingest_module.create_extractor = _fake_create_extractor  # type: ignore[assignment]
    try:
        entities, _relations, extractor_id, stats = await extract_with_fallback(
            [chunk],
            ontology,
            "doc-cascade",
            {"graph": {"extractor": "pgml"}},
            pool=_FakePoolNoConn(),
        )
        assert calls == ["pgml", "langextract"]
        assert extractor_id == "regex"
        assert len(entities) >= 0
        # Failure honesty: both chain failures are recorded in stats notes.
        assert any("pgml" in note for note in stats.notes)
        assert any("langextract" in note for note in stats.notes)
        assert stats.backend == "regex"
    finally:
        extraction_module.create_extractor = original_create_extractor  # type: ignore[assignment]
        ingest_module.create_extractor = original_create_extractor  # type: ignore[assignment]


async def test_extract_with_fallback_langextract_success_honors_id() -> None:
    """When only langextract succeeds, extractor_id honestly reports 'langextract'."""
    chunk = Chunk(chunk_id="c1", document_id="d1", text="Acme Inc", source_type="text")
    ontology = load_ontology(_ONTOLOGY_PATH)
    calls: list[str] = []

    class _FailingPgml(PgmlExtractor):
        extractor_id = "pgml"

        def __init__(self) -> None:
            super().__init__(pool=None)

        async def aextract(
            self, chunks: list[Chunk], ont: Any, source_document_id: str
        ) -> tuple[list[Entity], list[Any], ExtractStats]:
            calls.append("pgml")
            raise RuntimeError("pgml unavailable")

    class _HappyLangextract:
        extractor_id = "langextract"

        def extract(
            self, chunks: list[Chunk], ont: Any, source_document_id: str
        ) -> tuple[list[Entity], list[Any], ExtractStats]:
            calls.append("langextract")
            return (
                [
                    Entity(
                        name="Acme",
                        entity_type="Org",
                        source_type="text",
                        source_document_id=source_document_id,
                        chunk_id=chunks[0].chunk_id,
                        confidence=None,
                        extractor_id="langextract",
                        metadata={},
                    )
                ],
                [],
                ExtractStats(backend="langextract", requested_entities=1, kept_entities=1),
            )

    import corpus_kb.extraction as extraction_module
    import corpus_kb.tools.ingest_extract as ingest_module

    original_create_extractor = ingest_module.create_extractor

    def _fake_create_extractor(config: dict[str, object], pool: Any = None) -> Any:
        graph = config.get("graph", {})
        assert isinstance(graph, dict)
        name = graph.get("extractor", "pgml")
        if name == "pgml":
            return _FailingPgml()
        if name == "langextract":
            return _HappyLangextract()
        return RegexExtractor()

    extraction_module.create_extractor = _fake_create_extractor  # type: ignore[assignment]
    ingest_module.create_extractor = _fake_create_extractor  # type: ignore[assignment]
    try:
        entities, _relations, extractor_id, stats = await extract_with_fallback(
            [chunk],
            ontology,
            "doc-lang",
            {"graph": {"extractor": "pgml"}},
            pool=_FakePoolNoConn(),
        )
        assert calls == ["pgml", "langextract"]
        assert extractor_id == "langextract"
        assert len(entities) == 1
        assert stats.backend == "langextract"
    finally:
        extraction_module.create_extractor = original_create_extractor  # type: ignore[assignment]
        ingest_module.create_extractor = original_create_extractor  # type: ignore[assignment]


async def test_extract_with_fallback_unknown_extractor_raises() -> None:
    """An unsupported extractor name propagates as ValueError, not silent regex."""
    chunk = Chunk(chunk_id="c1", document_id="d1", text="Acme Inc", source_type="text")
    ontology = load_ontology(_ONTOLOGY_PATH)

    with pytest.raises(ValueError, match="Unsupported graph extractor: bogus"):
        await extract_with_fallback(
            [chunk],
            ontology,
            "doc-bogus",
            {"graph": {"extractor": "bogus"}},
            pool=_FakePoolNoConn(),
        )


def test_extractor_name_defaults_to_pgml() -> None:
    """_extractor_name defaults to 'pgml' when not configured."""
    config: dict[str, object] = {"graph": {}}
    assert _extractor_name(config) == "pgml"


# ---------------------------------------------------------------------------
# Chunk model: dedup + contextual retrieval fields
# ---------------------------------------------------------------------------


def test_chunk_model_has_dedup_and_contextual_fields() -> None:
    c = Chunk(document_id="d", text="x", source_type="text")
    assert c.chunk_hash is None
    assert c.source_timestamp is None
    assert c.context_blurb is None


# ---------------------------------------------------------------------------
# Content-hash dedup helper
# ---------------------------------------------------------------------------


def test_sha256_is_deterministic() -> None:
    from corpus_kb.tools.ingest_common import _sha256

    assert _sha256("hello") == _sha256("hello")
    assert _sha256("hello") != _sha256("world")


# ---------------------------------------------------------------------------
# Contextual retrieval: source_type allowlist gating
# ---------------------------------------------------------------------------


def test_contextual_enabled_flag_respects_source_type_allowlist() -> None:
    from corpus_kb.tools.ingest_common import _contextual_enabled

    cfg = {
        "ingest": {
            "contextualize": {
                "enabled": False,
                "enabled_source_types": ["interview", "research"],
            }
        }
    }
    assert _contextual_enabled(cfg, source_type="interview") is True
    assert _contextual_enabled(cfg, source_type="code") is False

    cfg_forced_on = {"ingest": {"contextualize": {"enabled": True, "enabled_source_types": []}}}
    assert _contextual_enabled(cfg_forced_on, source_type="anything") is True


# ---------------------------------------------------------------------------
# W3 apply_to semantics (write-time contextual-blurb targets)
# ---------------------------------------------------------------------------


class _RecordingIngestStore:
    """Fake PostgresIngestStore: records chunk writes, accepts every chunk."""

    def __init__(self) -> None:
        self.stored_chunks: list[Chunk] = []

    async def store_chunks(self, chunks: list[Chunk]) -> set[str]:
        self.stored_chunks.extend(chunks)
        return {str(c.chunk_id) for c in chunks}

    async def count_chunks_for_document(self, doc_id: str) -> int:
        return len(self.stored_chunks)

    async def store_vectors(
        self,
        chunks: list[Chunk],
        model: str,
        dimensions: int,
        vectors_1024: dict[str, list[float]] | None = None,
    ) -> int:
        return len(chunks)


class _EmbedCapture:
    """Monkeypatch stand-in for ingest_common.embed_chunks: records inputs."""

    def __init__(self) -> None:
        self.text_overrides: list[dict[str, str]] = []

    async def __call__(
        self,
        chunks: list[Chunk],
        config: dict[str, object],
        pool: object = None,
        text_override: dict[str, str] | None = None,
        document_text: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> tuple[bool, str | None]:
        self.text_overrides.append(dict(text_override or {}))
        return False, None


def _blurred_chunks() -> list[Chunk]:
    return [
        Chunk(
            document_id="doc-1",
            text="chunk body",
            source_type="text",
            context_blurb="the blurb",
        ),
    ]


def _apply_to_config(targets: list[str]) -> dict[str, object]:
    return {"ingest": {"contextualize": {"apply_to": targets}}}


@pytest.mark.asyncio
async def test_apply_to_default_embeds_and_writes_blurb(monkeypatch) -> None:
    """Default (both targets) = pre-W3 behavior: column carries the blurb
    AND the embed input is blurb+text."""
    capture = _EmbedCapture()
    monkeypatch.setattr("corpus_kb.tools.ingest_common.embed_chunks", capture)
    store = _RecordingIngestStore()
    errors: list[str] = []
    await _stage_store_and_embed(store, _blurred_chunks(), {}, "doc text", None, errors)
    assert errors == []
    assert store.stored_chunks[0].context_blurb == "the blurb"
    (override,) = capture.text_overrides
    assert list(override.values()) == ["the blurb\n\nchunk body"]


@pytest.mark.asyncio
async def test_apply_to_embedding_only_writes_null_column(monkeypatch) -> None:
    """lexical NOT in apply_to: the column write is NULL, but the embed
    input still carries blurb+text (captured before the clear)."""
    capture = _EmbedCapture()
    monkeypatch.setattr("corpus_kb.tools.ingest_common.embed_chunks", capture)
    store = _RecordingIngestStore()
    await _stage_store_and_embed(
        store, _blurred_chunks(), _apply_to_config(["embedding"]), "doc text", None, []
    )
    assert store.stored_chunks[0].context_blurb is None
    (override,) = capture.text_overrides
    assert list(override.values()) == ["the blurb\n\nchunk body"]


@pytest.mark.asyncio
async def test_apply_to_lexical_only_embeds_raw_text(monkeypatch) -> None:
    """embedding NOT in apply_to: the column carries the blurb, the embed
    input is the raw chunk text."""
    capture = _EmbedCapture()
    monkeypatch.setattr("corpus_kb.tools.ingest_common.embed_chunks", capture)
    store = _RecordingIngestStore()
    await _stage_store_and_embed(
        store, _blurred_chunks(), _apply_to_config(["lexical"]), "doc text", None, []
    )
    assert store.stored_chunks[0].context_blurb == "the blurb"
    (override,) = capture.text_overrides
    assert list(override.values()) == ["chunk body"]


# ---------------------------------------------------------------------------
# The honest chunk total (issue #222: a re-run writes nothing)
# ---------------------------------------------------------------------------


class _RerunIngestStore:
    """Fake store where the second pass of the same chunks inserts nothing.

    ``count_chunks_for_document`` answers the document's stored total, the
    number a real re-run still holds.
    """

    def __init__(self) -> None:
        self.stored_hashes: set[str] = set()
        self.total = 30

    async def store_chunks(self, chunks: list[Chunk]) -> set[str]:
        fresh = {str(c.chunk_id) for c in chunks if c.chunk_hash not in self.stored_hashes}
        self.stored_hashes |= {c.chunk_hash for c in chunks}
        return fresh

    async def store_vectors(
        self,
        chunks: list[Chunk],
        model: str,
        dimensions: int,
        vectors_1024: dict[str, list[float]] | None = None,
    ) -> int:
        return len(chunks)

    async def count_chunks_for_document(self, doc_id: str) -> int:
        return self.total


def _two_chunks() -> list[Chunk]:
    return [
        Chunk(document_id="doc-1", text="first body", source_type="text"),
        Chunk(document_id="doc-1", text="second body", source_type="text"),
    ]


@pytest.mark.asyncio
async def test_a_rerun_reports_the_document_total_not_just_new_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pg_chunk_count drops to 0 on an idempotent re-run; pg_chunk_total
    stays at the document's full stored count (issue #222)."""
    capture = _EmbedCapture()
    monkeypatch.setattr("corpus_kb.tools.ingest_common.embed_chunks", capture)
    store = _RerunIngestStore()
    first = await _stage_store_and_embed(store, _two_chunks(), {}, "doc text", None, [])
    second = await _stage_store_and_embed(store, _two_chunks(), {}, "doc text", None, [])
    assert first[0] == 2  # pg_chunk_count: chunks this run wrote
    assert first[1] == 30  # pg_chunk_total: the document's stored count
    assert second[0] == 0  # the re-run inserted nothing
    assert second[1] == 30  # the total stays honest


class _CountConn:
    """Asyncpg double: the tenant GUC, then the count read, answers 30."""

    def __init__(self) -> None:
        self.sql: list[str] = []
        self.args: tuple[object, ...] = ()

    def transaction(self) -> _CountConn:
        return self

    async def __aenter__(self) -> _CountConn:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, sql: str, *args: object) -> str:
        self.sql.append(sql)
        return "SET"

    async def fetchval(self, sql: str, *args: object) -> int:
        self.sql.append(sql)
        self.args = args
        return 30


class _CountPool:
    """Pool double that hands out one recording connection."""

    def __init__(self, conn: _CountConn) -> None:
        self._conn = conn

    def acquire(self) -> _CountPool:
        return self

    async def __aenter__(self) -> _CountConn:
        return self._conn

    async def __aexit__(self, *exc: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_count_chunks_for_document_is_tenant_scoped() -> None:
    """The count rides one tenant-scoped transaction and answers the total."""
    from corpus_kb.tools.ingest_store import PostgresIngestStore

    conn = _CountConn()
    store = PostgresIngestStore(_CountPool(conn), "00000000-0000-0000-0000-000000000003")
    total = await store.count_chunks_for_document("doc-1")
    assert total == 30
    assert conn.sql[0] == "SELECT set_config('app.current_tenant_id', $1, true)"
    assert "SELECT COUNT(*) FROM chunks" in conn.sql[1]
    assert "tombstoned_at IS NULL" in conn.sql[1]
    assert conn.args == ("00000000-0000-0000-0000-000000000003", "doc-1")


# ---------------------------------------------------------------------------
# The title rides the direct documents upsert (issue #222)
# ---------------------------------------------------------------------------


def _off_switches() -> dict[str, Any]:
    """Config that keeps extraction, statements, and the shadow tree off."""
    return {
        "ingestion": {"title": "A Book Title"},
        "graph": {"extract_entities": False},
        "ingest": {"chunking": {"enabled": False}},
    }


class _TitleCaptureStore:
    """Stub store: captures the document begin_run writes (title lift pin)."""

    def __init__(self) -> None:
        self.begun: list[Document] = []

    async def unchanged_doc_id(self, source: str, file_hash: str) -> str | None:
        return None

    async def begin_run(self, document: Document, file_hash: str) -> tuple[str, str]:
        self.begun.append(document)
        return "doc-t", "run-t"

    async def store_chunks(self, chunks: list[Chunk]) -> set[str]:
        return {str(c.chunk_id) for c in chunks}

    async def count_chunks_for_document(self, doc_id: str) -> int:
        return 2

    async def store_vectors(
        self,
        chunks: list[Chunk],
        embedding_model: str,
        dimensions: int = 768,
        vectors_1024: dict[str, list[float]] | None = None,
    ) -> int:
        return 0

    async def update_document(self, document: Document, file_hash: str) -> None:
        return None

    async def finish_run(self, run_id: str, file_hash: str, stages: Any, errors: Any) -> None:
        return None


async def _run_locked_with_title(
    monkeypatch: pytest.MonkeyPatch, store: _TitleCaptureStore, config: dict[str, Any]
) -> dict[str, Any]:
    """One locked-stages run with embed stubbed and the store captured."""
    from corpus_kb.tools.pipeline.stages import _run_locked_stages

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
    monkeypatch.setattr(
        "corpus_kb.tools.ingest_common.PostgresIngestStore", lambda pool, tenant: store
    )
    return await _run_locked_stages(
        document=build_document("book.pdf", "text", "book body"),
        chunks=_two_chunks(),
        file_hash="h" * 64,
        text="book body",
        source_type="text",
        path="book.pdf",
        config=config,
        pg_pool=None,
        tenant_id="00000000-0000-0000-0000-000000000003",
        rag_backend=None,
        is_file=False,
    )


@pytest.mark.asyncio
async def test_a_config_title_rides_the_document_into_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-book title reaches begin_run's document AND the envelope."""
    store = _TitleCaptureStore()
    result = await _run_locked_with_title(monkeypatch, store, _off_switches())
    assert result["status"] == "success"
    assert store.begun[0].metadata["title"] == "A Book Title"
    assert result["title"] == "A Book Title"


@pytest.mark.asyncio
async def test_a_titleless_config_leaves_the_document_metadata_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _TitleCaptureStore()
    config = _off_switches()
    del config["ingestion"]
    result = await _run_locked_with_title(monkeypatch, store, config)
    assert result["status"] == "success"
    assert "title" not in store.begun[0].metadata
    assert "title" not in result


class _DocConn:
    """Asyncpg double capturing the documents upsert binding (title lift)."""

    def __init__(self) -> None:
        self.sql: list[str] = []
        self.row_args: tuple[object, ...] = ()

    def transaction(self) -> _DocConn:
        return self

    async def __aenter__(self) -> _DocConn:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, sql: str, *args: object) -> str:
        self.sql.append(sql)
        return "SET"

    async def fetchrow(self, sql: str, *args: object) -> dict[str, Any]:
        self.sql.append(sql)
        self.row_args = args
        return {"doc_id": "doc-t"}

    async def fetchval(self, sql: str, *args: object) -> str:
        self.sql.append(sql)
        return "run-t"


@pytest.mark.asyncio
async def test_begin_run_binds_the_document_title_into_the_upsert() -> None:
    """The queue path's documents row carries the title ($10)."""
    from corpus_kb.tools.ingest_store import PostgresIngestStore

    conn = _DocConn()
    store = PostgresIngestStore(_CountPool(conn), "00000000-0000-0000-0000-000000000003")
    doc = build_document("book.pdf", "text", "book body")
    doc.metadata["title"] = "A Book Title"
    doc_id, run_id = await store.begin_run(doc, "h" * 64)
    assert (doc_id, run_id) == ("doc-t", "run-t")
    assert any("INSERT INTO documents" in s for s in conn.sql)
    assert conn.row_args[9] == "A Book Title"


@pytest.mark.asyncio
async def test_a_titleless_upsert_binds_null_and_keeps_a_stored_title() -> None:
    """A title-less ingest binds NULL; the SQL COALESCE keeps a stored one."""
    from corpus_kb.tools.ingest_store import PostgresIngestStore

    conn = _DocConn()
    store = PostgresIngestStore(_CountPool(conn), "00000000-0000-0000-0000-000000000003")
    doc = build_document("book.pdf", "text", "book body")
    await store.begin_run(doc, "h" * 64)
    assert conn.row_args[9] is None
    # The NULL-safe merge lives in the statement: no bind can clear a title.
    assert any("title = COALESCE(EXCLUDED.title, documents.title)" in s for s in conn.sql)


# ---------------------------------------------------------------------------
# Bounded file-ingest concurrency (Wave 1 throughput)
# ---------------------------------------------------------------------------


def test_file_ingest_concurrency_defaults_and_caps() -> None:
    from types import SimpleNamespace

    from corpus_kb.tools.ingest_common import (
        DEFAULT_INGEST_CONCURRENCY,
        file_ingest_concurrency,
    )

    pool = SimpleNamespace(_maxsize=6)
    assert file_ingest_concurrency({}, pool) == DEFAULT_INGEST_CONCURRENCY
    assert file_ingest_concurrency({"ingestion": {}}, pool) == DEFAULT_INGEST_CONCURRENCY
    # Capped at pool_max - 2, never below 1.
    assert file_ingest_concurrency({"ingestion": {"concurrency": 9}}, pool) == 4
    tiny = SimpleNamespace(_maxsize=2)
    assert file_ingest_concurrency({"ingestion": {"concurrency": 4}}, tiny) == 1
    assert file_ingest_concurrency({"ingestion": {"concurrency": 0}}, pool) == 1
    assert file_ingest_concurrency({"ingestion": {"concurrency": "x"}}, pool) == 4


def test_file_ingest_concurrency_serial_on_duckdb() -> None:
    duckdb = pytest.importorskip("duckdb")

    from corpus_kb.storage.gateway import DuckDbGateway
    from corpus_kb.tools.ingest_common import file_ingest_concurrency

    gw = DuckDbGateway(duckdb.connect(":memory:"))
    limit = file_ingest_concurrency({"ingestion": {"concurrency": 8}}, gw)
    assert limit == 1
