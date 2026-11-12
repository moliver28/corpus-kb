"""Shared deterministic infrastructure for the U29 inductive-coding baselines.

Three sibling benchmark scripts (GATOS-style, TnT-LLM-style, LLooM-style)
import from here so the corpus loading, tokenization, TF-IDF, greedy
agglomerative clustering, adjusted Rand index, and report scaffolding are
defined exactly once and behave identically across baselines. The numeric
metrics live in ``_inductive_metrics`` and are re-exported here so the
import surface of this module stays unchanged.

Everything is stdlib-only, offline, and deterministic given a seed: no LLM
calls, no network, no Postgres. Results are labeled local-machine recovery
baselines per the repo benchmark convention (see
``docs/research/benchmarks/inductive-baselines.md``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from _inductive_metrics import (
    adjusted_rand_index,
    cosine,
    distinctive_keywords,
    greedy_agglomerative,
    tfidf_vectors,
)

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

__all__ = [
    "LOCAL_MACHINE_LABEL",
    "MIN_DOCS",
    "RECOVERY_BASELINE_CAVEAT",
    "STATUS_INSUFFICIENT_DATA",
    "STATUS_OK",
    "Document",
    "adjusted_rand_index",
    "cosine",
    "distinctive_keywords",
    "greedy_agglomerative",
    "insufficient_report",
    "load_corpus",
    "load_planted_themes",
    "markdown_report",
    "tfidf_vectors",
    "tokenize",
]


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
