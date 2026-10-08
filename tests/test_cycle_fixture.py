"""Cycle fixture E2E (todo 20 acceptance, r13 test split).

The cycle runs the REAL pipeline (dynamic ingest, inductive pass,
promotion gate, deductive run, keywords, report, notebook), so this file
carries BOTH requires_postgres AND requires_ollama: CI's DB-less matrix
auto-skips via the conftest TCP probe, and the ollama probe below skips
model-less hosts. All cycle control-flow semantics that need no database
are pinned offline in test_cycle_offline.py.

The heavy live-inductive work runs ONCE (tenant X): one out-mode call
halts at the promotion gate with halt_on stripped to the hard floor AND
--guide teaching (two acceptance scenarios in one pass), then promotion +
resume completes the chain. Tenants are per-test (house rule: never share
a tenant another test count-asserts on). Requires the ``inductive``
extra locally (CI never installs it; the extras-less bar is re-proven
without this file running).
"""

from __future__ import annotations

import shutil
import socket
from io import StringIO
from pathlib import Path
from typing import cast
from unittest.mock import patch
from uuid import NAMESPACE_URL, UUID, uuid5

import asyncpg
import pytest

from corpus_kb.config import load_config
from corpus_kb.research import guide_copy
from corpus_kb.research.cycle import run_cycle
from corpus_kb.research.cycle_gates import GATE_EXIT_CODES
from corpus_kb.research.cycle_stages import build_stack, stage_ingest
from corpus_kb.research.cycle_state import last_cycle_checkpoint, pending_files

pytestmark = [pytest.mark.requires_postgres, pytest.mark.requires_ollama]

try:
    import umap  # noqa: F401

    _INDUCTIVE_EXTRA = True
except ImportError:
    _INDUCTIVE_EXTRA = False

OLLAMA_URL = "http://localhost:11434"
CYCLE_CORPUS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "research" / "cycle"
TRANSCRIPTS = (
    "interview_transit.txt",
    "interview_store_closures.txt",
    "interview_benefits.txt",
)
CYCLE_QUESTION = "What barriers make food shopping hard for residents?"

TENANT_X = UUID("00000000-0000-0000-0000-00000000c7a1")
TENANT_D = UUID("00000000-0000-0000-0000-00000000c7d4")
TENANT_E = UUID("00000000-0000-0000-0000-00000000c7e5")
PROJECT_X = uuid5(NAMESPACE_URL, "cycle-test/food-access")


def _ollama_up() -> bool:
    try:
        with socket.create_connection(("localhost", 11434), timeout=2):
            return True
    except OSError:
        return False


def _cycle_config(dsn: str) -> dict[str, object]:
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


def _drop_dir(tmp_path: Path, names: tuple[str, ...]) -> str:
    drop = tmp_path / "drop"
    drop.mkdir(exist_ok=True)
    for name in names:
        shutil.copy(CYCLE_CORPUS / name, drop / name)
    return str(drop)


async def _run_cycle(dsn: str, tenant: UUID, **kwargs: object) -> tuple[int, str]:
    kwargs.setdefault("cfg", _cycle_config(dsn))
    pool = await asyncpg.create_pool(dsn)
    captured = StringIO()
    try:
        with patch("sys.stdout", captured):
            code = await run_cycle(pool, tenant, **cast("dict", kwargs))  # type: ignore[arg-type]
    finally:
        await pool.close()
    return code, captured.getvalue()


async def _pool(dsn: str):
    return await asyncpg.create_pool(dsn)


async def _proposed_ids(dsn: str, tenant: UUID, run_id: str | None = None) -> list[int]:
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(tenant))
            rows = await conn.fetch(
                "SELECT proposed_id FROM research_proposed_codes "
                "WHERE tenant_id = $1 AND status = 'proposed' "
                "AND ($2::uuid IS NULL OR run_id = $2::uuid) ORDER BY proposed_id",
                tenant,
                run_id,
            )
        return [int(r["proposed_id"]) for r in rows]
    finally:
        await conn.close()


async def _last_checkpoint(dsn: str, tenant: UUID) -> dict[str, object]:
    pool = await _pool(dsn)
    try:
        return await last_cycle_checkpoint(pool, tenant)
    finally:
        await pool.close()


async def _any_inductive_run(dsn: str, tenant: UUID) -> str | None:
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(tenant))
            row = await conn.fetchrow(
                "SELECT run_id FROM research_proposed_codes "
                "WHERE tenant_id = $1 ORDER BY proposed_id DESC LIMIT 1",
                tenant,
            )
        return str(row["run_id"]) if row else None
    finally:
        await conn.close()


async def _codable_units(dsn: str, tenant: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(tenant))
            value = await conn.fetchval(
                "SELECT count(*) FROM research_units WHERE tenant_id = $1 "
                "AND is_codable AND role_in_exchange = 'answer'",
                tenant,
            )
        return int(value or 0)
    finally:
        await conn.close()


