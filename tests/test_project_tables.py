"""Per-project raw-table factory tests (Track C1).

Hermetic DuckDB legs through ``any_gateway``-style fixtures; the registry
table comes from the shipped migration file, so the test cannot drift from
the real DDL. Postgres legs join when the pg_pool fixture is live (the
same file runs on the CI ubuntu leg).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_kb._setup.migrate import DUCKDB_DSN_PREFIX, run_duckdb_migrations
from corpus_kb.errors import NotFoundError, ValidationError
from corpus_kb.storage.project_tables import (
    create_project_table,
    drop_project_table,
    list_project_tables,
    raw_table_name,
    store_raw_content,
)

pytest.importorskip("duckdb")

TENANT = "00000000-0000-0000-0000-000000000001"
PROJECT = "00000000-0000-0000-0000-000000000002"


@pytest.fixture
async def duckdb_with_registry(duckdb_path: Path):
    """A migrated DuckDB gateway (the full migration set, incl. 043)."""
    from corpus_kb.storage.gateway import DuckDbGateway

    run_duckdb_migrations(f"{DUCKDB_DSN_PREFIX}{duckdb_path.as_posix()}")
    gateway = await DuckDbGateway.connect(str(duckdb_path))
    try:
        yield gateway
    finally:
        await gateway.close()


def test_raw_table_name_validates_and_maps_hyphens() -> None:
    assert raw_table_name("meeting-notes") == "raw_meeting_notes"
    assert raw_table_name("Interviews2024") == "raw_Interviews2024"
    with pytest.raises(ValidationError):
        raw_table_name("bad_slug")
    with pytest.raises(ValidationError):
        raw_table_name("")
    with pytest.raises(ValidationError):
        raw_table_name("notes; DROP TABLE users")


async def test_provision_creates_table_and_registry_row(duckdb_with_registry) -> None:
    gateway = duckdb_with_registry
    out = await create_project_table(gateway, TENANT, PROJECT, "meeting-notes")
    assert out["table"] == "raw_meeting_notes"
    assert out["already_provisioned"] is False
    tables = await list_project_tables(gateway, TENANT)
    assert len(tables) == 1
    assert tables[0]["table"] == "raw_meeting_notes"
    assert tables[0]["row_count"] == 0


async def test_provision_is_idempotent(duckdb_with_registry) -> None:
    gateway = duckdb_with_registry
    first = await create_project_table(gateway, TENANT, PROJECT, "alpha")
    second = await create_project_table(gateway, TENANT, PROJECT, "alpha")
    assert second["already_provisioned"] is True
    assert second["registry_id"] == first["registry_id"]


async def test_raw_rows_are_content_addressed(duckdb_with_registry) -> None:
    gateway = duckdb_with_registry
    await create_project_table(gateway, TENANT, PROJECT, "alpha")
    slug = "alpha"
    hash_one = await store_raw_content(
        gateway,
        TENANT,
        PROJECT,
        slug,
        source_path="notes/a.txt",
        raw_bytes=None,
        raw_text="hello",
        content_type="text/plain",
    )
    hash_two = await store_raw_content(
        gateway,
        TENANT,
        PROJECT,
        slug,
        source_path="notes/a.txt",
        raw_bytes=None,
        raw_text="hello",
        content_type="text/plain",
    )
    assert hash_one == hash_two
    tables = await list_project_tables(gateway, TENANT)
    assert tables[0]["row_count"] == 1


async def test_raw_bytes_round_trip(duckdb_with_registry) -> None:
    gateway = duckdb_with_registry
    await create_project_table(gateway, TENANT, PROJECT, "pdfs")
    await store_raw_content(
        gateway,
        TENANT,
        PROJECT,
        "pdfs",
        source_path="docs/x.pdf",
        raw_bytes=b"%PDF-1.4",
        raw_text=None,
        content_type="application/pdf",
    )
    row = await gateway.fetchrow("SELECT raw_bytes, source_hash FROM raw_pdfs")
    assert bytes(row["raw_bytes"]) == b"%PDF-1.4"
    assert len(str(row["source_hash"])) == 64


async def test_drop_removes_table_and_registry_row(duckdb_with_registry) -> None:
    gateway = duckdb_with_registry
    await create_project_table(gateway, TENANT, PROJECT, "alpha")
    await store_raw_content(
        gateway,
        TENANT,
        PROJECT,
        "alpha",
        source_path="a.txt",
        raw_bytes=None,
        raw_text="one",
        content_type="text/plain",
    )
    out = await drop_project_table(gateway, TENANT, "alpha")
    assert out["dropped_rows"] == 1
    tables = await list_project_tables(gateway, TENANT)
    assert tables == []


async def test_drop_unknown_slug_is_not_found(duckdb_with_registry) -> None:
    with pytest.raises(NotFoundError):
        await drop_project_table(duckdb_with_registry, TENANT, "never-made")


async def test_slugs_are_tenant_scoped(duckdb_with_registry) -> None:
    gateway = duckdb_with_registry
    other = "00000000-0000-0000-0000-000000000003"
    await create_project_table(gateway, TENANT, PROJECT, "shared")
    await create_project_table(gateway, other, PROJECT, "shared")
    assert len(await list_project_tables(gateway, TENANT)) == 1
    assert len(await list_project_tables(gateway, other)) == 1
