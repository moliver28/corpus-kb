"""Embedding-model native-dimension profiles (config-v2 wave W1).

The ONE table mapping a known embedding model to its native output width.
Moved into src from the standalone bench script (scripts/bench_embedders.py
keeps its own copy for operator runs); ``OllamaEmbedder`` uses it for the
MRL ``dimensions`` passthrough and the returned-width validation, and the
doctor uses it to explain model/schema mismatches.

The widths are facts about the model files on the Ollama registry:

* ``nomic-embed-text`` — 768 dims, NOT Matryoshka-trained.
* ``qwen3-embedding:4b`` — 2560 dims, MRL-trained.
* ``qwen3-embedding:8b-q8_0`` — 4096 dims, MRL-trained.

An unknown model returns None: callers must treat "unknown" as "cannot
assume" and skip the optimization that needs the fact.
"""

from __future__ import annotations

from dataclasses import dataclass

from corpus_kb.dimensions import (
    EMBEDDING_DIMENSIONS,
    NOMIC_EMBEDDING_DIMENSIONS,
)


@dataclass(frozen=True)
class EmbeddingModelProfile:
    """Native facts about one embedding model."""

    model: str
    dimensions: int
    mrl_trained: bool


# Native output widths (probe-verified against Ollama 0.31.1; the 4b row is
# re-verified by tests/test_bench_embedders.py via the bench script table).
# The nomic and 4b rows share the schema-facing width constants in
# corpus_kb.dimensions; the 4096 row has no schema role, so it stays a
# literal model fact.
EMBEDDING_MODEL_PROFILES: tuple[EmbeddingModelProfile, ...] = (
    EmbeddingModelProfile(
        model="nomic-embed-text", dimensions=NOMIC_EMBEDDING_DIMENSIONS, mrl_trained=False
    ),
    EmbeddingModelProfile(
        model="qwen3-embedding:4b", dimensions=EMBEDDING_DIMENSIONS, mrl_trained=True
    ),
    EmbeddingModelProfile(model="qwen3-embedding:8b-q8_0", dimensions=4096, mrl_trained=True),
)


def profile_for_model(model: str) -> EmbeddingModelProfile:
    """The profile for ``model``; ValueError for an unknown model."""
    for profile in EMBEDDING_MODEL_PROFILES:
        if profile.model == model:
            return profile
    known = ", ".join(p.model for p in EMBEDDING_MODEL_PROFILES)
    raise ValueError(f"no EmbeddingModelProfile for {model!r}; known models: {known}")


def native_dimensions(model: str) -> int | None:
    """Native output width of ``model``, or None when unknown."""
    for profile in EMBEDDING_MODEL_PROFILES:
        if profile.model == model:
            return profile.dimensions
    return None


__all__ = [
    "EMBEDDING_MODEL_PROFILES",
    "EmbeddingModelProfile",
    "native_dimensions",
    "profile_for_model",
]
