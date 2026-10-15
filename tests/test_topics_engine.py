"""Wave 4 engine: the fit over stored vectors through a FAKE bertopic.

CI never installs the ``[topics]`` extra, so every test drives the lazy
import seam (``engine.load_bertopic`` / ``engine.load_umap``) with a fake
module that mirrors the real model surface (fit_transform, get_topic_info,
get_topic). The fakes prove:

* the fit consumes the passed embeddings (no re-embed call exists);
* determinism: the UMAP instance gets the pinned random state and the
  build records every parameter;
* ``min_topic_size``: "auto" resolves to the BERTopic default and is
  reported; an int passes through to the model untouched;
* outliers (topic -1) are excluded from topics and assignments;
* a missing extra is the typed ``TopicDependencyError`` with the
  ``pip install 'corpus-kb[topics]'`` remediation.
"""

from __future__ import annotations

from typing import Any

import pytest

from corpus_kb.errors import DependencyMissingError
from corpus_kb.topics import engine
from corpus_kb.topics.engine import StoredChunk
from corpus_kb.topics.settings import TopicsSettings

# ---------------------------------------------------------------------------
# The fake bertopic module (the lazy-import seam)
# ---------------------------------------------------------------------------


class _FakeTopicInfo:
    """The one DataFrame method the engine reads."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def to_dict(self, orient: str) -> list[dict[str, Any]]:
        assert orient == "records"
        return [dict(row) for row in self._rows]


class _FakeBERTopic:
    """Records its config; splits docs round-robin into two topics."""

    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_kwargs = dict(kwargs)

    def fit_transform(
        self, documents: list[str], embeddings: Any = None
    ) -> tuple[list[int], list[float]]:
        assert embeddings is not None
        assert len(embeddings) == len(documents)
        assigned = [0, 1, 0, 1, -1, 0][: len(documents)]
        probs = [0.9, 0.8, 0.7, 0.6, -1.0, 0.5][: len(documents)]
        return assigned, probs

    def get_topic_info(self) -> _FakeTopicInfo:
        return _FakeTopicInfo(
            [
                {"Topic": -1, "Count": 1, "Name": "-1_outliers"},
                {"Topic": 0, "Count": 3, "Name": "0_alpha_signals"},
                {"Topic": 1, "Count": 2, "Name": "1_beta_rules"},
            ]
        )

    def get_topic(self, topic_id: int) -> list[tuple[str, float]]:
        words = {0: [("signal", 0.9), ("alpha", 0.4)], 1: [("rule", 0.8), ("beta", 0.3)]}
        return words.get(topic_id, [])


class _FakeUMAP:
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_kwargs = dict(kwargs)


class _FakeBertopicModule:
    BERTopic = _FakeBERTopic


class _FakeUmapModule:
    UMAP = _FakeUMAP


@pytest.fixture
def fake_bertopic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "load_bertopic", lambda: _FakeBertopicModule())
    monkeypatch.setattr(engine, "load_umap", lambda: _FakeUmapModule())


def _chunks(n: int = 6) -> list[StoredChunk]:
    return [
        StoredChunk(chunk_id=f"chunk-{i}", text=f"document text {i}", vector=[float(i), 1.0])
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# The gates
# ---------------------------------------------------------------------------


def test_missing_extra_is_a_typed_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing bertopic package maps to the remediation-carrying error."""

    def _raise(name: str) -> Any:
        raise ModuleNotFoundError(f"No module named {name!r}")

    monkeypatch.setattr(engine, "_import_module", _raise)
    with pytest.raises(DependencyMissingError) as excinfo:
        engine.build_topics(_chunks(), TopicsSettings(enabled=True))
    assert "corpus-kb[topics]" in excinfo.value.remediation


def test_build_records_determinism_params_and_topics(fake_bertopic: None) -> None:
    result = engine.build_topics(_chunks(), TopicsSettings(enabled=True))
    assert [t.label for t in result.topics] == ["0_alpha_signals", "1_beta_rules"]
    assert result.topics[0].size == 3
    assert result.topics[0].keywords == [["signal", 0.9], ["alpha", 0.4]]
    # The outlier topic never becomes a row; its chunks have no assignment.
    assert all(t.topic_index != engine.OUTLIER_TOPIC for t in result.topics)
    assert result.outlier_count == 1
    assert ("chunk-0", 0, 0.9) in result.assignments
    assert not any(chunk_id == "chunk-4" for chunk_id, _t, _p in result.assignments)
    # Probabilities clamp into [0, 1] (the approximate-probabilities note).
    assert all(0.0 <= p <= 1.0 for _c, _t, p in result.assignments)
    params = result.params
    assert params["umap_random_state"] == 42
    assert params["embedding_source"] == "chunks_vectors.embedding"
    assert params["documents"] == 6
    assert params["calculate_probabilities"] is False


def test_umap_gets_the_pinned_random_state(fake_bertopic: None) -> None:
    engine.build_topics(_chunks(), TopicsSettings(enabled=True))
    assert _FakeUMAP.last_kwargs["random_state"] == 42
    assert _FakeUMAP.last_kwargs["metric"] == "cosine"


def test_min_topic_size_auto_resolves_and_reports(fake_bertopic: None) -> None:
    result = engine.build_topics(_chunks(), TopicsSettings(min_topic_size="auto"))
    assert _FakeBERTopic.last_kwargs["min_topic_size"] == 10  # the BERTopic default
    assert result.params["min_topic_size"] == 10
    assert result.params["min_topic_size_requested"] == "auto"


def test_min_topic_size_int_passes_through(fake_bertopic: None) -> None:
    result = engine.build_topics(_chunks(), TopicsSettings(min_topic_size=3))
    assert _FakeBERTopic.last_kwargs["min_topic_size"] == 3
    assert result.params["min_topic_size"] == 3
    assert result.params["min_topic_size_requested"] == 3


def test_missing_umap_is_also_a_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "load_bertopic", lambda: _FakeBertopicModule())

    def _raise(name: str) -> Any:
        raise ModuleNotFoundError(f"No module named {name!r}")

    monkeypatch.setattr(engine, "_import_module", _raise)
    with pytest.raises(DependencyMissingError):
        engine.build_topics(_chunks(), TopicsSettings(enabled=True))
