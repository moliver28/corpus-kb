"""Runtime ``config.yaml`` type and cross-field validation.

``validate_runtime_config`` checks every dotted key in
``RUNTIME_CONFIG_KEYS`` for presence and type, then applies the
cross-field enum/range/backend-mix rules through the per-block helpers
in ``validate_configs_rules.py``.
"""

from __future__ import annotations

from typing import Any

try:
    # Package context (installed checkout, tests).
    from corpus_kb._setup.validate_configs_keys import (
        RUNTIME_CONFIG_KEYS,
        SUPPORTED_DUCKDB_GRAPH_BACKENDS,
        SUPPORTED_GRAPH_POLICY_MODES,
        SUPPORTED_RETRIEVAL_MODES,
    )
    from corpus_kb._setup.validate_configs_rules import (
        _contextualize_block_errors,
        _database_block_errors,
        _max_hops_errors,
        _queue_block_errors,
        _reranking_block_errors,
        _return_top_k_errors,
        _textbook_block_errors,
        _topics_block_errors,
    )
except ImportError:  # pragma: no cover - script context (CI validate job)
    import importlib as _importlib

    _keys = _importlib.import_module("validate_configs_keys")
    RUNTIME_CONFIG_KEYS = _keys.RUNTIME_CONFIG_KEYS
    SUPPORTED_DUCKDB_GRAPH_BACKENDS = _keys.SUPPORTED_DUCKDB_GRAPH_BACKENDS
    SUPPORTED_GRAPH_POLICY_MODES = _keys.SUPPORTED_GRAPH_POLICY_MODES
    SUPPORTED_RETRIEVAL_MODES = _keys.SUPPORTED_RETRIEVAL_MODES
    _rules = _importlib.import_module("validate_configs_rules")
    _contextualize_block_errors = _rules._contextualize_block_errors
    _database_block_errors = _rules._database_block_errors
    _max_hops_errors = _rules._max_hops_errors
    _queue_block_errors = _rules._queue_block_errors
    _reranking_block_errors = _rules._reranking_block_errors
    _return_top_k_errors = _rules._return_top_k_errors
    _textbook_block_errors = _rules._textbook_block_errors
    _topics_block_errors = _rules._topics_block_errors


def _validate_cross_field_rules(config: dict[str, Any]) -> list[str]:
    """U32-style cross-field and enum rules beyond per-key type checks."""
    errors: list[str] = []

    errors.extend(_database_block_errors(config))

    graph = config.get("graph")
    if isinstance(graph, dict):
        errors.extend(_max_hops_errors(graph.get("max_hops")))
        backend = graph.get("duckdb_graph_backend")
        if backend is not None and backend not in SUPPORTED_DUCKDB_GRAPH_BACKENDS:
            engines = ", ".join(sorted(SUPPORTED_DUCKDB_GRAPH_BACKENDS))
            errors.append(
                f"graph.duckdb_graph_backend: must be one of ({engines}), got {backend!r}"
            )

    graph_policy = config.get("graph_policy")
    if isinstance(graph_policy, dict):
        mode = graph_policy.get("mode")
        if mode not in SUPPORTED_GRAPH_POLICY_MODES:
            modes = ", ".join(sorted(SUPPORTED_GRAPH_POLICY_MODES))
            errors.append(f"graph_policy.mode: must be one of ({modes}), got {mode!r}")

    textbook = config.get("textbook")
    if isinstance(textbook, dict):
        errors.extend(_textbook_block_errors(textbook))

    queue = config.get("queue")
    if isinstance(queue, dict):
        errors.extend(_queue_block_errors(queue))

    retrieval = config.get("retrieval")
    if isinstance(retrieval, dict):
        mode = retrieval.get("mode")
        if mode not in SUPPORTED_RETRIEVAL_MODES:
            errors.append(
                f"retrieval.mode: must be one of {sorted(SUPPORTED_RETRIEVAL_MODES)}, got {mode!r}"
            )
        if retrieval.get("matryoshka_enabled") is True:
            embedding = config.get("embedding")
            mrl = embedding.get("mrl_trained") if isinstance(embedding, dict) else None
            if mrl is not True:
                errors.append(
                    "retrieval.matryoshka_enabled: true requires embedding.mrl_trained: true "
                    "(two-stage matryoshka retrieval front-slices embeddings, which is only "
                    "sound for Matryoshka-trained models; qwen3-embedding is MRL-trained, "
                    "nomic-embed-text is not)"
                )

    ingest = config.get("ingest")
    if isinstance(ingest, dict):
        contextualize = ingest.get("contextualize")
        if isinstance(contextualize, dict):
            errors.extend(_contextualize_block_errors(contextualize))

    topics = config.get("topics")
    if isinstance(topics, dict):
        errors.extend(_topics_block_errors(topics))

    reranking = config.get("reranking")
    if isinstance(reranking, dict):
        errors.extend(_reranking_block_errors(reranking))
    errors.extend(_return_top_k_errors(config.get("reranking")))
    return errors


def validate_runtime_config(config: dict[str, Any]) -> list[str]:
    """Validate the runtime config.yaml keys in RUNTIME_CONFIG_KEYS.

    Every key must be PRESENT (explicit in both config.yaml files — the
    default lives in code, but the shipped YAML is the visible contract)
    and hold the expected type. Also enforces the cross-field rules:
    ``retrieval.mode`` is enum-locked to hybrid_rrf,
    ``reranking.backend`` to null or the supported set,
    ``reranking.return_top_k`` is a positive int in range, and
    ``retrieval.matryoshka_enabled: true`` requires ``embedding.mrl_trained:
    true`` (the two-stage arm front-slices embeddings, which is only sound
    for Matryoshka-trained models; the runtime refuses the same combination
    in ``config.load_config``).
    """
    errors: list[str] = []
    for dotted, expected in RUNTIME_CONFIG_KEYS.items():
        node: Any = config
        parts = dotted.split(".")
        for part in parts:
            if not isinstance(node, dict) or part not in node:
                errors.append(f"missing required key '{dotted}' (expected {expected.__name__})")
                node = None
                break
            node = node[part]
        else:
            # bool is a subclass of int: never accept a bool where a real
            # int/float is expected, and require bool-exactness for bool keys.
            # A float-expected key ALSO accepts int (web/settings.py accepts
            # int at runtime, e.g. `delay_seconds: 1`).
            if expected is bool:
                type_ok = isinstance(node, bool)
            elif isinstance(node, bool):
                type_ok = False
            else:
                type_ok = isinstance(node, expected) or (
                    expected is float and isinstance(node, int)
                )
            if not type_ok:
                errors.append(
                    f"key '{dotted}' must be {expected.__name__}, got {type(node).__name__}"
                )
    errors.extend(_validate_cross_field_rules(config))
    return errors
