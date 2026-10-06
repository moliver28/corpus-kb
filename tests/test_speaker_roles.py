from __future__ import annotations

from corpus_kb.coding.speaker_roles import classify_role


def test_role_classification() -> None:
    assert classify_role("Interviewer", doc_count=40, roster=set()) == "moderator"
    assert classify_role("Dana Okafor", doc_count=1, roster={"dana okafor"}) == "participant"
    assert classify_role("", doc_count=0, roster=set()) == "unknown"
