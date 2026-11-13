"""The chunk-metadata keys SearchResult exposes (issue #271).

The textbook strategy writes ``citation`` (a plain-English page
citation) and a sorted ``units`` list into chunk metadata;
``chunks.metadata`` stores them. The search family re-attaches both to
every result. One parse lives here: both engines return the JSON column
as a string, and any non-string, empty, or non-object value means the
keys are absent — the result fields stay None, so a corpus without the
metadata keeps the envelope shape it always had.
"""

from __future__ import annotations

import json


def citation_and_units(raw: object) -> tuple[str | None, list[str] | None]:
    """Parse one ``chunks.metadata`` value into the two exposed keys.

    The stored ``units`` list is already sorted by the textbook chunk
    builder, so the passthrough keeps the stored order and reads the
    same on every replay.
    """
    if not isinstance(raw, str) or not raw:
        return None, None
    metadata = json.loads(raw)
    if not isinstance(metadata, dict):
        return None, None
    citation = metadata.get("citation")
    units = metadata.get("units")
    names = [u for u in units if isinstance(u, str)] if isinstance(units, list) else []
    return (citation if isinstance(citation, str) else None, names or None)
