"""Wave 4 reduction: fixture-first labels/merges, fail-open, pure remap.

The honesty contract under test: an LLM failure NEVER raises into the
run — the build keeps BERTopic's c-TF-IDF labels and records the note.
Fixtures are SHA256-keyed like the contextualizer's; the live path is
gated by ``live_fallback``; merges are validated against the discovered
topic ids so a hallucinating model cannot corrupt the build.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from corpus_kb.topics import reduction
from corpus_kb.topics.engine import EngineResult, TopicBuild
from corpus_kb.topics.reduction import ReductionResult, TopicReducer, apply_reduction
from corpus_kb.topics.settings import TopicsSettings

TOPICS = [
    TopicBuild(topic_index=0, label="0_alpha", size=3, keywords=[["signal", 0.9]]),
    TopicBuild(topic_index=1, label="1_beta", size=2, keywords=[["rule", 0.8]]),
]

# The LLM model id is part of the fixture key; one constant keeps the
# recorded-fixture tests and the reducer in lockstep.
BASE_SETTINGS = TopicsSettings(enabled=True, live_fallback=False)


def _fixture_dir(tmp_path: Path) -> Path:
    path = tmp_path / "topics_recorded"
    path.mkdir()
    return path


def _settings(fixture: Path | None, live: bool = False) -> TopicsSettings:
    return TopicsSettings(
        enabled=True,
        fixture_dir=str(fixture) if fixture else None,
        live_fallback=live,
    )


def test_empty_topics_skip_without_llm_call() -> None:
    result = TopicReducer(BASE_SETTINGS).reduce([])
    assert result.status == "skipped"
    assert result.labels == {} and result.merge_map == {}


def test_no_fixture_and_no_live_fallback_keeps_labels(tmp_path: Path) -> None:
    result = TopicReducer(_settings(_fixture_dir(tmp_path))).reduce(TOPICS)
    assert result.status == "skipped"
    assert "live_fallback" in (result.note or "")
    assert result.labels == {}


def test_fixture_hit_applies_recorded_labels_and_merges(tmp_path: Path) -> None:
    fixture = _fixture_dir(tmp_path)
    key = reduction._fixture_key(BASE_SETTINGS.llm, TOPICS)
    answer = {"labels": {"0": "Embedding signals", "1": "Retrieval rules"}, "merge": []}
    (fixture / f"{key}.json").write_text(json.dumps(answer), encoding="utf-8")
    result = TopicReducer(_settings(fixture)).reduce(TOPICS)
    assert result.status == "fixture"
    assert result.labels == {0: "Embedding signals", 1: "Retrieval rules"}


def test_live_fallback_success_is_recorded_and_parsed(tmp_path: Path) -> None:
    class _CannedClient:
        def generate(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "response": '```json\n{"labels": {"0": "Signals"}, '
                '"merge": [[1, 0], [7, 0], [1, 1]]}\n```'
            }

    reducer = TopicReducer(_settings(None, live=True))
    reducer._client = _CannedClient()
    result = reducer.reduce(TOPICS)
    assert result.status == "applied"
    assert result.labels == {0: "Signals"}
    # Unknown topic id (7) and the self-merge (1->1) are dropped.
    assert result.merge_map == {1: 0}


def test_live_failure_keeps_labels_and_records_the_note(tmp_path: Path) -> None:
    class _DownClient:
        def generate(self, **kwargs: Any) -> dict[str, Any]:
            raise ConnectionError("ollama down")

    reducer = TopicReducer(_settings(None, live=True))
    reducer._client = _DownClient()
    result = reducer.reduce(TOPICS)
    assert result.status == "failed"
    assert "ConnectionError" in (result.note or "")
    assert result.labels == {}


def test_unparsable_answer_is_a_recorded_failure(tmp_path: Path) -> None:
    class _RamblingClient:
        def generate(self, **kwargs: Any) -> dict[str, Any]:
            return {"response": "I think topic one is about many things."}

    reducer = TopicReducer(_settings(None, live=True))
    reducer._client = _RamblingClient()
    result = reducer.reduce(TOPICS)
    assert result.status == "failed"


# ---------------------------------------------------------------------------
# apply_reduction: the pure remapper
# ---------------------------------------------------------------------------


def _engine_result() -> EngineResult:
    return EngineResult(
        topics=[TopicBuild(t.topic_index, t.label, t.size, t.keywords) for t in TOPICS],
        assignments=[("c0", 0, 0.9), ("c1", 1, 0.8), ("c2", 1, 0.7)],
        params={"documents": 3},
        outlier_count=1,
    )


def test_apply_labels_only_keeps_indexes_and_sets_reduced_labels() -> None:
    reduced = apply_reduction(_engine_result(), ReductionResult("applied", labels={0: "Signals"}))
    assert [t.topic_index for t in reduced.topics] == [0, 1]
    assert reduced.params["reduction_status"] == "applied"
    assert "merged_topics" not in reduced.params or reduced.params["merged_topics"] == []


def test_apply_merge_folds_sizes_and_assignments() -> None:
    reduced = apply_reduction(_engine_result(), ReductionResult("applied", merge_map={1: 0}))
    assert [t.topic_index for t in reduced.topics] == [0]
    assert reduced.topics[0].size == 3  # 1 own + 2 absorbed (recounted)
    assert [(c, t) for c, t, _p in reduced.assignments] == [("c0", 0), ("c1", 0), ("c2", 0)]
    assert reduced.params["merged_topics"] == [1]


def test_apply_transitive_merge_chain() -> None:
    three = EngineResult(
        topics=[
            TopicBuild(0, "a", 1, []),
            TopicBuild(1, "b", 1, []),
            TopicBuild(2, "c", 1, []),
        ],
        assignments=[("c0", 0, 1.0), ("c1", 1, 1.0), ("c2", 2, 1.0)],
        params={},
        outlier_count=0,
    )
    reduced = apply_reduction(three, ReductionResult("applied", merge_map={0: 1, 1: 2}))
    assert [t.topic_index for t in reduced.topics] == [2]
    assert [(c, t) for c, t, _p in reduced.assignments] == [("c0", 2), ("c1", 2), ("c2", 2)]
    assert sorted(reduced.params["merged_topics"]) == [0, 1]


def test_apply_without_reduction_is_identity() -> None:
    result = _engine_result()
    assert apply_reduction(result, ReductionResult("skipped")) is result


def test_cyclic_merge_cannot_loop() -> None:
    """A hostile model answer (0->1, 1->0) has no survivor; it is dropped."""
    survivors = reduction._resolve_survivors({0: 1, 1: 0})
    assert survivors == {}
    reduced = apply_reduction(_engine_result(), ReductionResult("applied", merge_map={0: 1, 1: 0}))
    assert [t.topic_index for t in reduced.topics] == [0, 1]
