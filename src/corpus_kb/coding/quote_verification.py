"""Deterministic quote verification.

Classifies every coder assignment's quote as one of:

  - "exact": a verbatim substring of the participant segment text.
  - "normalized_match": not verbatim, but after normalization (Unicode NFC,
    curly quotes -> straight quotes, the unicode ellipsis character -> three
    literal dots, whitespace-run collapse; comparison is case-preserving,
    never lowercased) it matches, AND exactly one span in the original
    segment text maps back to that normalized match. In that case the quote
    is auto-repaired to the exact transcript substring.
  - "not_found": no normalized match at all, or more than one candidate span
    (ambiguous) -- an ambiguous match is NEVER auto-picked, it is always
    reported not_found so a human/reconciler resolves it.

CRITICAL / attribution firewall: matching is against the participant
segment's `text` field only. Any `context_text` (the preceding moderator
turn) is never consulted here. This is what makes moderator context safe
to show coders without it ever becoming citable evidence.
"""

from __future__ import annotations

import difflib
import unicodedata
from typing import Literal, cast

_CURLY_QUOTES = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u2032": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "‟": '"',
    "″": '"',
}
_ELLIPSIS = {"…": "..."}


def _stage_nfc(text: str) -> tuple[str, list[int]]:
    """Stage 1: Unicode NFC normalization.

    Returns (nfc_text, bounds) where bounds[k] is the index into `text` that
    nfc_text[k] originated from. `bounds` has len(nfc_text) + 1 entries; the
    final entry is len(text) (the end-of-string boundary).
    """
    nfc_text = unicodedata.normalize("NFC", text)
    if nfc_text == text:
        return nfc_text, list(range(len(text) + 1))

    sm = difflib.SequenceMatcher(a=text, b=nfc_text, autojunk=False)
    bounds: list[int | None] = [None] * (len(nfc_text) + 1)
    for tag, i1, _, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(j1, j2):
                bounds[k] = i1 + (k - j1)
        else:
            for k in range(j1, j2):
                bounds[k] = i1
    bounds[len(nfc_text)] = len(text)
    last = 0
    for k in range(len(bounds)):
        if bounds[k] is None:
            bounds[k] = last
        else:
            last = bounds[k]
    return nfc_text, cast(list[int], bounds)


def _stage_charmap(text: str) -> tuple[str, list[int]]:
    """Stage 2: curly quotes -> straight quotes, unicode ellipsis -> '...'.

    One-to-one or one-to-many per character; returns (out_text, bounds)
    with bounds[k] = index into `text` (len(out_text) + 1 entries).
    """
    out_chars = []
    bounds = []
    for idx, ch in enumerate(text):
        repl = _CURLY_QUOTES.get(ch)
        if repl is None:
            repl = _ELLIPSIS.get(ch)
        if repl is None:
            repl = ch
        for c in repl:
            out_chars.append(c)
            bounds.append(idx)
    bounds.append(len(text))
    return "".join(out_chars), bounds


def _stage_wscollapse(text: str) -> tuple[str, list[int]]:
    """Stage 3: collapse runs of whitespace to a single space.

    Returns (out_text, bounds) with bounds[k] = index into `text`.
    """
    out_chars = []
    bounds = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            j = i
            while j < n and text[j].isspace():
                j += 1
            out_chars.append(" ")
            bounds.append(i)
            i = j
        else:
            out_chars.append(ch)
            bounds.append(i)
            i += 1
    bounds.append(n)
    return "".join(out_chars), bounds


def _compose(outer_bounds: list[int], inner_bounds: list[int]) -> list[int]:
    """outer_bounds maps stage-N positions -> stage-(N-1) positions.
    inner_bounds maps stage-(N-1) positions -> original positions.
    Returns bounds mapping stage-N positions -> original positions.
    """
    return [inner_bounds[b] for b in outer_bounds]


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Run the full normalization pipeline (NFC -> curly quotes/ellipsis ->
    whitespace collapse) on `text`.

    Returns (normalized_text, bounds) where bounds[k] is the original-text
    index corresponding to normalized_text[k]. Case-preserving throughout.
    """
    a_text, a_bounds = _stage_nfc(text)
    b_text, b_bounds = _stage_charmap(a_text)
    ab_bounds = _compose(b_bounds, a_bounds)
    c_text, c_bounds = _stage_wscollapse(b_text)
    abc_bounds = _compose(c_bounds, ab_bounds)
    return c_text, abc_bounds


def normalize_quote(text: str) -> str:
    """Normalize a short quote string for comparison: NFC, curly->straight,
    ellipsis->'...', whitespace-run collapse, then strip. Case preserving.
    Also strips trailing punctuation (dots, commas, etc.) that often appear
    when coders include an ellipsis to indicate continuation.
    """
    normalized, _ = normalize_with_map(text)
    normalized = normalized.strip()
    normalized = normalized.rstrip(".,;:!?…\"'")
    return normalized


def _find_all(haystack: str, needle: str) -> list[int]:
    """All (possibly overlapping) start indices of `needle` in `haystack`."""
    if not needle:
        return []
    positions = []
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 1
    return positions


def classify_quote(
    quote: str, chunk_text: str
) -> tuple[Literal["exact", "normalized_match", "not_found"], str]:
    """Classify a single quote against a chunk's text.

    Returns (status, repaired_quote) where status is one of:
      - "exact": verbatim substring found
      - "normalized_match": found after normalization, quote auto-repaired
      - "not_found": no match or ambiguous multi-span match

    The repaired_quote is the exact substring on success, empty string otherwise.
    """
    if not quote or not quote.strip():
        return ("not_found", "")

    if quote in chunk_text:
        return ("exact", quote)

    normalized_text, bounds = normalize_with_map(chunk_text)
    normalized_quote = normalize_quote(quote)

    if not normalized_quote:
        return ("not_found", "")

    positions = _find_all(normalized_text, normalized_quote)
    spans = set()
    for p in positions:
        end_pos = p + len(normalized_quote)
        start_is_boundary = p == 0 or bounds[p] != bounds[p - 1]
        end_is_boundary = end_pos == len(bounds) - 1 or bounds[end_pos] != bounds[end_pos - 1]
        if not (start_is_boundary and end_is_boundary):
            continue
        start = bounds[p]
        end = bounds[end_pos]
        spans.add((start, end))

    if len(spans) == 1:
        start, end = next(iter(spans))
        repaired = chunk_text[start:end]
        return ("normalized_match", repaired)

    return ("not_found", "")
