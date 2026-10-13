"""Pure core of late chunking (no torch / transformers imports).

Every function here operates on plain Python data (lists of floats, int
tuples, strings) so the pooling / windowing / char-span mapping math is
testable WITHOUT the heavy ``latechunk`` stack — the tests drive it directly
with deterministic fake token vectors. ``late_chunk.LateChunkEmbedder``
delegates the document path to :func:`build_document_chunk_vectors` and only
owns the torch/transformers side (tokenization + forward passes); the
research path's turn-boundary windowing (:func:`macro_windows`) and tensor
span pooling (:func:`pool_span`) live here too for the same reason.

True late chunking contract: every chunk vector is mean-pooled from
token-level hidden states produced by a forward pass over the WHOLE
document (or the sliding window(s) covering the chunk), so identical chunk
texts at different document positions get DIFFERENT vectors — context
leaks in. Mean-pool + L2-normalize at this boundary with the shared
zero-vector no-op contract (``rag.embedder.l2_normalize``).
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import dataclass

from corpus_kb.rag.embedder import assert_unit_or_zero, l2_normalize
from corpus_kb.rag.embedders._latechunk_types import TokenStates

# qwen3-embedding serves 32k tokens; 16384-token windows with 2048-token
# stride overlap keep every forward well inside context while capping
# recompute. Constructor-tunable on LateChunkEmbedder for other families.
DEFAULT_WINDOW_TOKENS = 16_384
DEFAULT_STRIDE_TOKENS = 2_048

# chars/4 approximation when the tokenizer cannot serve offset mappings
# (slow tokenizers); used consistently for mapping AND window text slicing.
CHARS_PER_TOKEN = 4

# v5 5.3 (research path): Qwen3-Embedding accepts 32k tokens; keep a safety
# margin under it. Turn-boundary macro-windows stay under this bound.
MAX_MACRO_TOKENS = 30_000
MACRO_OVERLAP_RATIO = 0.1


@dataclass(frozen=True)
class LateChunkSpan:
    """One chunk's late-chunking vector plus provenance for honesty reports."""

    vector: list[float]
    token_start: int
    token_end: int
    windows_used: int


@dataclass(frozen=True)
class SpanVectors:
    """The three retrieval views of one exchange, from one forward pass."""

    question: list[float]
    qa: list[float]
    answer: list[float]


def macro_windows(turn_tokens: list[int]) -> list[tuple[int, int]]:
    """Turn-boundary macro-split (v5 5.3, research path).

    Greedy windows of at most MAX_MACRO_TOKENS tokens (a single oversized
    turn forms its own window and is truncated at encode time); consecutive
    windows overlap by ~10% of turns. Returns half-open (lo, hi) turn-index
    ranges.
    """
    n = len(turn_tokens)
    if n == 0:
        return []
    windows: list[tuple[int, int]] = []
    lo = 0
    while lo < n:
        total = 0
        hi = lo
        while hi < n and (hi == lo or total + turn_tokens[hi] <= MAX_MACRO_TOKENS):
            total += turn_tokens[hi]
            hi += 1
        windows.append((lo, hi))
        if hi >= n:
            break
        overlap = min(max(1, round((hi - lo) * MACRO_OVERLAP_RATIO)), hi - lo - 1)
        lo = hi - overlap
    return windows


def pool_span(hidden: TokenStates, start: int, end: int, seq_len: int) -> list[float]:
    """Mean-pool token states over [start, end) clamped into [1, seq_len).

    A span past the truncation point pools the final token instead of an
    empty slice; normalization keeps the zero-vector no-op contract.
    """
    lo = max(1, min(start, seq_len - 1))
    hi = min(end, seq_len - 1)
    if hi <= lo:
        hi = min(lo + 1, seq_len)
    span = hidden[0, lo:hi, :]
    return l2_normalize(span.mean(dim=0).tolist())


def plan_windows(n_tokens: int, window: int, stride: int) -> list[tuple[int, int]]:
    """Sliding windows of <= ``window`` tokens covering [0, n_tokens).

    Consecutive windows overlap by ``stride`` tokens. A document fitting in
    one window takes the fast path (single window, no recompute). Returns
    half-open (lo, hi) token-index ranges.
    """
    if n_tokens <= 0:
        return []
    window = max(1, window)
    stride = max(1, min(stride, window))
    if n_tokens <= window:
        return [(0, n_tokens)]
    windows: list[tuple[int, int]] = []
    lo = 0
    while lo < n_tokens:
        hi = min(lo + window, n_tokens)
        windows.append((lo, hi))
        if hi >= n_tokens:
            break
        lo = hi - stride
    return windows


