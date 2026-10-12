"""Doctor research-subsystem checks (todo-19 (a)).

Offline tests cover the pure checks (extras, embedder sanity, printer).
DB-backed tests run against the shared throwaway research database
(requires_postgres, auto-skipped in CI) and include the QA failure scenario:
a HALF-APPLIED install must name the SPECIFIC missing migration (never a
stack trace), while doctor keeps its exit-0 contract.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import asyncpg
import pytest

from corpus_kb._setup import doctor_research as dr

pytestmark = pytest.mark.requires_postgres


def test_extras_checks_never_fail() -> None:
    checks = dr.extras_checks()
    assert [c.name for c in checks] == [
        "numeric stack (scipy)",
        "optional extra latechunk",
        "optional extra inductive",
    ]
    for check in checks:
        assert check.status in (dr.STATUS_OK, dr.STATUS_INFO)
        if check.status == dr.STATUS_INFO:
            assert check.fix, f"INFO check {check.name!r} must carry a remediation"
            assert check.doc
    # scipy is a transitive dependency of the optional extras, so its
    # remediation points at the extras below rather than a standalone
    # "pip install -e" of scipy itself.
    scipy_fix = checks[0].fix
    if checks[0].status == dr.STATUS_INFO:
        assert "extras" in scipy_fix
    for check in checks[1:]:
        if check.status == dr.STATUS_INFO:
            assert "pip install -e" in check.fix


def test_embedder_check_warns_pgml_absent() -> None:
    config = {
        "embedding": {"provider": "pgml", "model": "pgml"},
        "research": {"embedder": {"dimensions": 1024}},
    }
    check = dr.embedder_check(config, pgml_installed=False)
    assert check.status == dr.STATUS_WARN
    assert "abstain" in check.detail
    assert "embedding.provider: ollama" in check.fix


def test_embedder_check_warns_sub_1024_ollama() -> None:
    config = {
        "embedding": {
            "provider": "ollama",
            "model": "nomic-embed-text",
            "dimensions": 768,
        },
        "research": {"embedder": {"dimensions": 1024}},
    }
    check = dr.embedder_check(config, pgml_installed=None)
    assert check.status == dr.STATUS_WARN
    assert "never padded" in check.detail


def test_embedder_check_fails_non_1024_guard() -> None:
    config = {
        "embedding": {"provider": "ollama", "model": "m", "dimensions": 1024},
        "research": {"embedder": {"dimensions": 768}},
    }
    check = dr.embedder_check(config, pgml_installed=True)
    assert check.status == dr.STATUS_FAIL
    assert "exactly 1024" in check.detail


def test_embedder_check_ok() -> None:
    config = {
        "embedding": {
            "provider": "ollama",
            "model": "qwen3-embedding:8b-q8_0",
            "dimensions": 4096,
        },
        "research": {"embedder": {"dimensions": 1024}},
    }
    check = dr.embedder_check(config, pgml_installed=False)
    assert check.status == dr.STATUS_OK


def test_print_research_checks_renders_fix_and_doc() -> None:
    check = dr.ResearchCheck(
        name="coding/research migrations",
        status=dr.STATUS_FAIL,
        detail="not applied - 016 missing research_units",
        fix=dr.SETUP_FIX,
        doc=dr.DOC_GETTING_STARTED,
    )
    buffered = StringIO()
    with patch("sys.stdout", buffered):
        dr.print_research_checks([check])
    output = buffered.getvalue()
    assert "FAIL" in output
    assert "fix: corpus-kb setup" in output
    assert f"docs: {dr.DOC_GETTING_STARTED}" in output


async def test_db_checks_green_on_applied_database(research_dsn: str) -> None:
    conn = await asyncpg.connect(research_dsn)
    try:
        checks = await dr.db_checks(conn, {})
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                "00000000-0000-0000-0000-000000000001",
            )
            dlq_count = await conn.fetchval("SELECT count(*) FROM projection_dlq")
    finally:
        await conn.close()
    by_name = {c.name: c for c in checks}
    migrations = by_name["coding/research migrations"]
    assert migrations.status == dr.STATUS_OK, migrations.detail
    assert by_name["codebook / coding runs"].status == dr.STATUS_INFO
    # The DLQ check reports what is there: OK on a clean database, WARN with
    # the fix hint when this shared test database carries dead-lettered rows.
    expected = dr.STATUS_WARN if int(dlq_count) else dr.STATUS_OK
    assert by_name["projection DLQ"].status == expected


async def test_db_checks_name_missing_migration_when_half_applied(research_dsn: str) -> None:
    """QA failure scenario: half-applied migrations report the SPECIFIC file."""
    conn = await asyncpg.connect(research_dsn)
    try:
        await conn.execute("ALTER TABLE research_units RENAME TO research_units_bak")
        try:
            checks = await dr.db_checks(conn, {})
        finally:
            await conn.execute("ALTER TABLE research_units_bak RENAME TO research_units")
    finally:
        await conn.close()
    migrations = next(c for c in checks if c.name == "coding/research migrations")
    assert migrations.status == dr.STATUS_FAIL
    assert "016_research_domain.sql" in migrations.detail
    assert "research_units" in migrations.detail
    assert migrations.fix == dr.SETUP_FIX


async def test_research_checks_end_to_end_on_live_dsn(research_dsn: str) -> None:
    checks = await dr.research_checks(research_dsn, {}, extensions=None)
    assert any(c.name == "research.embedder config" for c in checks)
    assert any(c.name == "coding/research migrations" for c in checks)
