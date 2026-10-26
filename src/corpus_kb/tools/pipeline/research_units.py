"""The research front-end's canonical document (unified-ingestion Wave 3).

The research ingest keeps its own parse (``parse_transcript``) and its own
storage shape (event-sourced research units). What it adopts here is the
pipeline's CANONICAL DOCUMENT: one text, one span per unit, with the offset
invariant ``text[start_char:end_char] == unit text``. The canonical text is
the normalized turn texts joined by a blank line, so every span slices
exactly. The same coordinates feed Wave 4's validation subject later.
"""

from __future__ import annotations

from corpus_kb.domain.transcript import TurnSpec, normalize_turn_text, text_sha256
from corpus_kb.utils.models import Chunk, Document

from .document import IngestDocument

UNIT_SEPARATOR = "\n\n"


def research_document(path: str, source_type: str, turns: list[TurnSpec]) -> IngestDocument:
    """Build the canonical document and stamp each turn's span onto it.

    Each turn becomes one Chunk keyed by its text sha, so unit ids are
    stable across runs. The span is written back onto the TurnSpec, so the
    event payload carries it (rebuilds re-apply it). Chunks with an empty
    text are skipped; they carry no span.
    """
    parts: list[str] = []
    chunks: list[Chunk] = []
    offset = 0
    document = Document(path=path, source_type=source_type, content="", size_bytes=0)
    for turn in turns:
        text = normalize_turn_text(turn.text)
        if not text:
            continue
        start = offset
        end = start + len(text)
        sha = text_sha256(text)
        chunks.append(
            Chunk(
                document_id=document.document_id,
                chunk_id=sha,
                text=text,
                source_type=source_type,
                source_start_char=start,
                source_end_char=end,
                metadata={"seq": turn.seq, "speaker": turn.speaker, "text_sha256": sha},
            )
        )
        turn.start_char = start
        turn.end_char = end
        parts.append(text)
        offset = end + len(UNIT_SEPARATOR)
    canonical = UNIT_SEPARATOR.join(parts)
    document.content = canonical
    document.size_bytes = len(canonical)
    return IngestDocument(
        document=document,
        text=canonical,
        file_hash=text_sha256(canonical),
        source_type=source_type,
        path=path,
        is_file=True,
        chunks=chunks,
        strategy="research-transcript",
    )
