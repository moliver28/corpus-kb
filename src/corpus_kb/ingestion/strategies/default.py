"""The default strategy: the EXISTING partition + chunking dispatch, verbatim.

For files: Unstructured partition via the platform-safe wrapper in
``corpus_kb.partitioning`` -> ``chunk_elements`` — exactly the pre-registry
``run_pipeline`` entry. For raw text: the single NarrativeText element wrap.
Chunk output is identical to today's pipeline (pinned by test).
"""

from __future__ import annotations

from pathlib import Path

from ...chunking.unstructured_chunker import chunk_elements
from ...partitioning import ElementProxy
from ...partitioning import partition as unstructured_partition
from ..base import (
    DEFAULT_STRATEGY_NAME,
    ChunkPlan,
    DetectResult,
    SourceProfile,
    planned_from_chunk,
)


class DefaultStrategy:
    """Partition with Unstructured, chunk with the existing dispatch."""

    name = DEFAULT_STRATEGY_NAME

    def detect(self, source_type_hint: str | None, filename: str, head: str) -> DetectResult:
        """Never claims content: this strategy is the fallback, not a detector."""
        return DetectResult(0.0, "fallback")

    def build_chunks(self, raw: str, filename: str, profile: SourceProfile) -> ChunkPlan:
        """Partition (files) or wrap (raw text), then chunk — verbatim path."""
        if profile.is_file:
            elements = unstructured_partition(Path(profile.path))
            document_text = "\n\n".join(e.text for e in elements if e.text)
        else:
            elements = _elements_for_text(raw)
            document_text = raw
        models = chunk_elements(elements, document_text, document_id="")
        planned = [planned_from_chunk(model, index) for index, model in enumerate(models)]
        return ChunkPlan(
            strategy=self.name,
            chunks=planned,
            document_text=document_text,
            empty_reason=None if planned else "no elements recovered from source",
        )


def _elements_for_text(text: str) -> list[ElementProxy]:
    """Wrap raw text into a single-element list (the pre-registry helper)."""
    return [ElementProxy(text=text, element_type="NarrativeText", element_id="raw-text")]
