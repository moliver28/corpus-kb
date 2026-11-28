"""Typed Protocol wrappers for the optional ``[quality]`` dependency stack.

House pattern (``rag/embedders/_latechunk_types.py``): NO bare
``import sentence_transformers`` / ``import fasttext`` anywhere in the
package — a bare import, even guarded by try/except, trips pyright
``reportMissingImports=error`` in the extras-less CI type-check job, and
deptry DEP001 (the fasttext wheel's distribution name differs from its
import name). Every access goes through ``importlib.import_module`` +
``cast`` onto the Protocols below, which declare exactly the members
``translation_quality`` uses. Importing this module is SAFE without the
extra: nothing heavy loads until an ``import_*`` function runs, and
absence answers None — the honest ``not_measured`` path (AM-05).
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable
from typing import Protocol, cast


class EncoderLike(Protocol):
    """The sentence-transformers encoder surface the quality gate uses."""

    def encode(self, sentences: list[str]) -> Iterable[Iterable[float]]: ...


class SentenceTransformersModule(Protocol):
    """``sentence_transformers`` module surface used by the quality gate."""

    def SentenceTransformer(self, name: str) -> EncoderLike: ...  # noqa: N802


class FastTextDetectorLike(Protocol):
    """fastText LID detector surface (predict answers parallel label/score rows)."""

    def predict(self, text: str) -> tuple[list[str], list[float]]: ...


class FastTextModule(Protocol):
    """``fasttext`` module surface used by the quality gate."""

    def load_model(self, path: str) -> FastTextDetectorLike: ...


def import_sentence_transformers() -> SentenceTransformersModule | None:
    """Lazily import ``sentence_transformers``; None when the extra is absent."""
    try:
        return cast(
            SentenceTransformersModule,
            importlib.import_module("sentence_transformers"),
        )
    except ImportError:
        return None


def import_fasttext() -> FastTextModule | None:
    """Lazily import ``fasttext``; None when the extra is absent."""
    try:
        return cast(FastTextModule, importlib.import_module("fasttext"))
    except ImportError:
        return None
