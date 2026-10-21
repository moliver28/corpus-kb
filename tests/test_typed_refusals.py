"""Typed-refusal tests for the not-yet-ported DuckDB handler legs.

server_wiring_duckdb leaves the query handler and the research HTTP pool
unset on the DuckDB backend; the routes must fail with the error taxonomy
(DependencyMissingError, zero-drift rule 4), never a bare RuntimeError
that maps to 500. Hermetic: no server, no database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from corpus_kb.errors import DependencyMissingError, error_status

if TYPE_CHECKING:
    from pathlib import Path


def test_get_query_handler_refuses_when_unset_on_duckdb(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_kb.handlers.query_handler as qh

    monkeypatch.setattr(qh, "_query_handler", None)
    with pytest.raises(DependencyMissingError, match="not ported to the DuckDB backend"):
        qh.get_query_handler(None)


def test_dependency_missing_maps_to_503() -> None:
    assert error_status(DependencyMissingError.error_type) == 503


async def test_research_pool_refuses_the_duckdb_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from corpus_kb.api import research_http

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "database:\n"
        "  backend: duckdb\n"
        "  connection_string: C:/data/kb.duckdb\n"
        "graph:\n"
        "  backend: postgres\n"
        "extraction:\n"
        "  backend: regex\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CORPUS_KB_CONFIG", str(cfg))
    monkeypatch.setattr(research_http, "_pool", None)
    with pytest.raises(DependencyMissingError, match="Postgres pool"):
        await research_http.research_pool()
