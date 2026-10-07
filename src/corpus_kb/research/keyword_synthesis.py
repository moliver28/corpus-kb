"""Keyword / exclusion-keyword synthesis (todo 17, v5 §13).

Per code: positive keywords (Monroe informative-Dirichlet log-odds, or
c-TF-IDF) contrasting code-assigned unit texts against the rest; exclusion
keywords from the same method against rejected gray-zone units, widened
periodically by a seeded random sample of uncoded units (the contrast
widening set). Keyword hits are recorded with location (``answer``,
``question``, ``both``) — only ``answer`` hits count as explicit evidence;
question hits are candidate generators gated downstream by stance +
information gain. Below the minimum support a code's list is marked
``provisional``. Overlapping positive keywords between codes and
positive-vs-exclusion collisions are CONFLICT FLAGS for codebook review
(the v5 MECE intent implemented as actual mechanics — not a separate
"MECE" construct).

Pure functions here; persistence lives in research_store calls by the
synthesis runner.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from corpus_kb.coding.keyness import log_odds_dirichlet
from corpus_kb.research.keyword_text import (
    ctfidf as ctfidf,
)
from corpus_kb.research.keyword_text import (
    term_counts,
)
from corpus_kb.research.keyword_text import (
    tokenize as tokenize,
)

MIN_SUPPORT = 15
DEFAULT_TOP_N = 8


@dataclass(frozen=True)
class SynthesizedKeyword:
    """One synthesized keyword with its score and support."""

    term: str
    score: float
    kind: str  # positive | exclusion
    support_n: int
    provisional: bool
    method: str


@dataclass(frozen=True)
class KeywordConflict:
    """A cross-code keyword collision flagged for codebook review."""

    conflict_type: str  # shared_positive | positive_vs_exclusion
    term: str
    codes: list[str] = field(default_factory=list)


def synthesize_keywords(
    member_texts_by_code: Mapping[str, Sequence[str]],
    rest_texts_by_code: Mapping[str, Sequence[str]] | None = None,
    method: str = "log_odds",
    top_n: int = DEFAULT_TOP_N,
    min_support: int = MIN_SUPPORT,
) -> dict[str, list[SynthesizedKeyword]]:
    """Positive keywords per code vs the rest of the coded corpus.

    method ``log_odds``: Monroe informative-Dirichlet z (coding.keyness);
    method ``ctfidf``: class-based TF-IDF. The contrast set defaults to every
    OTHER code's member texts (code-assigned units vs rest, v5 §13). Support
    is the code's unit count; below ``min_support`` the list is marked
    provisional.
    """
    if method not in {"log_odds", "ctfidf"}:
        raise ValueError(f"unknown keyword method: {method}")
    rest_by_code: dict[str, Counter[str]] = {}
    if rest_texts_by_code is not None:
        rest_by_code = {code: term_counts(texts) for code, texts in rest_texts_by_code.items()}
    else:
        rest_by_code = {
            code: term_counts(
                [t for other, texts in member_texts_by_code.items() if other != code for t in texts]
            )
            for code in member_texts_by_code
        }
    member_counts = {code: term_counts(texts) for code, texts in member_texts_by_code.items()}
    result: dict[str, list[SynthesizedKeyword]] = {}
    if method == "ctfidf":
        ranked = _ctfidf_ranked(member_counts)
    else:
        ranked = _log_odds_ranked(member_counts, rest_by_code)
    for code_id, terms in ranked.items():
        support_n = len(member_texts_by_code.get(code_id, []))
        provisional = support_n < min_support
        result[code_id] = [
            SynthesizedKeyword(
                term=term,
                score=score,
                kind="positive",
                support_n=support_n,
                provisional=provisional,
                method=method,
            )
            for term, score in terms[:top_n]
        ]
    return result


def _log_odds_ranked(
    member_counts: Mapping[str, Counter[str]],
    rest_by_code: Mapping[str, Counter[str]],
) -> dict[str, list[tuple[str, float]]]:
    ranked: dict[str, list[tuple[str, float]]] = {}
    for code_id, counts in member_counts.items():
        rest_counts = rest_by_code.get(code_id, Counter())
        target_total = sum(counts.values())
        ref_total = sum(rest_counts.values())
        pairs: list[tuple[str, float]] = []
        for term, count in counts.items():
            z = log_odds_dirichlet(
                a=count,
                b=rest_counts.get(term, 0),
                target_total=target_total,
                ref_total=ref_total,
            )
            pairs.append((term, z))
        pairs.sort(key=lambda p: (-p[1], p[0]))
        ranked[code_id] = pairs
    return ranked


def _ctfidf_ranked(member_counts: Mapping[str, Counter[str]]) -> dict[str, list[tuple[str, float]]]:
    scores = ctfidf(member_counts)
    ranked: dict[str, list[tuple[str, float]]] = {}
    for code_id, term_scores in scores.items():
        pairs = sorted(term_scores.items(), key=lambda p: (-p[1], p[0]))
        ranked[code_id] = pairs
    return ranked


def synthesize_exclusion_keywords(
    rejected_texts: Sequence[str],
    coded_texts: Sequence[str],
    widen_pool_texts: Sequence[str] = (),
    widen_n: int = 0,
    seed: int = 42,
    top_n: int = DEFAULT_TOP_N,
) -> list[SynthesizedKeyword]:
    """Exclusion keywords: rejected gray-zone texts vs the coded corpus.

    ``widen_n`` units are drawn from ``widen_pool_texts`` (uncoded residue)
    with a seeded RNG and added to the contrast set — the v5 §13 "periodically
    widen with a random sample" mechanic.
    """
    contrast = list(rejected_texts)
    if widen_n > 0 and widen_pool_texts:
        import random

        rng = random.Random(seed)
        pool = list(widen_pool_texts)
        contrast.extend(rng.sample(pool, min(widen_n, len(pool))))
    ref_counts = term_counts(coded_texts)
    target_counts = term_counts(contrast)
    target_total = sum(target_counts.values()) or 1
    ref_total = sum(ref_counts.values()) or 1
    pairs: list[tuple[str, float]] = []
    for term, count in target_counts.items():
        z = log_odds_dirichlet(
            a=count, b=ref_counts.get(term, 0), target_total=target_total, ref_total=ref_total
        )
        pairs.append((term, z))
    pairs.sort(key=lambda p: (-p[1], p[0]))
    support = len(contrast)
    return [
        SynthesizedKeyword(
            term=term,
            score=score,
            kind="exclusion",
            support_n=support,
            provisional=False,
            method="log_odds",
        )
        for term, score in pairs[:top_n]
    ]


def hit_location(keyword: str, answer: str, question: str) -> str | None:
    """Where a keyword hits: 'answer', 'question', 'both', or None."""
    needle = keyword.lower()
    in_answer = needle in answer.lower()
    in_question = needle in question.lower()
    if in_answer and in_question:
        return "both"
    if in_answer:
        return "answer"
    if in_question:
        return "question"
    return None


def conflict_flags(
    positive_by_code: Mapping[str, Sequence[SynthesizedKeyword]],
    exclusion_keywords: Sequence[SynthesizedKeyword],
) -> list[KeywordConflict]:
    """Cross-code conflicts: shared positive terms + positive-vs-exclusion hits."""
    claims: dict[str, list[str]] = {}
    for code_id, keywords in positive_by_code.items():
        for kw in keywords:
            claims.setdefault(kw.term.lower(), []).append(code_id)
    flags: list[KeywordConflict] = []
    for term in sorted(claims):
        codes = claims[term]
        if len(codes) > 1:
            flags.append(KeywordConflict("shared_positive", term, sorted(codes)))
    exclusion_terms = {kw.term.lower() for kw in exclusion_keywords}
    for term in sorted(claims):
        if term in exclusion_terms:
            existing = next(
                (f for f in flags if f.term == term and f.conflict_type == "shared_positive"),
                None,
            )
            codes = sorted(set(claims[term]) | {"<exclusion>"})
            if existing is not None:
                flags.remove(existing)
            flags.append(KeywordConflict("positive_vs_exclusion", term, codes))
    return flags


def is_provisional(support_n: int, min_support: int = MIN_SUPPORT) -> bool:
    """v5 §13: minimum support ~15-20 units; below that mark provisional."""
    return support_n < min_support
