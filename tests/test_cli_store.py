"""Typed-bootstrap tests for the CLI store seam (storage portability).

Hermetic: the failure taxonomy of ``wired_store`` (zero-drift rule 4) and
one happy-path open per backend. No server, no Ollama; the locked-file
case holds a real DuckDB handle to force the gateway's typed lock error.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

duckdb = pytest.importorskip("duckdb")

from corpus_kb.errors import ConfigError, DatabaseUnavailableError, ValidationError  # noqa: E402


def _cfg(conn_str: str) -> dict[str, Any]:
    return {"database": {"connection_string": conn_str}}


async def test_wired_store_opens_the_duckdb_gateway_and_closes_it(tmp_path) -> None:
    from corpus_kb.cli.cli_store import wired_store
    from corpus_kb.storage.duckdb_gateway import DuckDbGateway

    conn_str = f"duckdb:///{(tmp_path / 'wired.duckdb').as_posix()}"
    async with wired_store(cfg=_cfg(conn_str)) as wired:
        assert isinstance(wired.store, DuckDbGateway)
        assert wired.embedder is None  # embedder=False by default
        assert await wired.store.fetchval("SELECT 40 + 2") == 42


async def test_wired_store_builds_the_research_embedder_on_duckdb(tmp_path) -> None:
    """``embedder=True`` on the duckdb path wires a ResearchEmbedder, not None."""
    from corpus_kb.cli.cli_store import wired_store
    from corpus_kb.projections.research._embed import ResearchEmbedder

    conn_str = f"duckdb:///{(tmp_path / 'wired-embedder.duckdb').as_posix()}"
    async with wired_store(embedder=True, cfg=_cfg(conn_str)) as wired:
        assert isinstance(wired.embedder, ResearchEmbedder)


async def test_wired_store_closes_the_store_when_the_embedder_build_fails(
    tmp_path, monkeypatch
) -> None:
    """An embedder construct failure closes the store, not leaks it (#523 P3-3)."""
    import corpus_kb.rag
    from corpus_kb.cli.cli_store import wired_store

    path = tmp_path / "wired-leak.duckdb"
    conn_str = f"duckdb:///{path.as_posix()}"

    def boom(cfg, store):
        raise RuntimeError("embedder construct failure")

    monkeypatch.setattr(corpus_kb.rag, "create_embedder", boom)
    with pytest.raises(RuntimeError, match="embedder construct failure"):
        async with wired_store(embedder=True, cfg=_cfg(conn_str)):
            pass  # never reached: the build raises before the yield
    # On Windows an open DuckDB file cannot be deleted; the unlink proves close().
    path.unlink()


@pytest.mark.requires_ollama
async def test_wired_store_duckdb_embedder_embeds_for_real(tmp_path) -> None:
    """Ollama up: the duckdb embedder returns a real 1024-d unit vector."""
    import math

    from corpus_kb._setup.migrate import run_migrations
    from corpus_kb.cli.cli_store import wired_store

    conn_str = f"duckdb:///{(tmp_path / 'wired-embed-live.duckdb').as_posix()}"
    await run_migrations(conn_str)
    async with wired_store(embedder=True, cfg=_cfg(conn_str)) as wired:
        vec = await wired.embedder.embed_cached(
            "00000000-0000-0000-0000-000000000001", "grid investment"
        )
        assert vec is not None, "Ollama is up: the embedder must embed, not abstain"
        assert len(vec) == 1024
        assert abs(math.sqrt(sum(x * x for x in vec)) - 1.0) < 1e-6


async def test_wired_store_refuses_a_fileless_duckdb_dsn() -> None:
    from corpus_kb.cli.cli_store import wired_store

    with pytest.raises(ConfigError, match="names no file"):
        async with wired_store(cfg=_cfg("duckdb:///")):
            pass


async def test_wired_store_maps_a_duckdb_open_failure_to_the_taxonomy(tmp_path) -> None:
    """A non-lock open failure (garbage file) raises DatabaseUnavailableError."""
    from corpus_kb.cli.cli_store import wired_store

    junk = tmp_path / "junk.duckdb"
    junk.write_bytes(b"definitely not a duckdb file" * 10)
    with pytest.raises(DatabaseUnavailableError, match="did not open"):
        async with wired_store(cfg=_cfg(f"duckdb:///{junk.as_posix()}")):
            pass


async def test_wired_store_keeps_the_typed_lock_error(tmp_path) -> None:
    """A held file handle trips the gateway's typed lock error unchanged."""
    from corpus_kb.cli.cli_store import wired_store

    path = str(tmp_path / "locked.duckdb")
    holder = duckdb.connect(path)
    try:
        with pytest.raises(DatabaseUnavailableError):
            async with wired_store(cfg=_cfg(f"duckdb:///{path}")):
                pass
    finally:
        holder.close()


async def test_wired_store_refuses_an_empty_postgres_dsn() -> None:
    from corpus_kb.cli.cli_store import wired_store

    with pytest.raises(ConfigError, match="No database connection string"):
        async with wired_store(cfg=_cfg("")):
            pass


async def test_wired_store_maps_a_malformed_postgres_dsn_to_config_error() -> None:
    from corpus_kb.cli.cli_store import wired_store

    with pytest.raises(ConfigError, match="not a usable postgresql:// DSN"):
        async with wired_store(cfg=_cfg("not a dsn at all")):
            pass


async def test_wired_store_maps_a_dead_postgres_to_database_unavailable() -> None:
    from corpus_kb.cli.cli_store import wired_store

    dead = f"postgresql://corpus_user:nope@127.0.0.1:1/{uuid4().hex}"
    with pytest.raises(DatabaseUnavailableError, match="did not accept a connection"):
        async with wired_store(cfg=_cfg(dead)):
            pass


async def test_wired_store_maps_a_dsn_shape_failure_to_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A converted DSN-shape failure keeps the friendly ConfigError translation.

    The gateway's DSN normalizer now raises the taxonomy's ValidationError
    (issue #229 wave 2); the CLI seam must still answer the master
    ConfigError message (exit 3), not the raw re-raise (exit 1).
    """
    import corpus_kb.storage.duckdb_gateway as duckdb_gateway_module
    from corpus_kb.cli.cli_store import wired_store

    async def refuse(_conn_str: str) -> None:
        raise ValidationError("Unsupported connection string scheme: 'mysql'")

    monkeypatch.setattr(duckdb_gateway_module.DuckDbGateway, "connect", refuse)
    with pytest.raises(ConfigError, match="is not a usable duckdb:/// DSN"):
        async with wired_store(cfg=_cfg("duckdb:///C:/data/x.duckdb")):
            pass
