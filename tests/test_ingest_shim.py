"""The corpus_kb.ingest compat shim still re-exports the hierarchy settings.

The settings moved to corpus_kb.chunking.hierarchy_settings (issue 239
naming debt, PR #255); this pins both import paths to the SAME objects.
"""

from __future__ import annotations

import corpus_kb.ingest.hierarchy_settings as shim_module
from corpus_kb.chunking.hierarchy_settings import (
    HIERARCHY_CONFIG_DEFAULTS,
    HierarchySettings,
    load_hierarchy_settings,
)


def test_ingest_shim_reexports_match_the_chunking_module() -> None:
    import corpus_kb.ingest

    assert corpus_kb.ingest.HierarchySettings is HierarchySettings
    assert corpus_kb.ingest.load_hierarchy_settings is load_hierarchy_settings
    assert shim_module.HIERARCHY_CONFIG_DEFAULTS is HIERARCHY_CONFIG_DEFAULTS
