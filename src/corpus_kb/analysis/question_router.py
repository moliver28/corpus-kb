"""The deterministic question router over the analysis method register.

Cascade stage 1 of the routing program (waves 2-4): a phrase pass forms
the candidate set from ``question_phrases`` substring hits and scores
the candidates by idf-weighted phrase evidence (``phrase_scores``). A
decisive stage 1 — threshold plus margin — resolves the question
without touching the embedder (the optional two-stage fast path). When
stage 1 cannot decide, stage 2 scores the candidates by embedding
cosine to per-method phrase centroids; with no embedder (or a
zero-vector question) the stage 1 phrase scores stand and the run is
degraded. The decision rule is threshold plus margin; both ride the
``analysis.routing`` settings block, with 0.55 and 0.10 as the pinned
defaults. The idf weighting is the per-row calibration of the evidence:
each matched phrase is scaled by its register-wide specificity, so a
question that is one seeded phrase resolves on its own evidence (issue
#447) and a phrase shared by many rows can never fake confidence. A
register-derived per-row threshold gate was analyzed and rejected: any
margin-satisfying winner must hold a phrase no other candidate row
authors (df 1, idf 1.0), so its score is at least 1.0 and clears any
bar at or below the base threshold — the margin rule already carries
the per-row semantics (semantic-router per-route ``score_threshold``).

The module is pure: no LLM, no config loading, no op-layer imports. The
embedder arrives as a parameter and only its ``embed(text) -> list[float]``
call is used (``OllamaEmbedder`` and the deterministic fakes qualify; an
async-only embedder must be bridged by the caller). Same question, same
register, same output — always.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from corpus_kb.analysis.method_register import MethodRecommendation, MethodRegister
from corpus_kb.analysis.method_register_rubric import rank_methods
from corpus_kb.analysis.question_intent import QuestionIntent
from corpus_kb.analysis.question_router_centroids import (
    RoutingEmbedder,
    centroids_for,
    cosine,
    normalize_text,
    phrase_scores,
    usable_vector,
)
from corpus_kb.analysis.settings import (
    DEFAULT_ROUTING_CONFIDENCE_THRESHOLD,
    DEFAULT_ROUTING_MARGIN,
)

DEFAULT_CONFIDENCE_THRESHOLD = DEFAULT_ROUTING_CONFIDENCE_THRESHOLD
DEFAULT_MARGIN = DEFAULT_ROUTING_MARGIN
# Exact-arithmetic gap equality (say 7/10 - 6/10 = 0.1) must pass the
# margin check; float subtraction lands just below, so allow this slack.
_MARGIN_EPS = 1e-9
TOP_SCORE_ROWS = 10
TOP_READINGS = 3
RANKED_CAP = 5
DEGRADED_NOTE = "degraded"


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
    to the pinned defaults. Stage 1 (idf-weighted phrase evidence)
    resolves a decisive question without the embedder; ``embedder=None``
    (or a zero-vector question embedding) only degrades the run when
    stage 1 cannot decide. ``intent.axes`` stays all-``None`` here.
    """
    if not isinstance(question, str):
        raise TypeError(
            f"question: expected text, got {type(question).__name__}. "
            "Pass the question as a string."
        )
    threshold = _setting(settings, "confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD)
    margin = _setting(settings, "margin", DEFAULT_MARGIN)
    normalized = normalize_text(question)

    hits, phrase_counts, names = _phrase_pass(normalized, register)
    candidate_ids = list(hits) or list(phrase_counts)
    if not candidate_ids:
        # No row carries phrases, so nothing can score. Honest dead end;
        # nothing was embedded either, so the trace stays in phrase mode.
        return RouteResult(
            status="insufficient",
            degraded=False,
            intent=QuestionIntent(question=normalized, status="insufficient"),
            ranked=[],
            trace=RoutingTrace(mode="phrase", threshold=threshold, margin=margin, scores=[]),
        )

    # Stage 1: idf-weighted phrase evidence decides when it can. Stage 2
    # (embedding cosine) runs only on a tie or a shortfall; an unusable
    # stage 2 leaves the stage 1 scores standing, run degraded.
    scores = phrase_scores(candidate_ids, hits, register)
    degraded, note, mode = False, None, "phrase"
    if not _decisive(scores, threshold, margin):
        if embedder is None:
            degraded, note = True, DEGRADED_NOTE
        else:
            question_vector = usable_vector(embedder.embed(normalized))
            if question_vector is None:
                degraded, note = True, DEGRADED_NOTE
            else:
                scores = _embedding_scores(register, embedder, candidate_ids, question_vector)
                mode = "embedding"

    decided = _decisive(scores, threshold, margin)
    ordered = sorted(scores, key=lambda method_id: (-scores[method_id], method_id))
    status = "resolved" if decided else "ambiguous"
    readings = [] if decided else _build_readings(ordered, hits, names)
    candidate_set = set(candidate_ids)
    ranked = [rec for rec in rank_methods(register) if rec.id in candidate_set][:RANKED_CAP]
    intent = QuestionIntent(
        question=normalized,
        status=status,
        confidence=min(1.0, scores[ordered[0]]),
        method_id=ordered[0] if decided else None,
        ranked_method_ids=ordered,
        readings=readings,
        note=note,
    )
    trace = _build_trace(mode, threshold, margin, scores, ordered)
    return RouteResult(status=status, degraded=degraded, intent=intent, ranked=ranked, trace=trace)


def _decisive(scores: dict[str, float], threshold: float, margin: float) -> bool:
    """True when the top score clears the threshold by at least the margin."""
    ordered = sorted(scores, key=lambda method_id: (-scores[method_id], method_id))
    top_score = scores[ordered[0]]
    second_score = scores[ordered[1]] if len(ordered) > 1 else None
    return top_score >= threshold and (
        second_score is None or top_score - second_score >= margin - _MARGIN_EPS
    )


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
    mode: str,
    threshold: float,
    margin: float,
    scores: dict[str, float],
    ordered: list[str],
) -> RoutingTrace:
    """The stable score trace: top rows, score desc then id asc."""
    return RoutingTrace(
        mode=mode,
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
        matched = [phrase for phrase in phrases if normalize_text(phrase) in normalized]
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
