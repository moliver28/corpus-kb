"""Bold speaker labels parse like plain labels (issue #352).

0.6.0 normalized "**P1:** text" lines to "P1: text" before ingest. The
engine parser now reads both, so the plugin needs no normalizer copy.
"""

from __future__ import annotations

from corpus_kb.research.transcript_parser import SPEAKER_LINE, parse_transcript_text


def speakers(raw: str) -> list[str]:
    return [turn.speaker for turn in parse_transcript_text(raw).turns]


def test_plain_label_still_parses() -> None:
    assert speakers("P1: Hello there.\nP2: Hi back.\n") == ["P1", "P2"]


def test_bold_label_with_colon_inside_bold() -> None:
    assert speakers("**P1:** Hello there.\n**P2:** Hi back.\n") == ["P1", "P2"]


def test_bold_label_with_colon_outside_bold() -> None:
    assert speakers("**P1**: Hello there.\n**P2**: Hi back.\n") == ["P1", "P2"]


def test_bold_and_plain_labels_mix() -> None:
    raw = "**Moderator:** Welcome.\nP1: Thanks.\n**P1:** Glad to be here.\n"
    assert speakers(raw) == ["Moderator", "P1", "P1"]


def test_unlabelled_prose_merges_into_last_bold_turn() -> None:
    turns = parse_transcript_text("**P1:** Start of thought\nand it continues here.\n").turns
    assert len(turns) == 1
    assert turns[0].speaker == "P1"
    assert turns[0].text == "Start of thought and it continues here."


def test_text_starting_with_bold_keeps_its_markers() -> None:
    turns = parse_transcript_text("P1: **bold point** and more.\n").turns
    assert turns[0].text == "**bold point** and more."


def test_prose_line_without_colon_is_not_a_turn() -> None:
    assert speakers("no label on this line\nnor this one\n") == []


def test_regex_accepts_bold_and_rejects_single_asterisk() -> None:
    assert SPEAKER_LINE.match("**P1:** hi") is not None
    assert SPEAKER_LINE.match("**P1**: hi") is not None
    assert SPEAKER_LINE.match("*P1: hi") is None


def test_parse_quality_stays_verified_for_bold_labels() -> None:
    parsed = parse_transcript_text("**P1:** Hello.\n")
    assert parsed.parse_quality == "verified"
