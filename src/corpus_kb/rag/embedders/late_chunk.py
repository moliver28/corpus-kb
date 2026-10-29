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
from typing import cast

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
from corpus_kb.rag.embedders.late_chunk_core import (
    CHARS_PER_TOKEN,
    DEFAULT_STRIDE_TOKENS,
    DEFAULT_WINDOW_TOKENS,
    MAX_MACRO_TOKENS,
    LateChunkSpan,
    SpanVectors,
    build_document_chunk_vectors,
    macro_windows,
    pool_span,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_DIMENSIONS = 1024

# Re-exports (stable public import path): the pure math lives in
# late_chunk_core so it stays testable without the heavy stack.
__all__ = [
    "DEFAULT_MODEL",
    "MAX_MACRO_TOKENS",
    "LateChunkEmbedder",
    "LateChunkSpan",
    "SpanVectors",
    "is_latechunk_installed",
    "macro_windows",
    "pool_span",
]


class LateChunkEmbedder:
    """Embedder Protocol backend pooling spans from one forward pass."""

    dimensions: int = DEFAULT_DIMENSIONS

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str = "cpu",
        window_tokens: int = DEFAULT_WINDOW_TOKENS,
        stride_tokens: int = DEFAULT_STRIDE_TOKENS,
    ) -> None:
        transformers = import_transformers()
        st_module = import_sentence_transformers()
        self._torch: TorchModule = import_torch()
        self._tokenizer: TokenizerLike = transformers.AutoTokenizer.from_pretrained(model_name)
        # train(False) is nn.Module's eval-mode switch (dropout off).
        self._encoder: AutoModelLike = transformers.AutoModel.from_pretrained(model_name).train(
            False
        )
        self._pooled: STModelLike = st_module.SentenceTransformer(model_name, device=device)
        self.model = model_name
        reported = self._pooled.get_sentence_embedding_dimension()
        self.dimensions = int(reported) if reported else DEFAULT_DIMENSIONS
        # Document-path sliding windows (late_chunk_core): qwen3-embedding's
        # 32k context takes 16384-token windows with 2048-token stride overlap.
        self.window_tokens = window_tokens
        self.stride_tokens = stride_tokens
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

    def embed_document_chunks(
        self, document_text: str, chunk_char_spans: list[tuple[int, int]]
    ) -> list[LateChunkSpan]:
        """Late-chunk a document: one tokenization, per-window forwards, span pooling.

        The document is tokenized ONCE for char->token span mapping (offset
        mapping; chars/4 heuristic when the tokenizer lacks offsets), then
        every chunk vector is pooled from token states that saw the whole
        document text (or the sliding window(s) covering its span — a chunk
        covered by >1 window gets the mean of its per-window poolings; see
        ``late_chunk_core.build_document_chunk_vectors``).
        """
        token_offsets, n_tokens = self._tokenize_document(document_text)
        return build_document_chunk_vectors(
            document_text=document_text,
            chunk_char_spans=chunk_char_spans,
            n_tokens=n_tokens,
            token_offsets=token_offsets,
            window_forward=self._forward_rows,
            window_tokens=self.window_tokens,
            stride_tokens=self.stride_tokens,
        )

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

    def _tokenize_document(self, document_text: str) -> tuple[list[tuple[int, int]] | None, int]:
        """The ONE full-document tokenization: (char offsets | None, token count).

        add_special_tokens=False keeps offset mappings clean (no (0,0)
        special markers); the per-window forwards re-add specials. Offsets
        come back None when the tokenizer cannot serve them (slow tokenizers
        raise ValueError; non-HF doubles raise TypeError) and callers apply
        the chars/4 heuristic consistently for both mapping and windows.
        """
        if not document_text:
            return None, 0
        try:
            encoded = self._tokenizer(
                [document_text],
                return_tensors="pt",
                padding=False,
                truncation=False,
                add_special_tokens=False,
                return_offsets_mapping=True,
            )
        except (TypeError, ValueError):
            return None, max(1, len(document_text) // CHARS_PER_TOKEN)
        mappings = cast("list[list[tuple[int, int]]] | None", encoded.get("offset_mapping"))
        n_tokens = len(encoded["input_ids"].tolist()[0])
        if not mappings:
            # No offsets: mapping falls back to chars/4 below — so window
            # PLANNING must use the same chars/4 estimate. Returning the real
            # token count here would slice windows past the end of the
            # document for token-dense text (CJK) while still labeling the
            # vectors late-chunked.
            return None, max(1, len(document_text) // CHARS_PER_TOKEN)
        return mappings[0], n_tokens

    def _forward_rows(self, text: str) -> list[list[float]]:
        """Forward one window and hand the pure core plain token rows."""
        hidden, seq_len = self._forward_hidden(text)
        return [list(row) for row in hidden[0, 0:seq_len, :].tolist()]

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
    """True when the optional latechunk stack actually imports (smoke/skip gates)."""
    import importlib

    try:
        importlib.import_module("sentence_transformers")
    except Exception:
        # Absent extra, or a half-installed stack (e.g. torch missing) that
        # find_spec alone would wrongly report as usable.
        return False
    return True
