"""Deterministic quality metrics and CLI for the textbook bench (W0).

The metrics are pure functions over parsed page items, planned chunks,
and an independent text layer; the per-book runner that produces those
inputs lives in ``textbook_bench_run``. The four scores:

* page-text fidelity: mean per-page normalized edit distance between
  the parser's page text and the independent text layer.
* section F1: detected section titles vs PDF outline bookmarks, the
  structure gold.
* chunk stats: chunk count, mean and max estimated tokens, and the
  budget-conformance rate at the production token budget.
* page-attribution exactness: the document text rebuilt from page
  texts with the assembly join rule, and every chunk's claimed page
  span must equal the span it truly covers.

Wall-clock parse time is a separate field, never part of a correctness
metric. The JSON report sorts keys and carries no timestamp unless
``--stamp`` is passed, so harnesses can snapshot it.

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
    """Levenshtein distance over the longer squashed text (exact two-row DP).

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
    """Mean per-page normalized distance over the pages both sides cover."""
    pages = sorted(set(parsed) & set(layer))
    if not pages:
        return {"compared_pages": 0, "mean_distance": 0.0, "parsed_pages": len(parsed)}
    total = sum(normalized_distance(parsed[page], layer[page]) for page in pages)
    return {
        "compared_pages": len(pages),
        "mean_distance": round(total / len(pages), 6),
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
    """The document text rebuilt from page texts, plus each page's span.

    The independent audit path: blank items drop (the rule
    ``section_blocks`` applies too), the rest join per page, then across
    pages, with the assembly join rule. A span is ``(page, start, end)``.
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
    chunk's char range. A split piece that claims its whole block's
    pages fails this audit; that gap is what wave W2-A closes.
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
        if claimed == true_span:
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
            if local.exists():
                if local.name not in have:
                    paths.append(local)
            else:
                miss = f"golden book not on disk (run golden_fetch): {book.filename}"
                print(miss, file=sys.stderr)
    if args.books_dir:
        paths.extend(sorted(args.books_dir.glob("*.pdf")))
    return paths


def main(argv: list[str] | None = None) -> int:
    """Run the bench over the given books and print one JSON report."""
    parser = argparse.ArgumentParser(description="Score textbook parsing quality over PDF books.")
    parser.add_argument("--pdf", action="append", help="A book to score; repeat for several.")
    parser.add_argument(
        "--golden", action="store_true", help="Add the pinned golden books on disk."
    )
    parser.add_argument(
        "--books-dir", type=Path, default=None, help="A directory of extra local PDFs."
    )
    parser.add_argument("--parser", choices=("docling", "pypdf"), default="pypdf")
    parser.add_argument("--window-pages", type=int, default=50, help="Pages per parse window.")
    parser.add_argument(
        "--max-pages", type=int, default=0, help="Cap the parsed span (0 = no cap)."
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=None, help="Window cache (default: temp dir)."
    )
    parser.add_argument("--out", type=Path, default=None, help="Write the JSON report here.")
    parser.add_argument("--stamp", action="store_true", help="Add a UTC run stamp to the report.")
    args = parser.parse_args(argv)
    paths = _book_paths(args)
    if not paths:
        print("no books to run: pass --pdf, --golden, or --books-dir", file=sys.stderr)
        return 2
    # Deferred: the runner imports this module's metric functions.
    from corpus_kb.eval.textbook_bench_run import run_book

    cache_dir = args.cache_dir or Path(tempfile.mkdtemp(prefix="textbook-bench-"))
    books = [
        run_book(path, args.parser, cache_dir, args.window_pages, args.max_pages) for path in paths
    ]
    payload: dict[str, object] = {"books": books, "parser": args.parser}
    if args.stamp:
        payload["stamp_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"report written to {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
