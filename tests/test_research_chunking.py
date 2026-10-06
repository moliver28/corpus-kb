"""Offline unit tests for the v5 §5 chunk split (todo 12).

Pure functions: context prefix determinism, child-kind taxonomy, meeting
sliding windows. No DB, no Ollama, no markers.
"""

from __future__ import annotations

from itertools import pairwise

from corpus_kb.research.chunking import (
    CHILD_ANSWER,
    CHILD_QA,
    CHILD_QUESTION,
    CHILD_WINDOW_MEMBER,
    WINDOW_SIZE,
    ChildChunk,
    build_context_prefix,
    meeting_windows,
    speaker_label,
    unit_kind,
)


def test_context_prefix_deterministic_and_ordered() -> None:
    once = build_context_prefix(
        project="Trust Study",
        doc_title="Interview 3",
        source_type="interview",
        speaker="Dana (participant)",
    )
    twice = build_context_prefix(
        project="Trust Study",
        doc_title="Interview 3",
        source_type="interview",
        speaker="Dana (participant)",
    )
    assert once == twice
    assert once.index("Project:") < once.index("Document:")
    assert once.index("Document:") < once.index("Source type:")
    assert once.index("Source type:") < once.index("Speaker:")


def test_context_prefix_omits_missing_fields() -> None:
    prefix = build_context_prefix(doc_title="Only title")
    assert prefix == "Document: Only title"
    assert build_context_prefix() == ""


def test_speaker_label_prefers_pseudonym_and_carries_role() -> None:
    assert speaker_label("P1 Dana", "participant", "Dana W.") == "Dana W. (participant)"
    assert speaker_label("P1 Dana", "participant", None) == "P1 Dana (participant)"
    assert speaker_label("Unknown", None, None) == "Unknown"


def test_unit_kind_taxonomy() -> None:
    assert unit_kind("main_question") == CHILD_QUESTION
    assert unit_kind("probe") == CHILD_QUESTION
    assert unit_kind("transition") == CHILD_QUESTION
    assert unit_kind("answer") == CHILD_ANSWER
    assert unit_kind("followup_answer") == CHILD_ANSWER
    assert unit_kind("other") == CHILD_WINDOW_MEMBER
    assert unit_kind(None) == CHILD_WINDOW_MEMBER


def test_child_embedded_text_prefixes_clean_text() -> None:
    child = ChildChunk(
        key="unit:1",
        kind=CHILD_ANSWER,
        text="The docs were stale.",
        prefix="Project: P | Source type: interview",
        doc_id="d1",
    )
    qa_child = ChildChunk(key="exchange:0", kind=CHILD_QA, text="Q: x A: y", prefix="", doc_id="d1")
    assert child.embedded_text == "Project: P | Source type: interview\nThe docs were stale."
    assert qa_child.embedded_text == "Q: x A: y"


def test_meeting_windows_size_and_overlap_invariants() -> None:
    seqs = list(range(20))
    windows = meeting_windows(seqs)
    assert windows, "no windows built"
    for window in windows:
        assert len(window.seqs) >= 5, f"window {window.seq} below the 5-turn floor"
        assert window.seqs == tuple(range(window.start_seq, window.end_seq + 1))
    for prev, nxt in pairwise(windows):
        overlap = len(set(prev.seqs) & set(nxt.seqs))
        assert 2 <= overlap <= 3, f"overlap {overlap} outside the 2-3 band"
    covered = {seq for window in windows for seq in window.seqs}
    assert covered == set(seqs), "windows must cover every turn"


def test_meeting_windows_short_sources_single_window() -> None:
    assert len(meeting_windows(list(range(4)))) == 1
    small = meeting_windows(list(range(WINDOW_SIZE)))
    assert len(small) == 1 and len(small[0].seqs) == WINDOW_SIZE
    assert meeting_windows([]) == []


def test_meeting_windows_tail_never_stands_alone() -> None:
    # 20 turns: naive tiling leaves a 4-turn tail; it must merge backwards.
    windows = meeting_windows(range(20))
    assert all(len(w.seqs) >= 5 for w in windows)
    assert windows[-1].seqs[-1] == 19
