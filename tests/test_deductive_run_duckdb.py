"""Seeded-unit deductive run pin on DuckDB (verified non-break).

W3's DuckDB coding leg ran over an EMPTY research corpus, so the scoring
reads never executed there. This module pins the wider leg: a migrated
DuckDB file with codable units, a loaded codebook, and gold exemplars goes
through the real ``coding_run`` tool — the three-view reads in
``research/run_inputs.py``, the in-Python scoring, and the assignment
events all answer on the gateway. Hermetic: no Postgres, no Ollama.
"""

from __future__ import annotations

from json import dumps
from pathlib import Path

import pytest

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
from tests._research_duckdb_seeds import TENANT, VERSION, seed_radar_fixture

CODE_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

#: A closed port on localhost: no Postgres can absorb the event writes.
DEAD_PG = "postgresql://corpus:corpus@127.0.0.1:59999/deadpg"


@pytest.mark.asyncio
async def test_coding_run_scores_seeded_units_on_duckdb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deductive run scores the seeded corpus through the real tool."""
    path = tmp_path / "deductive.duckdb"
    dsn = duckdb_path_to_conn_string(str(path))
    await run_migrations(dsn)
    await seed_radar_fixture(dsn)
    gateway = await DuckDbGateway.connect(str(path))
    # Deductive v2 scores UUID code ids only; the radar seed's non-UUID code
    # stays on the skipped list, so this code carries the gold exemplar ref.
    await gateway.execute(
        "INSERT INTO code_registry (code_id, tenant_id, codebook_version_id, name,"
        " brief_definition, inclusion_criteria, exclusion_criteria, theory)"
        " VALUES ($1, $2, $3, 'trust', 'Expressions of trust.', 'includes', 'excludes',"
        " $4::JSON)",
        CODE_ID,
        str(TENANT),
        VERSION,
        dumps({"exemplar_text_sha256": [format(1, "064x")]}),
    )
    # The run resolves its config at call time: bind the event store to
    # this file's SQLite sidecar and the analytics reads to the gateway.
    monkeypatch.setattr(
        "corpus_kb.config.load_config",
        lambda: {"database": {"backend": "duckdb", "connection_string": dsn}},
    )
    # The unmaskable shape: CI arms CORPUS_KB_DATABASE_URL on the test
    # legs. A stale env var pointing at a dead Postgres must not absorb
    # the event writes (a local Postgres on the env DSN masked this once).
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", DEAD_PG)
    try:
        from corpus_kb.tools.research_tools import coding_run

        summary = await coding_run(gateway, VERSION)
        assert summary["status"] == "success"
        assert summary["n_units"] == 4
        assert summary["n_codes"] == 1
        rows = await gateway.fetch(
            "SELECT unit_id FROM research_assignments WHERE tenant_id = $1",
            str(TENANT),
        )
        assert len(rows) >= 1
    finally:
        await gateway.close()
