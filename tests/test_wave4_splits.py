"""Wave W4-1 guard: the capped-file splits keep their import seams.

Pins three contracts the later W4 tasks build on: the surface registry
re-exports the wrapper table from its split module, the wrapper count
matches the harness-pack pin, and the chunk-write module exposes the
three module functions the claim branch needs.
"""

from __future__ import annotations


def test_surface_registry_reexports_wrappers() -> None:
    from corpus_kb.surface_registry import ACTIVE_WRAPPERS, WRAPPERS

    assert len(WRAPPERS) == 31  # the tests/test_harness_pack.py pin
    assert 0 < len(ACTIVE_WRAPPERS) <= len(WRAPPERS)


def test_ingest_store_chunks_exposes_write_functions() -> None:
    from corpus_kb.tools.ingest_store_chunks import (
        store_chunks_duckdb,
        store_vectors,
        vector_row,
    )

    assert callable(store_chunks_duckdb)
    assert callable(store_vectors)
    assert callable(vector_row)
