"""Chunking adapters for parsed document elements."""

from __future__ import annotations

from .hierarchy import Segment, estimate_tokens, merge_groups, pack_windows, split_oversized
from .hierarchy_paths import (
    segments_from_chunks,
    segments_from_slides,
    segments_from_transcript_chunks,
    segments_from_turns,
)
from .hierarchy_tree import (
    LEVEL_CHILD,
    LEVEL_CHUNK,
    LEVEL_PARENT,
    HierarchyNode,
    HierarchyTree,
    build_hierarchy,
)
from .unstructured_chunker import chunk_elements

__all__ = [
    "LEVEL_CHILD",
    "LEVEL_CHUNK",
    "LEVEL_PARENT",
    "HierarchyNode",
    "HierarchyTree",
    "Segment",
    "build_hierarchy",
    "chunk_elements",
    "estimate_tokens",
    "merge_groups",
    "pack_windows",
    "segments_from_chunks",
    "segments_from_slides",
    "segments_from_transcript_chunks",
    "segments_from_turns",
    "split_oversized",
]
