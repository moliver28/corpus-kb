"""Temporal topic-trends helpers (WP-4, the topics-over-time build).

Pure pieces of the ``topics_trends`` operation; the handler in
``tools/topics_tools.py`` owns the gates and the envelopes, this module
owns the date policy and the series math:

- DATE POLICY: a chunk's effective date is ``documents.document_date``
  (migration 027) when present, else the chunk's ``source_timestamp``.
  Undated chunks never reach BERTopic (upstream has no missing-date
  handling, F8): the ``drop`` policy excludes them (counted in the
  envelope), ``report`` additionally lists up to ``MAX_LISTED_UNDATED``
  chunk ids. Both policies pass dated chunks only, so the call keeps
  its one-to-one docs-to-timestamps contract.
- BINS: one ``topics_over_time`` call per build (never a re-fit per
  bin) with ``evolution_tuning=False`` (F9: the open upstream bug
  #2370). The upstream advice is to keep bins below 50, so a higher
  setting is clamped to 49 and the clamp is recorded. Bin edges are
  pandas equal-width cuts over the same dated timestamps: same corpus
  in, same bins out.
- FLAGS: per-topic z-score over the binned frequency series. A z of +2
  or more in the latest bin is EMERGING; -2 or less is DECLINING. A
  flat series (std 0) gets no flag.
- F9 GUARD: the call is wrapped upstream (BERTopic issues #2370 and
  #2530, the deleted-topics crash our wholesale-replace builds are
  exposed to). A fresh fit carries no deleted topics, but a failure
  still names the exception class and the rebuild remedy instead of
  crashing the op.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import cast

import asyncpg

from corpus_kb.analysis.settings import OverTimeSettings

from .engine import (
    OUTLIER_TOPIC,
    BERTopicModel,
    EngineResult,
    StoredChunk,
    TopicBuild,
    TopicInfoFrame,
)

# Upstream advice: keep the bin count below 50 (the TOT guide).
MAX_TOT_BINS = 50
CLAMPED_TOT_BINS = 49

# The most undated chunk ids listed on the report path.
MAX_LISTED_UNDATED = 20

# The z-score thresholds (the emergence/decline rule).
EMERGING_Z = 2.0
DECLINING_Z = -2.0

__all__ = [
    "CLAMPED_TOT_BINS",
    "MAX_LISTED_UNDATED",
    "MAX_TOT_BINS",
    "TrendsBuild",
    "collect_trends",
    "resolve_bins",
    "split_dated",
    "storage_ready",
    "z_flags",
]


async def storage_ready(pg_pool: asyncpg.Pool) -> bool:
    """True when the topics tables (026) AND the date column (027) exist."""
    async with pg_pool.acquire() as conn:
        tables = await conn.fetchval(
            "SELECT to_regclass('public.topics') IS NOT NULL"
            " AND to_regclass('public.chunk_topics') IS NOT NULL"
        )
        if not tables:
            return False
        column = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'documents'
                  AND column_name = 'document_date'
            )
            """
        )
    return bool(column)


def effective_date(chunk: StoredChunk) -> datetime | None:
    """The chunk's best date: ``document_date``, else ``source_timestamp``."""
    if chunk.document_date is not None:
        return chunk.document_date
    return chunk.source_timestamp


def split_dated(
    chunks: list[StoredChunk],
) -> tuple[list[tuple[StoredChunk, datetime]], list[StoredChunk]]:
    """(chunk, date) pairs for the dated chunks; undated chunks apart.

    Both halves keep the read order, so the build stays deterministic.
    """
    dated: list[tuple[StoredChunk, datetime]] = []
    undated: list[StoredChunk] = []
    for chunk in chunks:
        date = effective_date(chunk)
        if date is None:
            undated.append(chunk)
        else:
            dated.append((chunk, date))
    return dated, undated


