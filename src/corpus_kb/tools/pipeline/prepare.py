"""Stage 1b: chunk identity, provenance, and contextual blurbs."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._facade import _ic
from .document import IngestDocument


def _prepare_chunks_for_store(
    doc: IngestDocument,
    config: dict[str, Any],
    document_id: str,
    errors: list[str],
) -> None:
    """Stage 1: identity + provenance + optional contextual blurbs.

    A zero-chunk plan must never complete clean: the EmptyPlanError seeds
    the stage error list BEFORE finish_run, so the ledger closes failed and
    the unchanged-probe can never enshrine an empty run as success. The
    strategy's empty_reason (when it gave one) is surfaced verbatim in the
    result dict by the caller.
    """
    if not doc.chunks:
        errors.append(f"EmptyPlanError: {doc.empty_reason or 'strategy produced no chunks'}")
    doc.document.document_id = document_id
    for chunk in doc.chunks:
        chunk.document_id = document_id
        chunk.chunk_hash = _ic()._sha256(chunk.text)
        chunk.source_timestamp = (
            datetime.fromtimestamp(Path(doc.path).stat().st_mtime, tz=UTC)
            if doc.is_file
            else datetime.now(UTC)
        )

    if _ic()._contextual_enabled(config, doc.source_type):
        from ...rag.contextualizer import ContextGenerator

        blurbs = ContextGenerator(config).generate_blurbs(doc.text, [c.text for c in doc.chunks])
        for chunk, blurb in zip(doc.chunks, blurbs, strict=True):
            chunk.context_blurb = blurb or None
