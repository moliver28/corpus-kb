"""Stage-5 ingest front door: CLI exit codes, watch auto-heal, status/reembed tools.

Offline by construction: the Postgres/embedding edges are fakes at the SAME
seams the real wiring uses (``tools.ingest_tools.ingest_file``,
``tools.ingest_surface_tools.*``, ``cli_ingest._open_pool``), so the exit
codes and the heal loop are exercised end-to-end without a database.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from corpus_kb.cli import app
from corpus_kb.ingest_watch import (
    HEALABLE_MARKERS,
    WatchOptions,
    is_embedding_unavailable_error,
    is_healable_result,
    scan_watch_dir,
    watch_directory,
)
from tests.cli_fakes import FakePool

runner = CliRunner()

TENANT = "00000000-0000-0000-0000-000000000001"

EMBED_OUTAGE_ERRORS = [
    "EmbeddingError: Connection failed: ollama returned zero vectors",
    "EmbeddingError: Connection failed: ollama and fallback pgml both returned zero vectors",
]


def _result(status: str, path: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": status,
        "path": path,
        "chunk_count": 1,
        "entity_count": 0,
    }
    payload.update(extra)
    return payload


def _patch_pool(monkeypatch: pytest.MonkeyPatch) -> FakePool:
    pool = FakePool()

    async def _open(_cfg: dict[str, object]) -> FakePool:
        return pool

    monkeypatch.setattr("corpus_kb.cli.cli_ingest._open_pool", _open)
    return pool


# ---------------------------------------------------------------------------
# Unit: the embed-outage classifier
# ---------------------------------------------------------------------------


def test_classifier_matches_only_embedder_outages() -> None:
    for error in EMBED_OUTAGE_ERRORS:
        assert is_embedding_unavailable_error(error), error
    assert is_embedding_unavailable_error(None) is False
    assert is_embedding_unavailable_error("EmbeddingError: parse yielded nothing") is False
    assert is_embedding_unavailable_error("PostgresWriteError: relation does not exist") is False


def test_classifier_marker_contract_stays_tight() -> None:
    # Both markers required: "EmbeddingError" alone would catch parse errors,
    # "Connection failed" alone would catch arbitrary IO failures.
    assert HEALABLE_MARKERS == ("EmbeddingError", "Connection failed")


def test_healable_result_covers_partial_and_error_shapes() -> None:
    partial = _result("partial", "a.md", errors=["EmbeddingError: Connection failed: zero vectors"])
    assert is_healable_result(partial) is True
    assert is_healable_result(_result("success", "a.md")) is False
    assert is_healable_result(_result("error", "a.md", message="Connection failed: nope")) is False


# ---------------------------------------------------------------------------
# Unit: the watch loop (injected probe / sleeper / clock / ingest callback)
# ---------------------------------------------------------------------------


class _ScriptedIngest:
    """Path -> canned results, consumed per call (call-order keyed)."""

    def __init__(self, results: dict[str, list[dict[str, object]]]) -> None:
        self.results = results
        self.calls: list[Path] = []

    async def __call__(self, path: Path) -> dict[str, object]:
        self.calls.append(path)
        queue = self.results[str(path)]
        return queue.pop(0) if len(queue) > 1 else queue[0]


class _Recorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []
        self.messages: list[str] = []
        self.tick = 0.0

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.tick += seconds

    def clock(self) -> float:
        return self.tick

    def report(self, message: str) -> None:
        self.messages.append(message)


def _watch(
    tmp_path: Path,
    ingest: _ScriptedIngest,
    rec: _Recorder,
    probe_results: list[bool],
    max_passes: int = 6,
):
    from typing import Any

    probes = iter(probe_results)

    async def probe() -> bool:
        return next(probes, probe_results[-1])

    options = WatchOptions(
        interval_s=10.0,
        heal=True,
        heal_max_attempts=3,
        heal_backoff_s=5.0,
        max_passes=max_passes,
    )
    return watch_directory(
        tmp_path,
        cast(Any, ingest),
        options,
        probe=probe,
        sleeper=rec.sleep,
        clock=rec.clock,
        report=rec.report,
    )


def _run(coro: object) -> dict[str, object]:
    return cast("dict[str, object]", asyncio.run(coro))


def test_watch_ingests_supported_files_only(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("hello", encoding="utf-8")
    (tmp_path / "binary.xyz").write_text("x", encoding="utf-8")
    notes = tmp_path / "notes.md"
    ingest = _ScriptedIngest({str(notes): [_result("success", "notes.md")]})
    rec = _Recorder()
    summary = _run(_watch_once(tmp_path, ingest, rec, passes=1))
    assert [p.name for p in ingest.calls] == ["notes.md"]
    assert summary["ingested"] == 1
    assert summary["status"] == "success"


def _watch_once(tmp_path: Path, ingest: _ScriptedIngest, rec: _Recorder, passes: int) -> object:
    from typing import Any

    options = WatchOptions(interval_s=1.0, heal=False, max_passes=passes)

    async def probe() -> bool:
        return True

    return watch_directory(
        tmp_path,
        cast(Any, ingest),
        options,
        probe=probe,
        sleeper=rec.sleep,
        clock=rec.clock,
        report=rec.report,
    )


def test_watch_heals_embedding_outage_once_provider_returns(tmp_path: Path) -> None:
    target = tmp_path / "call.txt"
    target.write_text("transcript", encoding="utf-8")
    outage = _result(
        "partial",
        "call.txt",
        errors=["EmbeddingError: Connection failed: ollama returned zero vectors"],
    )
    ingest = _ScriptedIngest({str(target): [outage, outage, _result("success", "call.txt")]})
    rec = _Recorder()
    # Heal pass 1: provider still down. Heal pass 2: probe green, re-ingest succeeds.
    summary = _run(_watch(tmp_path, ingest, rec, probe_results=[False, True]))
    assert summary["healed"] == 1
    assert summary["still_broken"] == []
    assert summary["status"] == "success"
    # Exponential probe backoff before each heal attempt (5s base).
    assert rec.sleeps[0] == 5.0
    assert any("healed" in message for message in rec.messages)


def test_watch_heal_gives_up_loudly_after_capped_attempts(tmp_path: Path) -> None:
    target = tmp_path / "call.txt"
    target.write_text("transcript", encoding="utf-8")
    outage = _result(
        "partial",
        "call.txt",
        errors=["EmbeddingError: Connection failed: ollama returned zero vectors"],
    )
    ingest = _ScriptedIngest({str(target): [outage]})
    rec = _Recorder()
    summary = _run(_watch(tmp_path, ingest, rec, probe_results=[False] * 10))
    assert summary["healed"] == 0
    assert summary["still_broken"] == [str(target)]
    assert any("gave up" in message for message in rec.messages)


def test_watch_never_heals_non_embed_failures(tmp_path: Path) -> None:
    target = tmp_path / "broken.md"
    target.write_text("content", encoding="utf-8")
    ingest = _ScriptedIngest(
        {str(target): [_result("partial", "broken.md", errors=["PostgresWriteError: boom"])]}
    )
    rec = _Recorder()
    summary = _run(_watch(tmp_path, ingest, rec, probe_results=[True] * 5, max_passes=2))
    assert summary["healed"] == 0
    assert summary["still_broken"] == [str(target)]
    assert not any("healed" in message for message in rec.messages)


def test_scan_watch_dir_is_sorted_and_filtered(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text("x", encoding="utf-8")
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "skip.log").write_text("x", encoding="utf-8")
    names = [p.name for p in scan_watch_dir(tmp_path)]
    assert names == ["a.txt", "b.md"]
    single = tmp_path / "one.md"
    single.write_text("x", encoding="utf-8")
    assert scan_watch_dir(single) == [single]


# ---------------------------------------------------------------------------
# V1: --no-heal reaches the loop (CLI runner over the REAL _watch_flow seams)
# ---------------------------------------------------------------------------


def _patch_watch_seams(monkeypatch: pytest.MonkeyPatch, captured: dict[str, object]) -> None:
    async def fake_ledger_available(pool: object, tenant: str) -> bool:
        return False

    async def fake_watch(directory: object, ingest_cb: object, options: WatchOptions, **kw: object):
        captured["heal"] = options.heal
        return {"status": "success", "ingested": 0, "still_broken": []}

    monkeypatch.setattr("corpus_kb.ingest_watch.ledger_available", fake_ledger_available)
    monkeypatch.setattr("corpus_kb.ingest_watch.watch_directory", fake_watch)


def test_cli_watch_no_heal_disables_heal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "call.txt"
    target.write_text("transcript", encoding="utf-8")
    _patch_pool(monkeypatch)
    captured: dict[str, object] = {}
    _patch_watch_seams(monkeypatch, captured)

    result = runner.invoke(app, ["ingest", str(target), "--watch", "--no-heal"])
    assert result.exit_code == 0, result.output
    assert captured["heal"] is False
    assert "healed" not in result.output
    assert "auto-heal off" in result.output

    # Mirror scenario WITHOUT --no-heal: the heal loop stays enabled.
    captured.clear()
    result = runner.invoke(app, ["ingest", str(target), "--watch"])
    assert result.exit_code == 0, result.output
    assert captured["heal"] is True


# ---------------------------------------------------------------------------
# CLI exit codes (CliRunner over the REAL registered commands)
# ---------------------------------------------------------------------------


def _patch_ingest_file(
    monkeypatch: pytest.MonkeyPatch, results: dict[str, dict[str, object]]
) -> None:
    async def fake_ingest_file(
        file_path: str,
        pg_pool: object,
        config: dict[str, object] | None = None,
        tenant_id: str = TENANT,
        source_type: str | None = None,
    ) -> dict[str, object]:
        return results[file_path]

    monkeypatch.setattr("corpus_kb.tools.ingest_tools.ingest_file", fake_ingest_file)


def test_cli_ingest_exit_0_all_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file = tmp_path / "note.md"
    file.write_text("hello", encoding="utf-8")
    _patch_pool(monkeypatch)
    _patch_ingest_file(monkeypatch, {str(file): _result("success", str(file))})
    result = runner.invoke(app, ["ingest", str(file)])
    assert result.exit_code == 0, result.output
    assert "success" in result.output


def test_cli_ingest_exit_1_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    good = tmp_path / "good.md"
    bad = tmp_path / "bad.md"
    good.write_text("ok", encoding="utf-8")
    bad.write_text("boom", encoding="utf-8")
    _patch_pool(monkeypatch)
    _patch_ingest_file(
        monkeypatch,
        {
            str(bad): _result("partial", str(bad), errors=["PostgresWriteError: x"]),
            str(good): _result("success", str(good)),
        },
    )
    result = runner.invoke(app, ["ingest", str(tmp_path)])
    assert result.exit_code == 1, result.output


def test_cli_ingest_exit_2_total_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = tmp_path / "bad.md"
    bad.write_text("boom", encoding="utf-8")
    _patch_pool(monkeypatch)
    _patch_ingest_file(monkeypatch, {str(bad): _result("error", str(bad), message="unsupported")})
    result = runner.invoke(app, ["ingest", str(bad)])
    assert result.exit_code == 2, result.output


def test_cli_ingest_exit_3_unknown_source_type(tmp_path: Path) -> None:
    file = tmp_path / "note.md"
    file.write_text("hello", encoding="utf-8")
    result = runner.invoke(app, ["ingest", str(file), "--source-type", "meeting-notes"])
    assert result.exit_code == 3, result.output
    assert "unknown source-type strategy" in result.output


def test_cli_ingest_exit_3_missing_path(tmp_path: Path) -> None:
    result = runner.invoke(app, ["ingest", str(tmp_path / "missing.md")])
    assert result.exit_code == 3, result.output
    assert "path not found" in result.output


def test_cli_ingest_exit_3_on_postgres_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L11: bad DSN/credentials raise asyncpg.PostgresError (not OSError) —
    same honest config/connection bucket, exit 3."""
    import asyncpg

    file = tmp_path / "note.md"
    file.write_text("hello", encoding="utf-8")

    class _FakePostgresError(asyncpg.PostgresError):
        pass

    async def _open(_cfg: dict[str, object]) -> object:
        raise _FakePostgresError("password authentication failed for user")

    monkeypatch.setattr("corpus_kb.cli.cli_ingest._open_pool", _open)
    result = runner.invoke(app, ["ingest", str(file)])
    assert result.exit_code == 3, result.output
    assert "cannot reach the database" in result.output


