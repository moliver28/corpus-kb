"""Guided and zero-shot topics: seed terms, topic descriptions, the threshold.

Covers the AMR program's topics extensions end to end: the engine
threads ``seed_topic_list`` / ``zeroshot_topic_list`` +
``zeroshot_min_similarity`` into the model call and records them in
params; absent params leave the model call untouched; the envelope
reports ``unassigned_count`` (docs assigned topic -1) and carries the
bias/fallback notes; the threshold setting validates with remediation.
No bertopic import anywhere: the fit runs through the engine fakes.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from corpus_kb.tools import topics_tools
from corpus_kb.topics import engine
from corpus_kb.topics.engine import EngineResult, StoredChunk, TopicBuild
from corpus_kb.topics.settings import (
    DEFAULT_ZEROSHOT_MIN_SIMILARITY,
    load_topics_settings,
    topics_settings_error,
)

TENANT = "00000000-0000-0000-0000-000000000001"

ENABLED = {"topics": {"enabled": True, "llm_reduction": False, "min_topic_size": 2}}


def _chunks(n: int = 6) -> list[StoredChunk]:
    return [
        StoredChunk(chunk_id=f"chunk-{i}", text=f"document text {i}", vector=[float(i), 1.0])
        for i in range(n)
    ]


class _FakeBERTopic:
    last_kwargs: ClassVar[dict[str, Any]] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_kwargs = dict(kwargs)

    def fit_transform(
        self, documents: list[str], embeddings: Any = None
    ) -> tuple[list[int], list[float]]:
        pattern = [0, 1, 0, 1, -1, 0]
        n = len(documents)
        return [pattern[i % len(pattern)] for i in range(n)], [0.9] * n

    def transform(
        self, documents: list[str], embeddings: Any = None
    ) -> tuple[list[int], list[float]]:
        return [1] * len(documents), [0.4] * len(documents)

    def get_topic_info(self) -> Any:
        class _Frame:
            def to_dict(self, orient: str) -> list[dict[str, Any]]:
                return [
                    {"Topic": -1, "Count": 1, "Name": "-1_outliers"},
                    {"Topic": 0, "Count": 3, "Name": "0_alpha"},
                    {"Topic": 1, "Count": 2, "Name": "1_beta"},
                ]

        return _Frame()

    def get_topic(self, topic_id: int) -> list[tuple[str, float]]:
        return [("word", 0.5)]


class _FakeModule:
    BERTopic = _FakeBERTopic


class _FakeUmap:
    def __init__(self, **kwargs: Any) -> None:
        pass


class _FakeUmapModule:
    UMAP = _FakeUmap


@pytest.fixture
def fake_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "load_bertopic", lambda: _FakeModule())
    monkeypatch.setattr(engine, "load_umap", lambda: _FakeUmapModule())


# ---------------------------------------------------------------------------
# The engine threading
# ---------------------------------------------------------------------------


def test_seed_terms_reach_the_model_and_the_params(fake_fit: None) -> None:
    seeds = ["signal", "noise"]
    engine.build_topics(_chunks(), load_topics_settings(ENABLED), seed_terms=seeds)
    assert _FakeBERTopic.last_kwargs["seed_topic_list"] == [seeds]


def test_topic_descriptions_reach_the_model_with_the_threshold(fake_fit: None) -> None:
    descriptions = ["market reports", "weather logs"]
    engine.build_topics(_chunks(), load_topics_settings(ENABLED), topic_descriptions=descriptions)
    assert _FakeBERTopic.last_kwargs["zeroshot_topic_list"] == descriptions
    assert _FakeBERTopic.last_kwargs["zeroshot_min_similarity"] == 0.7


def test_absent_params_leave_the_model_call_untouched(fake_fit: None) -> None:
    """Absent params pass the library defaults (None) — behavior unchanged."""
    engine.build_topics(_chunks(), load_topics_settings(ENABLED))
    assert _FakeBERTopic.last_kwargs["seed_topic_list"] is None
    assert _FakeBERTopic.last_kwargs["zeroshot_topic_list"] is None
    assert _FakeBERTopic.last_kwargs["zeroshot_min_similarity"] is None


def test_guided_and_zeroshot_are_recorded_in_params(fake_fit: None) -> None:
    result = engine.build_topics(
        _chunks(),
        load_topics_settings(ENABLED),
        seed_terms=["signal"],
        topic_descriptions=["reports"],
    )
    assert result.params["guided"] is True
    assert result.params["seed_terms_count"] == 1
    assert result.params["zeroshot"] is True
    assert result.params["zeroshot_topic_count"] == 1
    assert result.params["zeroshot_min_similarity"] == 0.7
    assert "seed words bias" in str(result.params["guided_note"]).lower()
    assert "fall" in str(result.params["zeroshot_note"]).lower()
    plain = engine.build_topics(_chunks(), load_topics_settings(ENABLED))
    assert plain.params["guided"] is False
    assert plain.params["zeroshot"] is False


# ---------------------------------------------------------------------------
# The threshold setting (SUPPORTED range + the installed default)
# ---------------------------------------------------------------------------


def test_threshold_default_matches_the_bertopic_default() -> None:
    # bertopic 0.17.4 BERTopic.__init__ ships zeroshot_min_similarity=0.7.
    assert DEFAULT_ZEROSHOT_MIN_SIMILARITY == 0.7


@pytest.mark.parametrize("bad", [1.5, 0.0, -0.2, True, "high"])
def test_bad_threshold_is_rejected_with_remediation(bad: object) -> None:
    cfg: dict[str, object] = {"topics": {"zeroshot_min_similarity": bad}}
    error = topics_settings_error(cfg)
    assert error is not None
    assert "zeroshot_min_similarity" in error
    with pytest.raises(ValueError, match="zeroshot_min_similarity"):
        load_topics_settings(cfg)


def test_good_threshold_passes_through() -> None:
    cfg: dict[str, object] = {"topics": {"zeroshot_min_similarity": 0.55}}
    assert load_topics_settings(cfg).zeroshot_min_similarity == 0.55


# ---------------------------------------------------------------------------
# The op envelope: unassigned_count and the honest notes
# ---------------------------------------------------------------------------


def _fit_result(doc_topics: list[int]) -> EngineResult:
    return EngineResult(
        topics=[TopicBuild(0, "0_alpha", 3, [["word", 0.5]])],
        assignments=[("chunk-0", 0, 0.9)],
        params={"documents": len(doc_topics), "guided": False, "zeroshot": False},
        outlier_count=1,
        doc_topics=doc_topics,
    )


def _patch_build(monkeypatch: pytest.MonkeyPatch, result: EngineResult) -> None:
    async def fake_read(pool: Any, tenant_id: str, limit: int | None = None) -> list[StoredChunk]:
        return _chunks(len(result.doc_topics))

    monkeypatch.setattr(topics_tools.engine, "read_stored_embeddings", fake_read)
    monkeypatch.setattr(topics_tools.engine, "build_topics", lambda *a, **k: result)

    async def fake_write(pool: Any, tenant_id: str, rows: Any, assignments: Any) -> dict[str, Any]:
        return {"topics_written": 1, "assignments_written": 1, "topic_ids": {0: "t-0"}}

    monkeypatch.setattr(topics_tools.writer, "write_build", fake_write)


async def test_envelope_reports_unassigned_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The fit assigns -1 to one doc and the transform half to topic 1:
    # exactly one doc sits unassigned.
    _patch_build(monkeypatch, _fit_result([0, 1, 0, 1, -1, 1]))
    result = await topics_tools.topics_build(_ProbePool(), config=ENABLED, tenant_id=TENANT)
    assert result["status"] == "success"
    assert result["unassigned_count"] == 1


async def test_guided_note_names_the_bias_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_build(monkeypatch, _fit_result([0, 0]))
    result = await topics_tools.topics_build(
        _ProbePool(), config=ENABLED, tenant_id=TENANT, seed_terms=["signal", "noise"]
    )
    notes = " ".join(str(note) for note in result["notes"]).lower()
    assert "bias" in notes
    assert "never force" in notes


async def test_zeroshot_note_names_the_fallback_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_build(monkeypatch, _fit_result([0, 0]))
    result = await topics_tools.topics_build(
        _ProbePool(),
        config=ENABLED,
        tenant_id=TENANT,
        topic_descriptions=["reports"],
    )
    notes = " ".join(str(note) for note in result["notes"]).lower()
    assert "fall" in notes
    assert result["params"]["zeroshot"] is False  # the fake fit; see the engine tests


async def test_empty_lists_behave_like_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_build(monkeypatch, _fit_result([0, 0]))
    result = await topics_tools.topics_build(
        _ProbePool(), config=ENABLED, tenant_id=TENANT, seed_terms=[], topic_descriptions=[]
    )
    assert result["status"] == "success"
    assert result["notes"] == []
    assert result["params"]["guided"] is False
    assert result["params"]["zeroshot"] is False


class _ProbeConn:
    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "to_regclass" in sql:
            return "public.topics"
        return None

    def transaction(self) -> _ProbeConn:
        return self

    async def __aenter__(self) -> _ProbeConn:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _ProbePool:
    def acquire(self) -> _ProbePool:
        return self

    async def __aenter__(self) -> _ProbeConn:
        return _ProbeConn()

    async def __aexit__(self, *args: Any) -> bool:
        return False
