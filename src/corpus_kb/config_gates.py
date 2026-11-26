"""Cross-field config gates that run AT LOAD.

Extracted from ``config.py`` (size-ratchet F11): each gate reads two
config sections, returns one refusal message, and stays pure (no IO, no
imports from ``config`` — ``load_config`` lazy-imports the gate at its
call site, like the contextual/topics/analysis gates).
"""

from __future__ import annotations


def matryoshka_mrl_error(config: dict[str, object]) -> str | None:
    """U32 cross-field gate on the two-stage matryoshka search arm.

    ``retrieval.matryoshka_enabled: true`` front-slices stored embeddings to
    ``matryoshka_dim`` and re-ranks at full dimension. That is only sound
    when the embedder is Matryoshka-trained (``embedding.mrl_trained:
    true``); truncating a non-MRL model (nomic-embed-text) silently
    degrades retrieval. Returns the refusal message for the invalid
    combination, or None when the arm is off or the model declared
    MRL-trained. ``load_config`` raises the message as a config error, so
    the arm cannot activate silently; type checking of the two keys is
    owned by ``validate_configs.validate_runtime_config``. The legacy
    ``search.matryoshka_enabled`` spelling is normalized by the alias
    layer before this gate runs.
    """
    retrieval = config.get("retrieval")
    embedding = config.get("embedding")
    if not isinstance(retrieval, dict) or not isinstance(embedding, dict):
        return None
    # Truthiness, matching the runtime consumers (QueryHandler /
    # ingest_common read the key through bool()): a quoted "true" or 1 in a
    # hand-edited YAML must trip the gate, not slip past `is not True`.
    if not retrieval.get("matryoshka_enabled"):
        return None
    if embedding.get("mrl_trained") is True:
        return None
    return (
        "retrieval.matryoshka_enabled: true requires embedding.mrl_trained: true — "
        "two-stage matryoshka retrieval front-slices embeddings, which is only "
        "sound for Matryoshka-trained models (qwen3-embedding is MRL-trained; "
        "nomic-embed-text is NOT). Switch the embedding model first, or set "
        "retrieval.matryoshka_enabled: false."
    )


def store_width_mismatch_error(config_width: int, store_width: int) -> str | None:
    """The one width-refusal message for both engines (issue #559).

    The boot guard calls this on Postgres and DuckDB alike, so a store
    reopened with a different ``embedding.dimensions`` answers the same
    bytes on both engines. Returns None when the widths match.
    """
    if config_width == store_width:
        return None
    return (
        f"Embedding dimensions {config_width} do not match this store's width "
        f"{store_width}. Re-migrate the store with the new width, or set "
        f"embedding.dimensions back to {store_width}."
    )


__all__ = ["matryoshka_mrl_error", "store_width_mismatch_error"]
