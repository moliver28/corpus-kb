"""Window outcomes for the textbook strategy: one record per parsed window.

A window parse either served a cached record or ran a parser; the
strategy keeps one :class:`WindowOutcome` per window so callers can see
how each window was served and what it warned. :func:`summarize` folds
the outcomes into the conditional ingest-envelope ``textbook`` key
(contracts/envelopes.py ``TEXTBOOK_KEYS``): a summary appears only on a
textbook-strategy ingest, so every other envelope stays byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass

from .textbook_pages import WindowRecord


@dataclass(frozen=True)
class WindowOutcome:
    """One parsed window: its record, the parser that served it, and how.

    ``cache_hit`` marks a record the window cache served; ``warnings``
    holds that window's parse notes in emit order.
    """

    record: WindowRecord
    parser_used: str
    cache_hit: bool
    warnings: tuple[str, ...]


def summarize(outcomes: list[WindowOutcome]) -> dict[str, object]:
    """The envelope ``textbook`` summary: parser, cache counts, pages, warnings.

    ``parser`` names the parser that served the most windows (ties break
    alphabetically, so a snapshot is stable). ``pages`` sums the window
    spans — one pass over the outcomes only.
    """
    counts: dict[str, int] = {}
    hits = 0
    pages = 0
    warning_count = 0
    for outcome in outcomes:
        counts[outcome.parser_used] = counts.get(outcome.parser_used, 0) + 1
        if outcome.cache_hit:
            hits += 1
        pages += outcome.record.end_page - outcome.record.start_page + 1
        warning_count += len(outcome.warnings)
    parser = min(counts, key=lambda name: (-counts[name], name)) if counts else ""
    return {
        "parser": parser,
        "cache_hits": hits,
        "cache_misses": len(outcomes) - hits,
        "pages": pages,
        "warning_count": warning_count,
    }
