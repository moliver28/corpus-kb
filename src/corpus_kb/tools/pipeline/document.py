"""The canonical ingest document and its char-span coordinate system.

Every artifact a pipeline stage produces (chunk, entity, relation) refers
to ONE canonical text: ``IngestDocument.text``. An artifact's span is the
pair ``(start_char, end_char)`` on that text, and the offset invariant
holds: ``text[start_char:end_char] == artifact_text``.

Spans are read-only metadata in this wave. No envelope key, model field,
or stored column changes here; the span helpers only read what the
strategies and extractors already carry (``source_start_char`` /
``source_end_char``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...utils.models import Chunk, Document, Entity, Relation


@dataclass(frozen=True)
class ArtifactSpan:
    """One artifact's place: doc id plus a char span on the canonical text."""

    document_id: str
    start_char: int
    end_char: int


@dataclass
class IngestDocument:
    """The one canonical document every stage consumes.

    ``document`` is the storage model. ``text`` is the canonical text the
    strategy derived (``ChunkPlan.document_text``). ``file_hash`` is the
    SHA-256 of that text. Chunk spans come from the strategy's plan and
    stay optional: a span is reported only when it satisfies the offset
    invariant, so a strategy without spans degrades to ``None``.
    """

    document: Document
    text: str
    file_hash: str
    source_type: str
    path: str
    is_file: bool = False
    chunks: list[Chunk] = field(default_factory=list)
    empty_reason: str | None = None
    strategy: str | None = None

    def chunk_span(self, chunk: Chunk) -> ArtifactSpan | None:
        """The chunk's span, or None when unset or off the canonical text."""
        start, end = chunk.source_start_char, chunk.source_end_char
        if start is None or end is None:
            return None
        if self.text[start:end] != chunk.text:
            return None
        return ArtifactSpan(document_id=str(chunk.document_id), start_char=start, end_char=end)

    def spans(self) -> dict[str, ArtifactSpan | None]:
        """Span per chunk id. None where the invariant does not hold."""
        return {str(chunk.chunk_id): self.chunk_span(chunk) for chunk in self.chunks}

    def anchored_span(self, artifact: Entity | Relation) -> ArtifactSpan | None:
        """Span of the chunk an extracted artifact is anchored to, or None.

        Entity and relation offsets count from the CHUNK text, not from the
        document text. The anchor is the chunk's own span; the artifact's
        in-chunk offsets stay on the artifact model.
        """
        if artifact.chunk_id is None:
            return None
        for chunk in self.chunks:
            if str(chunk.chunk_id) == artifact.chunk_id:
                return self.chunk_span(chunk)
        return None

    def offset_invariant_problems(self) -> list[str]:
        """Chunk ids whose stored span does not slice to the chunk text."""
        problems: list[str] = []
        for chunk in self.chunks:
            start, end = chunk.source_start_char, chunk.source_end_char
            if start is None or end is None:
                continue
            if self.text[start:end] != chunk.text:
                problems.append(str(chunk.chunk_id))
        return problems