@pytest.mark.skipif(
    not _INDUCTIVE_EXTRA, reason="the inductive extra (umap-learn) is not installed"
)
async def test_out_mode_taught_halt_promote_resume_complete(
    research_dsn: str, tmp_path: Path
) -> None:
    """The full happy path: one-stage in-mode -> taught gate-halt (hard floor)
    -> human promotes -> resume -> cited notebook answer + report artifact."""
    if not _ollama_up():
        pytest.skip("Ollama not reachable")
    drop = _drop_dir(tmp_path, TRANSCRIPTS)

    # In-mode: executes exactly ONE next stage (the ingest) and exits.
    code, out = await _run_cycle(
        research_dsn, TENANT_X, mode="in", project_id=PROJECT_X, ingest_dir=drop, halt_on=[]
    )
    assert code == 0, out
    assert "--- ingest ---" in out
    assert "--- inductive ---" not in out, "in-mode ran more than one stage"
    checkpoint = await _last_checkpoint(research_dsn, TENANT_X)
    assert checkpoint["kind"] == "stage_complete"
    assert checkpoint["stage"] == "ingest"
    n_units = await _codable_units(research_dsn, TENANT_X)
    assert n_units > 0, "ingest must project codable answer units for the inductive stage"

    # Out-of-loop with the config halt list EMPTY: only the HARD-FLOOR gate
    # remains, the cycle STILL halts at codebook_promotion, and --guide
    # turns the halt into a taught decision (r12 block asserted verbatim).
    code, out = await _run_cycle(
        research_dsn,
        TENANT_X,
        mode="out",
        project_id=PROJECT_X,
        ingest_dir=drop,
        halt_on=[],
        guide=True,
    )
    assert code == GATE_EXIT_CODES["codebook_promotion"], out
    assert guide_copy.CYCLE_HALT_HEADER.format(gate="codebook_promotion") in out
    assert "--- deductive ---" not in out, "cycle ran past the promotion gate"
    assert guide_copy.GATE_CODEBOOK_PROMOTION_TAUGHT in out
    assert guide_copy.GATE_CODEBOOK_PROMOTION_LOOK in out
    assert guide_copy.CYCLE_PROPOSALS_HEADER in out
    assert "- [pending] #" in out, "proposal lines with ids must appear in the taught block"
    assert "example units:" in out
    assert guide_copy.GATE_CODEBOOK_PROMOTION_PROMOTE_CONSEQUENCE in out
    assert guide_copy.GATE_CODEBOOK_PROMOTION_SKIP_CONSEQUENCE in out
    assert "codebook promote" in out, "halt must print the actionable next command"

    run_id = await _any_inductive_run(research_dsn, TENANT_X)
    ids = await _proposed_ids(research_dsn, TENANT_X, run_id)
    assert ids, "fixture inductive pass produced no proposals"

    # The human acts through the NAMED surface (corpus-kb codebook promote),
    # which records the decision AND advances the projections.
    from corpus_kb.tools.research_tools import codebook_promote as promote_surface

    pool = await _pool(research_dsn)
    try:
        promoted = await promote_surface(
            pool,
            ids[0],
            "transportation_barrier",
            "Units where residents describe transit problems blocking access "
            "to food, care, or services.",
            tenant_id=str(TENANT_X),
        )
    finally:
        await pool.close()
    assert promoted["status"] == "promoted", promoted

    # Resume: the new cycle run continues after the gate and completes.
    code, out = await _run_cycle(
        research_dsn,
        TENANT_X,
        mode="out",
        project_id=PROJECT_X,
        ingest_dir=drop,
        question=CYCLE_QUESTION,
        halt_on=["codebook_promotion"],
    )
    assert code == 0, out
    assert "Resuming after stage: inductive" in out
    for stage in ("deductive", "keywords", "report"):
        assert f"--- {stage} ---" in out, stage
    checkpoint = await _last_checkpoint(research_dsn, TENANT_X)
    assert checkpoint["kind"] == "cycle_complete"
    # The cited notebook answer + the report artifact (traffic lights).
    assert guide_copy.NOTEBOOK_EVIDENCE_HEADER in out
    assert "[1] " in out
    assert any(tag in out for tag in ("[GREEN]", "[YELLOW]", "[RED]"))

    # In-mode after completion: precisely the next (fresh) stage, then stop.
    code, out = await _run_cycle(
        research_dsn, TENANT_X, mode="in", project_id=PROJECT_X, ingest_dir=drop, halt_on=[]
    )
    assert code == 0, out
    assert "--- ingest ---" in out
    assert "--- inductive ---" not in out


# ---------------------------------------------------------------------------
# On-mode: deny stops cleanly with state recorded
# ---------------------------------------------------------------------------


async def test_on_mode_deny_stops_clean(research_dsn: str, tmp_path: Path) -> None:
    if not _ollama_up():
        pytest.skip("Ollama not reachable")
    drop = _drop_dir(tmp_path, (TRANSCRIPTS[0],))

    async def deny(stage: str) -> bool:
        return False

    code, out = await _run_cycle(
        research_dsn, TENANT_D, mode="on", ingest_dir=drop, halt_on=[], approve=deny
    )
    assert code == 20, out
    assert guide_copy.CYCLE_APPROVAL_DENIED in out
    checkpoint = await _last_checkpoint(research_dsn, TENANT_D)
    assert checkpoint["kind"] == "approval_denied"
    assert checkpoint["stage"] == "ingest"


# ---------------------------------------------------------------------------
# Watch feed: a dropped file is picked up exactly once
# ---------------------------------------------------------------------------


async def test_watch_picks_up_new_file_once(research_dsn: str, tmp_path: Path) -> None:
    if not _ollama_up():
        pytest.skip("Ollama not reachable")
    drop = _drop_dir(tmp_path, (TRANSCRIPTS[0],))

    pool = await _pool(research_dsn)
    try:
        assert await pending_files(pool, TENANT_E, Path(drop)) == 1
        stack = build_stack(pool, _cycle_config(research_dsn), research_dsn)
        with patch("sys.stdout", StringIO()):
            receipt = await stage_ingest(pool, stack, TENANT_E, None, drop)
        assert receipt["status"] == "success"
        assert int(cast("dict[str, object]", receipt)["ingested"] or 0) == 1
        assert await pending_files(pool, TENANT_E, Path(drop)) == 0, (
            "the file-hash ledger must make the next idle poll see nothing "
            "(pickup-once; idle without busy-loop)"
        )
    finally:
        await pool.close()
