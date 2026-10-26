"""Transcript strategy: turn-aligned Q+A chunks with ``qa_context``.

Reuses the research domain's transcript machinery (parse -> roles ->
exchanges) and its chunking taxonomy (qa exchanges vs meeting windows, see
strategies/qa_planning.py) but bypasses Unstructured partitioning entirely:
labelled speaker lines are parsed directly (txt/vtt/srt/csv in memory; docx
via parse_transcript).

Every Q+A chunk carries metadata ``qa_context`` {question, participants,
exchange_id}. Facilitator-less content (no moderator/questions detected)
falls back to deterministic turn windows WITHOUT qa_context — a question
that was never there must not be invented. Token guard: every chunk stays
<= 0.8x2048 tokens (chars/4 heuristic); oversized turns are hard-split with
a warning.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from ...research.linking import link_exchanges
from ...research.roles import map_roles
from ...research.transcript_parser import (
    SPEAKER_LINE,
    ParsedTranscript,
    detect_format,
    parse_transcript,
    parse_transcript_text,
)
from ..base import (
    TRANSCRIPT_STRATEGY_NAME,
    ChunkPlan,
    DetectResult,
    PlannedChunk,
    SourceProfile,
)
from .qa_planning import positioned_chunks, reconstruct_turns

_FILENAME_HINTS = ("transcript", "interview")
_HINT_SOURCES = ("transcript", "interview")
_ROLE_WORDS = frozenset({"moderator", "participant", "interviewer", "interviewee", "facilitator"})


class TranscriptStrategy:
    """Turn-aligned Q+A chunking for interview/meeting transcripts."""

    name = TRANSCRIPT_STRATEGY_NAME

    def detect(self, source_type_hint: str | None, filename: str, head: str) -> DetectResult:
        """Recognize transcripts from head bytes + filename — no full parse."""
        reasons: list[tuple[float, str]] = []
        name = (filename or "").lower()
        if any(hint in name for hint in _FILENAME_HINTS):
            reasons.append((0.3, "filename-hint"))
        if name.endswith((".vtt", ".srt")):
            reasons.append((0.4, "subtitle-suffix"))
        if head.lstrip().upper().startswith("WEBVTT"):
            reasons.append((0.5, "webvtt-header"))
        if name.endswith(".csv") and _csv_header_sniff(head):
            reasons.append((0.5, "csv-speaker-header"))
        speakers = _head_speakers(head)
        if len(speakers) >= 3 and len(set(speakers)) >= 2:
            if any(speaker.lower() in _ROLE_WORDS for speaker in speakers):
                reasons.append((0.5, "moderator-pattern"))
            else:
                reasons.append((0.3, "speaker-pattern"))
        if source_type_hint in _HINT_SOURCES:
            reasons.append((0.2, "source-type-hint"))
        if not reasons:
            return DetectResult(0.0, "no transcript signal")
        confidence = min(sum(score for score, _ in reasons), 1.0)
        return DetectResult(confidence, max(reasons, key=lambda item: item[0])[1])

    def build_chunks(self, raw: str, filename: str, profile: SourceProfile) -> ChunkPlan:
        """Parse turns, link exchanges, emit qa/turn chunks with qa_context."""
        warnings: list[str] = []
        parsed = self._parse(raw, profile)
        document_text, starts = reconstruct_turns(parsed.turns)
        if not parsed.turns:
            return ChunkPlan(
                strategy=self.name,
                document_text=document_text,
                empty_reason="no speaker turns recovered from source",
                warnings=warnings,
            )
        if parsed.parse_quality != "verified":
            warnings.append(f"parse_quality={parsed.parse_quality}")
        map_roles(parsed.turns)
        exchanges = link_exchanges(parsed.turns)
        positioned = positioned_chunks(parsed.turns, exchanges, starts, warnings, profile.config)
        positioned.sort(key=lambda item: item[0])
        chunks: list[PlannedChunk] = []
        for _, group in positioned:
            for chunk in group:
                chunk.index = len(chunks)
                chunks.append(chunk)
        return ChunkPlan(
            strategy=self.name,
            chunks=chunks,
            document_text=document_text,
            empty_reason=None if chunks else "no exchanges or turns recoverable",
            warnings=warnings,
        )

    def _parse(self, raw: str, profile: SourceProfile) -> ParsedTranscript:
        """Files use the full parser (docx included); raw text parses in memory."""
        if profile.is_file:
            path = Path(profile.path)
            if path.exists():
                return parse_transcript(path)
        return parse_transcript_text(raw, detect_format(Path(profile.path)))


def _head_speakers(head: str) -> list[str]:
    """Labelled speaker names seen in the head (moderator/participant sniffing)."""
    speakers: list[str] = []
    for line in head.splitlines():
        match = SPEAKER_LINE.match(line.strip())
        if match:
            speakers.append(match.group(1).strip())
        if len(speakers) >= 64:
            break
    return speakers


def _csv_header_sniff(head: str) -> bool:
    lines = head.splitlines()[:1]
    if not lines:
        return False
    cells = [cell.strip().lower() for cell in next(csv.reader(io.StringIO(lines[0])))]
    return "speaker" in cells and "text" in cells
