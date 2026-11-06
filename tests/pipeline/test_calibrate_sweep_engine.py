"""Unit tests for the scratch-tenant sweep engine (no database needed)."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import UUID

import asyncpg
import pytest

from corpus_kb.tools.pipeline.calibrate_sweep_engine import ScratchTenantEngine, tenant_for

_CFG: dict[str, object] = {"database": {"backend": "postgres"}}


def test_tenant_for_is_stable_per_profile_and_golden() -> None:
    first = tenant_for("starter", "a" * 64)
    again = tenant_for("starter", "a" * 64)
    other = tenant_for("starter", "b" * 64)
    assert first == again
    assert first != other
    assert isinstance(first, UUID)


async def test_duckdb_backend_opens_the_gateway(tmp_path: Path) -> None:
    """Issue #330 inversion: the sweep opens the DuckDB gateway, not a refusal."""
    pytest.importorskip("duckdb")  # the validation-gate job installs no duckdb extra
    from corpus_kb.storage.gateway import DuckDbGateway, is_gateway

    cfg: dict[str, object] = {
        "database": {
            "backend": "duckdb",
            "connection_string": f"duckdb:///{(tmp_path / 'sweep.duckdb').as_posix()}",
        },
        # CI pulls nomic-embed-text only; the packaged default (qwen3) 404s
        # there and the open() probe would raise instead of degrade. The
        # dimensions travel with the model (768) so the probe validates.
        "embedding": {
            "provider": "ollama",
            "model": "nomic-embed-text",
            "dimensions": 768,
        },
    }
    engine = ScratchTenantEngine(cfg, tenant_for("starter", "a" * 64), min_overlap=0.5)
    await engine.open()
    try:
        assert is_gateway(engine._pool)
        assert isinstance(engine._pool, DuckDbGateway)
    finally:
        await engine.close()


class _StubHandler:
    """Counts ingest_transcript calls; returns one doc id."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        type(self).calls += 1

    async def ingest_transcript(
        self, tenant_id: object, path: str, force: bool = False, validate_mode: str = "off"
    ) -> dict[str, object]:
        return {"doc_id": "doc-1"}

    calls = 0


async def test_ingest_runs_once_for_repeated_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_advance(pool: object, tenant: object) -> None:
        return None

    monkeypatch.setattr("corpus_kb.handlers.research_handler.ResearchHandler", _StubHandler)
    monkeypatch.setattr("corpus_kb.projections.catchup.advance_all_projections", fake_advance)
    engine = ScratchTenantEngine(_CFG, tenant_for("starter", "a" * 64), min_overlap=0.5)
    # The stub handler never touches the pool, so a bare object stands in.
    engine._pool = cast("asyncpg.Pool", object())
    sample = tmp_path / "s.txt"
    sample.write_text("hello\n", encoding="utf-8")
    first = await engine.ingest(sample, "transcript")
    second = await engine.ingest(sample, "transcript")
    assert first.doc_id == "doc-1"
    assert second == first
    assert _StubHandler.calls == 1
