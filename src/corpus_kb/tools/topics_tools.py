"""MCP/HTTP/socket/CLI handlers for the topics ops (Wave 4).

One handler per operation, shared by every surface — the
``ingest_surface_tools`` pattern:

  * :func:`topics_build` — the offline BERTopic run over STORED chunk
    embeddings (never re-embeds), optional LLM reduction, wholesale
    write. Honest no-op when the feature is disabled; ``unavailable``
    when migration 026 has not run; typed ``TopicDependencyError`` when
    the ``[topics]`` extra is missing (surfaces map it: HTTP 503,
    socket -32005, CLI exit 3).
  * :func:`topics_list` — the tenant's built topics, largest first.

Envelope: ``{status, topic_count, topics}`` on every outcome, so the
parity gate holds on the disabled path too (the parity harness runs the
default offline config, where the feature is dormant).
"""

from __future__ import annotations

import asyncpg

from ..analysis.settings import load_analysis_settings
from ..topics import engine, writer
from ..topics import reduction as reduction_mod
from ..topics.settings import load_topics_settings
from .ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = ["topics_build", "topics_list", "topics_trends"]

# Read/list bounds (the ingest_status convention: bounded, honest windows).
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 500

# BERTopic cannot fit on a single document; below this the build is an
# honest empty result, never a crash.
MIN_BUILD_DOCUMENTS = 2


def _disabled() -> dict[str, object]:
    """The honest no-op envelope for ``topics.enabled: false``."""
    return {
        "status": "disabled",
        "reason": "topics.enabled is false in the loaded config.",
        "remedy": "Set topics.enabled: true (and install the [topics] extra), then retry.",
        "topic_count": 0,
        "topics": [],
    }


def _unavailable() -> dict[str, object]:
    """The honest pre-migration envelope (the ingest_status convention)."""
    return {
        "status": "unavailable",
        "reason": "topics tables absent (migration 026 not applied)",
        "remedy": "run corpus-kb setup",
        "topic_count": 0,
        "topics": [],
    }


def _insufficient(found: int, needed: int) -> dict[str, object]:
    """The honest empty-corpus envelope (below min_topic_size)."""
    return {
        "status": "insufficient_data",
        "reason": f"{found} embedded chunks is below the minimum topic size ({needed}).",
        "remedy": "Ingest more documents, or lower topics.min_topic_size.",
        "topic_count": 0,
        "topics": [],
    }


async def topics_build(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, object]:
    """Cluster stored embeddings into topics; write the build (one replace).

    Returns ``{status, topic_count, topics}`` plus, on the success path,
    the counts, the recorded model params and the reduction outcome.
    """
    settings = load_topics_settings(load_config_or_pass(config))
    if not settings.enabled:
        return _disabled()
    if not await writer.topics_tables_present(pg_pool):
        return _unavailable()
    chunks = await engine.read_stored_embeddings(pg_pool, tenant_id)
    floor = max(MIN_BUILD_DOCUMENTS, settings.resolved_min_topic_size())
    if len(chunks) < floor:
        return _insufficient(len(chunks), floor)

    engine_result = engine.build_topics(chunks, settings)
    reduction: reduction_mod.ReductionResult | None = None
    if settings.llm_reduction:
        reducer = reduction_mod.TopicReducer(settings)
        reduction = reducer.reduce(engine_result.topics)
        engine_result = reduction_mod.apply_reduction(engine_result, reduction)

    rows = _topic_rows(engine_result, reduction)
    written = await writer.write_build(pg_pool, tenant_id, rows, engine_result.assignments)
    raw_ids = written.get("topic_ids", {})
    topic_ids: dict[int, str] = raw_ids if isinstance(raw_ids, dict) else {}
    topics_out: list[dict[str, object]] = [
        {
            "topic_id": topic_ids.get(row.topic_index),
            "label": row.label,
            "reduced_label": row.reduced_label,
            "size": row.size,
            "keywords": row.keywords,
        }
        for row in rows
    ]
    return {
        "status": "success",
        "topic_count": len(rows),
        "topics": topics_out,
        "chunk_count": len(engine_result.assignments),
        "outlier_count": engine_result.outlier_count,
        "reduction": reduction.status if reduction is not None else "off",
        "params": engine_result.params,
    }


def _topic_rows(
    engine_result: engine.EngineResult,
    reduction: reduction_mod.ReductionResult | None,
) -> list[writer.TopicRow]:
    """Engine topics -> storage rows (reduced labels applied when present)."""
    labels = reduction.labels if reduction is not None else {}
    return [
        writer.TopicRow(
            topic_index=topic.topic_index,
            label=topic.label,
            reduced_label=labels.get(topic.topic_index),
            size=topic.size,
            keywords=topic.keywords,
            params=engine_result.params,
        )
        for topic in engine_result.topics
    ]


async def topics_list(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict[str, object]:
    """The tenant's built topics, largest first. Reads are never gated:
    inspecting an existing build while the feature flag is off is safe."""
    del config  # accepted for MCP-tool signature symmetry; reads are config-free
    bounded = max(1, min(int(limit), MAX_LIST_LIMIT))
    if not await writer.topics_tables_present(pg_pool):
        return _unavailable()
    rows = await writer.read_topics(pg_pool, tenant_id, bounded)
    return {"status": "success", "topic_count": len(rows), "topics": rows}


# ---------------------------------------------------------------------------
# topics_trends (WP-4, analysis program Wave 0)
# ---------------------------------------------------------------------------


def _trends_disabled() -> dict[str, object]:
    """The honest no-op envelope for ``analysis.topics.over_time.enabled:
    false`` (the documented dormant default)."""
    return {
        "status": "disabled",
        "reason": "analysis.topics.over_time.enabled is false in the loaded config.",
        "remedy": "Set analysis.topics.over_time.enabled: true, then retry.",
        "topic_count": 0,
        "trends": [],
    }


def _trends_unavailable() -> dict[str, object]:
    """The honest wave-0 envelope: the flag is on, the engine is not built."""
    return {
        "status": "unavailable",
        "reason": (
            "the topics-over-time engine is not built yet "
            "(the analysis program ships the registry first)"
        ),
        "remedy": "keep analysis.topics.over_time.enabled: false until the engine lands",
        "topic_count": 0,
        "trends": [],
    }


async def topics_trends(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    bins: int | None = None,
) -> dict[str, object]:
    """Per-topic frequency series over binned document dates (WP-4).

    Reuses the existing topics fit (never re-embeds, never re-fits per
    bin). Returns ``{status, topic_count, trends}`` plus, on the success
    path, the bins and the dated/undated coverage. Wave 0: disabled or
    unavailable, never a guess.
    """
    del pg_pool, tenant_id, bins  # the engine (WP-4) consumes the pool, tenant, bins
    settings = load_analysis_settings(load_config_or_pass(config))
    if not settings.over_time.enabled:
        return _trends_disabled()
    return _trends_unavailable()
