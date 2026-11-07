"""Behavior tests for transcript detection and exclude globs (issue #352)."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import transcripts  # noqa: E402


def make(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


TRANSCRIPT_BODY = (
    "P1: Good morning.\nP2: Morning.\nP1: Shall we start?\nP2: Ready.\n"
)


# ---------------------------------------------------------------------------
# Name rules
# ---------------------------------------------------------------------------


def test_vtt_and_srt_are_transcripts(tmp_path: Path) -> None:
    assert transcripts.is_transcript(make(tmp_path / "a.vtt", "WEBVTT\n"))
    assert transcripts.is_transcript(make(tmp_path / "b.srt", "1\n00:00:01,000 --> x\nhi\n"))


def test_named_transcript_documents(tmp_path: Path) -> None:
    for suffix in (".md", ".txt", ".docx"):
        name = tmp_path / f"interview-transcript{suffix}"
        make(name, "notes without labels")
        assert transcripts.is_transcript(name), name


def test_named_transcript_requires_known_suffix(tmp_path: Path) -> None:
    assert not transcripts.is_transcript(make(tmp_path / "transcript.pdf", "%PDF"))


def test_plain_document_without_labels_is_not_transcript(tmp_path: Path) -> None:
    assert not transcripts.is_transcript(make(tmp_path / "notes.md", "# Meeting notes\n"))


# ---------------------------------------------------------------------------
# Content rule: 3 or more labels with one repeated, in the first 4096 bytes
# ---------------------------------------------------------------------------


def test_content_rule_repeated_labels(tmp_path: Path) -> None:
    assert transcripts.is_transcript(make(tmp_path / "call.txt", TRANSCRIPT_BODY))


def test_content_rule_needs_three_labels(tmp_path: Path) -> None:
    assert not transcripts.is_transcript(make(tmp_path / "two.txt", "P1: hi\nP2: hello\n"))


def test_content_rule_needs_one_repeated(tmp_path: Path) -> None:
    body = "P1: a\nP2: b\nP3: c\n"
    assert not transcripts.is_transcript(make(tmp_path / "unique.txt", body))


def test_content_rule_scans_only_the_head(tmp_path: Path) -> None:
    """Labels past the first 4096 bytes do not make a transcript."""
    filler = "x" * 5000
    body = filler + "\nP1: a\nP2: b\nP1: c\nP3: d\n"
    assert not transcripts.is_transcript(make(tmp_path / "deep.txt", body))


def test_bold_labels_count_for_the_content_rule(tmp_path: Path) -> None:
    body = "**P1:** Good morning.\n**P2:** Morning.\n**P1:** Start?\n**P2:** Ready.\n"
    assert transcripts.is_transcript(make(tmp_path / "bold.txt", body))


def test_docx_head_comes_from_word_document_xml(tmp_path: Path) -> None:
    """A .docx transcript is detected through its zip head, no partitioner."""
    path = tmp_path / "session-transcript.docx"
    body = (
        '<?xml version="1.0"?><w:document><w:body>'
        "<w:p><w:r><w:t>P1: Good morning.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>P2: Morning.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>P1: Start?</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>P2: Ready.</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", body)
    assert transcripts.is_transcript(path)


def test_docx_without_labels_is_not_transcript(tmp_path: Path) -> None:
    path = tmp_path / "plain.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", "<w:document><w:body><w:t>hello</w:t></w:body></w:document>")
    assert not transcripts.is_transcript(path)


def test_docx_missing_document_xml_is_not_transcript(tmp_path: Path) -> None:
    path = tmp_path / "empty.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("other.xml", "<x/>")
    assert not transcripts.is_transcript(path)


# ---------------------------------------------------------------------------
# Exclude globs (gitwildmatch subset; a custom list replaces the defaults)
# ---------------------------------------------------------------------------


def test_default_excludes() -> None:
    patterns = transcripts.DEFAULT_EXCLUDES
    assert transcripts.excluded("interviews/participant-key.md", patterns)
    assert transcripts.excluded("sessions/raw-transcripts/a.vtt", patterns)
    assert transcripts.excluded("analysis/themes.md", patterns)
    assert transcripts.excluded("analysis/deep/themes.md", patterns)
    assert transcripts.excluded("deliverables/report.md", patterns)
    assert transcripts.excluded("notes/interview-1-summary.md", patterns)
    assert transcripts.excluded("interviews/p3-key-points.md", patterns)
    assert not transcripts.excluded("interviews/p3.md", patterns)
    assert not transcripts.excluded("sessions/p3.vtt", patterns)


def test_custom_excludes_replace_defaults(tmp_path: Path) -> None:
    patterns = ["private/**"]
    assert transcripts.excluded("private/notes.md", patterns)
    assert not transcripts.excluded("analysis/themes.md", patterns)


def test_slashless_pattern_matches_any_basename() -> None:
    assert transcripts.excluded("deep/nest/secret.md", ["secret.md"])
    assert not transcripts.excluded("deep/nest/other.md", ["secret.md"])


def test_star_matches_within_one_segment() -> None:
    assert transcripts.excluded("notes/tmp-a.txt", ["notes/tmp-*.txt"])
    assert not transcripts.excluded("notes/deep/tmp-a.txt", ["notes/tmp-*.txt"])


# ---------------------------------------------------------------------------
# Watch scan: depth 4, cap 300, deadline, excludes, hidden folders
# ---------------------------------------------------------------------------


def test_scan_depth_cap(tmp_path: Path) -> None:
    deep = tmp_path / "a/b/c/d/e/f/file.txt"
    make(deep, "hello")
    near = make(tmp_path / "a/b/c/d/near.txt", "hello")
    found = transcripts.scan_files(tmp_path, [])
    assert near in found
    assert deep not in found


def test_scan_cap_300(tmp_path: Path) -> None:
    for i in range(310):
        make(tmp_path / f"f{i:03}.txt", "x")
    found = transcripts.scan_files(tmp_path, [])
    assert len(found) == 300


def test_scan_skips_excluded_and_hidden(tmp_path: Path) -> None:
    make(tmp_path / "analysis/themes.md", "x")
    make(tmp_path / ".hidden/file.txt", "x")
    keep = make(tmp_path / "notes.md", "x")
    found = transcripts.scan_files(tmp_path, transcripts.DEFAULT_EXCLUDES)
    assert found == [keep]


def test_scan_respects_deadline(tmp_path: Path) -> None:
    for i in range(50):
        make(tmp_path / f"d{i}/f.txt", "x")
    found = transcripts.scan_files(tmp_path, [], deadline=0.0)
    assert found == []
