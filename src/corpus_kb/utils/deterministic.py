"""A deterministic pseudo-random source built on SHA-256.

Samplers, bootstrap evals, and fixture builders in this package need one
contract: one seed always yields one sequence, on every platform and
Python version. The stdlib Mersenne Twister meets the determinism half
but is a shared global stream that security tooling flags. This module
provides the same drawing methods over a counter-mode hash stream: every
draw hashes the seed and a counter, so sequences never share state and
re-running with the same seed reproduces the same draws exactly.

The draws are for statistics and fixtures, never for secrets. Use
``secrets`` or ``random.SystemRandom`` where unpredictability matters.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


class HashRandom:
    """Draw reproducible pseudo-random values from ``hashlib.sha256``."""

    def __init__(self, seed: str | int | bytes) -> None:
        if isinstance(seed, int):
            seed = str(seed)
        if isinstance(seed, bytes):
            seed = seed.hex()
        self._seed = seed
        self._counter = 0

    def _block(self) -> bytes:
        """The next 32 bytes of the seed's hash stream."""
        digest = hashlib.sha256(f"{self._seed}:{self._counter}".encode()).digest()
        self._counter += 1
        return digest

    def random(self) -> float:
        """One float in [0, 1)."""
        (value,) = struct.unpack(">Q", self._block()[:8])
        return value / (1 << 64)

    def uniform(self, low: float, high: float) -> float:
        """One float in [low, high)."""
        return low + (high - low) * self.random()

    def _below(self, bound: int) -> int:
        """One int in [0, bound), without modulo bias."""
        if bound <= 0:
            raise ValueError("bound must be positive")
        width = max(1, (bound.bit_length() + 7) // 8)
        span = 1 << (width * 8)
        top = span - (span % bound)
        while True:
            raw = int.from_bytes(self._block()[:width], "big")
            if raw < top:
                return raw % bound

    def randint(self, low: int, high: int) -> int:
        """One int in [low, high], both ends included."""
        return low + self._below(high - low + 1)

    def choice(self, population: Sequence[T]) -> T:
        """One element drawn from a non-empty sequence."""
        return population[self._below(len(population))]

    def choices(self, population: Sequence[T], k: int) -> list[T]:
        """k draws with replacement."""
        return [population[self._below(len(population))] for _ in range(k)]

    def sample(self, population: Sequence[T], k: int) -> list[T]:
        """k distinct draws without replacement, in draw order."""
        n = len(population)
        if not 0 <= k <= n:
            raise ValueError("sample larger than population")
        pool = list(population)
        for i in range(k):
            j = i + self._below(n - i)
            pool[i], pool[j] = pool[j], pool[i]
        return pool[:k]

    def shuffle(self, items: list[object]) -> None:
        """Shuffle in place with Fisher-Yates."""
        for i in reversed(range(1, len(items))):
            j = self._below(i + 1)
            items[i], items[j] = items[j], items[i]
