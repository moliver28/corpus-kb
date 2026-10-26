"""Call-time facade resolution for the pipeline stages.

Stage seams (``PostgresIngestStore``, ``embed_chunks``,
``extract_with_fallback``, ``run_pipeline``, the kept-in-place embed
stage) must resolve through the ``ingest_common`` facade's namespace so
test monkeypatches on ``ingest_common.*`` keep working after the split.
"""

from __future__ import annotations

from types import ModuleType


def _ic() -> ModuleType:
    """The ingest_common facade, resolved at call time."""
    from corpus_kb.tools import ingest_common

    return ingest_common
