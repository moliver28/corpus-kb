"""Lockstep pins for the accepted legacy config keys (W2 compat spine).

One frozen inventory lists every legacy key the config layer accepts.
Each declared alias keeps its mapping-table resolution, the structural
``search.candidates_per_branch`` split keeps feeding both candidate keys,
and ``load_config`` keeps calling both alias layers. A removed alias row
or a dropped loader call fails these tests instead of dying silently in
a user's config.

Known gap, not pinned here: a legacy key whose canonical key holds a
default never wins through ``load_config`` — the defaults merge runs
before the alias layer, so the canonical-present branch always fires.
The alias tables and their resolution stay pinned at the alias layer;
a follow-up issue tracks the loader precedence fix.
"""

from __future__ import annotations

from typing import cast

import pytest

from corpus_kb.backend_gates import _effective_extraction_backend
from corpus_kb.config import (
    apply_extraction_backend_alias,
    load_config,
)
from corpus_kb.config_aliases import _ALIAS_RENAMES, _BLOCK_RENAMES, apply_legacy_aliases
from corpus_kb.rag.reranker import IdentityReranker, PgmlReranker, create_reranker

#: Every one-to-one legacy rename the alias table declares. Exact-match
#: pinned: a dropped row fails the inventory test; an undeclared row
#: fails it too, so every new alias lands here on purpose.
FROZEN_RENAMES: frozenset[tuple[str, str]] = frozenset(
    {
        ("search.rrf_k", "retrieval.rrf_k"),
        ("search.exclude_degraded", "retrieval.exclude_degraded"),
        (
            "search.exact_filter_selectivity_threshold",
            "retrieval.exact_filter_selectivity_threshold",
        ),
        ("search.candidate_multiplier", "retrieval.candidate_multiplier"),
        ("search.matryoshka_enabled", "retrieval.matryoshka_enabled"),
        ("search.matryoshka_dim", "retrieval.matryoshka_dim"),
        ("search.self_query", "retrieval.self_query"),
        ("ingest.hierarchy.enabled", "ingest.chunking.enabled"),
        ("ingest.hierarchy.child_tokens", "ingest.chunking.child_tokens"),
        ("ingest.hierarchy.chunk_tokens", "ingest.chunking.chunk_tokens"),
        ("ingest.hierarchy.parent_tokens", "ingest.chunking.parent_tokens"),
        ("ingest.hierarchy.overlap_tokens", "ingest.chunking.overlap_tokens"),
        ("ingest.hierarchy.respect_boundaries", "ingest.chunking.respect_boundaries"),
        ("ingest.hierarchy.expand_to_parent", "retrieval.expand_to_parent"),
        ("contextual.enabled", "ingest.contextualize.enabled"),
        ("contextual.enabled_source_types", "ingest.contextualize.enabled_source_types"),
        ("contextual.model", "ingest.contextualize.model"),
        ("contextual.base_url", "ingest.contextualize.base_url"),
        ("contextual.temperature", "ingest.contextualize.temperature"),
        ("contextual.fixture_dir", "ingest.contextualize.fixture_dir"),
        ("contextual.live_fallback", "ingest.contextualize.live_fallback"),
    }
)

#: The two whole-block renames.
FROZEN_BLOCKS: frozenset[tuple[str, str, str]] = frozenset(
    {("search", "rerank", "reranking"), ("search", "hnsw", "retrieval.hnsw")}
)

#: The one structural alias: feeds two split keys (handled in code).
STRUCTURAL_LEGACY = "search.candidates_per_branch"

SENTINEL = "w2-lockstep-sentinel"


def _get(config: dict[str, object], dotted: str) -> object:
    node: object = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _config_with_pairs(*pairs: tuple[str, object]) -> dict[str, object]:
    """A config dict holding each dotted key at ``value``."""
    config: dict[str, object] = {}
    for dotted, value in pairs:
        node = config
        parts = dotted.split(".")
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = cast(dict[str, object], child)
        node[parts[-1]] = value
    return config


# ---------------------------------------------------------------------------
# Inventory lockstep: the declared tables match the frozen lists exactly.
# ---------------------------------------------------------------------------


def test_rename_rows_match_the_frozen_inventory() -> None:
    assert frozenset(_ALIAS_RENAMES) == FROZEN_RENAMES


def test_block_rows_match_the_frozen_inventory() -> None:
    assert frozenset(_BLOCK_RENAMES) == FROZEN_BLOCKS


