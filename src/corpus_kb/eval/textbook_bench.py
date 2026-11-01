"""Deterministic quality metrics and CLI for the textbook bench (W0).

Pure metrics over parsed page items, planned chunks, and an independent
text layer; the per-book runner lives in ``textbook_bench_run``. Scores:

* page-text fidelity: mean per-page normalized edit distance against
  the independent text layer (layer scope; unparsed pages score 1.0).
* section F1: detected section titles vs PDF outline bookmarks.
* chunk stats: count, estimated tokens, budget conformance.
* page-attribution exactness: claimed page spans must equal the spans
  rebuilt from page texts.

Parse wall-clock is a separate field, never a correctness metric. The
JSON sorts keys and carries no timestamp unless ``--stamp`` is passed.

Run: ``python -m corpus_kb.eval.textbook_bench --pdf BOOK.pdf --parser pypdf``
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

from corpus_kb.chunking.hierarchy import CHARS_PER_TOKEN, estimate_tokens
from corpus_kb.eval.golden_fetch import default_dir, load_manifest
from corpus_kb.ingestion.base import PlannedChunk
from corpus_kb.ingestion.strategies.textbook_pages import PageItem

__all__ = [
    "attribution_report",
    "chunk_report",
    "main",
    "normalized_distance",
    "page_fidelity",
    "rebuild_pages",
    "section_f1",
]

#: The one join rule, shared with the assembly half (textbook_assemble).
_PAGE_JOIN = "\n\n"


def _squash(text: str) -> str:
    """One space per whitespace run: the metrics ignore line wrapping."""
    return re.sub(r"\s+", " ", text).strip()


def normalized_distance(left: str, right: str) -> float:
    """Levenshtein over the longer squashed text (exact two-row DP).

    1.0 when exactly one side is empty; 0.0 when both squash the same.
    """
    a, b = _squash(left), _squash(right)
    if a == b:
        return 0.0
    if not a or not b:
        return 1.0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
        previous = current
    return previous[-1] / max(len(a), len(b))


def page_fidelity(parsed: dict[int, str], layer: dict[int, str]) -> dict[str, float | int]:
    """Mean per-page normalized distance over the text layer's pages.

    The layer is the gold scope: a page it covers but the parser missed
    scores 1.0, so dropping pages cannot inflate the score. Parser
    pages without a layer counterpart only count in ``parsed_pages``.
    """
    pages = sorted(layer)
    if not pages:
        empty = {"compared_pages": 0, "mean_distance": 0.0, "missing_pages": 0}
        return {**empty, "parsed_pages": len(parsed)}
    distances = [
        normalized_distance(parsed[page], layer[page]) if page in parsed else 1.0 for page in pages
    ]
    return {
        "compared_pages": len(pages),
        "mean_distance": round(sum(distances) / len(pages), 6),
        "missing_pages": sum(1 for page in pages if page not in parsed),
        "parsed_pages": len(parsed),
    }


def section_f1(detected: list[str], gold: list[str]) -> dict[str, float | int]:
    """Set F1 between detected section titles and outline bookmark titles."""
    wanted = {_squash(title).casefold() for title in gold if _squash(title)}
    got = {_squash(title).casefold() for title in detected if _squash(title)}
    hit = len(wanted & got)
    precision = hit / len(got) if got else 0.0
    recall = hit / len(wanted) if wanted else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "detected_titles": len(got),
        "f1": round(f1, 6),
        "gold_titles": len(wanted),
        "precision": round(precision, 6),
        "recall": round(recall, 6),
    }


def chunk_report(chunks: list[PlannedChunk], budget_chars: int) -> dict[str, float | int]:
    """Chunk count, mean and max estimated tokens, budget conformance."""
    tokens = [estimate_tokens(chunk.text) for chunk in chunks]
    target = max(1, budget_chars // CHARS_PER_TOKEN)
    within = sum(token <= target for token in tokens)
    return {
        "budget_conformance": round(within / len(tokens), 6) if tokens else 0.0,
        "budget_target_tokens": target,
        "count": len(chunks),
        "max_tokens": max(tokens) if tokens else 0,
        "mean_tokens": round(sum(tokens) / len(tokens), 6) if tokens else 0.0,
    }


def rebuild_pages(items: list[PageItem]) -> tuple[str, list[tuple[int, int, int]]]:
    """The document text rebuilt from page texts, plus page spans.

    Blank items drop (the ``section_blocks`` rule); a span is a
    ``(page, start, end)`` tuple into the rebuilt text.
    """
    by_page: dict[int, list[str]] = {}
    for item in items:
        if item.text.strip():
            by_page.setdefault(item.page, []).append(item.text)
    parts: list[str] = []
    spans: list[tuple[int, int, int]] = []
    cursor = 0
    for page in sorted(by_page):
        text = _PAGE_JOIN.join(by_page[page])
        parts.append(text)
        spans.append((page, cursor, cursor + len(text)))
        cursor += len(text) + len(_PAGE_JOIN)
    return _PAGE_JOIN.join(parts), spans


def attribution_report(
    chunks: list[PlannedChunk], spans: list[tuple[int, int, int]], join_matches: bool
) -> dict[str, object]:
    """The share of chunks whose claimed page span equals the true span.

    A chunk's true span is the pages whose rebuilt text overlaps the
    chunk's char range; a split piece claiming its whole block's pages
    fails here, which is what wave W2-A closes.
    """
    exact = 0
    examples: list[dict[str, object]] = []
    for chunk in chunks:
        start = chunk.source_start_char or 0
        end = chunk.source_end_char or 0
        covered = [
            page for page, span_start, span_end in spans if span_start < end and span_end > start
        ]
        true_span = (min(covered), max(covered)) if covered else (0, 0)
        claimed = (_page_meta(chunk, "page_start"), _page_meta(chunk, "page_end"))
        # No covered page is always a mismatch, even at (0, 0) == (0, 0).
        if covered and claimed == true_span:
            exact += 1
        elif len(examples) < 5:
            note = {"claimed": list(claimed), "index": chunk.index, "true": list(true_span)}
            examples.append(note)
    count = len(chunks)
    return {
        "chunks": count,
        "exact": exact,
        "exact_rate": round(exact / count, 6) if count else 0.0,
        "join_matches_document_text": join_matches,
        "mismatch_examples": examples,
    }


def _page_meta(chunk: PlannedChunk, key: str) -> int:
    """The int page coordinate from chunk metadata (0 when not an int)."""
    value = chunk.metadata.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def _book_paths(args: argparse.Namespace) -> list[Path]:
    """The books to run: explicit PDFs, golden books on disk, a books dir."""
    paths = [Path(raw) for raw in (args.pdf or [])]
    if args.golden:
        have = {path.name for path in paths}
        for book in load_manifest():
            local = default_dir() / book.filename
            if not local.exists():
                print(f"golden book missing (run golden_fetch): {book.filename}", file=sys.stderr)
            elif local.name not in have:
                paths.append(local)
    if args.books_dir:
        paths.extend(sorted(args.books_dir.glob("*.pdf")))
    return paths


def main(argv: list[str] | None = None) -> int:
    """Run the bench over the given books and print one JSON report."""
    parser = argparse.ArgumentParser(description="Score textbook parsing quality over PDF books.")
    parser.add_argument("--pdf", action="append", help="A book to score; repeatable.")
    parser.add_argument("--golden", action="store_true", help="Add the pinned golden books.")
    parser.add_argument("--books-dir", type=Path, help="A directory of extra local PDFs.")
    parser.add_argument("--parser", choices=("docling", "pypdf"), default="pypdf")
    parser.add_argument("--window-pages", type=int, default=50, help="Pages per window.")
    parser.add_argument("--max-pages", type=int, default=0, help="Cap the span (0 = no cap).")
    parser.add_argument("--cache-dir", type=Path, help="Window cache (default: temp dir).")
    parser.add_argument("--out", type=Path, help="Write the JSON report here.")
    parser.add_argument("--stamp", action="store_true", help="Add a UTC run stamp.")
    args = parser.parse_args(argv)
    paths = _book_paths(args)
    if not paths:
        print("no books to run: pass --pdf, --golden, or --books-dir", file=sys.stderr)
        return 2
    # Deferred: the runner imports this module's metric functions.
    from corpus_kb.eval.textbook_bench_run import run_book

    def score(cache: Path) -> list[dict[str, object]]:
        return [run_book(p, args.parser, cache, args.window_pages, args.max_pages) for p in paths]

    if args.cache_dir:
        books = score(args.cache_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="textbook-bench-") as tmp:
            books = score(Path(tmp))
    payload: dict[str, object] = {"books": books, "parser": args.parser}
    if args.stamp:
        payload["stamp_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"report written to {args.out}")
    else:
        print(text)
    failed = sum(1 for book in books if "error" in book)
    if failed == len(books):
        print("every book failed; see the error fields in the report", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
