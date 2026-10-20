"""WP-4 trends: the series math, the upstream guard, and the op gates.

Offline throughout: the model seam is the engine's lazy import path —
the handler tests monkeypatch ``engine.build_topics_with_model`` with a
fake fitted model whose ``topics_over_time`` mirrors the real DataFrame
contract (records of Topic/Name/Frequency/Timestamp). The fakes prove:

* z-score flags: EMERGING at z >= 2 in the latest bin, DECLINING at
  z <= -2, none on a flat series;
* bins: ``auto``/explicit resolution, the < 50 clamp, the recorded
  clamp, ``evolution_tuning=False`` reaching the model;
* the undated policy: drop excludes and counts, report lists up to 20
  ids; both keep undated chunks out of the one-to-one TOT call;
* the F9 guard: an upstream crash becomes the honest ``failed``
  envelope naming the exception class, never a raised op;
* deterministic row order: descending total, ties by topic index;
* the handler gates: disabled / unavailable (tables or date column) /
  insufficient, mirroring ``test_topics_ops.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from corpus_kb.analysis.settings import OverTimeSettings
from corpus_kb.tools import topics_tools
from corpus_kb.topics import trends
from corpus_kb.topics.engine import EngineResult, StoredChunk, TopicBuild

TENANT = "00000000-0000-0000-0000-000000000001"

ENABLED = {
    "topics": {"enabled": True, "llm_reduction": False, "min_topic_size": 2},
    "analysis": {"topics": {"over_time": {"enabled": True, "bins": 5, "undated": "drop"}}},
}
REPORT = {
    "topics": {"enabled": True, "llm_reduction": False, "min_topic_size": 2},
    "analysis": {"topics": {"over_time": {"enabled": True, "bins": 5, "undated": "report"}}},
}


# ---------------------------------------------------------------------------
# The pure series math
# ---------------------------------------------------------------------------


def test_z_flags_emerging_when_latest_bin_clears_two() -> None:
    # Six bins: the max z is sqrt(5) ~ 2.24, safely past the threshold.
    assert trends.z_flags([0, 0, 0, 0, 0, 10]) == (True, False)


def test_z_flags_declining_when_latest_bin_drops_below_minus_two() -> None:
    assert trends.z_flags([10, 10, 10, 10, 10, 0]) == (False, True)


def test_z_flags_none_for_flat_or_mild_series() -> None:
    assert trends.z_flags([4, 4, 4, 4]) == (False, False)  # std 0: no flag
    assert trends.z_flags([0, 0, 0, 8]) == (False, False)  # z = +1.73 (the n=4 max)
    assert trends.z_flags([1, 2, 1, 2, 1, 2]) == (False, False)  # z = +1.0
    assert trends.z_flags([]) == (False, False)


def test_resolve_bins_passes_explicit_and_auto_through() -> None:
    settings = OverTimeSettings(enabled=True, bins="auto", undated="drop")
    assert trends.resolve_bins(None, settings) == (10, None)  # the auto default
    assert trends.resolve_bins(7, settings) == (7, None)


def test_resolve_bins_clamps_the_upstream_advice_at_49() -> None:
    settings = OverTimeSettings(enabled=True, bins=60, undated="drop")
    assert trends.resolve_bins(None, settings) == (49, 60)
    assert trends.resolve_bins(120, settings) == (49, 120)


def test_split_dated_prefers_document_date_and_falls_back() -> None:
    chunks = [
        StoredChunk("c0", "t", [0.0], doc_id="d0", document_date=datetime(2024, 5, 1)),
        StoredChunk(
            "c1", "t", [0.0], doc_id="d1", source_timestamp=datetime(2023, 1, 1)
        ),  # the fallback path
        StoredChunk("c2", "t", [0.0], doc_id="d2"),  # undated
    ]
    dated, undated = trends.split_dated(chunks)
    assert [chunk.chunk_id for chunk, _date in dated] == ["c0", "c1"]
    assert [date for _chunk, date in dated] == [datetime(2024, 5, 1), datetime(2023, 1, 1)]
    assert [chunk.chunk_id for chunk in undated] == ["c2"]


# ---------------------------------------------------------------------------
# collect_trends through the fake model (the engine seam)
# ---------------------------------------------------------------------------


class _FakeTotFrame:
    """The one DataFrame method trends reads (records of the TOT df)."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def to_dict(self, orient: str) -> list[dict[str, Any]]:
        assert orient == "records"
        return [dict(row) for row in self._rows]


