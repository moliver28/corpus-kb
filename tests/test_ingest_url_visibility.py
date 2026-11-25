"""The external-site visibility property: inject, refuse, and stamp."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_ingest_url_injects_visibility_into_pipeline_config(monkeypatch):
    from corpus_kb.tools.ingest_url_tools import ingest_url

    captured = {}

    async def fake_run_pipeline(text, source_type, source, config, pool, tenant_id, **kwargs):
        captured["config"] = config
        return {"status": "success", "pages": [], "total_documents": 0}

    import httpx

    def _stub_transport(request):
        return httpx.Response(
            200,
            text="<html><body><p>stub body</p></body></html>",
            headers={"content-type": "text/html"},
        )

    monkeypatch.setattr("corpus_kb.tools.ingest_url_tools.run_pipeline", fake_run_pipeline)
    result = await ingest_url(
        "https://example.com/page",
        pg_pool=None,
        config={},
        visibility="internal",
        transport=httpx.MockTransport(_stub_transport),
    )
    assert result["status"] == "success"
    assert captured["config"]["document"]["visibility"] == "internal"


@pytest.mark.asyncio
async def test_ingest_url_refuses_unknown_visibility():
    import httpx

    from corpus_kb.tools.ingest_url_tools import ingest_url

    def _stub_transport(request):
        return httpx.Response(200, text="<html><body><p>stub body</p></body></html>")

    result = await ingest_url(
        "https://example.com",
        pg_pool=None,
        visibility="secret",
        transport=httpx.MockTransport(_stub_transport),
    )
    assert result["status"] == "error"
    assert "public" in result["message"] and "internal" in result["message"]


def test_declared_visibility_helper():
    from corpus_kb.tools.pipeline.stages import _declared_visibility

    assert _declared_visibility({"document": {"visibility": "public"}}) == "public"
    assert _declared_visibility({"document": {"visibility": "nope"}}) is None
    assert _declared_visibility({}) is None
