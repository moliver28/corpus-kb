"""CLI research ask/uncoded flows on DuckDB (wired_store embedder gap).

The two ``wired_store(embedder=True)`` CLI flows run against a real
migrated DuckDB file through the actual CLI coroutines and the env-var
DSN override. Ollama up: a real query embed flows through the research
embedder. Ollama down: the zero-vector fallback degrades to the lexical
arm. Either way the flows return a payload — never an AttributeError.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

from corpus_kb._setup.migrate import run_migrations  # noqa: E402
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string  # noqa: E402
from tests import _research_duckdb_seeds as seeds  # noqa: E402


@pytest.fixture
async def duckdb_dsn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A migrated DuckDB file wired in through the env-var DSN override."""
    dsn = duckdb_path_to_conn_string(tmp_path / "cli-research.duckdb")
    await run_migrations(dsn)
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", dsn)
    return dsn


def test_ask_flow_returns_evidence_on_duckdb(
    duckdb_dsn: str, capsys: pytest.CaptureFixture[str]
) -> None:
    from corpus_kb.cli.cli_research_ask import _ask_coroutine
    from corpus_kb.cli.cli_support import _run_async

    asyncio.run(seeds.seed_ask_fixture(duckdb_dsn))
    exit_code = _run_async(
        _ask_coroutine(
            seeds.QUESTION,
            seeds.PROJECT,
            True,  # retrieval_only: zero LLM calls
            5,
            None,
            None,
            None,
            (),
            None,
            None,
            None,
            True,  # json_output
        )
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] in {"evidence", "answer"}
    assert payload["evidence"], "expected at least one evidence exchange"


def test_uncoded_flow_ranks_residuals_on_duckdb(
    duckdb_dsn: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Flow coverage, not seam coverage (issue #523 P3-1).

    ``uncoded_units`` discards the embedder (the residuals rank through
    SQL-side kNN), so this test passes identically before or after any
    embedder-seam change. Never cite it as RED evidence for a seam fix.
    The order assertion IS semantic: unit 3 carries a non-orthogonal
    embedding (see ``seeds.tilt_unit_three``), so its residual differs
    from the 0.0 tie and it ranks last.
    """
    from corpus_kb.cli.cli_research_views import _uncoded_coroutine
    from corpus_kb.cli.cli_support import _run_async

    asyncio.run(seeds.seed_radar_fixture(duckdb_dsn))
    asyncio.run(seeds.tilt_unit_three(duckdb_dsn))
    exit_code = _run_async(_uncoded_coroutine(None, None, 25, True))
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert [u["unit_id"] for u in payload["units"]] == [2, 4, 3]


def test_ask_scopes_to_the_project_on_duckdb(
    duckdb_dsn: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unknown project id yields the typed empty status, not a crash."""
    from corpus_kb.cli.cli_research_ask import _ask_coroutine
    from corpus_kb.cli.cli_support import _run_async

    asyncio.run(seeds.seed_ask_fixture(duckdb_dsn))
    exit_code = _run_async(
        _ask_coroutine(
            seeds.QUESTION,
            "99999999-9999-9999-9999-999999999999",
            True,
            5,
            None,
            None,
            None,
            (),
            None,
            None,
            None,
            True,
        )
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "empty"
