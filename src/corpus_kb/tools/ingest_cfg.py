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
    ``search.matryoshka_dim`` falls back to the documented default
    (768 / 1024) instead of failing the run.
    """
    embedding_cfg = _nested_dict(config, "embedding")
    embedding_model = str(embedding_cfg.get("model", "nomic-embed-text"))
    raw_dimensions = embedding_cfg.get("dimensions", 768)
    dimensions = raw_dimensions if isinstance(raw_dimensions, int) else 768
    matryoshka_cfg = _nested_dict(config, "search")
    v1024_dim: int | None = None
    if bool(matryoshka_cfg.get("matryoshka_enabled", False)):
        raw_dim = matryoshka_cfg.get("matryoshka_dim", 1024)
        v1024_dim = raw_dim if isinstance(raw_dim, int) else 1024
    return embedding_model, dimensions, v1024_dim


def matryoshka_slice(vector: list[float], dim: int) -> list[float]:
    """Slice one full-dimension vector to ``dim`` and L2-normalize it.

    The lazy import keeps rag.embedder (a heavy module) off the import
    path unless matryoshka slicing is actually used.
    """
    from ..rag.embedder import _slice_normalize

    return _slice_normalize(vector, dim)
