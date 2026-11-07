"""The chunk-coding CLI commands (issue #350) against a real DuckDB file.

Each command is a thin adapter over the ONE tool function. These tests
drive the adapter through the Typer app with the env DSN pointed at a
migrated scratch file, so the honest exit codes (0 answered, 2 tables
missing, 1 failure) and the printed envelopes are pinned end to end.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from corpus_kb.cli import app

runner = CliRunner()

TENANT = "00000000-0000-0000-0000-000000000001"
COLUMNS = ["chunk_id", "code_id", "confidence", "decided_at", "text", "document_source"]


@pytest.fixture()
def ops_dsn(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> str:
    """One migrated DuckDB file; the env DSN points at it."""
    from corpus_kb._setup.migrate import run_duckdb_migrations

    dsn = "duckdb:///" + (tmp_path / "ops-cli.duckdb").as_posix()
    run_duckdb_migrations(dsn)
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", dsn)
    monkeypatch.delenv("CORPUS_KB_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)  # keep the repo config.yaml out of discovery
    return dsn


async def _seed_accepted_row(dsn: str) -> str:
    """One accepted final_codes row for the CLI's default tenant."""
    from corpus_kb.contracts.model import DEFAULT_TENANT_ID
    from corpus_kb.storage.gateway import DuckDbGateway

    doc_id = str(uuid4())
    chunk_id = str(uuid4())
    gw = await DuckDbGateway.connect(dsn)
    try:
        await gw.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
            " VALUES ($1, $2, 'ops-cli-seed', 'text')",
            doc_id,
            DEFAULT_TENANT_ID,
        )
        await gw.execute(
            "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text, source_type)"
            " VALUES ($1, $2, $3, 0, 'ops cli seed text', 'text')",
            chunk_id,
            DEFAULT_TENANT_ID,
            doc_id,
        )
        await gw.execute(
            "INSERT INTO final_codes (chunk_id, code_id, tenant_id, status, route, confidence)"
            " VALUES ($1, 'code-a', $2, 'accepted', 'explicit', 0.9)",
            chunk_id,
            DEFAULT_TENANT_ID,
        )
    finally:
        await gw.close()
    return chunk_id


def _json_output(result: Any) -> dict[str, Any]:
    return dict(json.loads(result.output))


def test_coding_status_cli_answers_envelope(ops_dsn: str) -> None:
    """``coding status`` prints the success envelope and exits 0."""
    result = runner.invoke(app, ["coding", "status"])
    assert result.exit_code == 0, result.output
    envelope = _json_output(result)
    assert envelope["status"] == "success"
    assert envelope["documents"] == 0
    assert set(envelope["coverage"]) == {"accepted", "rejected", "manual_review"}


def test_coding_export_cli_json_then_csv(ops_dsn: str) -> None:
    """``coding export`` prints the row JSON; ``--format csv`` the header."""
    chunk_id = asyncio.run(_seed_accepted_row(ops_dsn))

    result = runner.invoke(app, ["coding", "export"])
    assert result.exit_code == 0, result.output
    envelope = _json_output(result)
    assert envelope["status"] == "success"
    assert envelope["columns"] == COLUMNS
    assert envelope["count"] == 1
    assert envelope["rows"][0]["chunk_id"] == chunk_id

    result = runner.invoke(app, ["coding", "export", "--format", "csv"])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert lines[0].split(",") == COLUMNS
    assert chunk_id in lines[1]


def test_coding_export_cli_rejects_unknown_format(ops_dsn: str) -> None:
    """An unknown format exits 1 with one clear line."""
    result = runner.invoke(app, ["coding", "export", "--format", "xlsx"])
    assert result.exit_code == 1
    assert "unknown format" in result.output


def test_coding_embed_codebook_cli_reads_file_and_guards(ops_dsn: str, tmp_path: Any) -> None:
    """A missing file exits 1; a JSON list hits the typed tool guard."""
    result = runner.invoke(app, ["coding", "embed-codebook", str(tmp_path / "nope.json")])
    assert result.exit_code == 1
    assert "cannot read codebook file" in result.output

    listing = tmp_path / "codebook.json"
    listing.write_text("[1, 2, 3]", encoding="utf-8")
    result = runner.invoke(app, ["coding", "embed-codebook", str(listing)])
    assert result.exit_code == 1
    assert "codebook must be a JSON object" in result.output


def test_coding_pool_cli_guards_bad_uuid(ops_dsn: str) -> None:
    """A malformed codebook version id exits 1 through the typed guard."""
    result = runner.invoke(app, ["coding", "pool", "--codebook-version-id", "nope"])
    assert result.exit_code == 1
    assert "codebook_version_id is not a valid UUID" in result.output


def test_coding_code_batch_cli_guards_cells_shape(ops_dsn: str, tmp_path: Any) -> None:
    """A cells file that is not a JSON array exits 1 through the guard."""
    cells = tmp_path / "cells.json"
    cells.write_text('{"chunk_id": "x"}', encoding="utf-8")
    result = runner.invoke(app, ["coding", "code-batch", "--cells", str(cells)])
    assert result.exit_code == 1
    assert "cells must be a JSON array" in result.output


def test_coding_calibrate_floors_cli_guards_labels_shape(ops_dsn: str, tmp_path: Any) -> None:
    """A labels list exits 1; an object rides to the tool's own guard."""
    labels = tmp_path / "labels.json"
    labels.write_text("[1, 2]", encoding="utf-8")
    result = runner.invoke(app, ["coding", "calibrate-floors", str(labels)])
    assert result.exit_code == 1
    assert "labels file must hold a JSON object" in result.output

    labels.write_text(
        json.dumps({"code_id": "C1", "codebook_version_id": "nope"}), encoding="utf-8"
    )
    result = runner.invoke(app, ["coding", "calibrate-floors", str(labels)])
    assert result.exit_code == 1
    assert "codebook_version_id is not a valid UUID" in result.output


def test_coding_saturation_and_reliability_cli_answer(ops_dsn: str) -> None:
    """Empty-corpus saturation and reliability answer success, exit 0."""
    result = runner.invoke(app, ["coding", "saturation"])
    assert result.exit_code == 0, result.output
    envelope = _json_output(result)
    assert envelope["status"] == "success"
    assert envelope["total_applications"] == 0

    result = runner.invoke(app, ["coding", "reliability"])
    assert result.exit_code == 0, result.output
    envelope = _json_output(result)
    assert envelope["status"] == "success"
    assert envelope["insufficient_sample"] is True


def test_coding_status_cli_answers_unavailable_exit_2(tmp_path: Any, monkeypatch: Any) -> None:
    """A bare (unmigrated) database answers the setup remedy and exit 2."""
    monkeypatch.setenv(
        "CORPUS_KB_DATABASE_URL", "duckdb:///" + (tmp_path / "bare.duckdb").as_posix()
    )
    monkeypatch.delenv("CORPUS_KB_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["coding", "status"])
    assert result.exit_code == 2, result.output
    envelope = _json_output(result)
    assert envelope["status"] == "unavailable"
    assert "corpus-kb setup" in envelope["reason"]
