"""The claim flow on the Postgres leg (auto-skips without Postgres).

Same shape as the DuckDB claims module: the hash-first probe requires a
clean ledger run, a cross-project re-ingest claims instead of skipping,
and the claimed envelope carries exactly the declared keys.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from corpus_kb.tools import ingest_common
from corpus_kb.tools.pipeline.plan import run_pipeline
from corpus_kb.utils.models import Chunk

TENANT = "00000000-0000-0000-0000-000000000001"
P1 = "44444444-4444-4444-4444-444444444444"
P2 = "55555555-5555-5555-5555-555555555555"
DOC_PATH = "F:/claims/pg-seed.md"

pytestmark = pytest.mark.asyncio


def _stub_embed_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic embed/extract doubles (no Ollama, no network)."""
    from corpus_kb.extraction import ExtractStats

    async def fake_embed(
        chunks: list[Chunk],
        config: dict[str, Any],
        pool: Any = None,
        text_override: dict[str, str] | None = None,
        document_text: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        for chunk in chunks:
            chunk.embedding = [0.0] * 2560
        return False, None

    async def fake_extract(
        chunks: list[Chunk],
        ontology: Any,
        source_document_id: str,
        config: dict[str, Any],
        pool: Any = None,
    ) -> tuple[list[Any], list[Any], str, ExtractStats]:
        stats = ExtractStats(
            backend="stub",
            requested_entities=0,
            kept_entities=0,
            requested_relations=0,
            kept_relations=0,
        )
        return [], [], "stub", stats

    monkeypatch.setattr(ingest_common, "embed_chunks", fake_embed)
    monkeypatch.setattr(ingest_common, "extract_with_fallback", fake_extract)


async def test_claim_flow_pg(pg_pool, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hash match across projects claims; the envelope keys stay pinned."""
    # The membership tables ride the idempotent Postgres migration runner
    # (the same runner corpus-kb setup uses), so the leg works on any DSN.
    import os

    from corpus_kb._setup.migrate import run_migrations

    dsn = os.environ.get(
        "CORPUS_KB_DATABASE_URL",
        "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb_test",
    )
    await run_migrations(dsn)
    _stub_embed_extract(monkeypatch)
    # Unique per run: the shared dev Postgres keeps rows from earlier runs.
    stamp = str(uuid4())
    doc_text = f"Postgres claim seed {stamp} document body. " * 6
    config: dict[str, Any] = {
        "graph": {"extract_entities": True},
        "ingest": {"contextualize": {"enabled": False}},
    }
    first = await run_pipeline(doc_text, "text", DOC_PATH, config, pg_pool, TENANT, project_id=P1)
    assert first["status"] == "success"
    second = await run_pipeline(doc_text, "text", DOC_PATH, config, pg_pool, TENANT, project_id=P2)
    # run_pipeline decorates the pipeline envelope with the strategy
    # provenance keys (the skipped twin is decorated the same way), so the
    # exact-key pin lives on _claimed_envelope in the DuckDB module.
    claim_keys = {
        "status",
        "document_id",
        "path",
        "source_type",
        "reason",
        "source_project_id",
        "errors",
    }
    provenance = {
        "strategy",
        "strategy_basis",
        "profile",
        "detection_confidence",
        "detection_reason",
        "detected_strategy",
        "warnings",
        "textbook",
    }
    assert claim_keys <= set(second) <= claim_keys | provenance
    assert second["status"] == "claimed"
    assert second["source_project_id"] == P1


async def test_membership_tables_carry_tenant_rls(pg_pool) -> None:
    """RVW4-01: the three membership tables FORCE tenant RLS (048).

    The policy set matches the 043/045 convention (one
    ``<table>_tenant_isolation`` policy keyed on the
    ``app.current_tenant_id`` GUC), and a GUC-less session sees zero rows
    even for a tenant that owns claims — isolation holds regardless of
    who owns the table (migration 011 threat model).
    """
    import os

    from corpus_kb._setup.migrate import run_migrations

    dsn = os.environ.get(
        "CORPUS_KB_DATABASE_URL",
        "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb_test",
    )
    # Re-arm 048: the runner records applied filenames, and this database
    # applied the pre-RLS version of the file (047-test convention).
    import asyncpg

    arm = await asyncpg.connect(dsn)
    try:
        async with arm.transaction():
            await arm.execute(
                "DELETE FROM corpus.schema_migrations WHERE filename = '048_master_membership.sql'"
            )
    finally:
        await arm.close()
    await run_migrations(dsn)

    doc_id = "00000000-0000-0000-0000-0000000000a1"
    async with pg_pool.acquire() as con, con.transaction():
        await con.execute("SELECT set_config('app.current_tenant_id', $1, true)", TENANT)
        await con.execute(
            "INSERT INTO documents (doc_id, tenant_id, source) VALUES ($1, $2, $3)"
            " ON CONFLICT DO NOTHING",
            doc_id,
            TENANT,
            "rls-probe-doc",
        )
        await con.execute(
            "INSERT INTO project_claims (tenant_id, project_id, doc_id, source_project_id)"
            " VALUES ($1, $2, $3, NULL) ON CONFLICT DO NOTHING",
            TENANT,
            P1,
            doc_id,
        )

    # A GUC-less connection: FORCEd RLS hides every row.
    async with pg_pool.acquire() as con:
        assert await con.fetchval("SELECT count(*) FROM project_claims") == 0

    # The tenant's own session sees its row; policy + flags match 043/045.
    async with pg_pool.acquire() as con, con.transaction():
        await con.execute("SELECT set_config('app.current_tenant_id', $1, true)", TENANT)
        assert await con.fetchval("SELECT count(*) FROM project_claims") >= 1
        for table in ("project_claims", "project_activity", "master_state"):
            policies = await con.fetch(
                "SELECT polname FROM pg_policy WHERE polrelid = $1::regclass ORDER BY polname",
                f"public.{table}",
            )
            assert [p["polname"] for p in policies] == [f"{table}_tenant_isolation"]
            qual = await con.fetchval(
                "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy"
                " WHERE polrelid = $1::regclass LIMIT 1",
                f"public.{table}",
            )
            assert "app.current_tenant_id" in qual
            flags = await con.fetchrow(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = $1::regclass",
                f"public.{table}",
            )
            assert flags["relrowsecurity"] is True
            assert flags["relforcerowsecurity"] is True
