"""Tests for the legacy config namespace alias layer (config v2, W1).

The canonical namespaces are ``storage.*``, ``ingest.chunking.*``,
``ingest.contextualize.*``, ``retrieval.*`` and ``reranking.*``. Every
mapping in the plan table is exercised here: value copy-in, canonical
precedence, the candidates split, block renames, and the deprecation
warning.
"""

from __future__ import annotations

import logging
from typing import cast

from corpus_kb.config_aliases import apply_legacy_aliases, used_legacy_keys


def _get(config: dict[str, object], dotted: str) -> object:
    node: object = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def test_search_scalars_move_to_retrieval() -> None:
    config: dict[str, object] = {
        "search": {
            "rrf_k": 42,
            "exclude_degraded": False,
            "qa_context_in_fts": True,
            "exact_filter_selectivity_threshold": 0.2,
            "candidate_multiplier": 5,
            "matryoshka_enabled": False,
            "matryoshka_dim": 512,
        }
    }
    apply_legacy_aliases(config)
    assert _get(config, "retrieval.rrf_k") == 42
    assert _get(config, "retrieval.exclude_degraded") is False
    assert _get(config, "retrieval.qa_context_in_fts") is True
    assert _get(config, "retrieval.exact_filter_selectivity_threshold") == 0.2
    assert _get(config, "retrieval.candidate_multiplier") == 5
    assert _get(config, "retrieval.matryoshka_enabled") is False
    assert _get(config, "retrieval.matryoshka_dim") == 512


def test_self_query_block_moves_wholesale() -> None:
    config: dict[str, object] = {
        "search": {"self_query": {"enabled": False, "model": "qwen3:0.6b"}}
    }
    apply_legacy_aliases(config)
    block = cast(dict[str, object], _get(config, "retrieval.self_query"))
    assert block == {"enabled": False, "model": "qwen3:0.6b"}


def test_candidates_per_branch_feeds_both_split_keys() -> None:
    config: dict[str, object] = {"search": {"candidates_per_branch": 77}}
    apply_legacy_aliases(config)
    assert _get(config, "retrieval.vector_candidates") == 77
    assert _get(config, "retrieval.lexical_candidates") == 77


def test_candidates_alias_only_fills_missing_split_keys() -> None:
    config: dict[str, object] = {
        "search": {"candidates_per_branch": 77},
        "retrieval": {"vector_candidates": 20},
    }
    apply_legacy_aliases(config)
    assert _get(config, "retrieval.vector_candidates") == 20  # canonical wins
    assert _get(config, "retrieval.lexical_candidates") == 77  # legacy fills


def test_hnsw_block_moves_key_by_key() -> None:
    config: dict[str, object] = {
        "search": {"hnsw": {"iterative_scan": "off", "ef_search": 100}},
        "retrieval": {"hnsw": {"ef_search": 300}},
    }
    apply_legacy_aliases(config)
    hnsw = cast(dict[str, object], _get(config, "retrieval.hnsw"))
    assert hnsw["ef_search"] == 300  # canonical key wins inside the block
    assert hnsw["iterative_scan"] == "off"  # missing canonical key filled


def test_rerank_block_moves_to_reranking() -> None:
    config: dict[str, object] = {
        "search": {"rerank": {"enabled": False, "candidates": 40, "calibration": "none"}}
    }
    apply_legacy_aliases(config)
    assert _get(config, "reranking.enabled") is False
    assert _get(config, "reranking.candidates") == 40
    assert _get(config, "reranking.calibration") == "none"


def test_hierarchy_keys_move_to_chunking() -> None:
    config: dict[str, object] = {
        "ingest": {
            "hierarchy": {
                "enabled": False,
                "child_tokens": 96,
                "chunk_tokens": 384,
                "parent_tokens": 1024,
                "overlap_tokens": 32,
                "respect_boundaries": ["heading"],
            }
        }
    }
    apply_legacy_aliases(config)
    assert _get(config, "ingest.chunking.enabled") is False
    assert _get(config, "ingest.chunking.child_tokens") == 96
    assert _get(config, "ingest.chunking.chunk_tokens") == 384
    assert _get(config, "ingest.chunking.parent_tokens") == 1024
    assert _get(config, "ingest.chunking.overlap_tokens") == 32
    assert _get(config, "ingest.chunking.respect_boundaries") == ["heading"]


def test_expand_to_parent_moves_to_retrieval() -> None:
    config: dict[str, object] = {"ingest": {"hierarchy": {"expand_to_parent": True}}}
    apply_legacy_aliases(config)
    assert _get(config, "retrieval.expand_to_parent") is True


def test_contextual_block_moves_to_contextualize() -> None:
    config: dict[str, object] = {
        "contextual": {
            "enabled": True,
            "enabled_source_types": ["interview"],
            "model": "qwen3:0.6b",
            "temperature": 0.5,
            "live_fallback": True,
        }
    }
    apply_legacy_aliases(config)
    assert _get(config, "ingest.contextualize.enabled") is True
    assert _get(config, "ingest.contextualize.enabled_source_types") == ["interview"]
    assert _get(config, "ingest.contextualize.model") == "qwen3:0.6b"
    assert _get(config, "ingest.contextualize.temperature") == 0.5
    assert _get(config, "ingest.contextualize.live_fallback") is True


def test_canonical_present_legacy_ignored_silently(
    caplog: logging.LogCaptureFixture,
) -> None:
    config: dict[str, object] = {
        "search": {"rrf_k": 10},
        "retrieval": {"rrf_k": 42},
    }
    with caplog.at_level(logging.WARNING):
        apply_legacy_aliases(config)
    assert _get(config, "retrieval.rrf_k") == 42  # canonical untouched
    assert not any("search.rrf_k" in r.message for r in caplog.records)


def test_alias_use_logs_one_deprecation_warning_naming_both_keys(
    caplog: logging.LogCaptureFixture,
) -> None:
    config: dict[str, object] = {"search": {"rrf_k": 42}}
    with caplog.at_level(logging.WARNING):
        apply_legacy_aliases(config)
    records = [r for r in caplog.records if "search.rrf_k" in r.message]
    assert len(records) == 1
    assert "retrieval.rrf_k" in records[0].message


def test_used_legacy_keys_lists_raw_presence() -> None:
    config: dict[str, object] = {
        "search": {"rrf_k": 1, "rerank": {"enabled": True}},
        "ingest": {"hierarchy": {"child_tokens": 8}},
    }
    present = used_legacy_keys(config)
    assert "search.rrf_k" in present
    assert "search.rerank" in present
    assert "ingest.hierarchy.child_tokens" in present


def test_absent_legacy_keys_are_a_no_op() -> None:
    config: dict[str, object] = {"retrieval": {"rrf_k": 42}}
    before = dict(config)
    apply_legacy_aliases(config)
    assert config == before
