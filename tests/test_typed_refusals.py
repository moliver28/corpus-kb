"""Typed-refusal tests for the uninitialized handler legs (issue #330).

The DuckDB server wires every handler now, so the old "pending their
port" refusals are gone. What stays typed is the LAZY surface: a route
that reaches a handler singleton before any server wired it fails with
the error taxonomy (DependencyMissingError, zero-drift rule 4), never a
bare RuntimeError that maps to 500. Hermetic: no server, no database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from corpus_kb.errors import DependencyMissingError, error_status

if TYPE_CHECKING:
    from pathlib import Path


def test_get_query_handler_refuses_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_kb.handlers.query_handler as qh

    monkeypatch.setattr(qh, "_query_handler", None)
    with pytest.raises(DependencyMissingError, match="not initialized"):
        qh.get_query_handler(None)


def test_dependency_missing_maps_to_503() -> None:
    assert error_status(DependencyMissingError.error_type) == 503


async def test_research_pool_opens_the_duckdb_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The research routes open the gateway on a duckdb config (issue #330).

    The old typed refusal is inverted: ``research_pool`` hands back the
    same engine the config names, so the socket arms and HTTP routes run
    on both backends with one store seam.
    """
    from corpus_kb.api import research_http
    from corpus_kb.storage.gateway import DuckDbGateway, is_gateway

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "database:\n"
        "  backend: duckdb\n"
        f"  connection_string: {tmp_path.as_posix()}/kb.duckdb\n"
        "graph:\n"
        "  backend: duckdb\n"
        "extraction:\n"
        "  backend: regex\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CORPUS_KB_CONFIG", str(cfg))
    # The env DSN overrides the file's connection_string (load_config
    # priority), so pin it to the same duckdb file: a leaked
    # CORPUS_KB_DATABASE_URL from another fixture would otherwise win.
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", f"{tmp_path.as_posix()}/kb.duckdb")
    # Clear the module global BEFORE and AFTER: a prior test's store must
    # not leak in, and the freshly opened gateway must not leak out (a
    # later research_pool() call would hand back this closed gateway).
    research_http._pool = None
    store = await research_http.research_pool()
    try:
        assert is_gateway(store)
        assert isinstance(store, DuckDbGateway)
    finally:
        await cast("DuckDbGateway", store).close()
        research_http._pool = None
