"""Shared config reads for the ingest stage modules (zero-drift Wave 3B).

``_nested_dict`` was ingest_common's private helper; the stage modules
(ingest_embed, ingest_extract) and the orchestrator all read nested
config maps, so it lives here — below both — keeping the import graph a
DAG: ingest_common -> {ingest_embed, ingest_extract, ingest_reembed} ->
ingest_cfg. ``vector_write_config`` is the ONE reader for the
vector-write settings (model, dimensions, matryoshka dim) shared by the
ingest pipeline and the reembed repair path.
"""

from __future__ import annotations

from corpus_kb.rag.embedder import DEFAULT_EMBEDDING_DIMENSIONS, DEFAULT_EMBEDDING_MODEL

DEFAULT_MATRYOSHKA_DIM = 1024


def _nested_dict(config: dict[str, object], key: str) -> dict[str, object]:
    value = config.get(key)
    if isinstance(value, dict):
        return value
    return {}


def vector_write_config(config: dict[str, object]) -> tuple[str, int, int | None]:
    """(embedding_model, dimensions, matryoshka_dim) for vector writes.

    One reader for the ingest pipeline (``_run_locked_stages``) and the
    repair path (``reembed_degraded``): same keys, same defaults, same
    coercion. A non-int ``embedding.dimensions`` or
    ``retrieval.matryoshka_dim`` falls back to the documented default
    (2560 / 1024) instead of failing the run. The legacy
    ``search.matryoshka_*`` spellings are aliases the loader applies.
    """
    embedding_cfg = _nested_dict(config, "embedding")
    embedding_model = str(embedding_cfg.get("model", DEFAULT_EMBEDDING_MODEL))
    raw_dimensions = embedding_cfg.get("dimensions", DEFAULT_EMBEDDING_DIMENSIONS)
    dimensions = raw_dimensions if isinstance(raw_dimensions, int) else DEFAULT_EMBEDDING_DIMENSIONS
    matryoshka_cfg = _nested_dict(config, "retrieval")
    v1024_dim: int | None = None
    if bool(matryoshka_cfg.get("matryoshka_enabled", False)):
        raw_dim = matryoshka_cfg.get("matryoshka_dim", DEFAULT_MATRYOSHKA_DIM)
        v1024_dim = raw_dim if isinstance(raw_dim, int) else DEFAULT_MATRYOSHKA_DIM
    return embedding_model, dimensions, v1024_dim


def contextual_apply_to(config: dict[str, object]) -> tuple[bool, bool]:
    """(embedding_applies, lexical_applies) from ``ingest.contextualize.apply_to``.

        Write-time targets of the contextual blurb (W3). ``embedding`` puts the
        ``blurb

    text`` form into the embed input; ``lexical`` writes the
        ``chunks.context_blurb`` column the FTS expression reads. The default
        (missing/empty key or any list) is the pre-W3 behavior: both targets on.
        An invalid list is harmless here — validate_configs and
        config.load_config reject it; the pipeline just treats unknown values
        as both-on so a typo can never silently drop the blurb.
    """
    contextualize = _nested_dict(_nested_dict(config, "ingest"), "contextualize")
    apply_to = contextualize.get("apply_to")
    if not isinstance(apply_to, list) or not apply_to:
        return True, True
    targets = {str(item) for item in apply_to}
    return "embedding" in targets, "lexical" in targets


def matryoshka_slice(vector: list[float], dim: int) -> list[float]:
    """Slice one full-dimension vector to ``dim`` and L2-normalize it.

    The lazy import keeps rag.embedder (a heavy module) off the import
    path unless matryoshka slicing is actually used.
    """
    from ..rag.embedder import _slice_normalize

    return _slice_normalize(vector, dim)
