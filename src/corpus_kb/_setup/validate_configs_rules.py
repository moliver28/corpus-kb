"""Per-block rule helpers for the runtime ``config.yaml`` checks.

Each helper owns the when-present enum/range/shape rules for one config
block (contextualize, topics, textbook, queue, reranking, retrieval
knobs) and the backend-mix refusals. ``validate_configs_runtime.py``
composes them.
"""

from __future__ import annotations

from typing import Any

try:
    # Package context (installed checkout, tests).
    from corpus_kb._setup.validate_configs_keys import (
        AGE_ON_DUCKDB_ERROR,
        MAX_HOPS_CEILING,
        MAX_HOPS_MIN,
        PGML_EMBEDDER_ON_DUCKDB_ERROR,
        PGML_EXTRACTION_ON_DUCKDB_ERROR,
        PGML_RERANKER_ON_DUCKDB_ERROR,
        SUPPORTED_APPLY_TO_TARGETS,
        SUPPORTED_CONTEXTUAL_PROFILES,
        SUPPORTED_DATABASE_BACKENDS,
        SUPPORTED_TEXTBOOK_PARSERS,
        SUPPORTED_TOPIC_METHODS,
        backend_error,
        return_top_k_error,
    )
except ImportError:  # pragma: no cover - script context (CI validate job)
    import importlib as _importlib

    _keys = _importlib.import_module("validate_configs_keys")
    AGE_ON_DUCKDB_ERROR = _keys.AGE_ON_DUCKDB_ERROR
    MAX_HOPS_CEILING = _keys.MAX_HOPS_CEILING
    MAX_HOPS_MIN = _keys.MAX_HOPS_MIN
    PGML_EMBEDDER_ON_DUCKDB_ERROR = _keys.PGML_EMBEDDER_ON_DUCKDB_ERROR
    PGML_EXTRACTION_ON_DUCKDB_ERROR = _keys.PGML_EXTRACTION_ON_DUCKDB_ERROR
    PGML_RERANKER_ON_DUCKDB_ERROR = _keys.PGML_RERANKER_ON_DUCKDB_ERROR
    SUPPORTED_APPLY_TO_TARGETS = _keys.SUPPORTED_APPLY_TO_TARGETS
    SUPPORTED_CONTEXTUAL_PROFILES = _keys.SUPPORTED_CONTEXTUAL_PROFILES
    SUPPORTED_DATABASE_BACKENDS = _keys.SUPPORTED_DATABASE_BACKENDS
    SUPPORTED_TEXTBOOK_PARSERS = _keys.SUPPORTED_TEXTBOOK_PARSERS
    SUPPORTED_TOPIC_METHODS = _keys.SUPPORTED_TOPIC_METHODS
    backend_error = _keys.backend_error
    return_top_k_error = _keys.return_top_k_error


def _contextualize_block_errors(contextualize: dict[str, Any]) -> list[str]:
    """Enum + shape rules for the W3 ``ingest.contextualize`` keys."""
    errors: list[str] = []
    if "backend" in contextualize:
        backend = contextualize.get("backend")
        if backend != "ollama":
            errors.append(
                f"ingest.contextualize.backend: must be 'ollama' when present, got {backend!r}"
            )
    profile = contextualize.get("prompt_profile")
    if profile is not None and profile not in SUPPORTED_CONTEXTUAL_PROFILES:
        profiles = ", ".join(sorted(SUPPORTED_CONTEXTUAL_PROFILES))
        errors.append(
            f"ingest.contextualize.prompt_profile: must be one of ({profiles}), got {profile!r}"
        )
    errors.extend(_apply_to_errors(contextualize.get("apply_to")))
    return errors


def _apply_to_errors(apply_to: object) -> list[str]:
    """Non-empty subset-of-{embedding, lexical} rule for ``apply_to``."""
    if apply_to is None:
        return []
    if not isinstance(apply_to, list) or not apply_to:
        return [f"ingest.contextualize.apply_to: must be a non-empty list, got {apply_to!r}"]
    unknown = [t for t in apply_to if t not in SUPPORTED_APPLY_TO_TARGETS]
    if not unknown:
        return []
    targets = ", ".join(sorted(SUPPORTED_APPLY_TO_TARGETS))
    return [
        f"ingest.contextualize.apply_to: unknown target(s) {unknown!r}; "
        f"supported targets: {targets}"
    ]


def _topics_block_errors(topics: dict[str, Any]) -> list[str]:
    """Enum + union-type rules for the Wave 4 ``topics`` keys."""
    errors: list[str] = []
    if "method" in topics:
        method = topics.get("method")
        if method not in SUPPORTED_TOPIC_METHODS:
            errors.append(
                f"topics.method: must be one of {sorted(SUPPORTED_TOPIC_METHODS)} "
                f"when present, got {method!r}"
            )
    # Presence + union type: "auto" or a positive int (bool is an int
    # subclass — reject it the way the int keys do).
    if "min_topic_size" not in topics:
        errors.append(
            "missing required key 'topics.min_topic_size' "
            "(expected str or int: 'auto' or a positive int)"
        )
    else:
        size = topics.get("min_topic_size")
        size_ok = (isinstance(size, str) and size == "auto") or (
            not isinstance(size, bool) and isinstance(size, int) and size > 0
        )
        if not size_ok:
            errors.append(
                "key 'topics.min_topic_size' must be the string 'auto' or a "
                f"positive int, got {size!r}"
            )
    return errors