# ---------------------------------------------------------------------------
# Alias-layer resolution: every declared mapping copies legacy into
# canonical when canonical is absent, and canonical wins when present.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("legacy", "canonical"), sorted(FROZEN_RENAMES))
def test_rename_resolves_legacy_into_canonical(legacy: str, canonical: str) -> None:
    config = _config_with_pairs((legacy, SENTINEL))
    apply_legacy_aliases(config)
    assert _get(config, canonical) == SENTINEL


@pytest.mark.parametrize(("legacy", "canonical"), sorted(FROZEN_RENAMES))
def test_rename_leaves_canonical_in_place_when_present(legacy: str, canonical: str) -> None:
    config = _config_with_pairs((legacy, SENTINEL), (canonical, "canonical-value"))
    apply_legacy_aliases(config)
    assert _get(config, canonical) == "canonical-value"


def test_structural_candidates_alias_feeds_both_split_keys() -> None:
    config = _config_with_pairs((STRUCTURAL_LEGACY, 77))
    apply_legacy_aliases(config)
    assert _get(config, "retrieval.vector_candidates") == 77
    assert _get(config, "retrieval.lexical_candidates") == 77


@pytest.mark.parametrize(("section", "legacy_block", "canonical_block"), sorted(FROZEN_BLOCKS))
def test_block_rename_moves_keys_key_by_key(
    section: str, legacy_block: str, canonical_block: str
) -> None:
    config: dict[str, object] = {section: {legacy_block: {"enabled": False, "extra": 3}}}
    apply_legacy_aliases(config)
    assert _get(config, f"{canonical_block}.enabled") is False
    assert _get(config, f"{canonical_block}.extra") == 3


# ---------------------------------------------------------------------------
# Loader wiring: load_config runs both alias layers. The alias layer sees
# the merged tree, so these spies only prove the calls happen — the
# defaults-precedence gap is tracked separately (module docstring).
# ---------------------------------------------------------------------------


def _write_probe_config(tmp_path, monkeypatch: pytest.MonkeyPatch, payload: str) -> None:
    path = tmp_path / "w2-alias-probe.yaml"
    path.write_text(payload, encoding="utf-8")
    monkeypatch.setenv("CORPUS_KB_CONFIG", str(path))


def test_load_config_calls_the_legacy_alias_layer(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_probe_config(tmp_path, monkeypatch, "search:\n  rrf_k: 41\n")
    calls: list[object] = []
    monkeypatch.setattr(
        "corpus_kb.config.apply_legacy_aliases",
        lambda config: calls.append(dict(config)),
    )
    load_config()
    assert len(calls) == 1


def test_load_config_calls_the_extraction_alias_layer(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_probe_config(tmp_path, monkeypatch, "extraction:\n  backend: regex\n")
    calls: list[object] = []
    monkeypatch.setattr(
        "corpus_kb.config.apply_extraction_backend_alias",
        lambda config: calls.append(dict(config)),
    )
    load_config()
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Direct legacy reads through the real loader: the search block survives
# the merge, so the reranker consumers still see their legacy keys.
# ---------------------------------------------------------------------------


def test_search_reranker_selector_survives_the_loader(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_probe_config(
        tmp_path, monkeypatch, "search:\n  reranker: pgml\n  reranker_model: enc/small\n"
    )
    config = load_config()
    reranker = create_reranker(config)
    assert isinstance(reranker, PgmlReranker)
    assert reranker.model == "enc/small"


def test_search_reranker_none_selector_survives_the_loader(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_probe_config(tmp_path, monkeypatch, "search:\n  reranker: none\n")
    assert isinstance(create_reranker(load_config()), IdentityReranker)


# ---------------------------------------------------------------------------
# Extraction alias: extraction.backend wins and mirrors into
# graph.extractor; graph.extractor stays authoritative when no canonical
# backend is set (helper-level truth — the loader default overrides it,
# see the module docstring).
# ---------------------------------------------------------------------------


def test_extraction_backend_wins_and_mirrors_into_graph_extractor() -> None:
    config: dict[str, object] = {
        "extraction": {"backend": "regex"},
        "graph": {"extractor": "gliner"},
    }
    apply_extraction_backend_alias(config)
    graph = cast(dict[str, object], config["graph"])
    assert graph["extractor"] == "regex"
    assert _effective_extraction_backend(config) == "regex"


def test_graph_extractor_stays_authoritative_without_canonical_backend() -> None:
    config: dict[str, object] = {"graph": {"extractor": "gliner"}}
    assert _effective_extraction_backend(config) == "gliner"