def test_cli_ingest_help_documents_exit_codes() -> None:
    result = runner.invoke(app, ["ingest", "--help"])
    assert result.exit_code == 0
    assert "0" in result.output and "3" in result.output
    assert "Exit codes" in result.output


# ---------------------------------------------------------------------------
# reembed + status commands against the tool-layer seam
# ---------------------------------------------------------------------------


def _patch_reembed(
    monkeypatch: pytest.MonkeyPatch, summary: dict[str, object]
) -> dict[str, object]:
    calls: dict[str, object] = {}

    async def fake_reembed(
        pg_pool: object,
        config: dict[str, object] | None = None,
        tenant_id: str = TENANT,
        limit: int | None = None,
        doc_id: str | None = None,
    ) -> dict[str, object]:
        calls.update({"limit": limit, "doc_id": doc_id})
        return summary

    monkeypatch.setattr("corpus_kb.tools.ingest_surface_tools.ingest_reembed", fake_reembed)
    return calls


def test_cli_ingest_reembed_flag_exit_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pool(monkeypatch)
    _patch_reembed(monkeypatch, {"status": "success", "scanned": 4, "fixed": 3, "failed": 1})
    result = runner.invoke(app, ["ingest", "--reembed", "--limit", "4", "--json"])
    assert result.exit_code == 1, result.output

    _patch_reembed(monkeypatch, {"status": "success", "scanned": 0, "fixed": 0, "failed": 0})
    result = runner.invoke(app, ["ingest", "--reembed"])
    assert result.exit_code == 0, result.output

    _patch_reembed(
        monkeypatch,
        {
            "status": "unavailable",
            "reason": "migration 022 not applied",
            "scanned": 0,
            "fixed": 0,
            "failed": 0,
        },
    )
    result = runner.invoke(app, ["ingest", "--reembed"])
    assert result.exit_code == 3, result.output
    assert "migration 022" in result.output


