"""transcript_meta validator tests: research-transcript metadata sanity.

Three checks: every unit names a speaker, seq values strictly increase,
empty units are counted (reported, never failed). The mode rides the
advisory channel and never raises.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from corpus_kb.domain.transcript import TurnSpec
from corpus_kb.research.transcript_parser import parse_transcript_text
from corpus_kb.tools.pipeline.document import IngestDocument
from corpus_kb.tools.pipeline.research_units import research_document
from corpus_kb.tools.pipeline.transcript_meta import transcript_meta_findings
from corpus_kb.tools.pipeline.validate import resolve_mode, validate_document
from corpus_kb.utils.models import Chunk, Document

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "transcripts" / "meta_sanity.csv"
DOC_ID = "doc-1"


def _unit(text: str, seq: int, speaker: str, chunk_id: str) -> Chunk:
    """One research unit: the metadata keys research_document writes."""
    return Chunk(
        chunk_id=chunk_id,
        document_id=DOC_ID,
        text=text,
        source_type="transcript",
        metadata={"seq": seq, "speaker": speaker},
    )


def _doc(chunks: list[Chunk]) -> IngestDocument:
    text = "\n\n".join(chunk.text for chunk in chunks)
    return IngestDocument(
        document=Document(path="t", source_type="transcript", content=text, size_bytes=len(text)),
        text=text,
        file_hash="0" * 64,
        source_type="transcript",
        path="t",
        chunks=chunks,
    )


class TestSpeakerPresence:
    def test_clean_research_document_passes(self) -> None:
        turns = [
            TurnSpec(seq=0, speaker="Moderator", text="Welcome."),
            TurnSpec(seq=1, speaker="P1", text="Thanks."),
        ]
        doc = research_document("t.txt", "transcript", turns)
        assert transcript_meta_findings(doc) == []

    def test_blank_speaker_is_an_error(self) -> None:
        findings = transcript_meta_findings(_doc([_unit("hi", 0, "   ", "c1")]))
        assert len(findings) == 1
        assert findings[0].code == "missing-speaker"
        assert findings[0].severity == "error"
        assert findings[0].chunk_id == "c1"

    def test_fixture_unit_without_speaker_is_reported(self) -> None:
        """The csv fixture holds one empty speaker cell; the check finds it."""
        parsed = parse_transcript_text(FIXTURE.read_text(encoding="utf-8"), "csv")
        doc = research_document("meta_sanity.csv", "transcript", parsed.turns)
        findings = transcript_meta_findings(doc)
        assert [f.code for f in findings] == ["missing-speaker"]
        assert findings[0].severity == "error"

    def test_plain_chunks_without_metadata_are_skipped(self) -> None:
        chunk = Chunk(chunk_id="c1", document_id=DOC_ID, text="plain text", source_type="text")
        assert transcript_meta_findings(_doc([chunk])) == []


class TestSeqIntegrity:
    def test_strictly_increasing_seq_passes(self) -> None:
        doc = _doc([_unit("a", 0, "M", "c1"), _unit("b", 2, "P", "c2")])
        assert transcript_meta_findings(doc) == []

    def test_repeat_and_backwards_seq_are_errors(self) -> None:
        doc = _doc([_unit("a", 1, "M", "c1"), _unit("b", 1, "P", "c2"), _unit("c", 0, "P", "c3")])
        pairs = [(f.code, f.chunk_id) for f in transcript_meta_findings(doc)]
        assert pairs == [("seq-not-increasing", "c2"), ("seq-not-increasing", "c3")]

    def test_non_int_seq_is_an_error(self) -> None:
        chunk = _unit("a", 0, "M", "c1")
        chunk.metadata["seq"] = "3"
        findings = transcript_meta_findings(_doc([chunk]))
        assert [f.code for f in findings] == ["seq-not-int"]
        assert findings[0].severity == "error"


class TestEmptyUnits:
    def test_empty_units_are_counted_not_failed(self) -> None:
        doc = _doc([_unit("", 0, "M", "c1"), _unit("   ", 1, "P", "c2"), _unit("ok", 2, "P", "c3")])
        findings = transcript_meta_findings(doc)
        assert len(findings) == 1
        assert findings[0].code == "empty-unit"
        assert findings[0].severity == "warning"
        assert findings[0].chunk_id is None
        assert "2" in findings[0].detail


class TestModeSemantics:
    def test_resolve_mode_accepts_transcript_meta(self) -> None:
        assert resolve_mode("transcript_meta") == "transcript_meta"

    def test_transcript_meta_mode_never_raises(self) -> None:
        """Two findings on one unit; the mode returns a summary, not a raise."""
        doc = _doc([_unit("", 0, "  ", "c1")])
        report = validate_document(doc, "transcript_meta")
        assert report["mode"] == "transcript_meta"
        assert report["status"] == "findings"
        assert report["problem_count"] == 2

    def test_summary_lists_transcript_meta_first(self) -> None:
        report = validate_document(_doc([_unit("ok", 0, "M", "c1")]), "transcript_meta")
        assert [v["name"] for v in report["validators"]] == [
            "transcript_meta",
            "offset_invariant",
            "coverage",
            "budget",
        ]
        assert report["validators"][0]["status"] == "ok"

    def test_findings_are_deterministic(self) -> None:
        doc = _doc([_unit("a", 5, "", "c1"), _unit("b", 1, "P", "c2")])
        one = transcript_meta_findings(doc)
        assert [f.code for f in one] == ["missing-speaker", "seq-not-increasing"]
        two = transcript_meta_findings(doc)
        assert json.dumps([asdict(f) for f in one]) == json.dumps([asdict(f) for f in two])
