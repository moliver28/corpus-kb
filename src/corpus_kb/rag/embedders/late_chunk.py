"""Late-chunking embedder (todo 13, v5 5.3).

Late chunking embeds a whole document in ONE forward pass and pools
token-level hidden states over each chunk's span — instead of pooling each
chunk separately (naive). One pass over an exchange yields all three
retrieval views: the answer span, the QA span, and the question span.

* Model: Qwen3-Embedding-0.6B (1024-dim, 32k context) — the v5 17.5 default
  assumption; 4B/8B are A/B upgrades, never silent.
* Sources >32k tokens are macro-split at TURN boundaries with ~10% turn
  overlap before the per-window forward passes.
* Loaded via the optional latechunk stack (sentence-transformers /
  transformers / torch) behind the typed shim — Ollama's endpoint only
  returns pooled vectors and cannot serve token states.
* Every emitted vector is L2-normalized at this boundary with the shared
  zero-vector no-op contract (degraded runs pass through, house fallback).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from corpus_kb.rag.embedder import assert_unit_or_zero, instruct, l2_normalize
from corpus_kb.rag.embedders._latechunk_types import (
    AutoModelLike,
    STModelLike,
    TokenizerLike,
    TokenStates,
    TorchModule,
    import_sentence_transformers,
    import_torch,
    import_transformers,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_DIMENSIONS = 1024
# v5 5.3: Qwen3-Embedding accepts 32k tokens; keep a safety margin under it.
MAX_MACRO_TOKENS = 30_000
MACRO_OVERLAP_RATIO = 0.1


@dataclass(frozen=True)
class SpanVectors:
    """The three retrieval views of one exchange, from one forward pass."""

    question: list[float]
    qa: list[float]
    answer: list[float]


def macro_windows(turn_tokens: list[int]) -> list[tuple[int, int]]:
    """Turn-boundary macro-split (v5 5.3).

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


class LateChunkEmbedder:
    """Embedder Protocol backend pooling spans from one forward pass."""

    dimensions: int = DEFAULT_DIMENSIONS

    def __init__(self, model_name: str = DEFAULT_MODEL, device: str = "cpu") -> None:
        transformers = import_transformers()
        st_module = import_sentence_transformers()
        self._torch: TorchModule = import_torch()
        self._tokenizer: TokenizerLike = transformers.AutoTokenizer.from_pretrained(model_name)
        self._encoder: AutoModelLike = transformers.AutoModel.from_pretrained(model_name).eval()
        self._pooled: STModelLike = st_module.SentenceTransformer(model_name, device=device)
        self.model = model_name
        reported = self._pooled.get_sentence_embedding_dimension()
        self.dimensions = int(reported) if reported else DEFAULT_DIMENSIONS
        self.forward_calls = 0

    def embed_spans(self, turns: list[str], spans: list[tuple[int, int]]) -> list[list[float]]:
        """Pool per-span vectors from forward passes over macro-windows.

        ``turns`` are document turns in order; ``spans`` are half-open
        (lo, hi) TURN-index ranges (one exchange typically contributes the
        question span, the QA span, and the answer span). Each span is
        assigned to the FIRST macro-window containing its start turn; a span
        crossing a window boundary is pooled up to that window's end.
        """
        if not spans:
            return []
        counts = [self._count_tokens(t) for t in turns]
        results: list[list[float] | None] = [None] * len(spans)
        for w_lo, w_hi in macro_windows(counts):
            window_turns = turns[w_lo:w_hi]
            offsets = [1]
            for count in counts[w_lo:w_hi]:
                offsets.append(offsets[-1] + count + 1)
            hidden, seq_len = self._forward_hidden("\n".join(window_turns))
            for si, (lo, hi) in enumerate(spans):
                if results[si] is not None or lo < w_lo or lo >= w_hi:
                    continue
                start = offsets[lo - w_lo]
                end = offsets[min(hi, w_hi) - w_lo] - 1
                results[si] = pool_span(hidden, start, end, seq_len)
        vectors = [vec for vec in results if vec is not None]
        assert len(vectors) == len(spans), "every span must pool exactly once"
        for vec in vectors:
            assert_unit_or_zero(vec)
        return vectors

    def embed_exchange(self, question: str, answer: str) -> SpanVectors:
        """One forward pass -> 3 vectors (answer span / QA span / question span)."""
        vectors = self.embed_spans([question, answer], [(0, 1), (0, 2), (1, 2)])
        return SpanVectors(question=vectors[0], qa=vectors[1], answer=vectors[2])

    def encode_pooled(self, texts: list[str]) -> list[list[float]]:
        """Naive pooled embeddings via sentence-transformers (G1 arm b / queries)."""
        raw = self._pooled.encode(
            texts, batch_size=8, normalize_embeddings=True, show_progress_bar=False
        )
        return [l2_normalize(row) for row in raw.tolist()]

    def embed(self, text: str) -> list[float]:
        """Embedder Protocol: the whole text is one span."""
        return self.embed_spans([text], [(0, 1)])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embedder Protocol: independent per-text spans."""
        return [self.embed(text) for text in texts]

    def instruct(self, text: str) -> str:
        """Return the query-side instructed text (see rag.embedder.instruct)."""
        return instruct(text)

    def _count_tokens(self, text: str) -> int:
        encoded = self._tokenizer(
            [text],
            return_tensors="pt",
            padding=False,
            truncation=True,
            max_length=MAX_MACRO_TOKENS,
            add_special_tokens=False,
        )
        return len(encoded["input_ids"].tolist()[0])

    def _forward_hidden(self, text: str) -> tuple[TokenStates, int]:
        self.forward_calls += 1
        encoded = self._tokenizer(
            [text],
            return_tensors="pt",
            padding=False,
            truncation=True,
            max_length=MAX_MACRO_TOKENS,
        )
        seq_len = len(encoded["input_ids"].tolist()[0])
        with self._torch.no_grad():
            output = self._encoder(
                input_ids=encoded["input_ids"], attention_mask=encoded["attention_mask"]
            )
        return output.last_hidden_state, seq_len


def is_latechunk_installed() -> bool:
    """True when the optional latechunk stack is importable (smoke/skip gates)."""
    import importlib.util

    return importlib.util.find_spec("sentence_transformers") is not None
