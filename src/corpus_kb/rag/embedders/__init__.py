"""Embedder backends behind optional dependencies (todo 13).

``late_chunk.LateChunkEmbedder`` implements the master ``Embedder`` Protocol
(structural — no inheritance) for the optional ``latechunk`` extra. All
heavy imports live behind ``_latechunk_types`` (typed shim): importing this
package is safe without the extra.
"""

from __future__ import annotations

from corpus_kb.rag.embedders._latechunk_types import LateChunkDependencyError
from corpus_kb.rag.embedders.late_chunk import LateChunkEmbedder, is_latechunk_installed

__all__ = [
    "LateChunkDependencyError",
    "LateChunkEmbedder",
    "is_latechunk_installed",
]