def test_cli_ingest_reembed_dedicated_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pool(monkeypatch)
    calls = _patch_reembed(
        monkeypatch, {"status": "success", "scanned": 2, "fixed": 2, "failed": 0}
    )
    result = runner.invoke(app, ["ingest-reembed", "--limit", "2", "--doc-id", "not-a-uuid"])
    assert result.exit_code == 0, result.output
    assert calls["limit"] == 2  # typer parsed the flag; the tool layer validates types


def test_cli_ingest_status_command_json_and_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pool(monkeypatch)

    def _patch_status(report: dict[str, object]) -> None:
        async def fake_status(
            pg_pool: object,
            config: dict[str, object] | None = None,
            tenant_id: str = TENANT,
            limit: int = 20,
            project_id: str | None = None,
        ) -> dict[str, object]:
            return report

        monkeypatch.setattr("corpus_kb.tools.ingest_surface_tools.ingest_status", fake_status)

    _patch_status(
        {
            "status": "success",
            "ledger_present": True,
            "incomplete_runs": 1,
            "degraded": {"chunks": 2, "chunk_vectors": 3},
            "runs": [
                {
                    "run_id": "00000000-0000-0000-0000-00000000000a",
                    "source_type": "markdown",
                    "started_at": "2026-10-12T00:00:00+00:00",
                    "finished_at": "2026-10-12T00:00:05+00:00",
                    "status": "completed",
                    "start_hash": "aa",
                    "finish_hash": "bb",
                    "stages": {},
                    "error": None,
                }
            ],
        }
    )
    result = runner.invoke(app, ["ingest-status", "--json", "--limit", "5"])
    assert result.exit_code == 0, result.output
    assert '"degraded"' in result.output and '"completed"' in result.output

    _patch_status(
        {
            "status": "unavailable",
            "reason": "ingest_runs table absent (migration 022 not applied)",
            "remedy": "run corpus-kb setup",
            "ledger_present": False,
            "runs": [],
            "incomplete_runs": 0,
            "degraded": {"chunks": 0, "chunk_vectors": 0},
        }
    )
    result = runner.invoke(app, ["ingest-status"])
    assert result.exit_code == 3, result.output
    assert "UNAVAILABLE" in result.output


