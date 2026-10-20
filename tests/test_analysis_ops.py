"""Analysis program ops: honest outcomes on every stub handler.

The op layer (``tools/labeling_tools.py``, ``tools/classification_tools.py``,
``tools/analysis_tools.py``, and the ``topics_trends`` stub in
``tools/topics_tools.py``) is exercised at the pool/module boundary the
parity harness uses. Wave 0 has no engines, so exactly two outcomes
exist:

* the dormant default (every ``analysis.*.enabled`` false) -> the honest
  no-op envelope naming the flag, with the family's envelope keys zeroed;
* an enabled flag on the skeleton -> ``unavailable`` (the engine is not
  built yet), never a crash or fake data.

WP-1 exception: the date backfill engine is live, so its enabled path
needs a real database. Here it is pinned on the no-migration outcome
(tables absent); the live rule and idempotency tests live in
``tests/test_migration_027.py``.

Mostly no DB is touched: the disabled and unavailable paths run before
any pool use, so a probe pool keeps the calls hermetic.
"""

from __future__ import annotations

from typing import Any

import pytest

from corpus_kb.tools import analysis_tools, classification_tools, labeling_tools, topics_tools

TENANT = "00000000-0000-0000-0000-000000000001"


class _ProbePool:
    """Records any use; the stubs must never touch it on honest paths."""

    def __init__(self) -> None:
        self.touched = False


def _pool() -> _ProbePool:
    return _ProbePool()


OFF = {}  # no analysis block: the dormant default
CLASSIFICATION_ON = {"analysis": {"classification": {"enabled": True}}}
OVER_TIME_ON = {"analysis": {"topics": {"over_time": {"enabled": True}}}}


# ---------------------------------------------------------------------------
# labeling family
# ---------------------------------------------------------------------------


async def test_labeling_build_disabled_is_an_honest_noop() -> None:
    result = await labeling_tools.labeling_build(_pool(), config=OFF, tenant_id=TENANT)
    assert result["status"] == "disabled"
    assert "analysis.labeling.enabled" in result["reason"]
    assert result["label_count"] == 0
    assert result["labels"] == []


# The remaining labeling behavior moved to tests/test_labeling_ops.py when
# the WP-2 engine landed: the build answers the migration-028 probe and the
# LF-file gates there; both reads drop the flag gate (the topics_list rule)
# and answer the tables check with bounded windows.


# ---------------------------------------------------------------------------
# classification family
# ---------------------------------------------------------------------------


async def test_classification_build_disabled_is_an_honest_noop() -> None:
    result = await classification_tools.classification_build(_pool(), config=OFF, tenant_id=TENANT)
    assert result["status"] == "disabled"
    assert "analysis.classification.enabled" in result["reason"]
    assert result["prediction_count"] == 0
    assert result["class_count"] == 0
    assert result["review_count"] == 0
    assert result["params"] == {}


# The remaining classification behavior moved to tests/test_classification_ops.py
# when the WP-3 engine landed: the build answers the migration-029 probe and
# the seed guards; the list drops the flag gate (the topics_list rule) and
# reads the bounded prediction window.


# ---------------------------------------------------------------------------
# analysis family
# ---------------------------------------------------------------------------


async def test_backfill_disabled_names_the_gate() -> None:
    result = await analysis_tools.analysis_backfill_dates(_pool(), config=OFF, tenant_id=TENANT)
    assert result["status"] == "disabled"
    assert "analysis.topics.over_time.enabled" in result["reason"]
    assert result["documents_scanned"] == 0
    assert result["documents_dated"] == 0


class _TablesAbsentPool:
    """Answers the column probe with 'table or column not there'."""

    def acquire(self) -> _TablesAbsentPool:
        return self

    async def __aenter__(self) -> _TablesAbsentConn:
        return _TablesAbsentConn()

    async def __aexit__(self, *args: object) -> bool:
        return False


class _TablesAbsentConn:
    """Every existence probe answers False (migration 027 has not run)."""

    async def fetchval(self, sql: str) -> bool:
        del sql
        return False


async def test_backfill_enabled_without_migration_027_answers_unavailable() -> None:
    """Flag on + column absent = the honest no-migration envelope."""
    result = await analysis_tools.analysis_backfill_dates(
        _TablesAbsentPool(), config=OVER_TIME_ON, tenant_id=TENANT
    )
    assert result["status"] == "unavailable"
    assert "migration 027" in str(result["reason"])
    assert result["documents_scanned"] == 0
    assert result["documents_dated"] == 0


async def test_analyze_run_disabled_with_every_flag_off() -> None:
    result = await analysis_tools.analyze_run(_pool(), config=OFF, tenant_id=TENANT)
    assert result["status"] == "disabled"
    assert result["sections"] == []


# The wave-0 stub retirement (WP-5): the composed run is live, so "one
# flag on" no longer answers ``unavailable``. The composed behavior —
# fixed section order, per-section isolation, the classification probe,
# the deterministic artifact — lives in tests/test_analyze_run.py.


# ---------------------------------------------------------------------------
# topics_trends (the topics-family stub)
# ---------------------------------------------------------------------------


async def test_topics_trends_disabled_is_an_honest_noop() -> None:
    result = await topics_tools.topics_trends(_pool(), config=OFF, tenant_id=TENANT)
    assert result["status"] == "disabled"
    assert "analysis.topics.over_time.enabled" in result["reason"]
    assert result["topic_count"] == 0
    assert result["trends"] == []


async def test_topics_trends_enabled_answers_unavailable_on_the_skeleton() -> None:
    class _AbsentStorage:
        """The 026 tables and the 027 date column both absent."""

        async def fetchval(self, sql: str, *args: Any) -> None:
            return None

        def acquire(self) -> _AbsentStorage:
            return self

        async def __aenter__(self) -> _AbsentStorage:
            return self

        async def __aexit__(self, *args: Any) -> bool:
            return False

    # The trends engine is live now (WP-4), so the enabled path probes
    # storage first (the topics_build convention); an absent schema is
    # still the honest unavailable envelope, never a crash.
    result = await topics_tools.topics_trends(
        _AbsentStorage(), config=OVER_TIME_ON, tenant_id=TENANT, bins=12
    )
    assert result["status"] == "unavailable"


@pytest.mark.parametrize(
    ("tool", "keys"),
    [
        (labeling_tools.labeling_build, {"status", "reason", "remedy", "label_count", "labels"}),
        (
            classification_tools.classification_build,
            {
                "status",
                "reason",
                "remedy",
                "prediction_count",
                "class_count",
                "review_count",
                "params",
            },
        ),
        (
            analysis_tools.analysis_backfill_dates,
            {"status", "reason", "remedy", "documents_scanned", "documents_dated"},
        ),
        (analysis_tools.analyze_run, {"status", "reason", "remedy", "sections"}),
        (topics_tools.topics_trends, {"status", "reason", "remedy", "topic_count", "trends"}),
    ],
    ids=[
        "labeling_build",
        "classification_build",
        "analysis_backfill_dates",
        "analyze_run",
        "topics_trends",
    ],
)
async def test_disabled_envelopes_carry_exactly_the_family_keys(tool: Any, keys: set[str]) -> None:
    result = await tool(_pool(), config=OFF, tenant_id=TENANT)
    assert set(result) == keys
