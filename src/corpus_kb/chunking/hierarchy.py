"""U48/P6: deterministic window packing over natural boundary segments.

The packing half of the multi-granularity hierarchy (see
``chunking/hierarchy_tree.py`` for the three-level tree it feeds):

* TOKENS — there is no model tokenizer in the repo's dependency set (the
  only one is the optional HF stack behind ``rag/embedders/late_chunk.py``,
  which needs torch/transformers). This module uses a deterministic stdlib
  estimate, ``estimate_tokens`` (~4 chars/token, the standard BPE rule of
  thumb). Determinism is the property the shadow index needs: the same
  input always yields the same windows for a fixed estimator; a model-true
  counter can be swapped in later without changing the algorithm.
* SEGMENTS — the caller supplies the path's natural boundary units (main
  RAG path: Unstructured elements with heading groups; research-grain
  path: speaker turns with exchange groups). The packer never splits a
  segment except when it alone exceeds the level target (hard split at
  whitespace with token overlap).
* OVERLAP — adjacent windows share at least ``overlap_tokens`` estimated
  tokens of text, carried as whole trailing segments (or exact text tails
  for hard splits).
* BOUNDARY SNAPPING — window edges land on segment boundaries by
  construction; callers feed the chunk/parent levels group-merged segments
  (``merge_groups``) so mid-level windows start at heading/qa_pair
  boundaries.

Pure stdlib; fully deterministic; no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Standard BPE rule of thumb (~4 characters per token). Documented estimate,
# NOT a model-true count — see the module docstring.
CHARS_PER_TOKEN = 4

_WORD_RE = re.compile(r"\S+\s*")


def estimate_tokens(text: str) -> int:
    """Deterministic stdlib token estimate (~4 chars/token), minimum 1."""
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


@dataclass(frozen=True)
class Segment:
    """One atomic unit at the source path's natural boundary grain."""

    text: str
    start_char: int
    end_char: int
    kind: str = "paragraph"
    group_id: str | None = None


def merge_groups(segments: list[Segment], group_kind: str) -> list[Segment]:
    """Merge consecutive segments sharing a group_id into kind-tagged super-segments.

    Used by the chunk/parent levels so windows snap to group boundaries
    (heading sections on the main path, qa_pairs on the research path).
    Ungrouped segments (``group_id is None``) pass through untouched.
    """
    merged: list[Segment] = []
    for seg in segments:
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and seg.group_id is not None
            and prev.group_id == seg.group_id
            and prev.kind == group_kind
        ):
            merged[-1] = Segment(
                text=f"{prev.text}\n\n{seg.text}",
                start_char=prev.start_char,
                end_char=seg.end_char,
                kind=group_kind,
                group_id=seg.group_id,
            )
        else:
            merged.append(
                Segment(
                    text=seg.text,
                    start_char=seg.start_char,
                    end_char=seg.end_char,
                    kind=group_kind if seg.group_id is not None else seg.kind,
                    group_id=seg.group_id,
                )
            )
    return merged


def split_oversized(
    segment: Segment,
    target_tokens: int,
    overlap_tokens: int,
) -> list[Segment]:
    """Hard-split a single oversized segment at whitespace with token overlap.

    Word-chunks (token + trailing whitespace) are packed greedily up to the
    target; each next piece rewinds trailing words worth at least
    ``overlap_tokens``. Piece char offsets stay aligned to the source.
    """
    word_spans = [(m.group(), m.start(), m.end()) for m in _WORD_RE.finditer(segment.text)]
    if not word_spans:
        return [segment]

    def _piece(start_idx: int, end_idx: int) -> Segment:
        text = segment.text[word_spans[start_idx][1] : word_spans[end_idx - 1][2]]
        return Segment(
            text=text,
            start_char=segment.start_char + word_spans[start_idx][1],
            end_char=segment.start_char + word_spans[end_idx - 1][2],
            kind="split",
            group_id=segment.group_id,
        )

    pieces: list[Segment] = []
    lo = 0
    n = len(word_spans)
    while lo < n:
        tokens = 0
        hi = lo
        while hi < n and (hi == lo or tokens + estimate_tokens(word_spans[hi][0]) <= target_tokens):
            tokens += estimate_tokens(word_spans[hi][0])
            hi += 1
        pieces.append(_piece(lo, hi))
        if hi >= n:
            break
        rewind = 0
        cut = hi
        while cut > lo and rewind < overlap_tokens:
            cut -= 1
            rewind += estimate_tokens(word_spans[cut][0])
        lo = cut
    return pieces


def pack_windows(
    segments: list[Segment],
    target_tokens: int,
    overlap_tokens: int,
) -> list[list[Segment]]:
    """Greedy window packing with segment-boundary snapping and token overlap.

    Whole segments are packed up to ``target_tokens``; a window closes when
    the next segment would overflow, and the next window opens with the
    closed window's trailing segments worth at least ``overlap_tokens`` (the
    whole tail when the closed window is smaller than the overlap). A single
    segment larger than the target becomes one hard-split window per piece
    (the pieces already carry the token overlap). A multi-segment window may
    exceed the target only by the unavoidable ``rewind + one atomic
    segment`` slack.
    """
    windows: list[list[Segment]] = []
    current: list[Segment] = []
    current_tokens = 0
    for seg in segments:
        seg_tokens = estimate_tokens(seg.text)
        if seg_tokens > target_tokens:
            if current:
                windows.append(current)
                current = []
                current_tokens = 0
            windows.extend([piece] for piece in split_oversized(seg, target_tokens, overlap_tokens))
            continue
        if current and current_tokens + seg_tokens > target_tokens:
            windows.append(current)
            rewind: list[Segment] = []
            rewind_tokens = 0
            for prev in reversed(current):
                rewind.insert(0, prev)
                rewind_tokens += estimate_tokens(prev.text)
                if rewind_tokens >= overlap_tokens:
                    break
            current = list(rewind)
            current_tokens = rewind_tokens
        current.append(seg)
        current_tokens += seg_tokens
    if current:
        windows.append(current)
    return windows
