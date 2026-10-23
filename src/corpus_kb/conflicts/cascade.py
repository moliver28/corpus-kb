"""The contradiction cascade (Living Corpus W3).

Three gates, cheapest first (the literature verdict locked in the plan):

- **Gate A — temporal**: two statements sharing an anchored entity whose
  truth windows do NOT overlap is a REVISION, not a contradiction
  (bi-temporal rule: Zep/Graphiti, EvoKG). Overlapping windows stay in
  the candidate set.
- **Gate B — structured swaps**: relation swap (one statement's
  template implies the opposite stance — the Dialogue NLI relation
  swap) and entity swap (same template, same subject, different
  object). Both are free: no model call.
- **Gate C — NLI judge**: one temperature-0 zero-shot NLI judgment over
  the locked contradiction prompt. CONTRADICT confirms; anything else,
  or a transport failure, lands as ``uncertain`` — flagged for the
  human review queue, never deleted.

Flag-don't-delete: every verdict writes a ``statement_conflicts`` row.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StatementRow:
    """The statement fields the cascade needs (from the read path)."""

    statement_id: str
    text: str
    template_id: str
    entity_names: tuple[str, ...]
    valid_at: object | None
    observed_at: object | None
    superseded_by: str | None


@dataclass(frozen=True)
class CascadeVerdict:
    """One gated pair verdict, ready for the store."""

    statement_a_id: str
    statement_b_id: str
    anchor_name: str
    kind: str
    gate_reached: str
    verdict: str
    nli_verdict: str | None = None


def _overlap(a: StatementRow, b: StatementRow) -> bool:
    """True when the two truth windows overlap (Gate A)."""
    a_end = a.valid_at or a.observed_at
    b_start = b.valid_at or b.observed_at
    return a_end is not None and b_start is not None and a_end >= b_start


def gate_a(a: StatementRow, b: StatementRow, anchor: str) -> CascadeVerdict | None:
    """Temporal gate: non-overlapping windows are a revision, not a clash."""
    if _overlap(a, b):
        return None
    return CascadeVerdict(
        statement_a_id=a.statement_id,
        statement_b_id=b.statement_id,
        anchor_name=anchor,
        kind="temporal",
        gate_reached="A",
        verdict="revision",
    )


def relation_swap(a: StatementRow, b: StatementRow) -> bool:
    """True when the two texts assert opposite stances on the anchor.

    Text-level stance check (the statements carry their full sentence
    text): one text likes/loves/supports/prefers the anchor while the
    other dislikes/hates/opposes/avoids it. Template ids alone are too
    coarse (both may classify as ``preference``).
    """

    def stance(text: str) -> set[str]:
        words = _stance_words(text)
        return words

    return bool(stance(a.text) and stance(b.text) and not (stance(a.text) & stance(b.text)))


_POSITIVE = ("like", "love", "prefer", "enjoy", "support", "adore")
_NEGATIVE = ("dislike", "hate", "avoid", "oppose", "cannot stand", "can't stand")


def _stance_words(text: str) -> set[str]:
    low = " ".join(text.lower().split())
    found: set[str] = set()
    for word in _POSITIVE:
        if f" {word} " in f" {low} ":
            found.add("pos")
    for word in _NEGATIVE:
        if f" {word} " in f" {low} ":
            found.add("neg")
    return found


def entity_swap(a: StatementRow, b: StatementRow) -> bool:
    """True when same template, same subject, but a different object."""
    if a.template_id != b.template_id or len(a.entity_names) < 2 or len(b.entity_names) < 2:
        return False
    return a.entity_names[0] == b.entity_names[0] and a.entity_names[1] != b.entity_names[1]


def gate_b(a: StatementRow, b: StatementRow, anchor: str) -> CascadeVerdict | None:
    """Structured triple swaps (free; no model call)."""
    if relation_swap(a, b):
        return CascadeVerdict(
            statement_a_id=a.statement_id,
            statement_b_id=b.statement_id,
            anchor_name=anchor,
            kind="relation_swap",
            gate_reached="B",
            verdict="conflict",
        )
    if entity_swap(a, b):
        return CascadeVerdict(
            statement_a_id=a.statement_id,
            statement_b_id=b.statement_id,
            anchor_name=anchor,
            kind="entity_swap",
            gate_reached="B",
            verdict="conflict",
        )
    return None
