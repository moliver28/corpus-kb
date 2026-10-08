"""Offline tests for the review-F3 fix round: doctor env-DSN resolution
(F-1), the async CLI approval boundary (F-2), the honest all-noise inductive
no-op (F-6), doctor VRAM honesty (F-5), and clean CLI error presentation
(F-8 + F-3's GRANT remediation). NO database, NO Ollama: every network-bound
surface is driven against dead ports or fakes. The gray-zone convergence
E2E (F-7) needs Postgres and lives in test_cycle_gray_zone_convergence.py.
"""

from __future__ import annotations

import inspect
from io import StringIO
from pathlib import Path
from typing import cast
from unittest.mock import patch
from uuid import UUID

import asyncpg
import psycopg
import pytest

from corpus_kb import cli
from corpus_kb._setup import install as install_mod
from corpus_kb._setup import migrate as migrate_mod
from corpus_kb.research import cycle_gates as gates
from corpus_kb.research import cycle_render, guide_copy, inductive_run

REPO = Path(__file__).resolve().parents[1]

# Dead ports on localhost: connection refused is immediate, so doctor and
# the installer stay offline-fast; the port number itself is the assertion
# probe (which DSN each surface actually dialed).
CONFIG_PORT = 59998
ENV_PORT = 59999


def _doctor_config() -> dict[str, object]:
    return {
        "database": {
            "connection_string": f"postgresql://corpus_user:corpus_pass@localhost:{CONFIG_PORT}/config_db"
        },
    }


# ---------------------------------------------------------------------------
# F-1: doctor resolves the DSN the way the runtime does (env > config)
# ---------------------------------------------------------------------------


async def test_doctor_probes_env_dsn_over_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CORPUS_KB_DATABASE_URL",
        f"postgresql://corpus_user:corpus_pass@localhost:{ENV_PORT}/env_db",
    )
    real_check = install_mod.check_postgres
    seen: dict[str, str] = {}

    async def _spy(conn_str: str) -> tuple[bool, str]:
        seen["dsn"] = conn_str
        return await real_check(conn_str)

    monkeypatch.setattr(install_mod, "check_postgres", _spy)
    captured = StringIO()
    with patch("sys.stdout", captured):
        await install_mod.doctor_cmd(_doctor_config())  # type: ignore[arg-type]
    assert str(ENV_PORT) in seen["dsn"], "doctor must probe the ENV target when the env DSN is set"
    assert str(CONFIG_PORT) not in seen["dsn"], "doctor ignored the env override (F-1 regression)"
    assert "Postgres:     UNREACHABLE" in captured.getvalue(), (
        "the dead env target must be reported honestly"
    )


async def test_doctor_falls_back_to_config_dsn_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    real_check = install_mod.check_postgres
    seen: dict[str, str] = {}

    async def _spy(conn_str: str) -> tuple[bool, str]:
        seen["dsn"] = conn_str
        return await real_check(conn_str)

    monkeypatch.setattr(install_mod, "check_postgres", _spy)
    captured = StringIO()
    with patch("sys.stdout", captured):
        await install_mod.doctor_cmd(_doctor_config())  # type: ignore[arg-type]
    assert str(CONFIG_PORT) in seen["dsn"]
    assert "Postgres:     UNREACHABLE" in captured.getvalue()


# ---------------------------------------------------------------------------
# F-5: doctor VRAM honesty
# ---------------------------------------------------------------------------


def _doctor_info(vram_detected: bool) -> dict[str, object]:
    return {
        "os": "Windows",
        "python": "3.11.5",
        "cpu_cores": 8,
        "ram_gb": 16.0,
        "vram_gb": 0.0 if not vram_detected else 8.0,
        "vram_detected": vram_detected,
        "profile": "balanced",
        "postgres_ok": False,
        "postgres_msg": "unreachable",
        "ollama_ok": False,
        "ollama_msg": "unreachable",
        "recommended_model": "nomic-embed-text",
        "extensions": None,
    }


def test_doctor_vram_unknown_when_detection_unavailable() -> None:
    captured = StringIO()
    with patch("sys.stdout", captured):
        install_mod.print_doctor_report(_doctor_info(False), None)
    assert "GPU VRAM:     unknown" in captured.getvalue()
    assert "0.0 GB" not in captured.getvalue()


def test_doctor_vram_reported_when_detected() -> None:
    captured = StringIO()
    with patch("sys.stdout", captured):
        install_mod.print_doctor_report(_doctor_info(True), None)
    assert "GPU VRAM:     8.0 GB" in captured.getvalue()


# ---------------------------------------------------------------------------
# F-2: the on-mode approval boundary is async and works through the REAL
# cycle_render.approval path (the crash was sync _approve vs Awaitable).
# ---------------------------------------------------------------------------


class _Catch:
    async def catch_up(self, *args: object, **kwargs: object) -> None:
        pass


class _FakeHandler:
    def __init__(self) -> None:
        self.checkpoints: list[dict[str, object]] = []
        self.stopped: list[UUID] = []

    def checkpoint_coding_run(self, tenant_id: object, run_id: object, payload: object) -> None:
        self.checkpoints.append(cast("dict[str, object]", payload))

    def stop_coding_run(self, tenant_id: object, run_id: object) -> None:
        self.stopped.append(cast(UUID, run_id))


def _fake_stack() -> tuple[object, ...]:
    return (_Catch(), None, None, _Catch(), _Catch(), _Catch())


def test_cli_approve_helper_is_async() -> None:
    assert inspect.iscoroutinefunction(cli._cli_approve)


