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

from .ingest_common import DEFAULT_TENANT_ID, load_config_or_pass
from ..topics import engine, reduction as reduction_mod
from ..topics import writer
from ..topics.settings import load_topics_settings

__all__ = ["topics_build", "topics_list"]

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
    topic_ids = written.get("topic_ids", {})
    return {
        "status": "success",
        "topic_count": len(rows),
        "topics": [
            {
                "topic_id": topic_ids.get(row.topic_index),
                "label": row.label,
                "reduced_label": row.reduced_label,
                "size": row.size,
                "keywords": row.keywords,
            }
            for row in rows
        ],
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
