"""Legacy config namespace aliases (config v2, wave W1).

The canonical namespaces are ``storage.*``, ``ingest.chunking.*``,
``ingest.contextualize.*``, ``retrieval.*`` and ``reranking.*``. The
pre-v2 namespaces (``search.*``, ``ingest.hierarchy.*``, ``contextual.*``)
stay readable as aliases so existing user configs keep working.

Precedence (locked decision 3 of the config-alignment program):

* canonical key present -> the legacy key is ignored silently;
* canonical key absent -> the legacy value is copied in and ONE
  deprecation warning names both keys.

:func:`apply_legacy_aliases` runs inside ``config.load_config`` right
after the YAML merge, BEFORE the typed readers and the cross-field gates
read the config, so every consumer only ever sees canonical keys.
Mutates ``config`` in place; safe on any dict shape.
"""

from __future__ import annotations

import logging
from typing import cast

logger = logging.getLogger(__name__)

# (legacy dotted key, canonical dotted key) — one warning per applied alias.
# `search.candidates_per_branch` feeds BOTH split candidate keys through the
# same single legacy key, so it is handled structurally (see below), not via
# this one-to-one table.
_ALIAS_RENAMES: tuple[tuple[str, str], ...] = (
    ("search.rrf_k", "retrieval.rrf_k"),
    ("search.exclude_degraded", "retrieval.exclude_degraded"),
    ("search.qa_context_in_fts", "retrieval.qa_context_in_fts"),
    ("search.exact_filter_selectivity_threshold", "retrieval.exact_filter_selectivity_threshold"),
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
)

# Whole-block renames: (section, legacy block name, canonical block path).
# The legacy block maps onto the canonical block key by key (same key names
# inside the block).
_BLOCK_RENAMES: tuple[tuple[str, str, str], ...] = (
    ("search", "rerank", "reranking"),
    ("search", "hnsw", "retrieval.hnsw"),
)


def _node(config: dict[str, object], dotted: str) -> object:
    """Read a dotted path; None when any level is missing or not a mapping."""
    node: object = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _ensure_node(config: dict[str, object], dotted: str) -> dict[str, object]:
    """Walk/create the parent mapping of a dotted path, returning the parent."""
    node: dict[str, object] = config
    parts = dotted.split(".")
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = cast(dict[str, object], child)
    return node


def _set_dotted(config: dict[str, object], dotted: str, value: object) -> None:
    parent = _ensure_node(config, dotted)
    parent[dotted.split(".")[-1]] = value


def _warn(legacy: str, canonical: str) -> None:
    logger.warning(
        "Deprecated config key '%s' — rename to '%s' (the legacy key is still read).",
        legacy,
        canonical,
    )


def apply_legacy_aliases(config: dict[str, object]) -> None:
    """Copy legacy namespace values into canonical keys when canonical is absent.

    Canonical present -> legacy ignored silently. Canonical absent and
    legacy present -> value copied + one deprecation warning naming both
    keys. Mutates ``config`` in place.
    """
    for legacy, canonical in _ALIAS_RENAMES:
        legacy_value = _node(config, legacy)
        if legacy_value is None:
            continue
        if _node(config, canonical) is not None:
            continue
        _set_dotted(config, canonical, legacy_value)
        _warn(legacy, canonical)

    # `search.candidates_per_branch` feeds BOTH split candidate keys when
    # the split keys are absent (one legacy knob armed two arms).
    legacy_branches = _node(config, "search.candidates_per_branch")
    if legacy_branches is not None:
        targets = ("retrieval.vector_candidates", "retrieval.lexical_candidates")
        missing = [t for t in targets if _node(config, t) is None]
        if missing:
            for target in missing:
                _set_dotted(config, target, legacy_branches)
            _warn("search.candidates_per_branch", " and ".join(missing))

    # Whole-block renames: per-key, so a partial canonical block still wins
    # key by key instead of being clobbered wholesale.
    for section, legacy_block, canonical_block in _BLOCK_RENAMES:
        section_node = _node(config, section)
        if not isinstance(section_node, dict) or legacy_block not in section_node:
            continue
        block = cast(dict[str, object], section_node[legacy_block])
        if not isinstance(block, dict):
            continue
        for key, value in block.items():
            canonical_dotted = f"{canonical_block}.{key}"
            if _node(config, canonical_dotted) is not None:
                continue
            _set_dotted(config, canonical_dotted, value)
            _warn(f"{section}.{legacy_block}.{key}", canonical_dotted)


def used_legacy_keys(config: dict[str, object]) -> list[str]:
    """Legacy keys still present in ``config`` (diagnostics/tests helper).

    Returns the dotted legacy names that would feed the alias layer —
    canonical presence does NOT hide them here; this lists raw presence.
    """
    present: list[str] = []
    for legacy, _ in _ALIAS_RENAMES:
        if _node(config, legacy) is not None:
            present.append(legacy)
    if _node(config, "search.candidates_per_branch") is not None:
        present.append("search.candidates_per_branch")
    for section, legacy_block, _ in _BLOCK_RENAMES:
        section_node = _node(config, section)
        if isinstance(section_node, dict) and legacy_block in section_node:
            present.append(f"{section}.{legacy_block}")
    return present


__all__ = ["apply_legacy_aliases", "used_legacy_keys"]