async def test_cli_approve_deny_records_state(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    def _fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        return "n"

    monkeypatch.setattr("builtins.input", _fake_input)
    from corpus_kb.research import cycle_events as ev

    recorder = ev.CycleRecorder(json_output=True)
    handler = _FakeHandler()
    tenant = UUID(int=1)
    run_id = UUID(int=2)
    code = await cycle_render.approval(
        recorder, handler, _fake_stack(), tenant, run_id, "ingest", cli._cli_approve
    )
    assert code == gates.EXIT_APPROVAL_DENIED
    assert handler.checkpoints[-1]["kind"] == "approval_denied"
    assert handler.stopped == [run_id]
    assert prompts == [guide_copy.CYCLE_APPROVAL_PROMPT]


async def test_cli_approve_accept_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    from corpus_kb.research import cycle_events as ev

    recorder = ev.CycleRecorder(json_output=True)
    handler = _FakeHandler()
    captured = StringIO()
    with patch("sys.stdout", captured):
        code = await cycle_render.approval(
            recorder,
            handler,
            _fake_stack(),
            UUID(int=1),
            UUID(int=2),
            "ingest",
            cli._cli_approve,
        )
    assert code is None
    assert handler.checkpoints == []
    assert handler.stopped == []


# ---------------------------------------------------------------------------
# F-6: an all-noise inductive pass prints honest guidance
# ---------------------------------------------------------------------------


def test_all_noise_note_names_n_and_suggests_action() -> None:
    note = inductive_run.all_noise_note(0, 12)
    assert note is not None
    assert "12" in note
    assert "noise" in note
    assert "more" in note.lower() or "seed" in note.lower()
    assert inductive_run.all_noise_note(2, 5) is None
    assert inductive_run.all_noise_note(0, 0) is None


def test_inductive_all_noise_copy_is_guide_copy_sourced() -> None:
    assert "{n}" in guide_copy.INDUCTIVE_ALL_NOISE


# ---------------------------------------------------------------------------
# F-8 / F-3: expected CLI errors present clean messages + actionable hints
# ---------------------------------------------------------------------------


def test_error_maps_promote_unknown_id(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli._cli_error_exit(ValueError("proposal 999 not found for tenant"))
    out = capsys.readouterr().out
    assert code == 1
    assert "ERROR: proposal 999 not found for tenant" in out


def test_error_maps_unknown_gate(capsys: pytest.CaptureFixture[str]) -> None:
    exc = gates.UnknownGateError("unknown research.cycle.halt_on gates: ['bogus'] (known: [])")
    code = cli._cli_error_exit(exc)
    out = capsys.readouterr().out
    assert code == 1
    assert "bogus" in out


def test_error_maps_asyncpg_permission_denied_with_grant_hint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The real 42501 class carries sqlstate as a class attribute.
    exc = asyncpg.InsufficientPrivilegeError("permission denied for schema public")
    assert exc.sqlstate == "42501"
    code = cli._cli_error_exit(exc)
    out = capsys.readouterr().out
    assert code == 1
    assert "permission denied" in out
    assert "GRANT CREATE ON SCHEMA public TO corpus_user" in out


def test_error_maps_message_only_permission_denied(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Denied(asyncpg.PostgresError):
        pass

    code = cli._cli_error_exit(_Denied("permission denied for table research_assignments"))
    out = capsys.readouterr().out
    assert code == 1
    assert "GRANT CREATE ON SCHEMA public TO corpus_user" in out


def test_error_maps_libpq_too_old_with_binary_hint(capsys: pytest.CaptureFixture[str]) -> None:
    exc = psycopg.NotSupportedError(
        "the feature 'Connection.pipeline()' is not supported by this installation"
    )
    code = cli._cli_error_exit(exc)
    out = capsys.readouterr().out
    assert code == 1
    assert "psycopg-binary" in out


def test_unexpected_errors_keep_their_traceback() -> None:
    assert cli._cli_error_exit(RuntimeError("real bug")) is None


def test_grant_hint_names_the_exact_fix() -> None:
    hint = guide_copy.DB_PERMISSION_DENIED_HINT
    assert "GRANT CREATE ON SCHEMA public TO corpus_user" in hint
    assert "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO corpus_user" in hint


def test_install_md_grant_recipe_covers_pg15_default_revocation() -> None:
    text = (REPO / "docs" / "INSTALL.md").read_text(encoding="utf-8")
    assert "GRANT CREATE ON SCHEMA public TO corpus_user" in text
    assert "ALTER DEFAULT PRIVILEGES" in text


async def test_install_database_permission_failure_prints_grant_fix(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _DeniedError(Exception):
        sqlstate = "42501"

    async def _fail(conn_str: str) -> None:
        raise _DeniedError("permission denied for schema public")

    monkeypatch.setattr(migrate_mod, "run_migrations", _fail)
    monkeypatch.setattr(install_mod, "confirm", lambda step: True)
    rc = await install_mod.install_database(
        f"postgresql://corpus_user:corpus_pass@localhost:{ENV_PORT}/nope", True
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "GRANT CREATE ON SCHEMA public TO corpus_user" in out


# ---------------------------------------------------------------------------
# F-4: the fresh-install libpq guard is a declared dependency
# ---------------------------------------------------------------------------


def test_psycopg_binary_is_a_declared_dependency() -> None:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "psycopg-binary>=3.1" in text


# ---------------------------------------------------------------------------
# The cycle command wires the ASYNC helper and the guarded runner
# ---------------------------------------------------------------------------


def test_cycle_command_uses_the_module_level_async_helper() -> None:
    # run_cycle's contract is Callable[[str], Awaitable[bool]]; the CLI must
    # pass an awaitable-callable, not the sync prompt that crashed (F-2).
    assert inspect.iscoroutinefunction(cli._cli_approve)