def char_to_token_spans(
    char_spans: list[tuple[int, int]],
    token_offsets: list[tuple[int, int]] | None,
    text_length: int,
) -> list[tuple[int, int]]:
    """Map half-open char spans to half-open token spans.

    With ``token_offsets`` (per-token (start, end) char offsets from the ONE
    full-document tokenization) the mapping is precise: a chunk spans every
    token overlapping its char range. Without offsets (tokenizer lacks the
    mapping) the chars/4 heuristic is applied consistently. Char spans are
    clamped into the document; empty spans widen to one character.
    """
    if not token_offsets:
        return [
            (max(0, start // CHARS_PER_TOKEN), max(0, end // CHARS_PER_TOKEN))
            for start, end in char_spans
        ]
    ends = [offset[1] for offset in token_offsets]
    starts = [offset[0] for offset in token_offsets]
    spans: list[tuple[int, int]] = []
    for raw_start, raw_end in char_spans:
        cs = max(0, min(raw_start, text_length))
        ce = max(0, min(raw_end, text_length))
        if ce <= cs:
            ce = min(cs + 1, text_length)
            if ce <= cs:
                cs, ce = max(0, text_length - 1), text_length
        # First token ending after cs / last token starting before ce.
        first = bisect_right(ends, cs)
        last = bisect_left(starts, ce) - 1
        if last < first:
            last = first
        spans.append((first, min(last + 1, len(token_offsets))))
    return spans


def window_text(
    document_text: str, token_offsets: list[tuple[int, int]] | None, lo: int, hi: int
) -> str:
    """The document substring covering tokens [lo, hi) of the window plan."""
    if token_offsets and lo < len(token_offsets):
        start_char = token_offsets[lo][0]
        end_char = token_offsets[min(hi, len(token_offsets)) - 1][1]
        return document_text[start_char:end_char]
    return document_text[lo * CHARS_PER_TOKEN : hi * CHARS_PER_TOKEN]


def pool_token_rows(rows: list[list[float]], start: int, end: int) -> list[float]:
    """Mean-pool plain token rows over [start, end), L2-normalized.

    The zero-vector no-op contract holds: all-zero rows pool to a zero
    vector instead of dividing by zero. Degenerate inputs (no rows) return
    the empty vector the caller's zero-check treats as degraded.
    """
    if not rows:
        return []
    lo = max(0, min(start, len(rows) - 1))
    hi = max(lo + 1, min(end, len(rows)))
    picked = rows[lo:hi]
    means = [sum(col) / len(picked) for col in zip(*picked, strict=True)]
    return l2_normalize(means)


def merge_poolings(poolings: list[list[float]]) -> list[float]:
    """Element-wise mean of per-window poolings, re-normalized.

    A chunk covered by >1 window gets the mean of its per-window poolings
    (unit vectors average to a shorter vector; the boundary contract is a
    unit norm, hence the renormalize). Zero poolings stay zero.
    """
    if not poolings:
        return []
    if len(poolings) == 1:
        return poolings[0]
    merged = [sum(p[i] for p in poolings) / len(poolings) for i in range(len(poolings[0]))]
    return l2_normalize(merged)


def build_document_chunk_vectors(
    *,
    document_text: str,
    chunk_char_spans: list[tuple[int, int]],
    n_tokens: int,
    token_offsets: list[tuple[int, int]] | None,
    window_forward: Callable[[str], list[list[float]]],
    window_tokens: int = DEFAULT_WINDOW_TOKENS,
    stride_tokens: int = DEFAULT_STRIDE_TOKENS,
    special_prefix_tokens: int = 1,
) -> list[LateChunkSpan]:
    """Late-chunk a whole document: map spans, forward windows, pool spans.

    ``window_forward`` turns a window's text into plain token hidden rows
    (including the tokenizer's special prefix/suffix rows — BERT-style
    tokenizers prepend one, hence ``special_prefix_tokens``). Each chunk
    span is pooled from EVERY window covering it and the per-window poolings
    are averaged; ``windows_used`` records how many contributed.
    """
    if not chunk_char_spans:
        return []
    token_spans = char_to_token_spans(chunk_char_spans, token_offsets, len(document_text))
    windows = plan_windows(n_tokens, window_tokens, stride_tokens)
    if not windows:
        # Zero-token document: nothing to forward — emit the degraded marker
        # (empty vectors pass the zero-vector no-op contract) instead of
        # inventing a window.
        return [
            LateChunkSpan(vector=[], token_start=s, token_end=e, windows_used=0)
            for s, e in token_spans
        ]
    rows_per_window: list[list[list[float]]] = [
        window_forward(window_text(document_text, token_offsets, lo, hi)) for lo, hi in windows
    ]
    results: list[LateChunkSpan] = []
    for token_start, token_end in token_spans:
        pools: list[list[float]] = []
        for (w_lo, w_hi), rows in zip(windows, rows_per_window, strict=True):
            if token_start >= w_hi or token_end <= w_lo:
                continue
            local_start = max(token_start, w_lo) - w_lo + special_prefix_tokens
            local_end = min(token_end, w_hi) - w_lo + special_prefix_tokens
            pools.append(pool_token_rows(rows, local_start, local_end))
        if not pools:
            # Span past every (truncated) window: pool the final token of the
            # last window — same honest clamp as pool_span past truncation.
            rows = rows_per_window[-1]
            pools.append(pool_token_rows(rows, max(0, len(rows) - 1), len(rows)))
        vector = merge_poolings(pools)
        assert_unit_or_zero(vector)
        results.append(
            LateChunkSpan(
                vector=vector,
                token_start=token_start,
                token_end=token_end,
                windows_used=len(pools),
            )
        )
    return results
