"""Stage-contract pipeline package (unified-ingestion Wave 2).

Modules:

* ``document`` — the canonical :class:`IngestDocument` and the char-span
  coordinate system (offset invariant:
  ``text[start_char:end_char] == artifact_text``).
* ``plan`` — ``run_pipeline``: strategy resolution and the ChunkPlan.
* ``stages`` — ``_run_document_stages`` (``_run_locked_stages`` is the
  legacy keyword seam): the locked stage sequence under the advisory
  lock. One small module per stage: ``prepare``, ``produce``,
  ``shadow``, ``extract``, ``atomize``, ``result``.
* ``sinks`` — the :class:`IngestSink` write port, :class:`ChunkSink`
  (a verbatim wrapper over ``tools/ingest_store.py``) and
  :class:`UnitSink` (the research twin, Wave 3).
* ``research_units`` — the research front-end adapter: the parse becomes
  the canonical :class:`IngestDocument`, one span per turn.

``tools/ingest_common.py`` stays the public facade: it re-exports every
moved public name, so importers and the tests' monkeypatch seams
(``ingest_common.embed_chunks``, ``ingest_common.PostgresIngestStore``,
``ingest_common.run_pipeline``) keep working unchanged.
"""

from __future__ import annotations

from .document import ArtifactSpan, IngestDocument
from .plan import run_pipeline
from .sinks import ChunkSink, IngestSink, UnitSink

__all__ = [
    "ArtifactSpan",
    "ChunkSink",
    "IngestDocument",
    "IngestSink",
    "UnitSink",
    "run_pipeline",
]
