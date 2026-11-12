"""Deterministic text metrics for the U29 inductive-coding baselines.

TF-IDF, sparse cosine, greedy average-linkage clustering, distinctive
keyword ranking, and the adjusted Rand index. Everything is stdlib-only,
offline, and deterministic. Shared by the three sibling benchmark
scripts through ``_inductive_common`` (which re-exports these names).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _inductive_common import Document


def tfidf_vectors(docs: Sequence[Document]) -> list[dict[str, float]]:
    """Pure-Python TF-IDF: log(1 + tf) * idf, L2-normalized per document."""
    doc_count: Counter[str] = Counter()
    for doc in docs:
        doc_count.update(set(doc.tokens))
    n_docs = len(docs)
    vectors: list[dict[str, float]] = []
    for doc in docs:
        tf: Counter[str] = Counter(doc.tokens)
        vec = {
            term: (1.0 + math.log(count)) * math.log((1.0 + n_docs) / (1.0 + df))
            for term, count in tf.items()
            for df in [doc_count[term]]
        }
        norm = math.sqrt(sum(weight * weight for weight in vec.values())) or 1.0
        vectors.append({term: weight / norm for term, weight in vec.items()})
    return vectors


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity over sparse dicts (any order, deterministic)."""
    if len(b) < len(a):
        a, b = b, a
    return sum(weight * b[term] for term, weight in a.items() if term in b)


def greedy_agglomerative(vectors: Sequence[dict[str, float]], threshold: float) -> list[int]:
    """Greedy average-linkage agglomerative clustering.

    Repeatedly merges the single most-similar pair of clusters while their
    average pairwise cosine is >= ``threshold``; greedy best-pair-first, the
    shape GATOS-style inductive coding uses before a topic-model pass names
    the clusters.
    """
    clusters: list[list[int]] = [[i] for i in range(len(vectors))]

    def linkage(ci: list[int], cj: list[int]) -> float:
        total = 0.0
        pairs = 0
        for i in ci:
            for j in cj:
                total += cosine(vectors[i], vectors[j])
                pairs += 1
        return total / pairs if pairs else 0.0

    while len(clusters) > 1:
        best: tuple[float, int, int] = (-1.0, -1, -1)
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                score = linkage(clusters[a], clusters[b])
                if score > best[0]:
                    best = (score, a, b)
        if best[0] < threshold:
            break
        _, a, b = best
        clusters[a] = sorted([*clusters[a], *clusters[b]])
        del clusters[b]
    assignment = [0] * len(vectors)
    for label, members in enumerate(clusters):
        for i in members:
            assignment[i] = label
    return assignment


def distinctive_keywords(
    members: Sequence[Document],
    background: Sequence[Document],
    top_k: int,
    min_member_docs: int = 1,
) -> list[str]:
    """Top ``top_k`` terms weighting member frequency against the corpus.

    ``min_member_docs`` restricts candidates to terms appearing in at least
    that many member documents (grounding a hypothesis in multiple units);
    when no term qualifies the restriction is dropped rather than returning
    fewer keywords than asked for.
    """
    member_tf: Counter[str] = Counter()
    member_df: Counter[str] = Counter()
    background_df: Counter[str] = Counter()
    for doc in members:
        member_tf.update(doc.tokens)
        member_df.update(set(doc.tokens))
    for doc in background:
        background_df.update(set(doc.tokens))
    n_background = max(len(background), 1)
    candidates = [
        term for term in member_tf if min_member_docs <= 1 or member_df[term] >= min_member_docs
    ] or list(member_tf)
    ranked = sorted(
        candidates,
        key=lambda term: (
            member_tf[term] * math.log((1.0 + n_background) / (1.0 + background_df[term])),
            term,
        ),
        reverse=True,
    )
    return ranked[:top_k]


def adjusted_rand_index(labels_a: Sequence[str], labels_b: Sequence[str]) -> float:
    """Pair-counting adjusted Rand index (Hubert-Arabie) over two partitions.

    1.0 = identical partitions, ~0.0 = chance-level agreement, negative =
    worse than chance.
    """
    if len(labels_a) != len(labels_b) or not labels_a:
        raise ValueError("ARI needs two equal-length non-empty label sequences")
    sizes_a: Counter[str] = Counter(labels_a)
    sizes_b: Counter[str] = Counter(labels_b)
    sum_a = sum(math.comb(size, 2) for size in sizes_a.values())
    sum_b = sum(math.comb(size, 2) for size in sizes_b.values())
    total_pairs = math.comb(len(labels_a), 2)
    if total_pairs == 0:
        return 1.0
    observed = 0
    for i in range(len(labels_a)):
        for j in range(i + 1, len(labels_a)):
            if labels_a[i] == labels_a[j] and labels_b[i] == labels_b[j]:
                observed += 1
    expected = sum_a * sum_b / total_pairs
    max_index = (sum_a + sum_b) / 2.0
    if math.isclose(max_index, expected):
        return 1.0
    return (observed - expected) / (max_index - expected)
