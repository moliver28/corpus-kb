"""Compatibility shim for the old ``corpus_kb.ingest`` import path.

The hierarchy settings moved to ``corpus_kb.chunking.hierarchy_settings``.
The re-export below keeps ``corpus_kb.ingest`` imports working.
"""

from __future__ import annotations

from .hierarchy_settings import (
    HIERARCHY_CONFIG_DEFAULTS,
    HierarchySettings,
    load_hierarchy_settings,
)

__all__ = [
    "HIERARCHY_CONFIG_DEFAULTS",
    "HierarchySettings",
    "load_hierarchy_settings",
]