# ---------------------------------------------------------------------------
# Tool layer: ingest_status against a fake store (the test_search_honesty shape)
# ---------------------------------------------------------------------------


class _StatusConn:
    """Scripted asyncpg conn: ledger rows + degraded counts (no live SQL)."""

    def __init__(
        self,
        runs: list[dict[str, object]],
        ledger_present: bool = True,
        degraded_columns: int = 2,
        degraded_counts: tuple[int, int] = (0, 0),
        project_column: bool = True,
    ) -> None:
        self.runs = runs
        self.project_column = project_column
        self.ledger_present = ledger_present
        self.degraded_columns = degraded_columns
        self.degraded_counts = degraded_counts
        self.guc: list[str] = []

    async def execute(self, *args: object) -> str:
        self.guc.append(str(args))
        return "OK"

    def transaction(self) -> object:
        outer = self

        class _Tx:
            async def __aenter__(self) -> _StatusConn:
                return outer

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Tx()

    async def fetchval(self, statement: str, *args: object) -> object:
        if "to_regclass" in statement:
            return "ingest_runs" if self.ledger_present else None
        if "column_name = 'project_id'" in statement:
            return 1 if self.project_column else 0
        if "information_schema" in statement:
            return self.degraded_columns
        if "FROM ingest_runs WHERE tenant_id" in statement and "running" in statement:
            return 0
        if statement.count("degraded = TRUE") and "chunks_vectors" in statement:
            return self.degraded_counts[1]
        if "degraded = TRUE" in statement:
            return self.degraded_counts[0]
        return None

    async def fetch(self, statement: str, *args: object) -> list[dict[str, object]]:
        return self.runs


