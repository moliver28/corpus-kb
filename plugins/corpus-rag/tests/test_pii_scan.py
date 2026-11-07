"""Behavior tests for the setup PII scan (issue #353).

The output holds paths, kinds, and counts — never text. Role labels
(participant, interviewer, moderator) never count as person names.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import pii_scan  # noqa: E402

TRANSCRIPT = (
    "Interview with Jordan Lee (jordan.lee@example.com)\n"
    "Moderator: Thanks for coming, Jordan.\n"
    "Jordan Lee: The dashboard broke on Tuesday.\n"
    "Participant 2: I agree with Jordan Lee.\n"
    "Interviewer: Anything else?\n"
    "Jordan Lee: support@corp.example never replied.\n"
)


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Counts and shapes
# ---------------------------------------------------------------------------


def test_counts_emails_and_person_names() -> None:
    counts = pii_scan.scan_text(TRANSCRIPT)
    assert counts["email"] == 2
    # "Interview with ..." is skipped ("with" is a stop word); Jordan Lee
    # counts in the intro and on each speaker line, including inside a
    # participant's body text.
    assert counts["person_name"] == 4


def test_output_has_paths_kinds_counts_never_text(tmp_path: Path) -> None:
    path = write(tmp_path, "p1-transcript.md", TRANSCRIPT)
    rows = pii_scan.scan_files([path])
    assert len(rows) == 1
    blob = json.dumps(rows)
    assert "jordan.lee@example.com" not in blob
    assert "support@corp.example" not in blob
    assert "Jordan" not in blob and "Lee" not in blob
    assert rows[0]["path"] == str(path)
    assert set(rows[0]["counts"]) == {"email", "person_name"}


def test_role_labels_are_skipped(tmp_path: Path) -> None:
    text = (
        "Participant: Hello.\n"
        "interviewer: Hi.\n"
        "**Moderator:** Ok.\n"
        "Participant 2: Yes.\n"
        "Facilitator: Right.\n"
    )
    counts = pii_scan.scan_text(text)
    # The labels themselves never count as person names.
    assert counts["person_name"] == 0


def test_role_label_bodies_still_scan(tmp_path: Path) -> None:
    text = "Participant: I met Chris Fox on Tuesday.\n"
    counts = pii_scan.scan_text(text)
    assert counts["person_name"] == 1


def test_participant_number_labels_stay_out(tmp_path: Path) -> None:
    text = "Participant 2: Hello there.\nParticipant 3: Hi.\n"
    counts = pii_scan.scan_text(text)
    assert counts["person_name"] == 0


def test_empty_and_missing_files_are_safe(tmp_path: Path) -> None:
    empty = write(tmp_path, "empty.md", "")
    rows = pii_scan.scan_files([empty, tmp_path / "gone.md"])
    assert rows == [{"path": str(empty), "counts": {"email": 0, "person_name": 0}}]


def test_scan_folder_reports_only_hits_and_sorts(tmp_path: Path) -> None:
    write(tmp_path, "b-transcript.md", TRANSCRIPT)
    write(tmp_path, "a-transcript.md", TRANSCRIPT)
    write(tmp_path, "clean.md", "Nothing here.\n")
    rows = pii_scan.scan_folder(tmp_path)
    names = [Path(row["path"]).name for row in rows]
    assert names == ["a-transcript.md", "b-transcript.md"]


def test_docx_head_is_scanned_not_the_whole_file(tmp_path: Path) -> None:
    import zipfile

    path = tmp_path / "session.docx"
    xml = (
        "<w:document><w:body><w:p><w:r><w:t>Interviewer: Hello there.</w:t></w:r>"
        "</w:p><w:p><w:r><w:t>Jordan Lee: Hi.</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    row = pii_scan.scan_file(path)
    assert row["counts"]["person_name"] == 1
    assert row["counts"]["email"] == 0
