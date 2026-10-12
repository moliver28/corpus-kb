"""Ingest-side helpers (U48/P6 shadow hierarchy).

Currently: the typed multi-granularity hierarchy settings. The pipeline
orchestration itself stays in ``corpus_kb.tools.ingest_common``.
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
