"""DuckDB arms for the governance report runner (storage portability).

The analytics runners take a ``duck`` flag, but ``build_report`` never arms
it: on a DuckDB study the Postgres ``::vector`` probes ship to the engine
and the report dies with ``Catalog Error: Type with name vector does not
exist``. The legs here pin the armed report on one migrated DuckDB file.
Hermetic: no Postgres, no Ollama.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.research.governance_report import to_dict, validate_required
from corpus_kb.research.report_runner import build_report
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
from tests._research_duckdb_seeds import TENANT, seed_radar_fixture


@pytest.mark.asyncio
async def test_build_report_on_duckdb_answers_pinned_schema(tmp_path: Path) -> None:
    """The report builds on DuckDB through the armed exact-scan twins."""
    path = tmp_path / "report.duckdb"
    dsn = duckdb_path_to_conn_string(str(path))
    await run_migrations(dsn)
    await seed_radar_fixture(dsn)
    gateway = await DuckDbGateway.connect(str(path))
    try:
        report = await build_report(gateway, UUID(str(TENANT)))
        missing = validate_required(to_dict(report))
        assert missing == []
    finally:
        await gateway.close()
