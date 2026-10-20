"""Wave 4 engine: the fit over stored vectors through a FAKE bertopic.

CI never installs the ``[topics]`` extra, so every test drives the lazy
import seam (``engine.load_bertopic`` / ``engine.load_umap``) with a fake
module that mirrors the real model surface (fit_transform, transform,
topics_over_time, get_topic_info, get_topic). The fakes prove:

* the fit consumes the passed embeddings (no re-embed call exists);
* determinism: the UMAP instance gets the pinned random state and the
  build records every parameter;
* ``min_topic_size``: "auto" resolves to the BERTopic default and is
  reported; an int passes through to the model untouched;
* outliers (topic -1) are excluded from topics and assignments;
* WP-0 corpus scale: the fit consumes a seeded sample and the remainder
  is assigned by ``transform``; the same seed picks the same sample;
* the reader maps doc ids and both date columns into StoredChunk and
  pages through batches with a keyset cursor;
* a missing extra is the typed ``TopicDependencyError`` with the
  ``pip install 'corpus-kb[topics]'`` remediation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

import numpy as np
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
    """Records its config; splits docs round-robin into two topics.

    The fit assigns ``[0, 1, 0, 1, -1, 0, ...]`` (the pattern repeats);
    ``transform`` assigns everything topic 1, and both calls record what
    they received so the sample/transform split is observable.
    """

    last_kwargs: ClassVar[dict[str, Any]] = {}
    last_fit: ClassVar[dict[str, Any]] = {}
    last_transform: ClassVar[dict[str, Any] | None] = None
    last_tot: ClassVar[dict[str, Any] | None] = None

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_kwargs = dict(kwargs)

    def fit_transform(
        self, documents: list[str], embeddings: Any = None
    ) -> tuple[list[int], list[float]]:
        assert embeddings is not None
        assert len(embeddings) == len(documents)
        pattern = [0, 1, 0, 1, -1, 0]
        type(self).last_fit = {
            "texts": list(documents),
            "vectors": len(embeddings),
        }
        n = len(documents)
        assigned = [pattern[i % len(pattern)] for i in range(n)]
        probs = [0.9 - 0.1 * (i % 5) for i in range(n)]
        return assigned, probs

    def transform(
        self, documents: list[str], embeddings: Any = None
    ) -> tuple[list[int], list[float]]:
        assert embeddings is not None
        assert len(embeddings) == len(documents)
        type(self).last_transform = {
            "texts": list(documents),
            "vectors": len(embeddings),
        }
        n = len(documents)
        return [1] * n, [0.4] * n

    def topics_over_time(
        self, docs: list[str], timestamps: Any, *, topics: Any, nr_bins: int, evolution_tuning: bool
    ) -> _FakeTopicInfo:
        type(self).last_tot = {
            "docs": len(docs),
            "topics": list(topics),
            "nr_bins": nr_bins,
            "evolution_tuning": evolution_tuning,
        }
        return _FakeTopicInfo([])

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
    last_kwargs: ClassVar[dict[str, Any]] = {}

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


# ---------------------------------------------------------------------------
# WP-0: the corpus-scale sample-fit + transform path
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_fake_records() -> None:
    _FakeBERTopic.last_fit = {}
    _FakeBERTopic.last_transform = None
    _FakeBERTopic.last_tot = None


def test_below_the_cap_fits_everything_without_transform(fake_bertopic: None) -> None:
    """A corpus inside the sample cap keeps the one-call fit path."""
    result = engine.build_topics(_chunks(6), TopicsSettings(enabled=True))
    assert _FakeBERTopic.last_fit is not None
    assert _FakeBERTopic.last_fit["texts"] == [c.text for c in _chunks(6)]
    assert _FakeBERTopic.last_transform is None
    assert result.params["assignment_mode"] == "fit"
    assert result.params["fit_sample_size"] == 6
    assert result.params["total_chunks"] == 6
    assert result.params["fit_seed"] == 42


def test_above_the_cap_fits_the_sample_and_transforms_the_rest(
    fake_bertopic: None,
) -> None:
    """Only the seeded sample reaches fit_transform; the rest gets transform."""
    chunks = _chunks(10)
    result = engine.build_topics(chunks, TopicsSettings(enabled=True), max_embeddings=4)
    assert _FakeBERTopic.last_fit is not None
    assert _FakeBERTopic.last_transform is not None
    assert len(_FakeBERTopic.last_fit["texts"]) == 4
    assert len(_FakeBERTopic.last_transform["texts"]) == 6
    # The two doc sets partition the corpus, in the read order per half.
    expected_sample = sorted(
        int(i) for i in np.random.default_rng(42).choice(10, size=4, replace=False)
    )
    assert _FakeBERTopic.last_fit["texts"] == [chunks[i].text for i in expected_sample]
    rest = [i for i in range(10) if i not in set(expected_sample)]
    assert _FakeBERTopic.last_transform["texts"] == [chunks[i].text for i in rest]
    params = result.params
    assert params["assignment_mode"] == "fit+transform"
    assert params["fit_sample_size"] == 4
    assert params["total_chunks"] == 10
    assert params["fit_seed"] == 42


def test_seeded_sample_is_deterministic_across_builds(fake_bertopic: None) -> None:
    """Same seed, same corpus -> the same sample reaches the fit twice."""
    texts = [c.text for c in _chunks(12)]
    engine.build_topics(_chunks(12), TopicsSettings(enabled=True), max_embeddings=5)
    first_fit = list(_FakeBERTopic.last_fit["texts"])
    engine.build_topics(_chunks(12), TopicsSettings(enabled=True), max_embeddings=5)
    assert first_fit == _FakeBERTopic.last_fit["texts"]
    assert set(first_fit) <= set(texts)
    assert len(first_fit) == 5


def test_transform_assignments_land_in_input_order(fake_bertopic: None) -> None:
    """doc_topics and assignments align with the input chunk order."""
    chunks = _chunks(10)
    result = engine.build_topics(chunks, TopicsSettings(enabled=True), max_embeddings=4)
    expected_sample = sorted(
        int(i) for i in np.random.default_rng(42).choice(10, size=4, replace=False)
    )
    rest = [i for i in range(10) if i not in set(expected_sample)]
    assert len(result.doc_topics) == 10
    # The transform half is assigned topic 1 by the fake; the sample half
    # follows the fit pattern [0, 1, 0, 1].
    assert [result.doc_topics[i] for i in rest] == [1] * len(rest)
    pattern = [0, 1, 0, 1]
    assert [result.doc_topics[i] for i in expected_sample] == pattern
    assigned_ids = {chunk_id for chunk_id, _t, _p in result.assignments}
    assert {chunks[i].chunk_id for i in rest} <= assigned_ids


# ---------------------------------------------------------------------------
# WP-0: the batched reader (doc ids + date provenance, keyset paging)
# ---------------------------------------------------------------------------


class _ReadConn:
    """Hands out one canned batch per fetch call, then nothing."""

    def __init__(self, batches: list[list[dict[str, Any]]]) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self._batches = batches
        self._served = 0

    async def execute(self, sql: str, *args: Any) -> str:
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append(args)
        if self._served >= len(self._batches):
            return []
        batch = self._batches[self._served]
        self._served += 1
        return batch[: int(args[2])]  # honor the LIMIT parameter, as Postgres does

    def transaction(self) -> _ReadConn:
        return self

    async def __aenter__(self) -> _ReadConn:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _ReadPool:
    def __init__(self, conn: _ReadConn) -> None:
        self._conn = conn

    def acquire(self) -> _ReadPool:
        return self

    async def __aenter__(self) -> _ReadConn:
        return self._conn

    async def __aexit__(self, *args: Any) -> bool:
        return False


def _row(i: int, with_date: bool = True) -> dict[str, Any]:
    date = datetime(2024, 1, i + 1, tzinfo=UTC) if with_date else None
    return {
        "chunk_id": f"00000000-0000-0000-0000-{i:012d}",
        "text": f"text {i}",
        "embedding": f"[{float(i)},1.0]",
        "doc_id": f"doc-{i % 2}",
        "document_date": date,
        "source_timestamp": datetime(2023, 12, 1, tzinfo=UTC),
    }


async def test_reader_maps_doc_id_and_both_dates() -> None:
    conn = _ReadConn([[_row(0), _row(1, with_date=False)]])
    chunks = await engine.read_stored_embeddings(_ReadPool(conn), "tenant-1")
    assert [c.doc_id for c in chunks] == ["doc-0", "doc-1"]
    assert chunks[0].document_date == datetime(2024, 1, 1, tzinfo=UTC)
    assert chunks[1].document_date is None
    assert chunks[1].source_timestamp == datetime(2023, 12, 1, tzinfo=UTC)
    assert chunks[0].vector == [0.0, 1.0]


async def test_reader_pages_with_a_keyset_cursor_and_default_reads_all() -> None:
    """The cursor is the last chunk id of the previous batch; no limit cap."""
    first = [_row(i) for i in range(3)]
    second = [_row(i) for i in range(3, 5)]
    conn = _ReadConn([first, second])
    chunks = await engine.read_stored_embeddings(_ReadPool(conn), "tenant-1")
    assert len(chunks) == 5
    assert conn.calls[0][1] == ""  # the first cursor is the empty string
    assert conn.calls[1][1] == first[-1]["chunk_id"]
    assert conn.calls[2][1] == second[-1]["chunk_id"]  # the trailing empty page


async def test_reader_limit_caps_the_total_across_batches() -> None:
    conn = _ReadConn([[_row(i) for i in range(3)], [_row(i) for i in range(3, 6)]])
    chunks = await engine.read_stored_embeddings(_ReadPool(conn), "tenant-1", limit=4)
    assert len(chunks) == 4
    # The final fetch asks only for the remainder, never a full batch.
    assert conn.calls[-1][2] == 1
