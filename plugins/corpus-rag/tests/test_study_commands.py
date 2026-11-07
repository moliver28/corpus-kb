"""Behavior tests for the plugin commands (issue #351; 0.6.0 pinned).

Covers: the status false-ok fix (doctor exit 3 is not ok, database line
printed), the sql refusal passthrough, since-status ingest counting, the
``provision`` compatibility alias, DuckDB stop-before ordering in
migrate and backup, and the live ops count. No test starts a server or
clones anything: subprocess and HTTP seams are replaced.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import op_client  # noqa: E402
import study_admin  # noqa: E402
import study_commands  # noqa: E402
import study_maintenance  # noqa: E402
import study_query  # noqa: E402
import study_registry  # noqa: E402
import study_server  # noqa: E402


@pytest.fixture(autouse=True)
def plugin_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("CORPUS_KB_STATE_DIR", str(tmp_path / "engine-state"))
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_SUPERUSER_DSN", raising=False)
    return tmp_path


class Args:
    def __init__(self, **kwargs: object) -> None:
        self.study_slug = None
        for key, value in kwargs.items():
            setattr(self, key, value)


def _registry_with(slug: str = "alpha", backend: str = "postgres", pid: object = None) -> dict:
    registry = study_registry.empty_registry()
    study_registry.register_study(
        registry, slug, f"postgresql://corpus_user:pw@localhost:5432/corpus_kb_{slug}",
        8010, backend,
    )
    registry["studies"][slug]["pid"] = pid
    registry["active"] = slug
    study_registry.save_registry(registry)
    return registry


def _fake_completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(["fake"], returncode, stdout, stderr)


def test_status_reports_not_ok_on_doctor_exit_3(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_with("alpha")
    monkeypatch.setattr(study_commands, "require_engine", lambda: "corpus-kb")
    monkeypatch.setattr(
        study_commands, "_run_doctor_json",
        lambda cli, slug, study: _fake_completed(3, stdout=""),
    )
    called = {"ensure": False}
    monkeypatch.setattr(
        study_server, "ensure_running",
        lambda *a, **kw: called.__setitem__("ensure", True),
    )
    code = study_commands.cmd_status(Args())
    out = capsys.readouterr().out
    assert code == 1
    assert "not ok" in out
    assert "corpus_kb_alpha" in out  # the database line is printed
    assert called["ensure"] is False


def test_status_ok_prints_counts_and_resets_since(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _registry_with("alpha")
    monkeypatch.setattr(study_commands, "require_engine", lambda: "corpus-kb")
    doctor = json.dumps({"checks": {"ollama": {"reachable": True}}})
    monkeypatch.setattr(
        study_commands, "_run_doctor_json",
        lambda cli, slug, study: _fake_completed(0, stdout=doctor),
    )
    monkeypatch.setattr(study_server, "ensure_running", lambda cli, reg, slug, study: study)
    monkeypatch.setattr(
        op_client, "get_json",
        lambda url, timeout=120: {"documents": 3, "chunks": 9, "chunks_vectors": 8},
    )
    monkeypatch.setattr(
        op_client, "post_json", lambda url, payload, timeout=120: {"result": [1, 2]}
    )
    since = tmp_path / "data" / "since_status.json"
    since.write_text(json.dumps({"ingested": 4, "failed": 1}), encoding="utf-8")
    code = study_commands.cmd_status(Args())
    out = capsys.readouterr().out
    assert code == 0
    assert "documents: 3" in out
    assert "Queue: 2 row(s)." in out
    assert "Since last status: 4 ingested, 1 failed." in out
    assert json.loads(since.read_text(encoding="utf-8")) == {"ingested": 0, "failed": 0}
    assert "ok:" in out


def test_status_ollama_down_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_with("alpha")
    monkeypatch.setattr(study_commands, "require_engine", lambda: "corpus-kb")
    doctor = json.dumps({"checks": {"ollama": {"reachable": False}}})
    monkeypatch.setattr(
        study_commands, "_run_doctor_json",
        lambda cli, slug, study: _fake_completed(0, stdout=doctor),
    )
    monkeypatch.setattr(study_server, "ensure_running", lambda cli, reg, slug, study: study)
    monkeypatch.setattr(op_client, "get_json", lambda url, timeout=120: {})
    monkeypatch.setattr(op_client, "post_json", lambda url, payload, timeout=120: {})
    study_commands.cmd_status(Args())
    assert "Ollama is down" in capsys.readouterr().out


def test_status_without_study_prints_the_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(study_commands, "require_engine", lambda: "corpus-kb")
    with pytest.raises(study_commands.CommandError) as excinfo:
        study_commands.cmd_status(Args())
    assert study_registry.NO_STUDY_MESSAGE in str(excinfo.value)


def test_sql_passes_the_refusal_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_with("alpha")
    monkeypatch.setattr(
        study_commands, "scoped_study",
        lambda slug_arg, ensure_server=True: (
            "corpus-kb", "alpha", {"port": 8010, "database_url": "x", "backend": "postgres"}
        ),
    )

    def refuse(url, payload, timeout=120):
        raise op_client.HttpError("HTTP 400 from POST http://x: only SELECT statements run")

    monkeypatch.setattr(op_client, "post_json", refuse)
    code = study_query.cmd_sql(Args(query="DELETE FROM chunks"))
    out = capsys.readouterr().out
    assert code == 1
    assert "only SELECT statements run" in out


def test_ingest_directory_counts_land_in_since_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    folder = tmp_path / "docs"
    folder.mkdir()
    _registry_with("alpha")
    monkeypatch.setattr(
        study_commands, "scoped_study",
        lambda slug_arg, ensure_server=True: (
            "corpus-kb", "alpha", {"port": 8010, "database_url": "x", "backend": "postgres"}
        ),
    )
    seen: dict = {}

    def fake_run_op(cli, name, pairs, base_url, **kw):
        seen["op"] = name
        seen["args"] = dict(pair.split("=", 1) for pair in pairs)
        return {
            "status": "success",
            "results": [{"status": "success"}, {"status": "success"}, {"status": "error"}],
        }

    monkeypatch.setattr(study_query.op_runner, "run_op", fake_run_op)
    code = study_query.cmd_ingest(Args(path=str(folder)))
    out = capsys.readouterr().out
    assert code == 0
    assert seen["op"] == "ingest_directory"
    since = json.loads(
        (tmp_path / "data" / "since_status.json").read_text(encoding="utf-8")
    )
    assert since == {"ingested": 2, "failed": 1}
    assert "ok:" in out


def test_ingest_missing_path_is_honest(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _registry_with("alpha")
    monkeypatch.setattr(
        study_commands, "scoped_study",
        lambda slug_arg, ensure_server=True: (
            "corpus-kb", "alpha", {"port": 8010, "database_url": "x", "backend": "postgres"}
        ),
    )
    code = study_query.cmd_ingest(Args(path=str(tmp_path / "nothing-here")))
    assert code == 1
    assert "not ok" in capsys.readouterr().out


def test_provision_project_id_keeps_0_7_x_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        study_admin, "cmd_raw_provision",
        lambda args: seen.update(slug=args.slug, project_id=args.project_id) or 0,
    )
    code = study_admin.cmd_provision(Args(slug="meeting-notes", project_id="p-1"))
    assert code == 0
    assert seen == {"slug": "meeting-notes", "project_id": "p-1"}


def test_provision_duckdb_registers_study(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(study_commands, "require_engine", lambda: "corpus-kb")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _fake_completed(0, stdout="ok")
    )
    code = study_admin.cmd_provision(Args(slug="Interview Study!", backend="duckdb"))
    out = capsys.readouterr().out
    assert code == 0
    registry = study_registry.load_registry()
    study = registry["studies"]["interview_study"]
    assert study["backend"] == "duckdb"
    assert study["database_url"].endswith("corpus_kb_interview_study.duckdb")
    assert study["port"] >= 8010
    assert "corpus-rag use interview_study" in out


def test_provision_failure_prints_cleanup_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(study_commands, "require_engine", lambda: "corpus-kb")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _fake_completed(1, stderr="connection refused")
    )
    code = study_admin.cmd_provision(Args(slug="alpha", backend="postgres"))
    out = capsys.readouterr().out
    assert code == 1
    assert "Cleanup:" in out
    assert "DROP DATABASE IF EXISTS corpus_kb_alpha" in out


def test_migrate_stops_duckdb_server_before_the_engine_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_with("alpha", backend="duckdb", pid=99)
    order: list[str] = []
    monkeypatch.setattr(study_commands, "require_engine", lambda: "corpus-kb")
    monkeypatch.setattr(
        study_server, "stop_before",
        lambda slug, study, reason, grace=30: order.append(f"stop:{reason}") or True,
    )
    monkeypatch.setattr(
        study_admin, "subprocess",
        type("S", (), {
            "run": staticmethod(
                lambda command, **kw: order.append("migrate") or _fake_completed(
                    0, stdout='{"applied_now": [], "pending": []}'
                )
            )
        }),
    )
    code = study_admin.cmd_migrate(Args(all=False, reset_embeddings=False))
    out = capsys.readouterr().out
    assert code == 0
    assert order == ["stop:migrate", "migrate"]
    assert "alpha: applied now 0; pending 0." in out


def test_migrate_all_runs_each_study(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = _registry_with("alpha")
    study_registry.register_study(
        registry, "beta", f"postgresql://x/{study_registry.database_name('beta')}", 8011, "postgres"
    )
    study_registry.save_registry(registry)
    runs: list[str] = []
    monkeypatch.setattr(study_commands, "require_engine", lambda: "corpus-kb")
    monkeypatch.setattr(study_server, "stop_before", lambda *a, **kw: False)
    monkeypatch.setattr(
        study_admin, "subprocess",
        type("S", (), {
            "run": staticmethod(
                lambda command, **kw: runs.append(kw["env"]["CORPUS_KB_DATABASE_URL"])
                or _fake_completed(0, stdout='{"applied_now": [], "pending": []}')
            )
        }),
    )
    code = study_admin.cmd_migrate(Args(all=True, reset_embeddings=False))
    assert code == 0
    assert len(runs) == 2
    assert any("corpus_kb_beta" in url for url in runs)


def test_backup_duckdb_copies_all_files_and_stops_first(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    registry = _registry_with("alpha", backend="duckdb", pid=99)
    study_registry.save_registry(registry)
    db_file = study_registry.duckdb_file("alpha")
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_file.write_bytes(b"db")
    Path(str(db_file) + ".wal").write_bytes(b"wal")
    Path(str(db_file) + ".events.db").write_bytes(b"events")
    order: list[str] = []
    monkeypatch.setattr(
        study_server, "stop_before",
        lambda slug, study, reason, grace=30: order.append(f"stop:{reason}") or True,
    )
    code = study_maintenance.cmd_backup(Args(study_slug="alpha"))
    out = capsys.readouterr().out
    assert code == 0
    assert order == ["stop:backup"]
    backups = list((tmp_path / "data" / "backups" / "alpha").iterdir())
    assert len(backups) == 3
    assert "Restore:" in out


def test_ops_prints_the_live_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = {
        "operations": [
            {"name": "search", "family": "search", "description": "Hybrid search."},
            {"name": "ask", "family": "search", "description": "Cited answer."},
            {"name": "events_summary", "family": "events", "description": "Event health."},
        ]
    }
    monkeypatch.setattr(study_maintenance.op_runner, "load_inventory", lambda cli: inventory)
    monkeypatch.setattr(study_commands, "require_engine", lambda: "corpus-kb")
    study_maintenance.cmd_ops(Args(family=None))
    out = capsys.readouterr().out
    assert "3 operations." in out  # live count, never hard-coded
    study_maintenance.cmd_ops(Args(family="events"))
    out = capsys.readouterr().out
    assert "events_summary" in out and "search" not in out.split("\n")[0]


def test_op_unknown_name_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_with("alpha")
    monkeypatch.setattr(
        study_commands, "scoped_study",
        lambda slug_arg, ensure_server=True: (
            "corpus-kb", "alpha", {"port": 8010, "database_url": "x", "backend": "postgres"}
        ),
    )
    monkeypatch.setattr(
        study_maintenance.op_runner, "load_inventory", lambda cli: {"operations": []}
    )
    code = study_maintenance.cmd_op(Args(name="nonsense", pairs=[], json=None))
    out = capsys.readouterr().out
    assert code == 2
    assert "Unknown op 'nonsense'" in out


def test_connect_prints_one_entry_per_study(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_with("alpha")
    monkeypatch.setattr(study_commands, "engine_root", lambda: Path("/engine"))
    monkeypatch.setattr(study_commands, "engine_cli", lambda root: "/engine/corpus-kb")
    study_commands.cmd_connect(Args())
    config = json.loads(capsys.readouterr().out)
    assert "corpus-kb" in config["mcpServers"]
    entry = config["mcpServers"]["corpus-kb-alpha"]
    assert entry["env"]["CORPUS_KB_DATABASE_BACKEND"] == "postgres"
    assert entry["env"]["CORPUS_KB_DATABASE_URL"].endswith("corpus_kb_alpha")
