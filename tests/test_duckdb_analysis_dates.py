"""The analysis date ops on the DuckDB backend (storage parity).

``analysis-backfill-dates``, ``analyze-run``, and ``topics-trends`` must
answer their honest envelopes on DuckDB without an engine-shaped
exception. The backfill's write side stays Postgres-bound by an engine
wall: DuckDB rewrites every UPDATE of a ``documents`` row as a delete
plus insert, and a row that chunks reference cannot be deleted. Every
document the migration-027 rule would date has chunks, so the DuckDB
backfill answers ``unavailable`` with the real scanned count; forward
ingests still date new documents at INSERT time.

These tests run on a real migrated DuckDB file, per the backend-parity
test convention.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from corpus_kb._setup.migrate import run_duckdb_migrations
from corpus_kb.storage.gateway import DuckDbGateway

if TYPE_CHECKING:
    from pathlib import Path

TENANT = "00000000-0000-0000-0000-000000000001"

#: The analysis flag that unhides the date path (the parity world runs it
#: dormant, which is exactly the mask these tests remove).
OVER_TIME_ON: dict[str, object] = {
    "analysis": {"topics": {"over_time": {"enabled": True}}},
}


@pytest.fixture()
async def migrated_gateway(duckdb_path: Path) -> Any:
    """A migrated DuckDB file behind the gateway."""
    run_duckdb_migrations(f"duckdb:///{duckdb_path.as_posix()}")
    gateway = await DuckDbGateway.connect(f"duckdb:///{duckdb_path.as_posix()}")
    yield gateway
    await gateway.close()


async def _seed_dated_document(gateway: Any) -> None:
    """One document whose chunk carries a declared publish date."""
    await gateway.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
        " VALUES ($1::uuid, $2, $3, $4)",
        "11111111-1111-1111-1111-111111111111",
        TENANT,
        "duck-dates-seed",
        "text",
    )
    await gateway.execute(
        "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text, source_type,"
        " metadata) VALUES ($1::uuid, $2, $3::uuid, 0, $4, $5, $6::JSON)",
        "22222222-2222-2222-2222-222222222222",
        TENANT,
        "11111111-1111-1111-1111-111111111111",
        "duck dates seed text",
        "text",
        '{"published_time": "2024-05-01T12:00:00+02:00"}',
    )


async def test_backfill_answers_unavailable_on_duckdb(migrated_gateway: Any) -> None:
    """A dateable document answers ``unavailable``; nothing crashes."""
    from corpus_kb.tools.analysis_tools import analysis_backfill_dates

    await _seed_dated_document(migrated_gateway)
    result = await analysis_backfill_dates(migrated_gateway, config=OVER_TIME_ON, tenant_id=TENANT)
    assert result["status"] == "unavailable"
    assert "DuckDB" in str(result["reason"])
    assert str(result["remedy"]).strip()
    assert result["documents_scanned"] == 1
    assert result["documents_dated"] == 0


async def test_backfill_scans_without_dateable_documents_on_duckdb(
    migrated_gateway: Any,
) -> None:
    """A chunk-less-metadata document still answers the same envelope."""
    from corpus_kb.tools.analysis_tools import analysis_backfill_dates

    await _seed_dated_document(migrated_gateway)
    await migrated_gateway.execute(
        "UPDATE chunks SET metadata = JSON '{}'"
        " WHERE chunk_id = $1::uuid",
        "22222222-2222-2222-2222-222222222222",
    )
    result = await analysis_backfill_dates(migrated_gateway, config=OVER_TIME_ON, tenant_id=TENANT)
    assert result["status"] == "unavailable"
    assert result["documents_scanned"] == 1
    assert result["documents_dated"] == 0


async def test_analyze_run_answers_envelope_on_duckdb(migrated_gateway: Any) -> None:
    """The composed run answers with sections; the dates section is honest."""
    from corpus_kb.tools.analysis_tools import analyze_run

    await _seed_dated_document(migrated_gateway)
    result = await analyze_run(migrated_gateway, config=OVER_TIME_ON, tenant_id=TENANT)
    assert isinstance(result, dict)
    assert "status" in result
    sections = result["sections"]
    assert isinstance(sections, list) and sections
    dates = [s for s in sections if s["name"] == "dates-coverage"]
    assert dates and dates[0]["status"] == "unavailable"


async def test_topics_trends_answers_insufficient_data_on_duckdb(
    migrated_gateway: Any,
) -> None:
    """The trends leg reaches the store and answers honestly on no data."""
    from corpus_kb.tools.topics_tools import topics_trends

    await _seed_dated_document(migrated_gateway)
    result = await topics_trends(migrated_gateway, config=OVER_TIME_ON, tenant_id=TENANT)
    assert result["status"] == "insufficient_data"
    assert result["trends"] == []
