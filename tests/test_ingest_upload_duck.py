"""DuckDB multipart upload route parity (parity PR-8, gap U2).

The upload operation is HTTP-only by catalogue ruling, so its backend
leg is the ROUTE, not a tool call: a real ``startup_duckdb`` boot, its
wired HTTP app, one multipart POST. The round trip proves upload ->
pipeline -> store -> read-back on the file backend; the refusal test
pins the typed taxonomy envelopes (a wrong content type is the typed
415, a form without a file field the typed 400).

The module name keeps the ``test_ingest*`` filter arm: upload -> embed
genuinely needs the live embedder, so the CI path gate arms the
Ollama-up legs on this file (intentional, per the parity plan).
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient

from tests.live_embed import live_embed_dimensions, live_embed_model

pytestmark = [pytest.mark.ollama]

UPLOAD_BODY = b"Corpus upload parity body. " * 4


@pytest.fixture()
async def upload_world(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """One migrated DuckDB file behind a REAL startup_duckdb boot.

    Same shape as the backend-operation parity fixture; the boot's wired
    HTTP app rides the result, and the route pool seam points at the
    gateway so the upload route resolves the same store.
    """
    import yaml

    from corpus_kb._setup.migrate import run_duckdb_migrations
    from corpus_kb.api import research_http
    from corpus_kb.handlers import command_handler as command_module
    from corpus_kb.handlers import (
        graph_handler,
        idempotency,
        query_handler,
        router_handler,
        tag_handler,
        versioning_handler,
    )
    from corpus_kb.server_wiring_duckdb import startup_duckdb
    from corpus_kb.storage.gateway import DuckDbGateway

    command_module.reset_command_handler()
    idempotency.reset_idempotency_checker()
    graph_handler.reset_graph_handler()
    tag_handler.reset_tag_handler()
    versioning_handler.reset_versioning_handler()
    router_handler.reset_router_handler()
    monkeypatch.setattr(query_handler, "_query_handler", None)

    db_path = tmp_path / "upload.duckdb"
    dsn = f"duckdb:///{db_path.as_posix()}"
    run_duckdb_migrations(dsn)

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "database:\n"
        "  backend: duckdb\n"
        f"  connection_string: {db_path.as_posix()}\n"
        "graph:\n"
        "  backend: duckdb\n"
        "extraction:\n"
        "  backend: regex\n"
        "embedding:\n"
        "  provider: ollama\n"
        f"  model: {live_embed_model()}\n"
        f"  dimensions: {live_embed_dimensions()}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CORPUS_KB_CONFIG", str(cfg_path))
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", dsn)

    cfg = cast("dict[str, Any]", yaml.safe_load(cfg_path.read_text(encoding="utf-8")))
    services = await startup_duckdb(cfg, dsn)
    gateway = services["gateway"]
    assert isinstance(gateway, DuckDbGateway)
    research_http.set_research_pool(gateway)
    yield {"gateway": gateway, "app": services["http_app"]}
    research_http.set_research_pool(None)
    await gateway.close()
    from corpus_kb.domain.application import reset_app

    reset_app()


async def test_ingest_upload_round_trip_duck(upload_world: dict[str, Any]) -> None:
    """Multipart POST -> success receipt -> the document reads back."""
    gateway = upload_world["gateway"]
    transport = ASGITransport(app=upload_world["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/ingest/upload",
            files={"file": ("parity-upload.txt", UPLOAD_BODY, "text/plain")},
        )
    assert response.status_code == 200
    envelope = response.json()
    # "partial" is the honest degraded receipt: with Ollama down the
    # embedder returns zero vectors, the document still stores whole.
    # Armed CI answers "success" with the live embedder.
    assert envelope.get("status") in ("success", "partial"), envelope
    doc_id = envelope.get("document_id")
    assert doc_id, f"no document id in the upload receipt: {sorted(envelope)}"
    stored = await gateway.fetchval(
        "SELECT count(*) FROM documents WHERE doc_id::uuid = $1::uuid", doc_id
    )
    assert stored == 1
    chunks = await gateway.fetchval(
        "SELECT count(*) FROM chunks WHERE doc_id::uuid = $1::uuid", doc_id
    )
    assert chunks >= 1


async def test_ingest_upload_rejects_bad_field_duck(upload_world: dict[str, Any]) -> None:
    """A wrong content type and a missing file field are typed refusals.

    The 415 is the route's honest answer to a non-multipart body; the
    400 names the missing field. Neither touches the store.
    """
    transport = ASGITransport(app=upload_world["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        wrong_type = await client.post("/api/ingest/upload", json={"file_path": "x"})
        assert wrong_type.status_code == 415
        body = wrong_type.json()
        assert body["status"] == "error"
        assert body["error_type"] == "unsupported_media_type"

        missing_field = await client.post(
            "/api/ingest/upload",
            files={"not_file": ("x.txt", b"body", "text/plain")},
        )
        assert missing_field.status_code == 400
        refused = missing_field.json()
        assert refused["status"] == "error"
        assert refused["error_type"] == "validation_error"
