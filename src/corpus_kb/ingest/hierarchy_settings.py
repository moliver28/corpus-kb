"""Compatibility shim for the hierarchy settings move.

The module now lives in ``corpus_kb.chunking.hierarchy_settings``. This
path keeps working for imports made before the move. In-repo imports use
the new path.
"""

from __future__ import annotations

from corpus_kb.chunking.hierarchy_settings import (
    CHUNKING_CONFIG_DEFAULTS,
    DEFAULT_CHILD_TOKENS,
    DEFAULT_CHUNK_TOKENS,
    DEFAULT_OVERLAP_TOKENS,
    DEFAULT_PARENT_TOKENS,
    DEFAULT_RESPECT_BOUNDARIES,
    HIERARCHY_CONFIG_DEFAULTS,
    HierarchySettings,
    load_hierarchy_settings,
)

__all__ = [
    "CHUNKING_CONFIG_DEFAULTS",
    "DEFAULT_CHILD_TOKENS",
    "DEFAULT_CHUNK_TOKENS",
    "DEFAULT_OVERLAP_TOKENS",
    "DEFAULT_PARENT_TOKENS",
    "DEFAULT_RESPECT_BOUNDARIES",
    "HIERARCHY_CONFIG_DEFAULTS",
    "HierarchySettings",
    "load_hierarchy_settings",
]
