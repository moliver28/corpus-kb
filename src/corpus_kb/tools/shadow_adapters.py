"""Boundary-adapter dispatch for the shadow hierarchy (W2, ADDITIVE).

The shadow tree groups segments by each source family's NATURAL boundaries
instead of headings on every document. One adapter per family:

* ``slides``  — pptx/deck plans: chunks grouped per ``slide_number``
  (``chunking.hierarchy_paths.segments_from_slides``); mid levels snap to
  slide boundaries when ``slide`` is in ``respect_boundaries``.
* ``turns``   — transcript plans: turn-aligned chunks become speaker-turn
  segments, exchange-grouped via ``qa_context``
  (``segments_from_transcript_chunks``); mid levels snap to qa_pair
  boundaries when ``qa_pair`` is listed.
* ``heading`` — everything else (document, web, code, default): the
  historical heading grouping (``segments_from_chunks``), unchanged.

The choice is DETERMINISTIC — same strategy/plan, same adapter, so a
shadow tree stays rebuildable — and recorded (debug log + shadow note
suffix, see ``tools/shadow_ingest.write_shadow_hierarchy``). Pure
selection logic: no I/O, no embedder, no writer.
"""

from __future__ import annotations

from ..chunking.hierarchy import Segment
from ..chunking.hierarchy_paths import (
    segments_from_chunks,
    segments_from_slides,
    segments_from_transcript_chunks,
)
from ..chunking.hierarchy_tree import HierarchyTree, build_hierarchy
from ..ingest.hierarchy_settings import HierarchySettings
from ..ingestion.base import QA_CONTEXT_KEY, TRANSCRIPT_STRATEGY_NAME
from ..utils.models import Chunk

# The three boundary adapters (one per source family) and the group kind
# each feeds ``build_hierarchy``: a shared-``group_id`` run of that kind
# merges at the chunk/parent levels only when the kind is listed in
# ``ingest.chunking.respect_boundaries``.
ADAPTER_HEADING = "heading"
ADAPTER_SLIDES = "slides"
ADAPTER_TURNS = "turns"

# Literal, not an import: strategies.pptx drags python-pptx into every
# shadow ingest. tests/test_boundary_adapters.py pins it to the strategy's
# PPTX_STRATEGY_NAME so a rename cannot silently misroute decks.
_PPTX_STRATEGY_NAME = "pptx_structure"
_SLIDE_NUMBER_KEY = "slide_number"

# Each adapter's group kind for ``build_hierarchy`` (see the constants).
ADAPTER_GROUP_KINDS: dict[str, str] = {
    ADAPTER_SLIDES: "slide",
    ADAPTER_TURNS: "qa_pair",
    ADAPTER_HEADING: "heading",
}


def _has_slide_markers(chunks: list[Chunk]) -> bool:
    return any(_SLIDE_NUMBER_KEY in chunk.metadata for chunk in chunks)


def _has_transcript_markers(chunks: list[Chunk]) -> bool:
    return any(
        QA_CONTEXT_KEY in chunk.metadata
        or chunk.metadata.get("strategy") == TRANSCRIPT_STRATEGY_NAME
        for chunk in chunks
    )


def select_segment_adapter(chunks: list[Chunk], strategy: str | None = None) -> str:
    """Pick the shadow segment adapter for one document (deterministic).

    Precedence: the resolved ingestion strategy name first (the pipeline's
    truth), then the plan's own boundary markers in chunk metadata (covers
    direct callers and marker-setting plugins), then the heading default.
    """
    if strategy == _PPTX_STRATEGY_NAME or _has_slide_markers(chunks):
        return ADAPTER_SLIDES
    if strategy == TRANSCRIPT_STRATEGY_NAME or _has_transcript_markers(chunks):
        return ADAPTER_TURNS
    return ADAPTER_HEADING


def segments_for_adapter(
    chunks: list[Chunk],
    text: str,
    adapter: str,
) -> list[Segment]:
    """Run the adapter's segment builder over the document's chunks."""
    if adapter == ADAPTER_SLIDES:
        return segments_from_slides(chunks, text)
    if adapter == ADAPTER_TURNS:
        return segments_from_transcript_chunks(chunks)
    return segments_from_chunks(chunks, text)


def build_main_path_tree(
    document_id: str,
    text: str,
    chunks: list[Chunk],
    settings: HierarchySettings,
    adapter: str = ADAPTER_HEADING,
) -> HierarchyTree:
    """Deterministic shadow tree grouped by the adapter's natural boundaries."""
    segments = segments_for_adapter(chunks, text, adapter)
    return build_hierarchy(
        document_id,
        segments,
        settings,
        path="main",
        group_kind=ADAPTER_GROUP_KINDS[adapter],
    )


__all__ = [
    "ADAPTER_GROUP_KINDS",
    "ADAPTER_HEADING",
    "ADAPTER_SLIDES",
    "ADAPTER_TURNS",
    "build_main_path_tree",
    "segments_for_adapter",
    "select_segment_adapter",
]
