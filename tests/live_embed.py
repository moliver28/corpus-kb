"""The embedding model the live CI legs pull (zero-drift F3 follow-up).

The CI test legs pull the shipped default model. Fixtures that make
live requests derive the name and width from the same source CI uses,
so a stale hardcoded name cannot 404 a green leg again.
"""

from __future__ import annotations

import os


def live_embed_model() -> str:
    """The model name a live Ollama request should use.

    OLLAMA_MODEL wins when CI sets it: the leg pulled exactly that
    model. Local runs fall back to the shipped config default.
    """
    from_env = os.environ.get("OLLAMA_MODEL")
    if from_env:
        return from_env
    from corpus_kb.config import get_default_config

    return str(get_default_config()["embedding"]["model"])


def live_embed_dimensions() -> int:
    """The vector width that belongs to live_embed_model()."""
    from corpus_kb.rag.model_profiles import profile_for_model

    return int(profile_for_model(live_embed_model()).dimensions)
