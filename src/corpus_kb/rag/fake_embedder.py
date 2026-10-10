"""FakeEmbedder — deterministic hash-based embedder for eval baselines (no Ollama).

This is the EVAL ORACLE embedder: identical inputs produce byte-identical
vectors on any process, platform, Python or numpy version, because every
float is derived directly from SHA-256 digest bytes (no RNG stream, no
floating-point environment dependence beyond IEEE-754 division).

Distinction from ``corpus_kb.rag.embedder.FakeEmbedder``: that one is the
legacy CI stub seeded via ``random.Random(int(sha256(...)))`` and emits
NON-normalized uniform vectors; it is kept for compatibility with existing
tests. This module's FakeEmbedder is the contract-tested baseline oracle
for retrieval/eval harnesses (P3+): L2-normalized, configurable dims,
default 1024 (the research grain), never all-zero.

Satisfies the structural ``Embedder`` protocol from ``rag.embedder``
(``dimensions`` / ``embed`` / ``embed_batch`` / ``instruct``) plus the
matryoshka helpers OllamaEmbedder exposes.
"""

from __future__ import annotations

import hashlib
import struct

import numpy as np

from corpus_kb.rag.embedder import instruct as _instruct

DEFAULT_DIMENSIONS = 1024

# Fixed probe string for doctor/eval canaries: embed THIS and reject an
# all-zero reply (the qwen3 zero-vector degraded-mode incident contract).
PROBE_TEXT = "corpus-kb fake-embedder canary probe"


def _hash_unit_vector(text: str, dimensions: int) -> list[float]:
    """Derive an L2-normalized float vector purely from SHA-256 digest bytes.

    Counter-appended digests yield 8 uniform floats in [-1, 1) per 32-byte
    hash (big-endian uint32 words scaled into the signed unit interval).
    The zero vector is unreachable (a uniform digest is astronomically
    unlikely to be all-zero words, and the final normalization would be a
    no-op anyway), so downstream all-zero checks keep their meaning.
    """
    floats: list[float] = []
    counter = 0
    while len(floats) < dimensions:
        digest = hashlib.sha256(f"{counter}\x00{text}".encode()).digest()
        words = struct.unpack(f">{len(digest) // 4}I", digest)
        floats.extend((word / 2**31) - 1.0 for word in words)
        counter += 1
    array = np.asarray(floats[:dimensions], dtype=np.float64)
    norm = float(np.linalg.norm(array))
    if norm == 0.0:  # pragma: no cover - unreachable for SHA-256 digests
        raise ValueError("FakeEmbedder produced a degenerate all-zero vector")
    return (array / norm).tolist()


class FakeEmbedder:
    """Deterministic, process-stable, L2-normalized hash embedder.

    Usage:
        embedder = FakeEmbedder(dimensions=256)
        vector = embedder.embed("any text")  # stable forever
    """

    def __init__(self, dimensions: int = DEFAULT_DIMENSIONS) -> None:
        if dimensions <= 0:
            raise ValueError(f"FakeEmbedder dimensions must be positive; got {dimensions}")
        self.dimensions = int(dimensions)
        self.model = "fake-embedder-oracle"

    def embed(self, text: str) -> list[float]:
        """Return the stable unit vector for ``text``."""
        return _hash_unit_vector(text, self.dimensions)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return stable vectors for ``texts``, in input order."""
        return [self.embed(text) for text in texts]

    def embed_matryoshka(self, text: str, dim: int) -> list[float]:
        """Front-slice to ``dim`` dims and L2-renormalize (matryoshka contract)."""
        return _slice_normalize(self.embed(text), dim)

    def embed_batch_matryoshka(self, texts: list[str], dim: int) -> list[list[float]]:
        """Matryoshka-truncate a batch."""
        return [_slice_normalize(vector, dim) for vector in self.embed_batch(texts)]

    def instruct(self, text: str) -> str:
        """Return the query-side instructed text (same prefix as OllamaEmbedder)."""
        return _instruct(text)


def _slice_normalize(vector: list[float], dim: int) -> list[float]:
    """Front-slice and renormalize; zero-slice stays zero (defensive no-op)."""
    sliced = np.asarray(vector[:dim], dtype=np.float64)
    norm = float(np.linalg.norm(sliced))
    if norm == 0.0:
        return sliced.tolist()
    return (sliced / norm).tolist()