class _ConnPool:
    def __init__(self, conn: _StatusConn) -> None:
        self._conn = conn

    def acquire(self) -> object:
        outer = self

        class _Acquire:
            async def __aenter__(self) -> _StatusConn:
                return outer._conn

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Acquire()

    async def close(self) -> None:
        return None


def _run_tool(coro: object) -> dict[str, object]:
    return cast("dict[str, object]", asyncio.run(coro))


def test_ingest_status_tool_reports_runs_and_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from corpus_kb.tools import ingest_surface_tools

    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    from datetime import datetime

    started = datetime(2026, 10, 12, 12, 0, 0)
    conn = _StatusConn(
        runs=[
            {
                "run_id": "00000000-0000-0000-0000-00000000000a",
                "source_type": "markdown",
                "started_at": started,
                "finished_at": None,
                "status": "running",
                "start_hash": "aa",
                "finish_hash": None,
                "stages": "{}",
                "error": None,
                "project_id": None,
            }
        ],
        degraded_counts=(2, 1),
    )
    report = _run_tool(ingest_surface_tools.ingest_status(cast("object", _ConnPool(conn))))
    assert report["status"] == "success"
    assert report["ledger_present"] is True
    assert report["incomplete_runs"] == 0  # scripted: no incomplete rows in this fixture
    run = report["runs"][0]
    assert run["started_at"] == started.isoformat()  # JSON-safe serialization
    assert report["degraded"] == {"chunks": 2, "chunk_vectors": 1}


