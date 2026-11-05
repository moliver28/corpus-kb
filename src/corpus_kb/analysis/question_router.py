"""The deterministic question router over the analysis method register.

Cascade stage 1 of the routing program (waves 2-4): a phrase pass forms
the candidate set from ``question_phrases`` substring hits, then the
candidates are scored either by embedding cosine to per-method phrase
centroids (embedding mode) or by phrase-hit share (degraded mode). The
decision rule is threshold plus margin; both ride the ``analysis.routing``
settings block, with 0.55 and 0.10 as the pinned defaults.

The module is pure: no LLM, no config loading, no op-layer imports. The
embedder arrives as a parameter and only its ``embed(text) -> list[float]``
call is used (``OllamaEmbedder`` and the deterministic fakes qualify; an
async-only embedder must be bridged by the caller). Same question, same
register, same output — always.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from corpus_kb.analysis.method_register import MethodRecommendation, MethodRegister
from corpus_kb.analysis.method_register_rubric import rank_methods
from corpus_kb.analysis.question_intent import QuestionIntent
from corpus_kb.analysis.question_router_centroids import (
    centroids_for,
    cosine,
    phrase_scores,
    usable_vector,
)
from corpus_kb.analysis.settings import (
    DEFAULT_ROUTING_CONFIDENCE_THRESHOLD,
    DEFAULT_ROUTING_MARGIN,
)

DEFAULT_CONFIDENCE_THRESHOLD = DEFAULT_ROUTING_CONFIDENCE_THRESHOLD
DEFAULT_MARGIN = DEFAULT_ROUTING_MARGIN
# Exact-arithmetic gap equality (say 7/10 - 6/10 = 0.10) must pass the
# margin check; float subtraction lands just below, so allow this slack.
_MARGIN_EPS = 1e-9
TOP_SCORE_ROWS = 10
TOP_READINGS = 3
RANKED_CAP = 5
DEGRADED_NOTE = "degraded"


class RoutingEmbedder(Protocol):
    """The one embedder call this router makes (the query-router call)."""

    def embed(self, text: str) -> list[float]:
        """Return one embedding vector for the text."""
        ...


@dataclass
class RoutingTrace:
    """How the decision was scored: mode, knobs, and the top score rows."""

    mode: str
    threshold: float
    margin: float
    scores: list[dict[str, float | str]] = field(default_factory=list)


@dataclass
class RouteResult:
    """The full routing outcome: status, degraded flag, intent, rubric join, trace."""

    status: str
    degraded: bool
    intent: QuestionIntent
    ranked: list[MethodRecommendation]
    trace: RoutingTrace


def route_question(
    question: str,
    register: MethodRegister,
    embedder: RoutingEmbedder | None,
    settings: dict[str, object] | None,
) -> RouteResult:
    """Route one question to analysis method candidates. Deterministic.

    ``settings`` is the ``analysis.routing`` block; absent keys fall back
    to the pinned defaults. ``embedder=None`` (or a zero-vector question
    embedding) degrades to phrase-hit share, and the intent carries the
    ``"degraded"`` note. The intent question is the normalized text, and
    ``intent.axes`` stays all-``None`` until a later stage assigns it.
    """
    if not isinstance(question, str):
        raise TypeError(
            f"question: expected text, got {type(question).__name__}. "
            "Pass the question as a string."
        )
    threshold = _setting(settings, "confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD)
    margin = _setting(settings, "margin", DEFAULT_MARGIN)
    normalized = _normalize_text(question)

    hits, phrase_counts, names = _phrase_pass(normalized, register)
    candidate_ids = list(hits) or list(phrase_counts)
    if not candidate_ids:
        # No row carries phrases, so nothing can score. Honest dead end;
        # nothing was embedded either, so the trace stays in phrase mode.
        intent = QuestionIntent(question=normalized, status="insufficient")
        trace = RoutingTrace(
            mode="phrase",
            threshold=threshold,
            margin=margin,
            scores=[],
        )
        return RouteResult(
            status="insufficient", degraded=False, intent=intent, ranked=[], trace=trace
        )

    question_vector: list[float] | None = None
    if embedder is not None:
        question_vector = usable_vector(embedder.embed(normalized))
    degraded = question_vector is None
    if embedder is None or question_vector is None:
        scores = phrase_scores(candidate_ids, hits, phrase_counts)
    else:
        scores = _embedding_scores(register, embedder, candidate_ids, question_vector)

    ordered = sorted(scores, key=lambda method_id: (-scores[method_id], method_id))
    top_score = scores[ordered[0]]
    second_score = scores[ordered[1]] if len(ordered) > 1 else None
    resolved = top_score >= threshold and (
        second_score is None or top_score - second_score >= margin - _MARGIN_EPS
    )
    status = "resolved" if resolved else "ambiguous"

    readings = [] if resolved else _build_readings(ordered, hits, names)

    candidate_set = set(candidate_ids)
    ranked = [rec for rec in rank_methods(register) if rec.id in candidate_set][:RANKED_CAP]
    intent = QuestionIntent(
        question=normalized,
        status=status,
        confidence=top_score,
        method_id=ordered[0] if resolved else None,
        ranked_method_ids=ordered,
        readings=readings,
        note=DEGRADED_NOTE if degraded else None,
    )
    trace = _build_trace(degraded, threshold, margin, scores, ordered)
    return RouteResult(status=status, degraded=degraded, intent=intent, ranked=ranked, trace=trace)


def _embedding_scores(
    register: MethodRegister,
    embedder: RoutingEmbedder,
    candidate_ids: list[str],
    question_vector: list[float],
) -> dict[str, float]:
    """Cosine from the question vector to each candidate's phrase centroid."""
    dims = len(question_vector)
    centroids = centroids_for(register, embedder, dims, candidate_ids)
    return {
        method_id: cosine(question_vector, centroids.get(method_id, [0.0] * dims))
        for method_id in candidate_ids
    }


