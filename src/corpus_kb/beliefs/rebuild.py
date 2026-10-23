"""Belief rebuild: statements + conflict flags -> evidence (Living Corpus W4).

One belief per (subject, relation, object). The subject of a one-entity
statement is its speaker when the QA chain provides one
(``speaker:<exchange_id>``), else ``corpus`` — so interview
participants get their own beliefs. Stance comes from the statement
text (the same word lists the cascade uses); stance-free sentences do
not inform a stance belief. Conflicted statements count half; superseded
ones count a quarter — the history stays, discounted.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from .math_core import Evidence

_POSITIVE = ("like", "love", "prefer", "enjoy", "support", "adore")
_NEGATIVE = ("dislike", "hate", "avoid", "oppose")


def stance_of(text: str) -> str | None:
    low = " ".join(text.lower().split())
    has_pos = any(f" {w} " in f" {low} " or f" {w}s " in f" {low} " for w in _POSITIVE)
    has_neg = any(f" {w} " in f" {low} " or f" {w}s " in f" {low} " for w in _NEGATIVE)
    if has_pos and not has_neg:
        return "positive"
    if has_neg and not has_pos:
        return "negative"
    return None


@dataclass(frozen=True)
class BeliefKey:
    subject: str
    relation: str
    object: str


@dataclass(frozen=True)
class BeliefEvidence:
    key: BeliefKey
    items: list[tuple[Evidence, str | None]]  # (evidence, statement_id)


def build_evidence(
    statements: list[dict],
    *,
    conflict_discount: float = 0.5,
    superseded_discount: float = 0.25,
    now: datetime | None = None,
) -> list[BeliefEvidence]:
    """Group statement rows into per-belief evidence lists.

    Rows are the statements read path (entity names unfolded, ids as
    text) plus ``conflicted`` and ``exchange_id`` fields. Each item keeps
    its statement id so the revision log stays provanceable.
    """
    del now
    grouped: dict[BeliefKey, list[tuple[Evidence, str | None]]] = defaultdict(list)
    for row in statements:
        names = [str(n) for n in (row.get("entity_names") or [])]
        if not names:
            continue
        stance = stance_of(str(row.get("text", "")))
        if stance is None:
            continue
        subject = names[0] if len(names) >= 2 else _speaker_of(row)
        key = BeliefKey(subject, str(row.get("template_id", "statement")), names[-1])
        weight = float(row.get("confidence") or 0.5)
        if row.get("conflicted"):
            weight *= conflict_discount
        if row.get("superseded_by"):
            weight *= superseded_discount
        observed = row.get("observed_at") or datetime.now()
        statement_id = row.get("statement_id")
        grouped[key].append(
            (Evidence(stance=stance, weight=weight, observed_at=observed), statement_id)
        )
    ordered = sorted(grouped.items(), key=lambda kv: (kv[0].subject, kv[0].relation, kv[0].object))
    return [BeliefEvidence(key=k, items=v) for k, v in ordered]


def _speaker_of(row: dict) -> str:
    exchange_id = row.get("exchange_id")
    if isinstance(exchange_id, int):
        return f"speaker:{exchange_id}"
    return "corpus"