def test_ingest_status_tool_unavailable_pre_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from corpus_kb.tools import ingest_surface_tools

    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    conn = _StatusConn(runs=[], ledger_present=False)
    report = _run_tool(ingest_surface_tools.ingest_status(cast("object", _ConnPool(conn))))
    assert report["status"] == "unavailable"
    assert "migration 022" in str(report["reason"])
    assert report["runs"] == []


def test_ingest_reembed_tool_validates_doc_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_kb.tools import ingest_surface_tools

    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    bad = _run_tool(
        ingest_surface_tools.ingest_reembed(
            cast("object", None), {"embedding": {"provider": "pgml"}}, doc_id="nope"
        )
    )
    assert bad["status"] == "error"
    assert "not a UUID" in str(bad["message"])
    neg = _run_tool(
        ingest_surface_tools.ingest_reembed(
            cast("object", None), {"embedding": {"provider": "pgml"}}, limit=0
        )
    )
    assert neg["status"] == "error"


# ---------------------------------------------------------------------------
# HTTP route smoke (real route table, injected pool)
# ---------------------------------------------------------------------------


def test_ingest_routes_wired_with_methods() -> None:
    from corpus_kb.api.http import create_http_app

    table = {route.path: route.methods - {"HEAD"} for route in create_http_app().routes}
    assert table["/api/ingest/status"] == {"GET"}
    assert table["/api/ingest/reembed"] == {"POST"}


def test_ingest_status_route_smoke() -> None:
    from corpus_kb.api import research_http
    from corpus_kb.api.http import create_http_app

    conn = _StatusConn(
        runs=[
            {
                "run_id": "00000000-0000-0000-0000-00000000000a",
                "source_type": "text",
                "started_at": None,
                "finished_at": None,
                "status": "failed",
                "start_hash": "aa",
                "finish_hash": None,
                "stages": "{}",
                "error": "EmbeddingError: Connection failed: zero vectors",
                "project_id": None,
            }
        ],
        degraded_counts=(1, 1),
    )
    research_http.set_research_pool(cast("object", _ConnPool(conn)))
    try:
        client = TestClient(create_http_app())
        response = client.get("/api/ingest/status?limit=5")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["runs"][0]["status"] == "failed"
        assert body["degraded"] == {"chunks": 1, "chunk_vectors": 1}
    finally:
        research_http.set_research_pool(None)


def test_ingest_reembed_route_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_kb.api import research_http
    from corpus_kb.api.http import create_http_app
    from corpus_kb.tools import ingest_surface_tools

    async def fake_reembed(
        pg_pool: object,
        config: dict[str, object] | None = None,
        tenant_id: str = TENANT,
        limit: int | None = None,
        doc_id: str | None = None,
    ) -> dict[str, object]:
        return {"status": "success", "scanned": 1, "fixed": 1, "failed": 0, "limit": limit}

    monkeypatch.setattr(ingest_surface_tools, "ingest_reembed", fake_reembed)
    research_http.set_research_pool(cast("object", FakePool()))
    try:
        client = TestClient(create_http_app())
        response = client.post("/api/ingest/reembed", json={"limit": 1})
        assert response.status_code == 200
        assert response.json() == {
            "status": "success",
            "scanned": 1,
            "fixed": 1,
            "failed": 0,
            "limit": 1,
        }
    finally:
        research_http.set_research_pool(None)


# ---------------------------------------------------------------------------
# --validate (issue #188): the optional gate flag reaches the shared handlers
# ---------------------------------------------------------------------------


def _flat_help(output: str) -> str:
    ansi = re.compile(r"\[[0-9;]*m")
    return " ".join(ansi.sub("", output).split())