def _build_readings(
    ordered: list[str],
    hits: dict[str, list[str]],
    names: dict[str, str],
) -> list[dict[str, str]]:
    """The top candidate restatements for an ambiguous answer."""
    readings: list[dict[str, str]] = []
    for method_id in ordered[:TOP_READINGS]:
        matched = hits.get(method_id)
        readings.append(
            {
                "method_id": method_id,
                "method_name": names[method_id],
                "why": matched[0] if matched else names[method_id],
            }
        )
    return readings


def _build_trace(
    degraded: bool,
    threshold: float,
    margin: float,
    scores: dict[str, float],
    ordered: list[str],
) -> RoutingTrace:
    """The stable score trace: top rows, score desc then id asc."""
    return RoutingTrace(
        mode="phrase" if degraded else "embedding",
        threshold=threshold,
        margin=margin,
        scores=[
            {"method_id": method_id, "score": scores[method_id]}
            for method_id in ordered[:TOP_SCORE_ROWS]
        ],
    )


def _phrase_pass(
    normalized: str, register: MethodRegister
) -> tuple[dict[str, list[str]], dict[str, int], dict[str, str]]:
    """Match seed phrases as substrings; skip rows without phrases.

    Returns the matched phrases per method, the authored phrase count per
    method, and the method names. A row whose ``question_phrases`` is
    absent or empty never joins the candidate pool.
    """
    hits: dict[str, list[str]] = {}
    phrase_counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for record in register.methods:
        phrases = record.question_phrases
        if not phrases:
            continue
        phrase_counts[record.id] = len(phrases)
        names[record.id] = record.name
        matched = [phrase for phrase in phrases if _normalize_text(phrase) in normalized]
        if matched:
            hits[record.id] = matched
    return hits, phrase_counts, names


def _setting(settings: dict[str, object] | None, key: str, default: float) -> float:
    """One routing knob; an absent or unusable value falls back to the default."""
    if not settings:
        return default
    value = settings.get(key, default)
    if isinstance(value, bool | int | float | str):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return default


def _normalize_text(text: str) -> str:
    """Casefold and collapse whitespace runs to single spaces."""
    return " ".join(text.split()).casefold()
