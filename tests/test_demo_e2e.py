"""Full demo E2E on the bundled corpus (todo-19, r13 test split).

The narrated demo runs the REAL pipeline (handler + projections + deductive
run + review + report + notebook), so it carries BOTH requires_postgres AND
requires_ollama: on the DB-less 3-OS CI matrix it auto-skips via the conftest
TCP probe, and the ollama probe below handles the no-model host. The
narration/guide_copy wiring asserts live offline in test_demo_offline.py.
"""

from __future__ import annotations

import socket
from io import StringIO
from typing import cast
from unittest.mock import patch

import pytest

from corpus_kb.config import load_config
from corpus_kb.research import demo as demo_mod
from corpus_kb.research import guide_copy

pytestmark = [pytest.mark.requires_postgres, pytest.mark.requires_ollama]

OLLAMA_URL = "http://localhost:11434"


def _ollama_up() -> bool:
    try:
        with socket.create_connection(("localhost", 11434), timeout=2):
            return True
    except OSError:
        return False


def _demo_config(dsn: str) -> dict[str, object]:
    cfg = load_config()
    db = cast(dict[str, object], cfg["database"])
    db["connection_string"] = dsn
    cfg["embedding"] = {
        "provider": "ollama",
        "model": "qwen3-embedding:8b-q8_0",
        "base_url": OLLAMA_URL,
        "dimensions": 4096,
        "batch_size": 8,
    }
    return cfg


async def test_demo_runs_end_to_end_with_narration(research_dsn: str) -> None:
    if not _ollama_up():
        pytest.skip("Ollama not reachable")

    captured = StringIO()
    with patch("sys.stdout", captured):
        exit_code = await demo_mod.run_demo(_demo_config(research_dsn))
    assert exit_code == 0

    output = captured.getvalue()
    # Narration: title, intro, every stage header + guide_copy explanation
    # snippet + doc pointer (r12: every stage emits explanation + doc link).
    assert guide_copy.DEMO_TITLE in output
    assert guide_copy.DEMO_INTRO in output
    for stage in demo_mod.demo_stages():
        assert f"=== {stage.name.upper()} ===" in output, stage.name
        assert stage.explain in output, stage.name
        assert f"docs: {stage.doc}" in output, stage.name
    # Pipeline receipts.
    assert "codebook version " in output
    assert "accepted assignment " in output
    assert guide_copy.DEMO_OUTRO in output
    # Report: traffic lights + per-section guide links (r12/r13 anchors).
    for section in ("isr", "residual", "irr"):
        assert "docs/understanding-your-report.md#" in output
        assert f"guide[{section}]" in output
    # Notebook: EVIDENCE header + exchange-level citations.
    assert guide_copy.NOTEBOOK_EVIDENCE_HEADER in output
    assert "[1] " in output


async def test_demo_research_dlq_stays_empty(research_dsn: str) -> None:
    """The bundled corpus must run the pipeline clean: the demo's OWN events
    must never dead-letter.

    Delta-based (snapshot before, assert no new rows after) so a shared dev
    database with pre-existing DLQ residue from other test files does not
    mask the demo's own cleanliness - a fresh install has no backlog, and
    that is the surface this contract protects.
    """
    import asyncpg

    from corpus_kb.projections.research._common import DEFAULT_TENANT

    conn = await asyncpg.connect(research_dsn)
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", str(DEFAULT_TENANT)
            )
            before = {
                str(r["dlq_id"])
                for r in await conn.fetch(
                    "SELECT dlq_id FROM projection_dlq WHERE tenant_id = $1",
                    str(DEFAULT_TENANT),
                )
            }
        with patch("sys.stdout", StringIO()):
            exit_code = await demo_mod.run_demo(_demo_config(research_dsn))
        assert exit_code == 0
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", str(DEFAULT_TENANT)
            )
            after = await conn.fetch(
                "SELECT dlq_id FROM projection_dlq WHERE tenant_id = $1",
                str(DEFAULT_TENANT),
            )
    finally:
        await conn.close()
    new_rows = [r for r in after if str(r["dlq_id"]) not in before]
    assert new_rows == [], f"demo events produced DLQ rows: {new_rows}"
