"""Ingestion strategy registry + transcript qa pipeline (plan Section 0, S4).

Offline suite: registry precedence, entry-point discovery (mocked),
default-strategy behavioral equivalence with the pre-registry path,
transcript strategy on a realistic fixture, conformance invariants
parametrized over ALL registered strategies, and the run_pipeline /
MCP-tool / CLI seams.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest
from test_ingest_integrity import _FakePool, _LockConn
from typer.testing import CliRunner

from corpus_kb.ingestion.base import (
    DETECT_ADOPT_CONFIDENCE,
    QA_CONTEXT_KEY,
    TOKEN_BUDGET_CHARS,
    ChunkPlan,
    DetectResult,
    PlannedChunk,
    SourceProfile,
    chunks_from_plan,
    validate_plan,
)
from corpus_kb.ingestion.registry import (
    UnknownStrategyError,
    get_strategy,
    resolve_strategy,
    strategy_names,
)
from corpus_kb.ingestion.strategies.default import DefaultStrategy
from corpus_kb.ingestion.strategies.transcript import TranscriptStrategy
from corpus_kb.utils.models import Chunk

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "transcripts" / "sample_interview.txt"

TRANSCRIPT_TEXT = FIXTURE.read_text(encoding="utf-8")
PLAIN_TEXT = (
    "# Alpha\n\nGateway routes requests to shard pools.\n\n"
    "# Beta\n\nAlerts page the on-call engineer after failures."
)

CFG_MAPPING: dict[str, object] = {
    "ingestion": {"strategies": {"transcript": "transcript"}, "default": "default"}
}
CFG_EMPTY: dict[str, object] = {"ingestion": {"strategies": {}, "default": "default"}}


@pytest.fixture
def fresh_registry():
    """Reset registry state around a test (discovery tests mutate it)."""
    import corpus_kb.ingestion.registry as reg

    saved_ready = reg._ready
    saved_registry = dict(reg._REGISTRY)
    reg._ready = False
    yield reg
    reg._ready = saved_ready
    reg._REGISTRY.clear()
    reg._REGISTRY.update(saved_registry)


# ---------------------------------------------------------------------------
# Registry precedence (explicit > config > detection > default)
# ---------------------------------------------------------------------------


def test_precedence_explicit_beats_config_and_detection() -> None:
    resolution = resolve_strategy(
        "transcript",
        "interview.txt",
        TRANSCRIPT_TEXT,
        CFG_MAPPING,
        strategy_override="default",
    )
    assert resolution.strategy.name == "default"
    assert resolution.basis == "explicit"


def test_precedence_config_mapping_beats_detection() -> None:
    resolution = resolve_strategy("transcript", "interview.txt", TRANSCRIPT_TEXT, CFG_MAPPING)
    assert resolution.strategy.name == "transcript"
    assert resolution.basis == "config"
    assert resolution.mapped_source_type == "transcript"


def test_precedence_detection_without_mapping() -> None:
    resolution = resolve_strategy("text", "interview.txt", TRANSCRIPT_TEXT, CFG_EMPTY)
    assert resolution.strategy.name == "transcript"
    assert resolution.basis == "detection"
    assert resolution.detection is not None
    assert resolution.detection.reason == "moderator-pattern"
    assert resolution.detection.confidence >= DETECT_ADOPT_CONFIDENCE


def test_precedence_default_fallback() -> None:
    resolution = resolve_strategy("text", "notes.txt", PLAIN_TEXT, CFG_EMPTY)
    assert resolution.strategy.name == "default"
    assert resolution.basis == "default"


def test_detection_always_runs_for_provenance() -> None:
    resolution = resolve_strategy(
        "transcript", "interview.txt", TRANSCRIPT_TEXT, CFG_MAPPING, strategy_override="default"
    )
    assert resolution.detected == "transcript"
    assert resolution.detection is not None
    assert resolution.detection.confidence > 0


def test_unknown_explicit_strategy_error_lists_valid() -> None:
    with pytest.raises(UnknownStrategyError, match="Valid strategies") as excinfo:
        resolve_strategy("text", "x.txt", "hello", CFG_EMPTY, strategy_override="bogus")
    assert "default" in str(excinfo.value)
    assert "transcript" in str(excinfo.value)


def test_unknown_config_mapped_strategy_is_loud() -> None:
    cfg: dict[str, object] = {"ingestion": {"strategies": {"transcript": "bogus"}}}
    with pytest.raises(UnknownStrategyError, match="bogus"):
        resolve_strategy("transcript", "interview.txt", TRANSCRIPT_TEXT, cfg)


# ---------------------------------------------------------------------------
# Entry-point discovery (mocked; error-tolerant, cached)
# ---------------------------------------------------------------------------


class _FakeEntry:
    def __init__(
        self, name: str, obj: object | None = None, error: Exception | None = None
    ) -> None:
        self.name = name
        self._obj = obj
        self._error = error

    def load(self) -> object:
        if self._error is not None:
            raise self._error
        return self._obj


class _PluginStrategy:
    name = "plugin"

    def detect(self, source_type_hint: str | None, filename: str, head: str) -> DetectResult:
        return DetectResult(0.9, "always")

    def build_chunks(self, raw: str, filename: str, profile: SourceProfile) -> ChunkPlan:
        return ChunkPlan(
            strategy=self.name, chunks=[PlannedChunk(text=raw, index=0)], document_text=raw
        )


def test_entry_point_discovery_loads_plugins_and_skips_broken(
    fresh_registry, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_entry_points(group: str) -> list[_FakeEntry]:
        assert group == fresh_registry.ENTRY_POINT_GROUP
        return [
            _FakeEntry("good", obj=_PluginStrategy),
            _FakeEntry("broken", error=RuntimeError("boom")),
        ]

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
    with caplog.at_level("WARNING"):
        names = fresh_registry.strategy_names()
    assert "plugin" in names
    assert {"default", "transcript", "plugin"} <= set(names)
    assert any(
        "Skipping broken ingestion-strategy plugin 'broken'" in record.getMessage()
        for record in caplog.records
    )


def test_entry_point_discovery_is_cached(fresh_registry, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_entry_points(group: str) -> list[_FakeEntry]:
        calls["n"] += 1
        return [_FakeEntry("good", obj=_PluginStrategy)]

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
    fresh_registry.strategy_names()
    fresh_registry.strategy_names()
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Default strategy: behavioral equivalence with the pre-registry path
# ---------------------------------------------------------------------------


def _assert_chunks_identical(old: list, new: list) -> None:  # list[corpus_kb.utils.models.Chunk]
    assert [c.text for c in new] == [c.text for c in old]
    assert [c.metadata for c in new] == [c.metadata for c in old]
    assert [(c.start_line, c.end_line) for c in new] == [(c.start_line, c.end_line) for c in old]
    assert [(c.source_start_char, c.source_end_char) for c in new] == [
        (c.source_start_char, c.source_end_char) for c in old
    ]
    assert [c.heading_path for c in new] == [c.heading_path for c in old]
    assert [c.source_type for c in new] == [c.source_type for c in old]


def test_default_strategy_equivalence_raw_text() -> None:
    from corpus_kb.chunking.unstructured_chunker import chunk_elements
    from corpus_kb.partitioning import ElementProxy

    old = chunk_elements(
        [ElementProxy(text=PLAIN_TEXT, element_type="NarrativeText", element_id="raw-text")],
        PLAIN_TEXT,
        document_id="d",
    )
    plan = DefaultStrategy().build_chunks(
        PLAIN_TEXT, "notes.txt", SourceProfile(source_type="text", is_file=False, path="notes.txt")
    )
    assert plan.document_text == PLAIN_TEXT
    _assert_chunks_identical(old, chunks_from_plan(plan, "d"))
    assert validate_plan(plan) == []


def test_default_strategy_equivalence_file(tmp_path: Path) -> None:
    from corpus_kb.chunking.unstructured_chunker import chunk_elements
    from corpus_kb.partitioning import partition as unstructured_partition

    # .txt on purpose: partition_md needs the unstructured[md] extra that CI
    # never installs; the plain auto partition on txt is the CI-real path.
    doc = tmp_path / "doc.txt"
    doc.write_text(PLAIN_TEXT, encoding="utf-8")
    elements = unstructured_partition(doc)
    old_text = "\n\n".join(e.text for e in elements if e.text)
    old = chunk_elements(elements, old_text, document_id="d")
    plan = DefaultStrategy().build_chunks(
        "", "doc.txt", SourceProfile(source_type="text", is_file=True, path=str(doc))
    )
    assert plan.document_text == old_text
    _assert_chunks_identical(old, chunks_from_plan(plan, "d"))


# ---------------------------------------------------------------------------
# Transcript strategy: qa chunks, turn windows, detection
# ---------------------------------------------------------------------------


def _transcript_plan(text: str = TRANSCRIPT_TEXT, filename: str = "interview.txt") -> ChunkPlan:
    return TranscriptStrategy().build_chunks(
        text, filename, SourceProfile(source_type="text", is_file=False, path=filename)
    )


def test_transcript_qa_chunks_carry_qa_context() -> None:
    plan = _transcript_plan()
    assert plan.chunks
    qa = [chunk for chunk in plan.chunks if QA_CONTEXT_KEY in chunk.metadata]
    assert qa, "no qa chunks produced"
    first = qa[0]
    context = first.metadata[QA_CONTEXT_KEY]
    assert set(context) == {"question", "participants", "exchange_id"}
    assert "when the outage began" in str(context["question"])
    assert str(context["exchange_id"]).startswith("exchange-")
    assert isinstance(context["participants"], list)
    # turn alignment: the question turn's words are in the chunk text
    assert "Can you start by describing" in first.text
    # every qa chunk's metadata stays JSON-serializable (conformance)
    assert validate_plan(plan) == []


def test_transcript_exchanges_cover_all_turns_in_order() -> None:
    plan = _transcript_plan()
    indices = [chunk.index for chunk in plan.chunks]
    assert indices == list(range(len(plan.chunks)))
    covered = [seq for chunk in plan.chunks for seq in chunk.metadata["turn_seqs"]]
    assert sorted(covered) == list(range(12))
    first_lines = [chunk.start_line for chunk in plan.chunks]
    assert first_lines == sorted(first_lines)


def test_transcript_turn_windows_without_qa_context() -> None:
    text = "".join(f"Speaker{i}: line {i} about the topic at hand\n" for i in range(1, 7))
    plan = TranscriptStrategy().build_chunks(
        text, "meeting.txt", SourceProfile(source_type="text", is_file=False, path="meeting.txt")
    )
    assert plan.chunks
    assert all(QA_CONTEXT_KEY not in chunk.metadata for chunk in plan.chunks)
    assert all(chunk.metadata.get("grouping") == "turns" for chunk in plan.chunks)


def test_transcript_empty_content_has_empty_reason() -> None:
    plan = TranscriptStrategy().build_chunks(
        "", "empty.txt", SourceProfile(source_type="text", is_file=False, path="empty.txt")
    )
    assert plan.chunks == []
    assert plan.empty_reason == "no speaker turns recovered from source"
    assert validate_plan(plan) == []


def test_transcript_long_turn_hard_split_keeps_budget() -> None:
    long_text = "word " * (TOKEN_BUDGET_CHARS // 4 + 100)
    text = f"Moderator: sum up the incident\nParticipant: {long_text}"
    plan = TranscriptStrategy().build_chunks(
        text, "long.txt", SourceProfile(source_type="text", is_file=False, path="long.txt")
    )
    assert any("hard-split" in warning for warning in plan.warnings)
    assert validate_plan(plan) == []


def test_detect_moderator_pattern() -> None:
    result = TranscriptStrategy().detect("text", "notes.txt", TRANSCRIPT_TEXT[:1024])
    assert result.confidence >= DETECT_ADOPT_CONFIDENCE
    assert result.reason == "moderator-pattern"


def test_detect_webvtt_header() -> None:
    head = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nModerator: hello\nParticipant: hi there\n"
    result = TranscriptStrategy().detect(None, "call.vtt", head)
    assert result.confidence >= DETECT_ADOPT_CONFIDENCE


def test_detect_csv_speaker_header() -> None:
    result = TranscriptStrategy().detect(None, "data.csv", "speaker,text\nalice,hello world\n")
    assert result.reason == "csv-speaker-header"
    assert result.confidence >= DETECT_ADOPT_CONFIDENCE


def test_detect_plain_prose_zero() -> None:
    result = TranscriptStrategy().detect("text", "notes.txt", PLAIN_TEXT)
    assert result.confidence == 0.0
    assert result.reason == "no transcript signal"


# ---------------------------------------------------------------------------
# Conformance suite: parametrized over ALL registered strategies
# ---------------------------------------------------------------------------


_CONFORMANCE_INPUTS = {
    "plain": (PLAIN_TEXT, SourceProfile(source_type="text", is_file=False, path="notes.txt")),
    "transcript": (
        TRANSCRIPT_TEXT,
        SourceProfile(source_type="text", is_file=False, path="interview.txt"),
    ),
    "empty": ("", SourceProfile(source_type="text", is_file=False, path="empty.txt")),
}


@pytest.mark.parametrize("sample", sorted(_CONFORMANCE_INPUTS))
def test_conformance_all_registered_strategies(sample: str) -> None:
    raw, profile = _CONFORMANCE_INPUTS[sample]
    for name in strategy_names():
        strategy = get_strategy(name)
        plan = strategy.build_chunks(raw, Path(profile.path).name, profile)
        problems = validate_plan(plan)
        assert problems == [], f"strategy {name!r} sample {sample!r}: {problems}"


def test_conformance_rejects_qa_context_from_non_transcript() -> None:
    plan = ChunkPlan(
        strategy="default",
        chunks=[PlannedChunk(text="x", index=0, metadata={QA_CONTEXT_KEY: {}})],
    )
    assert any(QA_CONTEXT_KEY in problem for problem in validate_plan(plan))


def test_conformance_rejects_budget_overflow_and_bad_indices() -> None:
    big = "x" * (TOKEN_BUDGET_CHARS + 1)
    plan = ChunkPlan(strategy="custom", chunks=[PlannedChunk(text=big, index=5)])
    problems = validate_plan(plan)
    assert any("token budget" in problem for problem in problems)
    assert any("indices" in problem for problem in problems)


def test_conformance_requires_empty_reason_when_no_chunks() -> None:
    assert validate_plan(ChunkPlan(strategy="custom")) != []
    assert validate_plan(ChunkPlan(strategy="custom", empty_reason="nothing")) == []


def test_conformance_rejects_non_serializable_metadata() -> None:
    plan = ChunkPlan(
        strategy="custom",
        chunks=[PlannedChunk(text="x", index=0, metadata={"bad": object()})],
    )
    assert any("JSON-serializable" in problem for problem in validate_plan(plan))


# ---------------------------------------------------------------------------
# run_pipeline seam: strategy provenance in the result dict (offline)
# ---------------------------------------------------------------------------


_INGEST_CONFIG: dict[str, object] = {
    "graph": {"extract_entities": False},
    "ingest": {"hierarchy": {"enabled": False}},
    "embedding": {
        "provider": "pgml",
        "fallback_provider": "ollama",
        "model": "nomic-embed-text",
        "base_url": "http://localhost:65432",
        "dimensions": 768,
        "batch_size": 32,
    },
    "ingestion": {"strategies": {}, "default": "default"},
}


def _patch_stores(monkeypatch: pytest.MonkeyPatch) -> _FakePool:
    """Replace the whole Postgres store (integrity-split API) with a fake.

    The class moved to tools.ingest_store and grew the ledger API
    (unchanged_doc_id/begin_run/finish_run), so the tests swap the class on
    the ingest_common module — the pattern test_ingest_integrity.py uses —
    and stub embed_chunks so no network or database is touched. Returns a
    fake pool (reused from test_ingest_integrity) for the advisory-lock
    handshake.
    """
    from corpus_kb.tools import ingest_common

    class _FakeStore:
        def __init__(self, pool: object, tenant_id: str = "") -> None:
            self._pool = pool
            self._tenant_id = tenant_id

        async def unchanged_doc_id(self, path: str, file_hash: str) -> None:
            return None

        async def begin_run(self, document: object, file_hash: str) -> tuple[str, str]:
            return "doc-strategy-test", "run-strategy-test"

        async def store_chunks(self, chunks: list[Chunk]) -> set[str]:
            return {str(chunk.chunk_id) for chunk in chunks}

        async def store_vectors(
            self,
            chunks: list[object],
            embedding_model: str,
            dimensions: int = 768,
            vectors_1024: object = None,
        ) -> int:
            return len(chunks)

        async def update_document(self, document: object, file_hash: str) -> None:
            return None

        async def finish_run(
            self, run_id: str, file_hash: str, stages: dict[str, object], errors: list[str]
        ) -> None:
            return None

        async def fail_run(self, run_id: str, error: str) -> None:
            return None

    async def fake_embed_chunks(chunks, config, pool=None, text_override=None):
        for chunk in chunks:
            chunk.embedding = [0.1] * 768
        return False, None

    monkeypatch.setattr(ingest_common, "PostgresIngestStore", _FakeStore)
    monkeypatch.setattr(ingest_common, "embed_chunks", fake_embed_chunks)
    return _FakePool(_LockConn(acquired=True))


async def test_run_pipeline_provenance_detection_adopts_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _patch_stores(monkeypatch)
    from corpus_kb.tools.ingest_common import run_pipeline

    result = await run_pipeline(TRANSCRIPT_TEXT, "text", "strategy-prov", _INGEST_CONFIG, pool)
    assert result["status"] == "success"
    assert result["strategy"] == "transcript"
    assert result["strategy_basis"] == "detection"
    assert result["detection_reason"] == "moderator-pattern"
    assert result["detected_strategy"] == "transcript"
    assert result["chunk_count"] > 0


async def test_run_pipeline_explicit_override_forces_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _patch_stores(monkeypatch)
    from corpus_kb.tools.ingest_common import run_pipeline

    result = await run_pipeline(
        TRANSCRIPT_TEXT,
        "text",
        "strategy-ovr",
        _INGEST_CONFIG,
        pool,
        strategy_name="default",
    )
    assert result["strategy"] == "default"
    assert result["strategy_basis"] == "explicit"
    # default path: whole raw text is ONE NarrativeText chunk
    assert result["chunk_count"] == 1


async def test_run_pipeline_unknown_override_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _patch_stores(monkeypatch)
    from corpus_kb.tools.ingest_common import run_pipeline

    with pytest.raises(UnknownStrategyError, match="Valid strategies"):
        await run_pipeline(
            TRANSCRIPT_TEXT,
            "text",
            "strategy-bad",
            _INGEST_CONFIG,
            pool,
            strategy_name="bogus",
        )


# ---------------------------------------------------------------------------
# MCP tool + ingest_text param validation
# ---------------------------------------------------------------------------


async def test_ingest_file_rejects_unknown_strategy(tmp_path: Path) -> None:
    from corpus_kb.tools.ingest_tools import ingest_file

    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")
    result = await ingest_file(str(target), None, source_type="bogus")
    assert result["status"] == "error"
    assert "Valid strategies" in str(result["message"])


async def test_ingest_file_forwards_known_strategy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import corpus_kb.tools.ingest_tools as tools

    captured: dict[str, object] = {}

    async def fake_run_pipeline(
        text,
        source_type,
        path,
        config,
        pg_pool,
        tenant_id,
        rag_backend=None,
        is_file=False,
        strategy_name=None,
    ):
        captured["strategy_name"] = strategy_name
        captured["source_type"] = source_type
        return {"status": "success"}

    monkeypatch.setattr(tools, "run_pipeline", fake_run_pipeline)
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")
    result = await tools.ingest_file(str(target), None, source_type="transcript")
    assert result["status"] == "success"
    assert captured["strategy_name"] == "transcript"
    # document source_type is still extension-derived, NOT the strategy name
    assert captured["source_type"] == "text"


async def test_ingest_directory_rejects_unknown_strategy(tmp_path: Path) -> None:
    from corpus_kb.tools.ingest_tools import ingest_directory

    result = await ingest_directory(str(tmp_path), None, source_type="bogus")
    assert result["status"] == "error"
    assert "Valid strategies" in str(result["message"])


async def test_ingest_text_accepts_transcript_source_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from corpus_kb.tools import ingest_common

    captured: dict[str, object] = {}

    async def fake_run_pipeline(
        text,
        source_type,
        path,
        config,
        pg_pool,
        tenant_id,
        rag_backend=None,
        is_file=False,
        strategy_name=None,
    ):
        captured["source_type"] = source_type
        return {"status": "success"}

    monkeypatch.setattr(ingest_common, "run_pipeline", fake_run_pipeline)
    result = await ingest_common.ingest_text(
        "Moderator: hi?\nParticipant: hello", None, source_type="transcript"
    )
    assert result["status"] == "success"
    assert captured["source_type"] == "transcript"


async def test_ingest_text_rejects_unknown_source_type() -> None:
    from corpus_kb.tools import ingest_common

    result = await ingest_common.ingest_text("x", None, source_type="bogus")
    assert result == {"status": "error", "message": "Invalid source_type: bogus"}


# ---------------------------------------------------------------------------
# CLI: --source-type validation + detection summary line
# ---------------------------------------------------------------------------


def test_cli_rejects_unknown_source_type() -> None:
    from corpus_kb.cli import app

    result = CliRunner().invoke(
        app, ["research", "ingest-transcript", "x.txt", "--source-type", "bogus"]
    )
    assert result.exit_code != 0
    assert "Valid strategies" in result.output


def test_cli_help_lists_source_type() -> None:
    from corpus_kb.cli import app

    result = CliRunner().invoke(app, ["research", "ingest", "--help"])
    assert result.exit_code == 0
    assert "--source-type" in result.output


def test_detection_summary_line_format(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from corpus_kb.cli import _print_detection_summary

    target = tmp_path / "interview.txt"
    target.write_text(TRANSCRIPT_TEXT, encoding="utf-8")
    _print_detection_summary(target, CFG_EMPTY, None)
    out = capsys.readouterr().out.strip()
    assert out.startswith("detected: transcript (")
    assert "moderator-pattern" in out
    assert "-> strategy transcript (via detection)" in out