class _FakeFittedModel:
    """Mirrors the fitted BERTopic surface the trends build touches."""

    last_kwargs: dict[str, Any] | None = None

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def topics_over_time(
        self, docs: list[str], timestamps: Any, *, topics: Any, nr_bins: int, evolution_tuning: bool
    ) -> _FakeTotFrame:
        type(self).last_kwargs = {
            "docs": len(docs),
            "timestamps": list(timestamps),
            "topics": list(topics),
            "nr_bins": nr_bins,
            "evolution_tuning": evolution_tuning,
        }
        return _FakeTotFrame(self._rows)


def _edge(month: int) -> datetime:
    return datetime(2024, month, 1, tzinfo=UTC)


def _fit(topics: list[TopicBuild], n_docs: int = 6) -> EngineResult:
    return EngineResult(
        topics=topics,
        assignments=[],
        params={"documents": n_docs, "fit_seed": 42, "assignment_mode": "fit"},
        outlier_count=1,
        doc_topics=[0] * n_docs,
    )


def test_collect_records_the_upstream_guard_and_bins() -> None:
    model = _FakeFittedModel()
    dated = [(StoredChunk(f"c{i}", "t", [0.0]), _edge(1)) for i in range(3)]
    outcome = trends.collect_trends(
        model,
        _fit([TopicBuild(0, "0_a", 3)], n_docs=3),
        dated,
        OverTimeSettings(),
        bins=None,
    )
    assert outcome.failure is None
    assert _FakeFittedModel.last_kwargs is not None
    assert _FakeFittedModel.last_kwargs["nr_bins"] == 10  # the auto default
    assert _FakeFittedModel.last_kwargs["evolution_tuning"] is False
    assert _FakeFittedModel.last_kwargs["docs"] == 3
    # The per-document topics ride along: they cover every dated chunk,
    # while the model's own topics_ covers only the fit sample (WP-0).
    assert _FakeFittedModel.last_kwargs["topics"] == [0, 0, 0]
    assert outcome.bins == 10
    assert outcome.clamped_from is None


def test_collect_series_zero_fills_missing_bins_and_orders_rows() -> None:
    rows = [
        # Topic 1: the largest total (50) and a DECLINING latest bin.
        {"Topic": 1, "Name": "1_b", "Frequency": 10, "Timestamp": _edge(m)}
        for m in (1, 2, 3, 4, 5)
    ] + [
        # Topic 0 appears late: [0, 0, 0, 0, 0, 10], total 10, EMERGING.
        {"Topic": 0, "Name": "0_a", "Frequency": 10, "Timestamp": _edge(6)},
        # Topic 2 ties topic 0 on total (10) but appears in the same bin.
        {"Topic": 2, "Name": "2_c", "Frequency": 10, "Timestamp": _edge(6)},
        # The outlier row never becomes a trend row.
        {"Topic": -1, "Name": "-1_o", "Frequency": 5, "Timestamp": _edge(1)},
    ]
    model = _FakeFittedModel(rows)
    topics = [
        TopicBuild(0, "0_a", 10),
        TopicBuild(1, "1_b", 50),
        TopicBuild(2, "2_c", 10),
    ]
    dated = [(StoredChunk(f"c{i}", "t", [0.0]), _edge(1 + i % 6)) for i in range(6)]
    outcome = trends.collect_trends(model, _fit(topics), dated, OverTimeSettings(), bins=6)
    assert outcome.failure is None
    order = [row["topic_index"] for row in outcome.trends]
    assert order == [1, 0, 2]  # descending total; the tie breaks by topic index
    first = outcome.trends[1]
    assert first["emerging"] is True
    assert first["declining"] is False
    assert [point["count"] for point in first["series"]] == [0, 0, 0, 0, 0, 10]
    assert first["series"][0]["bin_start"] == _edge(1).isoformat()
    second = outcome.trends[0]
    assert second["declining"] is True
    assert [point["count"] for point in second["series"]] == [10, 10, 10, 10, 10, 0]
    third = outcome.trends[2]
    assert [point["count"] for point in third["series"]] == [0, 0, 0, 0, 0, 10]
    assert (third["emerging"], third["declining"]) == (True, False)


