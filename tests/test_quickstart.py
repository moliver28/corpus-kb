"""Tests for the ``corpus-kb quickstart`` machine, write-back, and CLI.

All Ollama/Postgres/Docker/subprocess work is faked through
QuickstartDeps; the CLI tests monkeypatch ``cli_quickstart.build_deps``.
Offline: no real Ollama, no Postgres, no Docker, no pulls.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corpus_kb._setup.db_provision import DsnParts, PgProbe
from corpus_kb._setup.quickstart import QuickstartRunner
from corpus_kb._setup.quickstart_types import (
    QuickstartDeps,
    QuickstartOptions,
    StepResult,
    model_present,
    runtime_dsn,
)
from corpus_kb._setup.schema_apply import SchemaApplyResult
from corpus_kb.cli import app

runner = CliRunner()

DSN_5433 = "postgresql://corpus_user:corpus_pass@localhost:5433/corpus_kb"
DSN_5432 = "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"
SUPER_DSN = "postgresql://postgres:postgres@localhost:5432/postgres"

CONFIG: dict[str, object] = {
    "database": {"connection_string": DSN_5433},
    "embedding": {"model": "m-emb", "base_url": "http://localhost:11434"},
    "judge": {"model": "m-judge"},
    "retrieval": {"self_query": {"model": "m-sq"}},
    "ingest": {"contextualize": {"model": "m-ctx"}},
    "reranking": {"model": "rr:model"},
}
ALL_MODELS = {"m-emb", "m-judge", "m-sq", "m-ctx", "rr:model"}


def _step(steps: list[StepResult], name: str) -> StepResult:
    found = [s for s in steps if s.name == name]
    assert found, f"step {name} missing from {[s.name for s in steps]}"
    return found[-1]


@dataclass
class FakeStack:
    """Every collaborator the machine needs, recording what it was given."""

    probes: dict[str, PgProbe] = field(default_factory=dict)
    docker: bool = False
    present: set[str] = field(default_factory=lambda: set(ALL_MODELS))
    pull_rc: int = 0
    ollama_up: bool = True
    provisioned: list[tuple[str, str]] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    schema_error: str | None = None
    written: list[str] = field(default_factory=list)
    pulled: list[str] = field(default_factory=list)
    env_dsns: list[str] = field(default_factory=list)
    doctor_calls: list[dict[str, object]] = field(default_factory=list)

    def deps(self, config: dict[str, object]) -> QuickstartDeps:
        return QuickstartDeps(
            config=config,
            ollama_get=self._ollama_get,
            pg_probe=self._pg_probe,
            docker_available=lambda: self.docker,
            provision=self._provision,
            apply_schema=self._apply_schema,
            write_dsn=self._write_dsn,
            pull_model=self._pull_model,
            run_doctor=self._run_doctor,
            set_env_dsn=self._set_env,
        )

    async def _ollama_get(self, _base_url: str, path: str):
        if not self.ollama_up:
            return None
        if path == "/api/version":
            return 200, {"version": "0.6.9"}
        return 200, {"models": [{"name": name} for name in sorted(self.present)]}

    async def _pg_probe(self, dsn: str) -> PgProbe:
        return self.probes.get(dsn, PgProbe(False, f"no listener for {dsn}"))

    async def _provision(self, superuser: str, parts: DsnParts) -> str:
        self.provisioned.append((superuser, parts.database))
        self.probes = {key: PgProbe(True, "up after provisioning") for key in self.probes}
        return f"created role {parts.user}; created database {parts.database}"

    async def _apply_schema(self, dsn: str) -> SchemaApplyResult:
        if self.schema_error is not None:
            raise RuntimeError(self.schema_error)
        self.applied.append(dsn)
        return SchemaApplyResult(
            applied=["001_corpus_schema.sql"],
            skipped=["004_enable_age.sql", "005_enable_pgml.sql"],
        )

    def _write_dsn(self, dsn: str) -> str:
        self.written.append(dsn)
        return f"wrote config.yaml ({dsn})"

    def _pull_model(self, model: str) -> int:
        self.pulled.append(model)
        return self.pull_rc

    async def _run_doctor(self, config: dict[str, object]) -> int:
        self.doctor_calls.append(config)
        return 0

    def _set_env(self, dsn: str) -> None:
        self.env_dsns.append(dsn)


def _run_machine(stack: FakeStack, **opt_kwargs) -> QuickstartRunner:
    machine = QuickstartRunner(QuickstartOptions(**opt_kwargs), stack.deps(dict(CONFIG)))
    asyncio.run(machine.run())
    return machine


# ---------------------------------------------------------------------------
# The step machine
# ---------------------------------------------------------------------------


def test_happy_path_configured_dsn_reachable() -> None:
    stack = FakeStack(probes={DSN_5433: PgProbe(True, "up")})
    result = _run_machine(stack)
    assert result.outcome.exit_code == 0
    assert result.outcome.adopted_dsn == DSN_5433
    assert _step(result.outcome.steps, "preflight-ollama").state == "OK"
    assert "0.6.9" in _step(result.outcome.steps, "preflight-ollama").detail
    assert _step(result.outcome.steps, "preflight-postgres").state == "OK"
    assert _step(result.outcome.steps, "postgres").state == "OK"
    assert _step(result.outcome.steps, "schema").state == "OK"
    assert stack.applied == [DSN_5433]
    # The configured DSN already matches: write-back skips.
    assert _step(result.outcome.steps, "write-back").state == "SKIP"
    assert stack.written == []
    assert _step(result.outcome.steps, "models").state == "OK"
    assert stack.pulled == []
    assert stack.env_dsns == [DSN_5433]
    assert stack.doctor_calls and stack.doctor_calls[-1]["database"] == {
        "connection_string": DSN_5433
    }


def test_fallback_adopts_localhost_5432_and_writes_back() -> None:
    stack = FakeStack(
        probes={DSN_5433: PgProbe(False, "connection refused"), DSN_5432: PgProbe(True, "up")}
    )
    result = _run_machine(stack)
    assert result.outcome.exit_code == 0
    assert result.outcome.adopted_dsn == DSN_5432
    postgres = _step(result.outcome.steps, "postgres")
    assert postgres.state == "OK"
    assert "5432" in postgres.detail
    assert _step(result.outcome.steps, "write-back").state == "FIX"
    assert stack.written == [DSN_5432]


def test_no_write_leaves_config_untouched() -> None:
    stack = FakeStack(probes={DSN_5433: PgProbe(False, "refused"), DSN_5432: PgProbe(True, "up")})
    result = _run_machine(stack, no_write=True)
    assert result.outcome.adopted_dsn == DSN_5432
    assert _step(result.outcome.steps, "write-back").state == "SKIP"
    assert stack.written == []


def test_docker_offer_prints_setup_command_but_fails() -> None:
    stack = FakeStack(probes={DSN_5433: PgProbe(False, "connection refused")}, docker=True)
    result = _run_machine(stack)
    assert result.outcome.exit_code == 1
    postgres = _step(result.outcome.steps, "postgres")
    assert postgres.state == "FAIL"
    assert any("corpus-kb setup" in line for line in postgres.extra)
    assert stack.applied == []
    assert stack.written == []


def test_no_docker_hints_install_or_superuser() -> None:
    stack = FakeStack(probes={DSN_5433: PgProbe(False, "connection refused")}, docker=False)
    result = _run_machine(stack)
    assert result.outcome.exit_code == 1
    postgres = _step(result.outcome.steps, "postgres")
    assert any("--superuser-dsn" in line for line in postgres.extra)


def test_missing_database_provisions_via_superuser() -> None:
    stack = FakeStack(
        probes={
            DSN_5433: PgProbe(False, 'database "corpus_kb" does not exist', database_missing=True)
        }
    )
    result = _run_machine(stack, superuser_dsn=SUPER_DSN)
    assert result.outcome.exit_code == 0
    assert stack.provisioned == [(SUPER_DSN, "corpus_kb")]
    assert _step(result.outcome.steps, "provision").state == "FIX"
    assert result.outcome.adopted_dsn == DSN_5433
    assert stack.applied == [DSN_5433]


def test_auth_failure_provisions_too() -> None:
    stack = FakeStack(
        probes={DSN_5433: PgProbe(False, "password authentication failed", auth_failed=True)}
    )
    result = _run_machine(stack, superuser_dsn=SUPER_DSN)
    assert result.outcome.exit_code == 0
    assert stack.provisioned == [(SUPER_DSN, "corpus_kb")]
    assert result.outcome.adopted_dsn == DSN_5433


def test_unprovisionable_failure_has_no_provision_step() -> None:
    stack = FakeStack(probes={DSN_5433: PgProbe(False, "connection refused")})
    result = _run_machine(stack, superuser_dsn=SUPER_DSN)
    assert result.outcome.exit_code == 1
    assert stack.provisioned == []
    assert all(step.name != "provision" for step in result.outcome.steps)


def test_explicit_dsn_is_probed_and_adopted() -> None:
    stack = FakeStack(probes={DSN_5433: PgProbe(True, "up"), DSN_5432: PgProbe(True, "up")})
    result = _run_machine(stack, dsn=DSN_5432)
    assert result.outcome.adopted_dsn == DSN_5432
    assert stack.applied == [DSN_5432]
    assert stack.written == [DSN_5432]


def test_schema_failure_fails_the_run() -> None:
    stack = FakeStack(probes={DSN_5433: PgProbe(True, "up")}, schema_error="boom")
    result = _run_machine(stack)
    assert result.outcome.exit_code == 1
    assert _step(result.outcome.steps, "schema").state == "FAIL"


def test_ollama_down_fails_preflight_but_not_everything() -> None:
    stack = FakeStack(probes={DSN_5433: PgProbe(True, "up")}, ollama_up=False)
    result = _run_machine(stack)
    assert _step(result.outcome.steps, "preflight-ollama").state == "FAIL"
    assert "ollama serve" in _step(result.outcome.steps, "preflight-ollama").detail
    assert _step(result.outcome.steps, "models").state == "FAIL"
    assert result.outcome.exit_code == 1


def test_missing_models_pulled_with_flag() -> None:
    stack = FakeStack(probes={DSN_5433: PgProbe(True, "up")}, present={"m-emb", "rr:model"})
    result = _run_machine(stack)
    models = _step(result.outcome.steps, "models")
    assert models.state == "FIX"
    assert sorted(stack.pulled) == ["m-ctx", "m-judge", "m-sq"]
    assert any("llama-server" in line for line in models.extra)
    assert result.outcome.exit_code == 0


def test_missing_models_print_commands_without_flag() -> None:
    stack = FakeStack(probes={DSN_5433: PgProbe(True, "up")}, present={"m-emb", "rr:model"})
    result = _run_machine(stack, pull_models=False)
    models = _step(result.outcome.steps, "models")
    assert models.state == "FIX"
    assert stack.pulled == []
    assert "ollama pull m-judge" in models.extra
    assert "ollama pull m-sq" in models.extra


def test_pull_failure_is_a_fail_with_command() -> None:
    stack = FakeStack(
        probes={DSN_5433: PgProbe(True, "up")}, present={"m-emb", "rr:model"}, pull_rc=1
    )
    result = _run_machine(stack)
    models = _step(result.outcome.steps, "models")
    assert models.state == "FAIL"
    assert "ollama pull m-judge" in models.extra
    assert result.outcome.exit_code == 1


def test_runtime_dsn_missing_raises() -> None:
    with pytest.raises(ValueError):
        runtime_dsn({})


def test_model_present_tag_rules() -> None:
    present = {"nomic-embed-text:latest", "qwen3:4b"}
    assert model_present(present, "nomic-embed-text")
    assert model_present(present, "qwen3:4b")
    assert not model_present(present, "qwen3:8b")


# ---------------------------------------------------------------------------
# The config write-back
# ---------------------------------------------------------------------------


def test_write_connection_string_replaces_one_line(tmp_path: Path) -> None:
    from corpus_kb._setup.config_writeback import write_connection_string

    target = tmp_path / "config.yaml"
    target.write_text(
        "# header comment\ndatabase:\n"
        "  # the local runtime contract\n"
        "  connection_string: postgresql://old@localhost:5433/corpus_kb\n"
        "server:\n  port: 8010\n",
        encoding="utf-8",
    )
    notice = write_connection_string(target, DSN_5432)
    text = target.read_text(encoding="utf-8")
    assert DSN_5432 in text
    assert "postgresql://old@" not in text
    assert "# header comment" in text
    assert "# the local runtime contract" in text
    assert "port: 8010" in text
    assert "Wrote" in notice


def test_write_connection_string_appends_block(tmp_path: Path) -> None:
    import yaml

    from corpus_kb._setup.config_writeback import write_connection_string

    target = tmp_path / "config.yaml"
    target.write_text("server:\n  port: 8010\n", encoding="utf-8")
    write_connection_string(target, DSN_5432)
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["database"]["connection_string"] == DSN_5432
    assert data["server"]["port"] == 8010


def test_write_connection_string_creates_missing_file(tmp_path: Path) -> None:
    import yaml

    from corpus_kb._setup.config_writeback import write_connection_string

    target = tmp_path / "config.yaml"
    write_connection_string(target, DSN_5432)
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["database"]["connection_string"] == DSN_5432


def test_write_connection_string_rejects_ambiguous(tmp_path: Path) -> None:
    from corpus_kb._setup.config_writeback import write_connection_string

    target = tmp_path / "config.yaml"
    target.write_text(
        "database:\n  connection_string: a\nserver:\n  port: 1\ndatabase:\n"
        "  connection_string: b\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="connection_string"):
        write_connection_string(target, DSN_5432)


def test_config_write_target_env_pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_kb._setup.config_writeback import config_write_target

    monkeypatch.setenv("CORPUS_KB_CONFIG", str(tmp_path / "pinned.yaml"))
    assert config_write_target() == tmp_path / "pinned.yaml"
    monkeypatch.delenv("CORPUS_KB_CONFIG")
    assert config_write_target() == Path("config.yaml")


# ---------------------------------------------------------------------------
# The CLI front door (fakes injected through build_deps)
# ---------------------------------------------------------------------------


def _write_cli_config(tmp_path: Path, dsn: str) -> Path:
    target = tmp_path / "config.yaml"
    target.write_text(f"database:\n  connection_string: {dsn}\n", encoding="utf-8")
    return target


def _use(monkeypatch: pytest.MonkeyPatch, stack: FakeStack) -> None:
    """Swap build_deps for fakes, but keep the REAL config write-back."""
    import corpus_kb.cli.cli_quickstart as cli_qs
    from corpus_kb._setup.config_writeback import config_write_target, write_connection_string

    def factory(config: dict[str, object]) -> QuickstartDeps:
        deps = stack.deps(config)

        def write(dsn: str) -> str:
            stack.written.append(dsn)
            return write_connection_string(config_write_target(), dsn)

        deps.write_dsn = write
        return deps

    monkeypatch.setattr(cli_qs, "build_deps", factory)


def _cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dsn: str) -> None:
    monkeypatch.setenv("CORPUS_KB_CONFIG", str(_write_cli_config(tmp_path, dsn)))
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)


def test_cli_json_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stack = FakeStack(probes={DSN_5433: PgProbe(True, "up")})
    _use(monkeypatch, stack)
    _cli_env(tmp_path, monkeypatch, DSN_5433)
    result = runner.invoke(app, ["quickstart", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["exit_code"] == 0
    assert payload["adopted_dsn"] == DSN_5433
    assert any(step["name"] == "schema" for step in payload["steps"])


def test_cli_writes_adopted_dsn_to_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stack = FakeStack(probes={DSN_5433: PgProbe(False, "refused"), DSN_5432: PgProbe(True, "up")})
    _use(monkeypatch, stack)
    _cli_env(tmp_path, monkeypatch, DSN_5433)
    result = runner.invoke(app, ["quickstart"])
    assert result.exit_code == 0, result.output
    text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert DSN_5432 in text
    assert "corpus-kb ingest docs" in result.output


def test_cli_no_write_does_not_touch_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = FakeStack(probes={DSN_5433: PgProbe(False, "refused"), DSN_5432: PgProbe(True, "up")})
    _use(monkeypatch, stack)
    _cli_env(tmp_path, monkeypatch, DSN_5433)
    result = runner.invoke(app, ["quickstart", "--no-write"])
    assert result.exit_code == 0, result.output
    text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert DSN_5432 not in text
    assert DSN_5433 in text


def test_cli_exit_1_when_no_postgres(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stack = FakeStack(probes={DSN_5433: PgProbe(False, "refused")}, docker=False)
    _use(monkeypatch, stack)
    _cli_env(tmp_path, monkeypatch, DSN_5433)
    result = runner.invoke(app, ["quickstart", "--json"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "failed"
    assert payload["adopted_dsn"] is None


def test_cli_provision_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stack = FakeStack(
        probes={
            DSN_5433: PgProbe(False, 'database "corpus_kb" does not exist', database_missing=True)
        }
    )
    _use(monkeypatch, stack)
    _cli_env(tmp_path, monkeypatch, DSN_5433)
    result = runner.invoke(
        app, ["quickstart", "--json", "--superuser-dsn", SUPER_DSN, "--no-write"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["adopted_dsn"] == DSN_5433
    assert stack.provisioned == [(SUPER_DSN, "corpus_kb")]


def test_cli_config_error_exit_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "retrieval:\n  matryoshka_enabled: true\nembedding:\n  mrl_trained: false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CORPUS_KB_CONFIG", str(bad))
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    result = runner.invoke(app, ["quickstart", "--json"])
    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "config_error"
    assert payload["exit_code"] == 3


def test_cli_quickstart_registered_in_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "quickstart" in result.output


def test_real_build_deps_wires_shared_modules() -> None:
    """The real factory wires the shared db_provision/schema_apply paths."""
    from corpus_kb._setup.db_provision import ensure_role_and_database
    from corpus_kb.cli.cli_quickstart import build_deps

    deps = build_deps(dict(CONFIG))
    assert deps.provision is ensure_role_and_database
    assert callable(deps.apply_schema)
    assert callable(deps.pg_probe)
