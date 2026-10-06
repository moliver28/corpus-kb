"""Exchange linking (todo-11 (d), v5 section 3.2, cheapest first).

1. Adjacency (default): an exchange opens at a main_question (moderator turn
   with '?' or imperative opener) or transition; following moderator probes
   are ABSORBED into the open exchange; participant turns until the next
   opener are its answers.
2. Cross-reference (non-adjacent): if an answer's similarity to its open
   exchange question is low, cosine against the previous LOOKBACK moderator
   questions; link only if best > TAU_XREF and beats the open exchange by
   MARGIN. link_method=cross_ref, confidence capped at medium.
Turn typing (v5 section 3.1): main_question | probe | transition | ack |
instruction | answer | followup_answer | reply_to_peer | other.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from corpus_kb.domain.transcript import ExchangeSpec, TurnSpec

LOOKBACK = 10
TAU_XREF = 0.5
MARGIN = 0.1
LOW_SIM_FLOOR = 0.35

OPENER_MARKERS = ("moving on", "next question", "let's talk about", "section")
FOLLOW_UP_STARTS = (
    "can you",
    "could you",
    "would you",
    "why was",
    "why do",
    "how so",
    "tell me more",
    "say more",
    "what do you mean",
    "any examples",
    "like what",
    "go on",
    "and after",
)


def _moderator_type(turn: TurnSpec, has_open_exchange: bool) -> str:
    text = turn.text.strip().lower()
    if any(marker in text for marker in OPENER_MARKERS):
        return "transition"
    words = turn.text.split()
    if has_open_exchange and text.startswith(FOLLOW_UP_STARTS):
        return "probe"
    if "?" in turn.text:
        if has_open_exchange and len(words) <= 3:
            return "probe"
        return "main_question"
    if len(words) <= 6:
        return "ack"
    return "probe"


def link_exchanges(
    turns: list[TurnSpec],
    embed: Callable[[str], list[float]] | None = None,
) -> list[ExchangeSpec]:
    """Build exchange specs: adjacency + probe absorption (+ cross_ref)."""
    exchanges: list[ExchangeSpec] = []
    open_idx: ExchangeSpec | None = None
    open_question = ""

    for turn in turns:
        if turn.role == "moderator":
            turn.turn_type = _moderator_type(turn, open_idx is not None)
            turn.role_in_exchange = turn.turn_type
            turn.is_codable = False
            if turn.turn_type in ("main_question", "transition"):
                open_idx = ExchangeSpec(
                    seq=len(exchanges),
                    q_unit_seqs=[turn.seq],
                    a_unit_seqs=[],
                    link_method="adjacency",
                    link_confidence="high",
                    topic_id=turn.seq if turn.turn_type == "transition" else None,
                )
                exchanges.append(open_idx)
                open_question = turn.text
            elif open_idx is not None and turn.turn_type == "probe":
                open_idx.q_unit_seqs.append(turn.seq)
                open_question += " " + turn.text
            continue

        if not turn.turn_type:
            turn.turn_type = "answer" if turn.role != "unknown" else "other"
        turn.role_in_exchange = turn.turn_type
        if open_idx is None or turn.turn_type == "other":
            continue

        method, confidence, score, target = _link_decision(
            turn, open_question, exchanges, turns, embed
        )
        if method == "cross_ref":
            exchanges.append(
                ExchangeSpec(
                    seq=len(exchanges),
                    q_unit_seqs=list(target.q_unit_seqs),
                    a_unit_seqs=[turn.seq],
                    link_method="cross_ref",
                    link_confidence=confidence,
                    link_score=score,
                )
            )
            continue
        open_idx.a_unit_seqs.append(turn.seq)
        open_idx.link_score = score
        if confidence != "high":
            open_idx.link_confidence = confidence

    _attach_stance_and_origin(turns, exchanges)
    return exchanges


def _link_decision(
    turn: TurnSpec,
    open_question: str,
    exchanges: list[ExchangeSpec],
    turns: list[TurnSpec],
    embed: Callable[[str], list[float]] | None,
) -> tuple[str, str, float | None, ExchangeSpec]:
    if embed is None:
        return "adjacency", "high", None, exchanges[-1]
    open_sim = _cosine(embed(turn.text), embed(open_question)) if open_question else 0.0
    if open_sim >= LOW_SIM_FLOOR or not exchanges:
        return "adjacency", "high", open_sim, exchanges[-1]
    best_sim, best_target = open_sim, exchanges[-1]
    for candidate in reversed(exchanges[-LOOKBACK:]):
        q_texts = [t.text for t in turns if t.seq in candidate.q_unit_seqs]
        if not q_texts:
            continue
        sim = max(_cosine(embed(turn.text), embed(q)) for q in q_texts)
        if sim > best_sim:
            best_sim, best_target = sim, candidate
    if best_target is not exchanges[-1] and best_sim > TAU_XREF and best_sim - open_sim >= MARGIN:
        return "cross_ref", "medium", best_sim, best_target
    return "adjacency", "medium", open_sim, exchanges[-1]


def _attach_stance_and_origin(turns: list[TurnSpec], exchanges: list[ExchangeSpec]) -> None:
    by_seq = {t.seq: t for t in turns}
    for exchange in exchanges:
        answers = [by_seq[s] for s in exchange.a_unit_seqs if s in by_seq]
        questions = [by_seq[s] for s in exchange.q_unit_seqs if s in by_seq]
        exchange.stance = _stance(questions, answers)
        exchange.term_origin = _term_origin(questions, answers)


def _stance(questions: list[TurnSpec], answers: list[TurnSpec]) -> str | None:
    """Yes/no-shaped exchanges get a lexical stance; open questions n/a."""
    q_text = " ".join(q.text.lower() for q in questions)
    if "?" not in q_text:
        return None
    a_text = " ".join(a.text.lower() for a in answers)
    if not a_text:
        return None
    if a_text.startswith(("no", "not really", "nope")):
        return "deny"
    if a_text.startswith(("yes", "yeah", "yep", "definitely")):
        return "affirm"
    if " but " in a_text or " partly" in a_text or " mostly" in a_text:
        return "partial"
    if len(a_text.split()) < 4:
        return "deflect"
    return None


def _term_origin(questions: list[TurnSpec], answers: list[TurnSpec]) -> str | None:
    """v5 section 3.3: where the loaded term first appeared.

    Earliest salient-token occurrence across the exchange's turns decides;
    the same token opening both sides yields 'both'.
    """
    q_first = _first_salient_token([q.text for q in questions])
    a_first = _first_salient_token([a.text for a in answers])
    if not q_first and not a_first:
        return None
    if q_first and a_first and q_first == a_first:
        return "both"
    if q_first:
        return "moderator"
    return "participant"


def _first_salient_token(texts: list[str]) -> str | None:
    tokens = _content_tokens(" ".join(texts))
    return tokens[0] if tokens else None


def _content_tokens(text: str) -> list[str]:
    stop = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "is",
        "it",
        "that",
        "this",
        "with",
        "for",
        "you",
        "your",
        "i",
        "we",
        "do",
        "does",
        "did",
        "what",
        "how",
        "when",
        "was",
        "were",
        "there",
    }
    words = "".join(c if c.isalnum() else " " for c in text.lower()).split()
    return [w for w in words if w not in stop and len(w) > 3]


def _cosine(a: list[float], b: list[float]) -> float:
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if not norm_a or not norm_b:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True)) / (norm_a * norm_b)