def _database_block_errors(config: dict[str, Any]) -> list[str]:
    """Enum + backend-mix rules for the storage-portability keys."""
    errors: list[str] = []
    database = config.get("database")
    if isinstance(database, dict):
        backend = database.get("backend")
        if backend is not None and backend not in SUPPORTED_DATABASE_BACKENDS:
            backends = ", ".join(sorted(SUPPORTED_DATABASE_BACKENDS))
            errors.append(f"database.backend: must be one of ({backends}), got {backend!r}")
    errors.extend(_backend_mix_errors(config))
    return errors


def _effective_extraction_backend(config: dict[str, Any]) -> str | None:
    """The extraction backend the loader's alias layer settles on.

    Mirrors corpus_kb.config.apply_extraction_backend_alias precedence: a
    non-empty ``extraction.backend`` wins; the legacy ``graph.extractor``
    alias stays authoritative only when the preferred key is absent.
    """
    extraction = config.get("extraction")
    if isinstance(extraction, dict):
        raw = extraction.get("backend")
        if isinstance(raw, str) and raw:
            return raw
    graph = config.get("graph")
    if isinstance(graph, dict):
        raw = graph.get("extractor")
        if isinstance(raw, str) and raw:
            return raw
    return None


def _backend_mix_errors(config: dict[str, Any]) -> list[str]:
    """Mirror of corpus_kb.config.backend_mix_errors (same bank texts).

    AGE and PostgresML selections cannot run while ``database.backend`` is
    duckdb; the loader refuses them AT LOAD and this validator reports the
    same combinations for the shipped YAMLs and user configs. Returns every
    violation in a fixed order; empty unless the backend is exactly
    ``duckdb`` (an unknown value is the enum rule's job, not a mix).
    """
    database = config.get("database")
    if not isinstance(database, dict) or database.get("backend") != "duckdb":
        return []
    errors: list[str] = []
    graph = config.get("graph")
    if isinstance(graph, dict) and graph.get("backend") == "age":
        errors.append(AGE_ON_DUCKDB_ERROR)
    embedding = config.get("embedding")
    if isinstance(embedding, dict) and "pgml" in (
        embedding.get("provider"),
        embedding.get("fallback_provider"),
    ):
        errors.append(PGML_EMBEDDER_ON_DUCKDB_ERROR)
    search = config.get("search")
    if isinstance(search, dict) and search.get("reranker") == "pgml":
        errors.append(PGML_RERANKER_ON_DUCKDB_ERROR)
    if _effective_extraction_backend(config) == "pgml":
        errors.append(PGML_EXTRACTION_ON_DUCKDB_ERROR)
    return errors


def _max_hops_errors(hops: object) -> list[str]:
    """Positive-int range rule for ``graph.max_hops`` (ceiling 25)."""
    if isinstance(hops, bool) or not isinstance(hops, int):
        return [f"graph.max_hops: must be an int, got {type(hops).__name__}"]
    if not MAX_HOPS_MIN <= hops <= MAX_HOPS_CEILING:
        return [
            f"graph.max_hops: must be between {MAX_HOPS_MIN} and {MAX_HOPS_CEILING}, got {hops}"
        ]
    return []


def _textbook_block_errors(textbook: dict[str, Any]) -> list[str]:
    """Enum + positive-int rules for the ``textbook`` block (Wave 1)."""
    errors: list[str] = []
    parser = textbook.get("parser")
    if parser is not None and parser not in SUPPORTED_TEXTBOOK_PARSERS:
        parsers = ", ".join(sorted(SUPPORTED_TEXTBOOK_PARSERS))
        errors.append(f"textbook.parser: must be one of ({parsers}), got {parser!r}")
    for key in ("min_pages", "window_pages"):
        value = textbook.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            errors.append(f"textbook.{key}: must be a positive int, got {value!r}")
    timeout = textbook.get("window_timeout_s")
    if timeout is not None and (
        isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0
    ):
        errors.append(f"textbook.window_timeout_s: must be a positive number, got {timeout!r}")
    cap = textbook.get("cache_max_mb")
    if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int) or cap < 0):
        errors.append(f"textbook.cache_max_mb: must be a non-negative int, got {cap!r}")
    for key in ("window_subprocess", "structured_output", "citations"):
        flag = textbook.get(key)
        if flag is not None and not isinstance(flag, bool):
            errors.append(f"textbook.{key}: must be a bool, got {flag!r}")
    return errors


def _queue_block_errors(queue: dict[str, Any]) -> list[str]:
    """Positive-number rules for the ``queue`` worker knobs (Wave 1)."""
    errors: list[str] = []
    for key in ("poll_seconds", "stale_lease_seconds", "max_attempts"):
        value = queue.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            errors.append(f"queue.{key}: must be a positive int, got {value!r}")
    return errors


def _fts_debounce_errors(debounce: object) -> list[str]:
    """Positive-number rule for ``retrieval.fts_rebuild_debounce_s``."""
    if isinstance(debounce, bool) or not isinstance(debounce, (int, float)):
        return [
            f"retrieval.fts_rebuild_debounce_s: must be a positive number, "
            f"got {type(debounce).__name__}"
        ]
    if debounce <= 0:
        return [f"retrieval.fts_rebuild_debounce_s: must be a positive number, got {debounce}"]
    return []


def _reranking_block_errors(reranking: dict[str, Any]) -> list[str]:
    """Enum rule for ``reranking.backend`` (shared rule in rerank_rules)."""
    error = backend_error(reranking.get("backend"))
    return [error] if error is not None else []


def _return_top_k_errors(reranking: object) -> list[str]:
    """Positive-int range rule for ``reranking.return_top_k`` (shared rule)."""
    if not isinstance(reranking, dict):
        return []
    error = return_top_k_error(reranking.get("return_top_k"))
    return [error] if error is not None else []
