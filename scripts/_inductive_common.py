"""Shared deterministic infrastructure for the U29 inductive-coding baselines.

Three sibling benchmark scripts (GATOS-style, TnT-LLM-style, LLooM-style)
import from here so the corpus loading, tokenization, TF-IDF, greedy
agglomerative clustering, adjusted Rand index, and report scaffolding are
defined exactly once and behave identically across baselines.

Everything is stdlib-only, offline, and deterministic given a seed: no LLM
calls, no network, no Postgres. Results are labeled local-machine recovery
baselines per the repo benchmark convention (see
``docs/research/benchmarks/inductive-baselines.md``).
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

RECOVERY_BASELINE_CAVEAT = (
    "GATOS's validation uses synthetic planted themes, so results are "
    "recovery baselines, not proof of validity on real interviews."
)
LOCAL_MACHINE_LABEL = (
    "local-machine baseline on a synthetic fixture corpus; "
    "absolute numbers are not deployment expectations"
)
STATUS_OK = "ok"
STATUS_INSUFFICIENT_DATA = "insufficient_data"
MIN_DOCS = 8
_DEFAULT_STOPWORDS = frozenset(
    (
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "did",
        "do",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "out",
        "over",
        "she",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "too",
        "up",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "after",
        "before",
        "again",
        "still",
        "just",
        "once",
        "every",
        "until",
        "while",
        "about",
    )
)
_TOKEN_RE = re.compile(r"[a-z][a-z-]{2,}")


@dataclass(frozen=True)
class Document:
    """One corpus unit (a fixture interview file) with its token list."""

    doc_id: str
    name: str
    text: str
    tokens: tuple[str, ...] = field(default=())


def tokenize(text: str) -> tuple[str, ...]:
    """Lowercase word tokens, length >= 3, stopwords removed. Deterministic."""
    lowered = text.lower().replace("_", "-")
    return tuple(t for t in _TOKEN_RE.findall(lowered) if t not in _DEFAULT_STOPWORDS)


def load_corpus(input_dir: Path) -> list[Document]:
    """Load every ``*.md``/``*.txt`` file in ``input_dir`` (sorted by name)."""
    if not input_dir.is_dir():
        raise FileNotFoundError(f"input corpus directory does not exist: {input_dir}")
    docs: list[Document] = []
    for path in sorted(input_dir.iterdir()):
        if path.suffix.lower() not in {".md", ".txt"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        docs.append(
            Document(
                doc_id=path.stem,
                name=path.name,
                text=text,
                tokens=tokenize(text),
            )
        )
    if not docs:
        raise ValueError(f"no .md/.txt documents found in {input_dir}")
    return docs


def load_planted_themes(input_dir: Path) -> dict[str, object]:
    """Read the fixture labels file; returns {} when absent (unlabeled corpus)."""
    labels = input_dir / "planted_themes.json"
    if not labels.is_file():
        return {}
    loaded: object = json.loads(labels.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)  # fixture-controlled shape
    return loaded


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


def insufficient_report(
    benchmark: str, doc_count: int, min_docs: int, extra: dict[str, object] | None = None
) -> dict[str, object]:
    """The honest not-enough-corpus report (never fabricate numbers)."""
    report: dict[str, object] = {
        "benchmark": benchmark,
        "status": STATUS_INSUFFICIENT_DATA,
        "reason": (
            f"fixture corpus has {doc_count} documents; at least {min_docs} are "
            "needed for a meaningful inductive-coding run, so no numbers are reported"
        ),
        "corpus_status": STATUS_INSUFFICIENT_DATA,
        "document_count": doc_count,
        "label": LOCAL_MACHINE_LABEL,
        "recovery_baseline_caveat": RECOVERY_BASELINE_CAVEAT,
    }
    if extra:
        report.update(extra)
    return report


def markdown_report(title: str, data: dict[str, object]) -> str:
    """Minimal deterministic markdown rendering of a JSON report dict."""
    lines = [f"# {title}", ""]
    for key, value in data.items():
        rendered = json.dumps(value, indent=2, sort_keys=True)
        if isinstance(value, dict):
            lines.append(f"## {key.replace('_', ' ').title()}")
            lines.append("")
            lines.append("```json")
            lines.append(rendered)
            lines.append("```")
        else:
            lines.append(f"- **{key}**: {rendered}")
    lines.append("")
    return "\n".join(lines)
