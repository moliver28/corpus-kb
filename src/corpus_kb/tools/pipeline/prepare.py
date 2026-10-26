"""Stage 1b: chunk identity, provenance, and contextual blurbs."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...utils.models import Chunk, Document
from ._facade import _ic


def _prepare_chunks_for_store(
    document: Document,
    chunks: list[Chunk],
    path: str,
    is_file: bool,
    config: dict[str, Any],
    source_type: str,
    text: str,
    document_id: str,
    errors: list[str],
    empty_reason: str | None,
) -> None:
    """Stage 1: identity + provenance + optional contextual blurbs.

    A zero-chunk plan must never complete clean: the EmptyPlanError seeds
    the stage error list BEFORE finish_run, so the ledger closes failed and
    the unchanged-probe can never enshrine an empty run as success. The
    strategy's empty_reason (when it gave one) is surfaced verbatim in the
    result dict by the caller.
    """
    if not chunks:
        errors.append(f"EmptyPlanError: {empty_reason or 'strategy produced no chunks'}")
    document.document_id = document_id
    for chunk in chunks:
        chunk.document_id = document_id
        chunk.chunk_hash = _ic()._sha256(chunk.text)
        chunk.source_timestamp = (
            datetime.fromtimestamp(Path(path).stat().st_mtime, tz=UTC)
            if is_file
            else datetime.now(UTC)
        )

    if _ic()._contextual_enabled(config, source_type):
        from ...rag.contextualizer import ContextGenerator

        blurbs = ContextGenerator(config).generate_blurbs(text, [c.text for c in chunks])
        for chunk, blurb in zip(chunks, blurbs, strict=True):
            chunk.context_blurb = blurb or None
