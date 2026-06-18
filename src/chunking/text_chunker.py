"""Semantic text chunker for prose.

Uses sentence-level analysis with optional semantic similarity gap detection
to split text at natural topic boundaries. Falls back to paragraph or
character-based splitting when embeddings are unavailable.

Strategy overview:
1. Split text into sentences (via regex/fast segmenter)
2. Optionally embed sentences and measure cosine similarity gaps
3. Split where gaps exceed threshold (topic boundaries)
4. Fallback: split at paragraph boundaries or character limit
"""

from __future__ import annotations

import re
from typing import Optional

from utils.models import Chunk
from .base import Chunker

# Paragraph splitter
PARAGRAPH_RE = re.compile(r"\n\n+")

# Common abbreviations that should not trigger sentence breaks
_ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr",
    "vs", "etc", "approx", "dept", "est", "govt",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    "inc", "ltd", "corp", "co",
})


class TextChunker(Chunker):
    """Semantic text chunker with embedding-aware topic segmentation.

    Two modes:
    - **Semantic** (default): Uses Ollama embedding to measure topic shifts
      between sentence windows, splits at the largest cosine gap.
    - **Paragraph**: Splits at double-newline boundaries; merges small paras.
    - **Character**: Hard character-limit split (last resort).
    """

    def __init__(
        self,
        max_size: int = 2048,
        use_semantic: bool = False,
        gap_threshold: float = 0.3,
        window_size: int = 3,
        model: str = "qwen3-embedding:8b-q8_0",
    ):
        self.max_size = max_size
        self.use_semantic = use_semantic
        self.gap_threshold = gap_threshold
        self.window_size = window_size
        self.model = model

    def chunk(self, text: str, file_path: Optional[str] = None) -> list[Chunk]:
        """Split text into chunks using configured strategy."""
        if not text or not text.strip():
            return []
        if self.use_semantic:
            return self._semantic_chunk(text, file_path)
        return self._paragraph_chunk(text, file_path)

    def _paragraph_chunk(self, text: str,
                         file_path: Optional[str] = None) -> list[Chunk]:
        """Split at paragraph boundaries, merging small paragraphs."""
        paragraphs = PARAGRAPH_RE.split(text.strip())
        if not paragraphs:
            return []

        chunks: list[Chunk] = []
        buffer: list[str] = []
        buffer_size = 0
        start_line = 0

        def flush_buffer():
            nonlocal buffer, buffer_size, start_line
            if not buffer:
                return
            chunk_text = "\n\n".join(buffer)
            lines = chunk_text.count("\n")
            chunks.append(Chunk(
                text=chunk_text,
                chunk_index=len(chunks),
                chunk_type="paragraph_group",
                source_type="text",
                file_path=file_path,
                start_line=start_line,
                end_line=start_line + lines,
            ))
            buffer = []
            buffer_size = 0

        line_offset = 0
        for para in paragraphs:
            para_len = len(para)
            para_lines = para.count("\n") + 1

            if buffer_size + para_len > self.max_size and buffer:
                flush_buffer()
                start_line = line_offset - para_lines  # approximate

            buffer.append(para)
            buffer_size += para_len
            line_offset += para_lines

        flush_buffer()
        return chunks

    def _semantic_chunk(self, text: str,
                        file_path: Optional[str] = None) -> list[Chunk]:
        """Split using embedding similarity gaps between sentence windows."""
        sentences = self._split_sentences(text)
        if len(sentences) <= 1:
            return self._paragraph_chunk(text, file_path)

        if not self.use_semantic:
            return self._paragraph_chunk(text, file_path)

        # Try to get embeddings for each sentence
        embeddings = self._get_sentence_embeddings(sentences)

        if embeddings is None:
            # Embedding unavailable — fall back to paragraph-based
            return self._paragraph_chunk(text, file_path)

        # Compute cosine gaps between sliding windows of sentences
        gaps = self._compute_gaps(embeddings)

        # Find split points: where gap > threshold or where cumulative
        # character count exceeds max_size
        chunks = self._split_at_gaps(
            sentences, gaps, file_path
        )

        return chunks if chunks else self._paragraph_chunk(text, file_path)

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences, respecting common abbreviations."""
        sentences: list[str] = []
        current: list[str] = []
        words = text.replace("\n", " ").split(" ")

        for word in words:
            current.append(word)
            # Check if word ends with sentence-ending punctuation
            if word and word[-1] in ".?!":
                stripped = word.rstrip(".?!")
                abbr_key = stripped.lower() if stripped else ""
                if abbr_key not in _ABBREVIATIONS:
                    sentences.append(" ".join(current))
                    current = []

        if current:
            sentences.append(" ".join(current))

        # If single result, try line-based fallback
        if len(sentences) <= 1:
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            if len(lines) > 3:
                return lines

        return sentences if sentences else [text]

    def _get_sentence_embeddings(self, sentences: list[str]) -> Optional[list[list[float]]]:
        """Get embeddings for each sentence via Ollama.

        Returns None if Ollama is unreachable.
        """
        try:
            import ollama
            embeddings = []
            # Batch sentences to reduce API calls
            batch_size = 10
            for i in range(0, len(sentences), batch_size):
                batch = sentences[i:i + batch_size]
                resp = ollama.embed(
                    model=self.model,
                    input=batch,
                )
                embeddings.extend(resp.embeddings)
            return embeddings
        except Exception:
            return None

    def _compute_gaps(self, embeddings: list[list[float]]) -> list[float]:
        """Compute cosine gap between adjacent sliding windows."""
        import math

        def cosine_sim(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(x * x for x in b))
            if na == 0 or nb == 0:
                return 0.0
            return dot / (na * nb)

        def window_embedding(embeddings: list[list[float]],
                             start: int, size: int) -> list[float]:
            """Average embedding over a window."""
            window = embeddings[start:start + size]
            if not window:
                return [0.0] * len(embeddings[0])
            avg = [0.0] * len(window[0])
            for e in window:
                for i, v in enumerate(e):
                    avg[i] += v
            n = len(window)
            return [v / n for v in avg]

        gaps = []
        w = self.window_size
        for i in range(len(embeddings) - w):
            left = window_embedding(embeddings, i, w)
            right = window_embedding(embeddings, i + w, w)
            gap = 1.0 - cosine_sim(left, right)
            gaps.append(gap)

        return gaps

    def _split_at_gaps(
        self,
        sentences: list[str],
        gaps: list[float],
        file_path: Optional[str],
    ) -> list[Chunk]:
        """Split sentences at gap boundaries, respecting max_size."""
        chunks: list[Chunk] = []
        current_group: list[str] = []
        current_size = 0
        start_line = 0
        line_offset = 0

        for i, sentence in enumerate(sentences):
            sent_len = len(sentence)
            sent_lines = sentence.count("\n") + 1

            # Check if we should split BEFORE this sentence
            should_split = False

            if current_size + sent_len > self.max_size and current_group:
                should_split = True
            elif (i < len(gaps)
                  and gaps[i] > self.gap_threshold
                  and current_group
                  and current_size > 200):
                should_split = True

            if should_split:
                chunk_text = " ".join(current_group)
                chunks.append(Chunk(
                    text=chunk_text,
                    chunk_index=len(chunks),
                    chunk_type="paragraph_group",
                    source_type="text",
                    file_path=file_path,
                    start_line=start_line,
                    end_line=start_line + line_offset - 1,
                ))
                start_line = line_offset
                current_group = []
                current_size = 0

            current_group.append(sentence)
            current_size += sent_len
            line_offset += sent_lines

        # Final chunk
        if current_group:
            chunk_text = " ".join(current_group)
            chunks.append(Chunk(
                text=chunk_text,
                chunk_index=len(chunks),
                chunk_type="paragraph_group",
                source_type="text",
                file_path=file_path,
                start_line=start_line,
                end_line=start_line + line_offset - 1,
            ))

        return chunks
