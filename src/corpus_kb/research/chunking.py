"""Coding-unit vs retrieval-chunk split (todo 12, v5 section 5).

v5 5.1: the CODING UNIT for interviews is the participant answer turn
(research_units rows, built by todo-11 ingest); moderator turns stay
is_codable=false but remain mandatory exchange context.

v5 5.2: the RETRIEVAL CHUNK differs from the coding unit:
  * interviews: children of kind answer / qa / question; search children,
    return the parent exchange (small-to-big retrieval);
  * facilitator-less conversational sources (meetings): sliding windows of
    5-7 turns with 2-3-turn overlap;
  * every chunk carries a DETERMINISTIC context prefix (project / doc title
    / source type / speaker role+pseudonym / topic / timestamp) - the
    zero-model-call form of contextual retrieval.

This module owns the taxonomy and the deterministic text builders; the
projection embeds prefix+text (corpus_kb.projections.research._embed) and
corpus_kb.research.retrieval searches the children.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# v5 5.2: windows of 5-7 turns with 2-3 turn overlap. Pinned mid-range as
# module constants (todo-14 precedent: tuning params are never new config).
WINDOW_SIZE = 6
WINDOW_OVERLAP = 2
MIN_WINDOW = 5

CHILD_ANSWER = "answer"
CHILD_QA = "qa"
CHILD_QUESTION = "question"
CHILD_WINDOW_MEMBER = "window_member"

QUESTION_ROLES = frozenset({"main_question", "probe", "transition"})
ANSWER_ROLES = frozenset({"answer", "followup_answer"})


def build_context_prefix(
    project: str | None = None,
    doc_title: str | None = None,
    source_type: str | None = None,
    speaker: str | None = None,
    topic: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Deterministic v5 5.2 context prefix. Zero model calls.

    Missing fields are omitted entirely (never rendered as empty labels) so
    the prefix for a given row is a stable function of its context alone.
    """
    fields = [
        ("Project", project),
        ("Document", doc_title),
        ("Source type", source_type),
        ("Speaker", speaker),
        ("Topic", topic),
        ("Timestamp", timestamp),
    ]
    return " | ".join(f"{name}: {value}" for name, value in fields if value)


def speaker_label(label: str, role: str | None, pseudonym: str | None) -> str:
    """v5 5.2 speaker field: role + pseudonym (pseudonym falls back to label)."""
    who = pseudonym or label
    return f"{who} ({role})" if role else who


def unit_kind(role_in_exchange: str | None) -> str:
    """Map a unit's exchange role onto the retrieval child taxonomy."""
    if role_in_exchange in ANSWER_ROLES:
        return CHILD_ANSWER
    if role_in_exchange in QUESTION_ROLES:
        return CHILD_QUESTION
    return CHILD_WINDOW_MEMBER


@dataclass(frozen=True)
class ChildChunk:
    """One retrieval child: search it, return its parent (small-to-big)."""

    key: str
    kind: str
    text: str
    prefix: str
    doc_id: str
    parent_exchange_seq: int | None = None
    parent_unit_seq: int | None = None

    @property
    def embedded_text(self) -> str:
        """Text the embedder sees: deterministic prefix + clean text."""
        return f"{self.prefix}\n{self.text}" if self.prefix else self.text


@dataclass(frozen=True)
class MeetingWindow:
    """One sliding-window retrieval parent over facilitator-less turns."""

    seq: int
    start_seq: int
    end_seq: int
    seqs: tuple[int, ...]


def meeting_windows(unit_seqs: Iterable[int]) -> list[MeetingWindow]:
    """v5 5.2 sliding windows over turn seqs: WINDOW_SIZE turns advancing by
    WINDOW_SIZE - WINDOW_OVERLAP (2-3 turn overlap preserved). A tail smaller
    than MIN_WINDOW merges into the previous window instead of standing alone
    (deterministic choice: keeps the overlap and minimum-size invariants
    exact; the merged final window may exceed the 7-turn target slightly).
    """
    ordered = sorted(unit_seqs)
    if not ordered:
        return []
    n = len(ordered)
    if n <= WINDOW_SIZE:
        return [MeetingWindow(0, ordered[0], ordered[-1], tuple(ordered))]
    stride = WINDOW_SIZE - WINDOW_OVERLAP
    bounds: list[tuple[int, int]] = []
    i = 0
    while i < n:
        end = min(i + WINDOW_SIZE, n)
        bounds.append((i, end))
        if end == n:
            break
        i += stride
    if len(bounds) > 1 and bounds[-1][1] - bounds[-1][0] < MIN_WINDOW:
        bounds.pop()
        bounds[-1] = (bounds[-1][0], n)
    return [
        MeetingWindow(k, ordered[a], ordered[b - 1], tuple(ordered[a:b]))
        for k, (a, b) in enumerate(bounds)
    ]