def test_collect_failure_names_the_exception_class() -> None:
    class _CrashingModel(_FakeFittedModel):
        def topics_over_time(
            self,
            docs: list[str],
            timestamps: Any,
            *,
            topics: Any,
            nr_bins: int,
            evolution_tuning: bool,
        ) -> _FakeTotFrame:
            raise ValueError("bins must be unique")

    model = _CrashingModel()
    dated = [(StoredChunk("c0", "t", [0.0]), _edge(1))]
    outcome = trends.collect_trends(
        model, _fit([TopicBuild(0, "0_a", 1)], n_docs=1), dated, OverTimeSettings(), bins=5
    )
    assert outcome.failure is not None
    assert outcome.failure.startswith("ValueError")
    assert outcome.trends == []


# ---------------------------------------------------------------------------
# The op gates (mirroring test_topics_ops.py)
# ---------------------------------------------------------------------------


class _TrendsConn:
    """Probes the tables and the date column; reads return nothing."""

    def __init__(self, tables: bool, date_column: bool) -> None:
        self._tables = tables
        self._date_column = date_column

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "to_regclass" in sql:
            return "public.topics" if self._tables else None
        if "information_schema" in sql:
            return self._date_column
        return None

    async def execute(self, sql: str, *args: Any) -> str:
        return "OK"

    def transaction(self) -> _TrendsConn:
        return self

    async def __aenter__(self) -> _TrendsConn:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _TrendsPool:
    def __init__(self, tables: bool = True, date_column: bool = True) -> None:
        self._conn = _TrendsConn(tables, date_column)

    def acquire(self) -> _TrendsPool:
        return self

    async def __aenter__(self) -> _TrendsConn:
        return self._conn

    async def __aexit__(self, *args: Any) -> bool:
        return False


def _chunks(n_dated: int, n_undated: int) -> list[StoredChunk]:
    dated = [
        StoredChunk(
            chunk_id=f"c{i}",
            text=f"text {i}",
            vector=[0.1, 1.0],
            doc_id=f"d{i}",
            document_date=_edge(1 + i % 5),
        )
        for i in range(n_dated)
    ]
    undated = [
        StoredChunk(chunk_id=f"u{j}", text=f"undated {j}", vector=[0.1, 1.0], doc_id=f"d{j}")
        for j in range(n_undated)
    ]
    return dated + undated


def _patch_engine(
    monkeypatch: pytest.MonkeyPatch,
    chunks: list[StoredChunk],
    topics: list[TopicBuild],
    model: Any,
) -> None:
    async def fake_read(
        pg_pool: Any, tenant_id: str, limit: int | None = None
    ) -> list[StoredChunk]:
        return chunks

    def fake_build(
        chunk_list: list[StoredChunk], settings: Any, max_embeddings: int | None = None
    ) -> tuple[EngineResult, Any]:
        return _fit(topics), model

    monkeypatch.setattr(topics_tools.engine, "read_stored_embeddings", fake_read)
    monkeypatch.setattr(topics_tools.engine, "build_topics_with_model", fake_build)


async def test_trends_disabled_is_an_honest_noop() -> None:
    result = await topics_tools.topics_trends(_TrendsPool(), config=None, tenant_id=TENANT)
    assert result["status"] == "disabled"
    assert result["topic_count"] == 0
    assert result["trends"] == []
    assert "analysis.topics.over_time.enabled" in str(result["reason"])


async def test_trends_without_tables_is_unavailable() -> None:
    result = await topics_tools.topics_trends(
        _TrendsPool(tables=False), config=ENABLED, tenant_id=TENANT
    )
    assert result["status"] == "unavailable"
    assert "migration 027" in str(result["reason"])
    assert result["remedy"] == "run corpus-kb setup"