def resolve_bins(bins: int | None, settings: OverTimeSettings) -> tuple[int, int | None]:
    """The bin count for the call, plus the clamped-away value if any."""
    resolved = bins if bins is not None else settings.resolved_bins()
    if resolved >= MAX_TOT_BINS:
        return CLAMPED_TOT_BINS, resolved
    return resolved, None


def z_flags(counts: list[int]) -> tuple[bool, bool]:
    """(emerging, declining) from the latest bin's z-score; std 0: none."""
    n = len(counts)
    if n == 0:
        return False, False
    mean = sum(counts) / n
    std = math.sqrt(sum((count - mean) ** 2 for count in counts) / n)
    if std == 0.0:
        return False, False
    z = (counts[-1] - mean) / std
    return z >= EMERGING_Z, z <= DECLINING_Z


@dataclass(frozen=True)
class TrendsBuild:
    """The computed series plus the bin record (or the failure text)."""

    trends: list[dict[str, object]]
    bins: int
    clamped_from: int | None
    failure: str | None


def collect_trends(
    model: BERTopicModel,
    engine_result: EngineResult,
    dated: list[tuple[StoredChunk, datetime]],
    settings: OverTimeSettings,
    bins: int | None,
) -> TrendsBuild:
    """One ``topics_over_time`` call over the fit, then the series rows.

    The per-document topics (``EngineResult.doc_topics``) go in
    explicitly: they cover EVERY dated chunk, while the model's own
    ``topics_`` covers only the fit sample (WP-0).
    """
    resolved, clamped_from = resolve_bins(bins, settings)
    docs = [chunk.text for chunk, _date in dated]
    timestamps: list[object] = [date for _chunk, date in dated]
    try:
        frame = model.topics_over_time(
            docs,
            timestamps,
            topics=engine_result.doc_topics,
            nr_bins=resolved,
            evolution_tuning=False,
        )
    except Exception as exc:  # the guarded upstream call (F9; see module doc)
        return TrendsBuild([], resolved, clamped_from, f"{type(exc).__name__}: {exc}")
    return TrendsBuild(_series_rows(frame, engine_result.topics), resolved, clamped_from, None)


def _series_rows(frame: TopicInfoFrame, topics: list[TopicBuild]) -> list[dict[str, object]]:
    """Per-topic frequency series over the bins, flags attached.

    A topic absent from a bin counts 0 there. Rows sort by descending
    total then topic index (deterministic); the outlier topic never
    becomes a row (it is not among the fitted topics).
    """
    counts: dict[int, dict[object, int]] = {}
    edges: list[object] = []
    for record in frame.to_dict("records"):
        topic = int(cast("int", record["Topic"]))
        if topic == OUTLIER_TOPIC:
            continue
        edge = record["Timestamp"]
        if edge not in edges:
            edges.append(edge)
        counts.setdefault(topic, {})[edge] = int(cast("int", record["Frequency"]))
    # Bin edges sort by their ISO text (one build's edges share one
    # type and timezone, so ISO order IS chronological order).
    edges.sort(key=_iso)
    rows: list[tuple[int, int, dict[str, object]]] = []
    for topic in topics:
        per_edge = counts.get(topic.topic_index, {})
        series_counts = [per_edge.get(edge, 0) for edge in edges]
        emerging, declining = z_flags(series_counts)
        rows.append(
            (
                sum(series_counts),
                topic.topic_index,
                {
                    "topic_index": topic.topic_index,
                    "label": topic.label,
                    "series": [
                        {"bin_start": _iso(edge), "count": count}
                        for edge, count in zip(edges, series_counts, strict=True)
                    ],
                    "emerging": emerging,
                    "declining": declining,
                },
            )
        )
    rows.sort(key=lambda row: (-row[0], row[1]))
    return [row for _total, _index, row in rows]


def _iso(edge: object) -> str:
    """A bin edge as ISO text (pandas Timestamps carry isoformat)."""
    iso = getattr(edge, "isoformat", None)
    if callable(iso):
        return str(iso())
    return str(edge)
