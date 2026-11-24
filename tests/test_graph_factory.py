"""Graph store factory selection tests (parity program PR-6: G09).

The factory is the last line of defense for hand-built config dicts: a
``graph.backend: postgres`` choice under the DuckDB engine must meet the
same typed refusal the loader gives user files — never a silent
DuckDbGraphStore build.
"""

from __future__ import annotations

from typing import Any

import pytest

from corpus_kb.errors import ConfigError
from corpus_kb.storage.graph_factory import create_graph_store


@pytest.mark.asyncio
async def test_postgres_graph_backend_under_duck_refused() -> None:
    """A hand-built duck dict naming postgres gets the G09 refusal."""
    cfg: dict[str, Any] = {
        "database": {"backend": "duckdb"},
        "graph": {"backend": "postgres"},
    }
    with pytest.raises(ConfigError) as excinfo:
        await create_graph_store(cfg, source=None)
    assert "graph.backend: postgres cannot run on the DuckDB engine." in str(excinfo.value)


@pytest.mark.asyncio
async def test_duck_graph_backend_builds_under_duck(tmp_path: Any) -> None:
    """The guard only fires on postgres: the duck selection passes it."""
    cfg: dict[str, Any] = {
        "database": {
            "backend": "duckdb",
            "connection_string": f"duckdb:///{tmp_path / 'guard_graph.duckdb'}",
        },
        "graph": {"backend": "duckdb"},
    }
    store = await create_graph_store(cfg, source=None)
    assert store is not None
    await store.close()
