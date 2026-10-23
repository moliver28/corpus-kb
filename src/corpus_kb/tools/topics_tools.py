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
  * :func:`topics_trends` — per-topic frequency series over binned
    document dates (WP-4): one fit through the engine path above, then
    one BERTopic ``topics_over_time`` call; undated chunks follow the
    configured drop/report policy.

Envelope: ``{status, topic_count, topics}`` on every outcome, so the
parity gate holds on the disabled path too (the parity harness runs the
default offline config, where the feature is dormant).
"""

from __future__ import annotations

import asyncpg

from ..analysis.settings import load_analysis_settings
from ..topics import engine, writer
from ..topics import reduction as reduction_mod
from ..topics import trends as trends_mod
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
    """The honest no-op envelope for ``topics.enabled: false``.

    The build contract keys ride every outcome: nothing is unassigned
    because nothing ran, and there are no guided/zero-shot notes.
    """
    return {
        "status": "disabled",
        "reason": "topics.enabled is false in the loaded config.",
        "remedy": "Set topics.enabled: true (and install the [topics] extra), then retry.",
        "topic_count": 0,
        "topics": [],
        "unassigned_count": 0,
        "notes": [],
    }


def _unavailable() -> dict[str, object]:
    """The honest pre-migration envelope (the ingest_status convention)."""
    return {
        "status": "unavailable",
        "reason": "topics tables absent (migration 026 not applied)",
        "remedy": "run corpus-kb setup",
        "topic_count": 0,
        "topics": [],
        "unassigned_count": 0,
        "notes": [],
    }


def _insufficient(found: int, needed: int) -> dict[str, object]:
    """The honest empty-corpus envelope (below min_topic_size)."""
    return {
        "status": "insufficient_data",
        "reason": f"{found} embedded chunks is below the minimum topic size ({needed}).",
        "remedy": "Ingest more documents, or lower topics.min_topic_size.",
        "topic_count": 0,
        "topics": [],
        "unassigned_count": 0,
        "notes": [],
    }


async def topics_build(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    seed_terms: list[str] | None = None,
    topic_descriptions: list[str] | None = None,
) -> dict[str, object]:
    """Cluster stored embeddings into topics; write the build (one replace).

    Returns ``{status, topic_count, topics}`` plus, on the success path,
    the counts, the recorded model params, the reduction outcome, the
    ``unassigned_count`` (docs assigned topic -1) and the guided/
    zero-shot notes. ``seed_terms`` guides the representation (it never
    forces discovery); ``topic_descriptions`` arms zero-shot matching
    against ``topics.zeroshot_min_similarity``. Empty lists count as
    absent, and absent params leave the build byte-identical to the
    pre-AMR path.
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

    guided = [str(term) for term in seed_terms] if seed_terms else []
    zeroshot = [str(text) for text in topic_descriptions] if topic_descriptions else []
    if guided or zeroshot:
        engine_result = engine.build_topics(
            chunks, settings, seed_terms=guided or None, topic_descriptions=zeroshot or None
        )
    else:
        # The exact pre-AMR call: absent params change nothing downstream.
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
        "unassigned_count": _unassigned_count(engine_result),
        "reduction": reduction.status if reduction is not None else "off",
        "params": engine_result.params,
        "notes": _guided_notes(guided, zeroshot),
    }


def _unassigned_count(engine_result: engine.EngineResult) -> int:
    """Docs assigned topic -1 across the fit sample and the transform half."""
    return sum(1 for topic in engine_result.doc_topics if int(topic) == engine.OUTLIER_TOPIC)


def _guided_notes(guided: list[str], zeroshot: list[str]) -> list[str]:
    """The honest guided/zero-shot notes; empty when neither arm is on."""
    notes: list[str] = []
    if guided:
        notes.append(engine.GUIDED_NOTE)
    if zeroshot:
        notes.append(engine.ZEROSHOT_NOTE)
    return notes


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
    """The honest pre-migration envelope: the flag is on, storage is not."""
    return {
        "status": "unavailable",
        "reason": ("topics tables or documents.document_date absent (migration 027 not applied)"),
        "remedy": "run corpus-kb setup",
        "topic_count": 0,
        "trends": [],
    }


def _trends_insufficient(dated: int, undated: int, needed: int) -> dict[str, object]:
    """The honest empty-corpus envelope: too few dated chunks to fit."""
    return {
        "status": "insufficient_data",
        "reason": (
            f"{dated} dated chunks is below the minimum topic size ({needed}); "
            f"{undated} undated chunks excluded."
        ),
        "remedy": "Ingest more dated documents, or lower topics.min_topic_size.",
        "topic_count": 0,
        "trends": [],
    }


def _trends_failed(reason: str) -> dict[str, object]:
    """The honest failed envelope for the guarded upstream TOT call (F9)."""
    return {
        "status": "failed",
        "reason": (f"topics_over_time raised {reason}. A fresh fit carries no deleted topics."),
        "remedy": "rebuild topics first (topics-build), then retry",
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

    One fit through the same engine path as ``topics_build`` (never
    re-embeds, never re-fits per bin), then ONE ``topics_over_time``
    call over the dated chunks. Undated chunks follow the configured
    policy (``drop`` by default; ``report`` lists up to 20 chunk ids).
    Returns ``{status, topic_count, trends}`` plus, on the success path,
    the bins, the dated/undated coverage and the recorded params.
    """
    cfg = load_config_or_pass(config)
    analysis = load_analysis_settings(cfg)
    if not analysis.over_time.enabled:
        return _trends_disabled()
    if not await trends_mod.storage_ready(pg_pool):
        return _trends_unavailable()
    topics_settings = load_topics_settings(cfg)
    floor = max(MIN_BUILD_DOCUMENTS, topics_settings.resolved_min_topic_size())
    chunks = await engine.read_stored_embeddings(pg_pool, tenant_id)
    dated, undated = trends_mod.split_dated(chunks)
    if len(dated) < floor:
        return _trends_insufficient(len(dated), len(undated), floor)
    engine_result, model = engine.build_topics_with_model(
        [chunk for chunk, _date in dated],
        topics_settings,
        max_embeddings=analysis.topics_max_embeddings,
    )
    outcome = trends_mod.collect_trends(model, engine_result, dated, analysis.over_time, bins)
    if outcome.failure is not None:
        return _trends_failed(outcome.failure)
    params: dict[str, object] = {
        **engine_result.params,
        "bins": outcome.bins,
        "evolution_tuning": False,
        "undated_policy": analysis.over_time.undated,
    }
    if outcome.clamped_from is not None:
        params["bins_clamped_from"] = outcome.clamped_from
    envelope: dict[str, object] = {
        "status": "success",
        "topic_count": len(outcome.trends),
        "trends": outcome.trends,
        "dated_count": len(dated),
        "undated_count": len(undated),
        "bins": outcome.bins,
        "params": params,
    }
    if analysis.over_time.undated == "report" and undated:
        envelope["undated_chunk_ids"] = [
            chunk.chunk_id for chunk in undated[: trends_mod.MAX_LISTED_UNDATED]
        ]
    return envelope