async def test_trends_without_the_date_column_is_unavailable() -> None:
    result = await topics_tools.topics_trends(
        _TrendsPool(date_column=False), config=ENABLED, tenant_id=TENANT
    )
    assert result["status"] == "unavailable"
    assert "migration 027" in str(result["reason"])


async def test_trends_below_the_dated_floor_is_insufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_engine(monkeypatch, _chunks(1, 5), [TopicBuild(0, "0_a", 1)], _FakeFittedModel())
    result = await topics_tools.topics_trends(_TrendsPool(), config=ENABLED, tenant_id=TENANT)
    assert result["status"] == "insufficient_data"
    assert "1 dated chunks is below the minimum topic size (2)" in str(result["reason"])
    assert "5 undated chunks excluded" in str(result["reason"])


async def test_trends_success_drop_policy_counts_the_undated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _FakeFittedModel([{"Topic": 0, "Name": "0_a", "Frequency": 6, "Timestamp": _edge(1)}])
    _patch_engine(monkeypatch, _chunks(6, 3), [TopicBuild(0, "0_a", 6)], model)
    result = await topics_tools.topics_trends(_TrendsPool(), config=ENABLED, tenant_id=TENANT)
    assert result["status"] == "success"
    assert result["topic_count"] == 1
    assert result["dated_count"] == 6
    assert result["undated_count"] == 3
    assert result["bins"] == 5
    assert result["params"]["evolution_tuning"] is False
    assert result["params"]["undated_policy"] == "drop"
    assert "undated_chunk_ids" not in result
    # The undated chunks never reach the one-to-one TOT call.
    assert _FakeFittedModel.last_kwargs is not None
    assert _FakeFittedModel.last_kwargs["docs"] == 6


async def test_trends_success_report_policy_lists_up_to_twenty_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _FakeFittedModel([{"Topic": 0, "Name": "0_a", "Frequency": 6, "Timestamp": _edge(1)}])
    _patch_engine(monkeypatch, _chunks(6, 25), [TopicBuild(0, "0_a", 6)], model)
    result = await topics_tools.topics_trends(_TrendsPool(), config=REPORT, tenant_id=TENANT)
    assert result["status"] == "success"
    listed = result["undated_chunk_ids"]
    assert isinstance(listed, list)
    assert len(listed) == trends.MAX_LISTED_UNDATED
    assert listed[0] == "u0"
    assert result["params"]["undated_policy"] == "report"


async def test_trends_bins_clamp_is_recorded_in_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _FakeFittedModel([{"Topic": 0, "Name": "0_a", "Frequency": 6, "Timestamp": _edge(1)}])
    _patch_engine(monkeypatch, _chunks(6, 0), [TopicBuild(0, "0_a", 6)], model)
    clamped = {
        "topics": {"enabled": True, "llm_reduction": False, "min_topic_size": 2},
        "analysis": {"topics": {"over_time": {"enabled": True, "bins": 60}}},
    }
    result = await topics_tools.topics_trends(
        _TrendsPool(), config=clamped, tenant_id=TENANT, bins=None
    )
    assert result["status"] == "success"
    assert result["bins"] == trends.CLAMPED_TOT_BINS
    assert result["params"]["bins_clamped_from"] == 60
    assert _FakeFittedModel.last_kwargs is not None
    assert _FakeFittedModel.last_kwargs["nr_bins"] == trends.CLAMPED_TOT_BINS


async def test_trends_failed_envelope_on_an_upstream_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Crashing(_FakeFittedModel):
        def topics_over_time(
            self,
            docs: list[str],
            timestamps: Any,
            *,
            topics: Any,
            nr_bins: int,
            evolution_tuning: bool,
        ) -> _FakeTotFrame:
            raise KeyError("c_tf_idf_")

    _patch_engine(monkeypatch, _chunks(6, 0), [TopicBuild(0, "0_a", 6)], _Crashing())
    result = await topics_tools.topics_trends(_TrendsPool(), config=ENABLED, tenant_id=TENANT)
    assert result["status"] == "failed"
    assert "KeyError" in str(result["reason"])
    assert result["remedy"] == "rebuild topics first (topics-build), then retry"
    assert result["topic_count"] == 0
    assert result["trends"] == []
