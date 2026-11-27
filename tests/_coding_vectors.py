"""Shared deterministic vectors for the coding tests (sha256 one-hot, DIM 2560).

The byte-identical helpers make cosine outcomes exact, so coding tests
assert shares and distances exactly without a model call (wave law).
"""

from __future__ import annotations

import hashlib

DIM = 2560


def _one_hot(text: str, instructed: bool = False) -> list[float]:
    index = int(hashlib.sha256(f"{instructed}:{text}".encode()).hexdigest(), 16) % DIM
    vector = [0.0] * DIM
    vector[index] = 1.0
    return vector


async def _embed(text: str, instructed: bool = False) -> str:
    return "[" + ",".join(format(float(v), ".9g") for v in _one_hot(text, instructed)) + "]"