def _patch_ingest_file_capture(
    monkeypatch: pytest.MonkeyPatch, result: dict[str, object]
) -> list[dict[str, object]]:
    """Fake ingest_file that records every kwarg (the --validate seam)."""
    calls: list[dict[str, object]] = []

    async def fake_ingest_file(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append({"args": args, **kwargs})
        return result

    monkeypatch.setattr("corpus_kb.tools.ingest_tools.ingest_file", fake_ingest_file)
    return calls


def test_cli_ingest_validate_reaches_the_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = tmp_path / "note.md"
    file.write_text("hello", encoding="utf-8")
    _patch_pool(monkeypatch)
    calls = _patch_ingest_file_capture(monkeypatch, _result("success", str(file)))
    result = runner.invoke(app, ["ingest", str(file), "--validate", "enforce"])
    assert result.exit_code == 0, result.output
    assert calls[0]["validate"] == "enforce"


def test_cli_ingest_validate_omitted_keeps_the_profile_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = tmp_path / "note.md"
    file.write_text("hello", encoding="utf-8")
    _patch_pool(monkeypatch)
    calls = _patch_ingest_file_capture(monkeypatch, _result("success", str(file)))
    result = runner.invoke(app, ["ingest", str(file)])
    assert result.exit_code == 0, result.output
    assert "validate" not in calls[0]


def test_cli_ingest_validate_rejects_unknown_mode(tmp_path: Path) -> None:
    file = tmp_path / "note.md"
    file.write_text("hello", encoding="utf-8")
    result = runner.invoke(app, ["ingest", str(file), "--validate", "strict"])
    assert result.exit_code == 2, result.output
    assert "not one of" in result.output
    assert "calibrate" in result.output


def test_cli_ingest_url_validate_reaches_the_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    async def fake_ingest_url(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append({"args": args, **kwargs})
        return {
            "status": "success",
            "pages": [_result("success", "https://example.test/")],
            "total_documents": 1,
        }

    monkeypatch.setattr("corpus_kb.tools.ingest_tools.ingest_url", fake_ingest_url)
    _patch_pool(monkeypatch)
    result = runner.invoke(app, ["ingest-url", "https://example.test/", "--validate", "advisory"])
    assert result.exit_code == 0, result.output
    assert calls[0]["validate"] == "advisory"


def test_cli_watch_validate_reaches_the_per_file_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "call.txt"
    target.write_text("transcript", encoding="utf-8")
    _patch_pool(monkeypatch)
    calls = _patch_ingest_file_capture(monkeypatch, _result("success", str(target)))

    async def fake_ledger_available(pool: object, tenant: str) -> bool:
        return False

    async def fake_watch(
        directory: object,
        ingest_cb: Callable[[Path], Awaitable[dict[str, object]]],
        options: WatchOptions,
        **kw: object,
    ) -> dict[str, object]:
        await ingest_cb(target)
        return {"status": "success", "ingested": 0, "still_broken": []}

    monkeypatch.setattr("corpus_kb.ingest_watch.ledger_available", fake_ledger_available)
    monkeypatch.setattr("corpus_kb.ingest_watch.watch_directory", fake_watch)
    result = runner.invoke(app, ["ingest", str(target), "--watch", "--validate", "off"])
    assert result.exit_code == 0, result.output
    assert calls[0]["validate"] == "off"


def test_cli_ingest_prints_the_active_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = tmp_path / "note.md"
    file.write_text("hello", encoding="utf-8")
    _patch_pool(monkeypatch)
    _patch_ingest_file_capture(monkeypatch, _result("success", str(file), profile="interviews"))
    result = runner.invoke(app, ["ingest", str(file)])
    assert result.exit_code == 0, result.output
    assert "profile: interviews" in result.output


def test_cli_ingest_omits_the_profile_line_without_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = tmp_path / "note.md"
    file.write_text("hello", encoding="utf-8")
    _patch_pool(monkeypatch)
    _patch_ingest_file_capture(monkeypatch, _result("success", str(file)))
    result = runner.invoke(app, ["ingest", str(file)])
    assert result.exit_code == 0, result.output
    assert "profile:" not in result.output


def test_cli_ingest_help_documents_the_validate_choices() -> None:
    result = runner.invoke(app, ["ingest", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    flat = _flat_help(result.output)
    assert "--validate" in flat
    assert "off|advisory|enforce|calibrate" in flat
