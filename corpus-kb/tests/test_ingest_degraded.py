"""Degraded-mode pipeline test (Ollama unavailable)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from src.config import load_config
from src.domain.models import DEFAULT_TENANT_ID
from src.storage.tenant_conn import tenant_connection
from src.tools.ingest_tools import ingest_file

_FIXTURE_DIR = Path(__file__).with_name("fixtures") / "langextract_recorded"
_SAMPLE_MD = Path(__file__).with_name("fixtures") / "ontology_sample.md"


def _build_degraded_config() -> dict[str, object]:
    """Build a config that points Ollama at a dead port."""
    config = load_config()
    graph = cast(dict[str, object], config.setdefault("graph", {}))
    graph["extractor"] = "langextract"
    graph["fixture_dir"] = str(_FIXTURE_DIR.resolve())
    graph["live_fallback"] = False
    embedding = cast(dict[str, object], config.setdefault("embedding", {}))
    embedding["model"] = "qwen3-embedding:8b"
    embedding["dimensions"] = 4096
    embedding["base_url"] = "http://localhost:99999"
    embedding["batch_size"] = 32
    return config


async def _delete_document(pg_pool, source: str) -> None:
    """See identical helper + rationale in tests/test_ingest_e2e.py: this
    test's fixed file-path source must start clean on every run since
    corpus_kb_test persists across test sessions and content-hash dedup
    (migration 007) would otherwise return "skipped" on any run after the
    first."""
    async with tenant_connection(pg_pool, DEFAULT_TENANT_ID) as conn:
        await conn.execute("DELETE FROM documents WHERE tenant_id = $1 AND source = $2", str(DEFAULT_TENANT_ID), source)


@pytest.mark.asyncio
async def test_degraded_mode_ollama_unavailable(pg_pool) -> None:
    """Ingest with a dead Ollama port — pipeline succeeds in degraded mode."""
    await _delete_document(pg_pool, str(_SAMPLE_MD))
    config = _build_degraded_config()
    result = await ingest_file(str(_SAMPLE_MD), pg_pool, config=config)

    assert result["status"] == "success"
    assert result["degraded"] is True
    assert isinstance(result["errors"], list)
    assert len(result["errors"]) >= 1
    entity_count = result["entity_count"]
    assert isinstance(entity_count, int)
    pg_chunk_count = result.get("pg_chunk_count", 0)
    assert isinstance(pg_chunk_count, int)
