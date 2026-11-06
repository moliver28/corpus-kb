"""Per-method phrase centroids for the question router (wave 2).

The router module owns the decision; this module owns the vector math:
the phrase-material fingerprint, the per-register centroid cache, and
the small deterministic helpers (mean, unit-norm, cosine). Kept separate
so the decision logic stays under the 250-line soft limit.

The cache key binds the register version AND the phrase material AND the
vector width AND the embedder model name, so a version bump, an edited
register, or another embedder can never reuse stale centroids. Entries
are computed on demand, one method at a time.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from corpus_kb.analysis.method_register import MethodRegister
    from corpus_kb.analysis.question_router import RoutingEmbedder

_CENTROID_CACHE: dict[tuple[str, str, int, str], dict[str, list[float]]] = {}


def phrase_fingerprint(register: MethodRegister) -> str:
    """A stable digest of the phrase material behind the centroids."""
    parts: list[str] = []
    for record in register.methods:
        joined = "\x1e".join(sorted(record.question_phrases or []))
        parts.append(f"{record.id}\x1f{joined}")
    return hashlib.sha256("\x1d".join(parts).encode("utf-8")).hexdigest()


def centroids_for(
    register: MethodRegister,
    embedder: RoutingEmbedder,
    dims: int,
    wanted: list[str],
) -> dict[str, list[float]]:
    """Per-method phrase centroids for the wanted methods, cached.

    The centroid is the L2-normalized mean of the row's phrase vectors.
    Phrase vectors of a foreign width are skipped, so a misbehaving
    embedder degrades to a zero centroid instead of crashing.
    """
    key = (
        register.register_version,
        phrase_fingerprint(register),
        dims,
        str(getattr(embedder, "model", "")),
    )
    cache = _CENTROID_CACHE.setdefault(key, {})
    wanted_set = set(wanted)
    for record in register.methods:
        if record.id not in wanted_set or record.id in cache:
            continue
        phrases = record.question_phrases or []
        vectors = [vector for vector in map(embedder.embed, phrases) if len(vector) == dims]
        cache[record.id] = normalized(mean(vectors)) if vectors else [0.0] * dims
    return cache


def mean(vectors: list[list[float]]) -> list[float]:
    """The coordinate-wise mean. Same input order, same float, always."""
    count = len(vectors)
    dim = len(vectors[0])
    return [sum(vector[index] for vector in vectors) / count for index in range(dim)]


def normalized(vector: list[float]) -> list[float]:
    """Scale to unit length; a zero vector stays zero."""
    norm = sum(value * value for value in vector) ** 0.5
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def usable_vector(vector: list[float]) -> list[float] | None:
    """The question vector, or ``None`` when it is empty or all zero.

    A zero-vector embedding is the degraded-embedder signal; it never
    fakes confidence.
    """
    if vector and not all(value == 0.0 for value in vector):
        return vector
    return None


def phrase_scores(
    candidate_ids: list[str],
    hits: dict[str, list[str]],
    phrase_counts: dict[str, int],
) -> dict[str, float]:
    """Hit share over the authored phrase count (the degraded mode)."""
    return {
        method_id: len(hits.get(method_id, [])) / phrase_counts[method_id]
        for method_id in candidate_ids
    }


def cosine(question_vector: list[float], centroid: list[float]) -> float:
    """Cosine of two unit vectors: their plain dot product."""
    return sum(left * right for left, right in zip(question_vector, centroid, strict=True))
