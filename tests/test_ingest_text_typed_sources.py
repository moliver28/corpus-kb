"""Typed source names in ingest-text: the registry gate and the canonical stamp."""

from __future__ import annotations


def test_ingest_text_rejects_unknown_type_with_registry_message():
    import asyncio

    from corpus_kb.tools.ingest_common import ingest_text

    result = asyncio.run(ingest_text("body", pg_pool=None, source_type="nonsense"))
    assert result["status"] == "error"
    assert result["message"].startswith("Unknown source type")  # the owned refusal message
    assert "nonsense" in result["message"]
    assert len(result["message"].split(". ")[0].split()) <= 20  # STE100 shape


def test_ingest_text_accepts_registry_names_and_aliases():
    import asyncio

    from corpus_kb.tools import ingest_common

    called = []

    async def fake_run_pipeline(text, source_type, source, config, pool, tenant_id, **kwargs):
        called.append(source_type)
        return {"status": "success"}

    orig = ingest_common.run_pipeline
    ingest_common.run_pipeline = fake_run_pipeline
    try:
        for name in ("participant-session", "transcript", "internal-meeting", "text"):
            result = asyncio.run(ingest_common.ingest_text("body", pg_pool=None, source_type=name))
            assert result["status"] == "success"
    finally:
        ingest_common.run_pipeline = orig
    assert called == ["participant-session", "transcript", "internal-meeting", "text"]


def test_build_document_stamps_canonical_and_preserves_hint_paths():
    from corpus_kb.tools.ingest_common import build_document

    assert build_document("/p", "transcript", "body").source_type == "participant-session"
    assert build_document("/p", "text", "body").source_type == "text"
    assert build_document("/p", "pptx_structure", "body").source_type == "pptx_structure"
    assert build_document("/p", "nonsense", "body").source_type == "nonsense"
