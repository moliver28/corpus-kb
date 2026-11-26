"""The declared visibility folds into the unchanged probe (no silent skip).

Hermetic DuckDB E2E: the same text re-ingested under a changed visibility
declaration must NOT hit the ``file_hash`` short-circuit (the stamp would
never land), the same declaration still skips (dedupe preserved), and no
declaration keeps the legacy content-only skip. The runs are offline: the
embed stage is stubbed, no Ollama, no Postgres, no network.

Backend notes: on DuckDB the documents row is immutable once referenced,
so the probe reads the declared visibility from the ledger stages JSONB
and the row's own metadata stamp lands on the first insert only (the
accepted title gap). The Postgres upsert merge refreshes the row stamp;
``tests/test_ingest_integrity.py`` covers that path behind the
``requires_postgres`` mark.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("duckdb")

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
from corpus_kb.storage.gateway import DuckDbGateway
from corpus_kb.tools import ingest_common

pytestmark = [pytest.mark.asyncio]

TENANT = "00000000-0000-0000-0000-000000000001"
STUB_DIMS = 2560
DOC_PATH = "vis-probe.txt"
DOC_TEXT = (
    "Gateway routes requests to shard pools.\n\n"
    "Alerts page the on-call engineer after failures.\n\n"
    "The rollout window stays inside the maintenance slot."
)


def _stub_vector(text: str) -> list[float]:
    """A deterministic, never-all-zero vector derived from the chunk text."""
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
    return [((seed + i) % 997) / 997.0 + 0.001 for i in range(STUB_DIMS)]


def _config(visibility: str | None) -> dict[str, Any]:
    config: dict[str, Any] = {
        "database": {"backend": "duckdb"},
        "embedding": {"model": "stub-embed", "dimensions": STUB_DIMS},
        "graph": {"extract_entities": False},
        "ingest": {"contextualize": {"enabled": False}},
    }
    if visibility is not None:
        config["document"] = {"visibility": visibility}
    return config


@pytest.fixture
async def vis_gateway(tmp_path: Path):
    """A migrated tmp .duckdb file behind one DuckDbGateway."""
    path = tmp_path / "vis-probe.duckdb"
    await run_migrations(duckdb_path_to_conn_string(str(path)))
    gateway = await DuckDbGateway.connect(str(path))
    try:
        yield gateway
    finally:
        await gateway.close()
        # Close BEFORE delete: duckdb keeps a .wal sidecar while open.
        for suffix in ("", ".wal"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)


@pytest.fixture
def stub_embed(monkeypatch: pytest.MonkeyPatch):
    """The embed stage returns deterministic vectors (clean, non-degraded)."""

    async def fake_embed(
        chunks: list[Any],
        config: dict[str, Any],
        pool: Any = None,
        text_override: dict[str, str] | None = None,
        document_text: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        for chunk in chunks:
            source = (text_override or {}).get(str(chunk.chunk_id), chunk.text)
            chunk.embedding = _stub_vector(source)
        if meta is not None:
            meta["embedding_mode"] = "pooled"
        return False, None

    monkeypatch.setattr(ingest_common, "embed_chunks", fake_embed)


async def _run(gateway: DuckDbGateway, visibility: str | None) -> dict[str, Any]:
    return await ingest_common.run_pipeline(
        DOC_TEXT,
        "text",
        DOC_PATH,
        _config(visibility),
        gateway,
        TENANT,
    )


async def _row_metadata(gateway: DuckDbGateway) -> str | None:
    rows = await gateway.fetch(
        "SELECT metadata FROM documents WHERE tenant_id = $1 AND source = $2",
        TENANT,
        DOC_PATH,
    )
    assert len(rows) == 1
    raw = rows[0]["metadata"]
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    return parsed.get("visibility")


async def _latest_completed_stages(gateway: DuckDbGateway) -> dict[str, Any]:
    rows = await gateway.fetch(
        "SELECT stages FROM ingest_runs WHERE tenant_id = $1 AND status = 'completed' "
        "ORDER BY started_at DESC, run_id DESC LIMIT 1",
        TENANT,
    )
    assert len(rows) == 1
    raw = rows[0]["stages"]
    return json.loads(raw) if isinstance(raw, str) else raw


async def test_first_ingest_stamps_declared_visibility(
    vis_gateway: DuckDbGateway, stub_embed: None
) -> None:
    result = await _run(vis_gateway, "public")
    assert result["status"] == "success"
    assert await _row_metadata(vis_gateway) == "public"
    stages = await _latest_completed_stages(vis_gateway)
    assert stages["visibility"] == "public"


async def test_changed_visibility_reingests_instead_of_skipping(
    vis_gateway: DuckDbGateway, stub_embed: None
) -> None:
    assert (await _run(vis_gateway, "public"))["status"] == "success"
    # The regression: the old probe compared file_hash alone, so this call
    # returned a skipped envelope and the internal stamp never landed.
    changed = await _run(vis_gateway, "internal")
    assert changed["status"] == "success", changed
    stages = await _latest_completed_stages(vis_gateway)
    assert stages["visibility"] == "internal"


async def test_same_visibility_still_skips(vis_gateway: DuckDbGateway, stub_embed: None) -> None:
    assert (await _run(vis_gateway, "internal"))["status"] == "success"
    again = await _run(vis_gateway, "internal")
    assert again["status"] == "skipped"
    assert again["reason"] == "unchanged"


async def test_no_declaration_keeps_legacy_skip(
    vis_gateway: DuckDbGateway, stub_embed: None
) -> None:
    assert (await _run(vis_gateway, "internal"))["status"] == "success"
    legacy = await _run(vis_gateway, None)
    assert legacy["status"] == "skipped"
    assert legacy["reason"] == "unchanged"
