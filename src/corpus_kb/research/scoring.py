"""Three-view cosine scoring: U P^T with L2-normalized rows (todo 14, v5 §8).

All vectors are normalized at the boundary; the unit-norm assert no-ops on
zero rows so degraded runs (no Ollama) keep passing. Scoring is streamed in
batches of ``SCORING_BATCH_ROWS`` so the full U matrix is never materialized.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

SCORING_BATCH_ROWS = 1024
UNIT_NORM_TOLERANCE = 0.02


class NonUnitVectorError(ValueError):
    """Raised when a non-zero vector is not unit-normalized at the boundary."""


def normalize_rows(matrix: np.ndarray) -> np.ndarray:
    """L2-normalize every row; zero rows stay zero."""
    if matrix.ndim != 2:
        raise ValueError("matrix must be 2-D")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    # Avoid divide-by-zero: replace zero norms with 1 in the divisor.
    safe = np.where(norms == 0, 1.0, norms)
    return matrix / safe


def assert_unit_or_zero_rows(matrix: np.ndarray, tol: float = UNIT_NORM_TOLERANCE) -> None:
    """Boundary assert: every row is unit norm OR all-zero.

    Degraded runs emit zero vectors and must skip-pass (house contract).
    """
    norms = np.linalg.norm(matrix, axis=1)
    for norm in norms:
        if norm == 0.0:
            continue
        if abs(norm - 1.0) > tol:
            raise NonUnitVectorError(f"expected unit-norm row, got norm {norm:.6f}")


def _as_matrix(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    m = np.array(vectors, dtype=np.float32)
    if m.ndim != 2:
        raise ValueError("vectors must be a sequence of same-length vectors")
    return m


def score_units_against_prototypes(
    unit_vectors: Sequence[Sequence[float]],
    prototype_vectors: Sequence[Sequence[float]],
) -> np.ndarray:
    """Max cosine of each unit against the prototype set.

    Args:
        unit_vectors: (n_units, dim) matrix or list of vectors.
        prototype_vectors: (n_prototypes, dim) matrix or list of vectors.

    Returns:
        (n_units,) array of max cosine similarities in [-1, 1].
    """
    unit_matrix = normalize_rows(_as_matrix(unit_vectors))
    proto_matrix = normalize_rows(_as_matrix(prototype_vectors))
    assert_unit_or_zero_rows(unit_matrix)
    assert_unit_or_zero_rows(proto_matrix)
    if unit_matrix.shape[1] != proto_matrix.shape[1]:
        raise ValueError(
            f"unit dim {unit_matrix.shape[1]} does not match prototype dim {proto_matrix.shape[1]}"
        )
    sims = np.clip(unit_matrix @ proto_matrix.T, -1.0, 1.0)
    return np.max(sims, axis=1)


def stream_scores(
    unit_vectors: Iterable[Sequence[float]],
    prototype_vectors: Sequence[Sequence[float]],
    batch_size: int = SCORING_BATCH_ROWS,
) -> Iterable[float]:
    """Stream max-prototype cosine scores without materialising all of U.

    Args:
        unit_vectors: Iterable of unit vectors (will be consumed in batches).
        prototype_vectors: Prototype matrix.
        batch_size: Rows per batch (default ``SCORING_BATCH_ROWS``).

    Yields:
        Max cosine score for each unit in input order.
    """
    batch: list[Sequence[float]] = []
    for vec in unit_vectors:
        batch.append(vec)
        if len(batch) >= batch_size:
            yield from score_units_against_prototypes(batch, prototype_vectors).tolist()
            batch = []
    if batch:
        yield from score_units_against_prototypes(batch, prototype_vectors).tolist()


@dataclass(frozen=True)
class UnitViewScores:
    """The three view scores for one unit against one code's prototype sets."""

    answer: float
    qa: float
    question: float


def score_unit_views(
    answer_vector: Sequence[float],
    qa_vector: Sequence[float],
    question_vector: Sequence[float],
    prototypes: Mapping[str, Sequence[Sequence[float]]],
) -> UnitViewScores:
    """Score one unit's three views against a code's per-view prototypes.

    Args:
        answer_vector: Answer embedding.
        qa_vector: Question+answer embedding.
        question_vector: Question embedding.
        prototypes: {"answer": [...], "qa": [...], "question": [...]}.

    Returns:
        UnitViewScores with max-over-prototype cosine per view.
    """
    return UnitViewScores(
        answer=_safe_score(answer_vector, prototypes.get("answer", [])),
        qa=_safe_score(qa_vector, prototypes.get("qa", [])),
        question=_safe_score(question_vector, prototypes.get("question", [])),
    )


def _safe_score(unit_vector: Sequence[float], prototypes: Sequence[Sequence[float]]) -> float:
    if not prototypes:
        return 0.0
    scores = score_units_against_prototypes([unit_vector], prototypes)
    return float(scores[0])


@dataclass(frozen=True)
class CodeScores:
    """All per-unit, per-view scores for a batch of units against one code."""

    answer: np.ndarray
    qa: np.ndarray
    question: np.ndarray


def score_code_batch(
    answer_vectors: Sequence[Sequence[float]],
    qa_vectors: Sequence[Sequence[float]],
    question_vectors: Sequence[Sequence[float]],
    prototypes: Mapping[str, Sequence[Sequence[float]]],
) -> CodeScores:
    """Score a batch of units against one code's per-view prototypes."""
    return CodeScores(
        answer=score_units_against_prototypes(answer_vectors, prototypes.get("answer", []))
        if prototypes.get("answer")
        else np.zeros(len(answer_vectors), dtype=np.float32),
        qa=score_units_against_prototypes(qa_vectors, prototypes.get("qa", []))
        if prototypes.get("qa")
        else np.zeros(len(qa_vectors), dtype=np.float32),
        question=score_units_against_prototypes(question_vectors, prototypes.get("question", []))
        if prototypes.get("question")
        else np.zeros(len(question_vectors), dtype=np.float32),
    )
