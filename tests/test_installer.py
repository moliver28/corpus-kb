"""Tests for the full-stack installer (doctor and setup commands)."""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

# Make the corpus_kb package importable from the repo source tree.
_REPO_SRC = Path(__file__).parent.parent / "src"
if str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

from corpus_kb._setup import install as install_module  # noqa: E402


def _minimal_config(data_dir: Path | None = None) -> dict:
    """Return a config dict with an optional installer data_dir override."""
    cfg: dict = {"installer": {}}
    if data_dir is not None:
        cfg["installer"]["data_dir"] = str(data_dir)
    return cfg


@pytest.mark.asyncio
async def test_doctor_cpu_only() -> None:
    """A low-RAM, no-GPU machine is recommended the minimal profile."""
    stdout_capture = StringIO()
    with (
        patch.object(sys, "stdout", stdout_capture),
        patch.object(install_module.psutil, "virtual_memory", return_value=_Mem(total=4 * 1024**3)),
        patch.object(install_module.psutil, "cpu_count", return_value=2),
        patch.object(install_module, "_detect_gpu_vram_gb", return_value=0.0),
        patch.object(install_module, "check_postgres", return_value=(False, "no server")),
        patch.object(install_module, "check_ollama", return_value=(False, "no server")),
    ):
        result = await install_module.doctor_cmd({"installer": {}})
        assert result == 0

    output = stdout_capture.getvalue()
    assert "Profile:      minimal" in output
    assert "ollama pull nomic-embed-text" in output


@pytest.mark.asyncio
async def test_doctor_gpu_detected() -> None:
    """A high-RAM machine with 8GB VRAM is recommended the performance profile."""
    stdout_capture = StringIO()
    with (
        patch.object(sys, "stdout", stdout_capture),
        patch.object(
            install_module.psutil, "virtual_memory", return_value=_Mem(total=32 * 1024**3)
        ),
        patch.object(install_module.psutil, "cpu_count", return_value=16),
        patch.object(install_module, "_detect_gpu_vram_gb", return_value=8.0),
        patch.object(install_module, "check_postgres", return_value=(True, "PostgreSQL 17")),
        patch.object(install_module, "check_ollama", return_value=(True, "status 200")),
    ):
        result = await install_module.doctor_cmd(
            {
                "installer": {
                    "profiles": {
                        "performance": {
                            "ram_gb_max": 999,
                            "vram_gb_max": 8,
                            "model": "qwen3-embedding:8b-q8_0",
                            "llm": "qwen3:14b",
                        }
                    }
                }
            }
        )
        assert result == 0

    output = stdout_capture.getvalue()
    assert "Profile:      performance" in output
    assert "ollama pull qwen3-embedding:8b-q8_0" in output


class _Mem:
    """Tiny psutil virtual_memory stub."""

    def __init__(self, total: int) -> None:
        self.total = total


def _doctor_patches(
    stdout_capture: StringIO,
    extensions: dict[str, tuple[bool, str]] | None,
) -> tuple:
    """Common patch stack for doctor extension tests (no live services)."""
    return (
        patch.object(sys, "stdout", stdout_capture),
        patch.object(install_module.psutil, "virtual_memory", return_value=_Mem(total=4 * 1024**3)),
        patch.object(install_module.psutil, "cpu_count", return_value=2),
        patch.object(install_module, "_detect_gpu_vram_gb", return_value=0.0),
        patch.object(install_module, "check_ollama", return_value=(False, "no server")),
        patch.object(install_module, "check_extensions", return_value=extensions),
    )


@pytest.mark.asyncio
async def test_doctor_reports_extensions_installed() -> None:
    """Doctor prints AGE + pgml with versions when both extensions are installed."""
    stdout_capture = StringIO()
    patches = _doctor_patches(stdout_capture, {"age": (True, "1.5.0"), "pgml": (True, "2.10.0")})
    config = {
        "installer": {},
        "database": {"connection_string": "postgresql://fake/db"},
    }
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patch.object(install_module, "check_postgres", return_value=(True, "PostgreSQL 17")),
    ):
        result = await install_module.doctor_cmd(config)
        assert result == 0

    output = stdout_capture.getvalue().lower()
    assert "age" in output and "1.5.0" in output
    assert "pgml" in output and "2.10.0" in output


@pytest.mark.asyncio
async def test_doctor_reports_extensions_missing_with_remediation() -> None:
    """Absent extensions are reported MISSING with a remediation hint, never OK."""
    stdout_capture = StringIO()
    patches = _doctor_patches(stdout_capture, {"age": (False, ""), "pgml": (False, "")})
    config = {
        "installer": {},
        "database": {"connection_string": "postgresql://fake/db"},
    }
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patch.object(install_module, "check_postgres", return_value=(True, "PostgreSQL 17")),
    ):
        result = await install_module.doctor_cmd(config)
        assert result == 0

    output = stdout_capture.getvalue()
    lowered = output.lower()
    assert "missing" in lowered
    assert "age" in lowered and "pgml" in lowered
    assert "docker" in lowered  # remediation hint points at the compose stack


@pytest.mark.asyncio
async def test_doctor_extensions_unknown_when_postgres_unreachable() -> None:
    """Unreachable Postgres => extensions UNKNOWN, never reported OK."""
    stdout_capture = StringIO()
    patches = _doctor_patches(stdout_capture, None)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patch.object(install_module, "check_postgres", return_value=(False, "no server")),
    ):
        result = await install_module.doctor_cmd({"installer": {}})
        assert result == 0

    output = stdout_capture.getvalue()
    lowered = output.lower()
    assert "unknown" in lowered
    age_lines = [
        line for line in output.splitlines() if "age" in line.lower() or "pgml" in line.lower()
    ]
    assert age_lines, "doctor must still print extension status lines"
    assert not any("OK" in line for line in age_lines)


def test_install_extension_packages_windows_errors_clearly() -> None:
    """On Windows, extension install points at docker-compose and returns non-zero."""
    stdout_capture = StringIO()
    with (
        patch.object(sys, "stdout", stdout_capture),
        patch.object(install_module.platform, "system", return_value="Windows"),
    ):
        rc = install_module.install_extension_packages(apply=True)

    assert rc != 0
    output = stdout_capture.getvalue().lower()
    assert "age" in output and "pgml" in output
    assert "docker" in output


def test_load_install_state_returns_fresh_when_missing(tmp_path: Path) -> None:
    """Loading state from a non-existent data dir returns an empty checkpoint."""
    config = _minimal_config(tmp_path)
    state = install_module.load_install_state(config)
    assert state["version"] == install_module.INSTALL_STATE_VERSION
    assert state["completed"] == []
    assert state["last_run"] is None


def test_save_and_load_install_state_round_trip(tmp_path: Path) -> None:
    """Persisted state can be re-loaded intact."""
    config = _minimal_config(tmp_path)
    state = install_module.load_install_state(config)
    state["completed"] = ["compose", "python"]
    install_module.save_install_state(state, config)

    loaded = install_module.load_install_state(config)
    assert loaded["completed"] == ["compose", "python"]
    assert loaded["last_run"] is not None


def test_reset_install_state_removes_file(tmp_path: Path) -> None:
    """reset_install_state deletes the persisted checkpoint."""
    config = _minimal_config(tmp_path)
    state = install_module.load_install_state(config)
    state["completed"] = ["compose"]
    install_module.save_install_state(state, config)
    assert install_module._install_state_path(config).exists()

    install_module.reset_install_state(config)
    assert not install_module._install_state_path(config).exists()


@pytest.mark.asyncio
async def test_setup_dry_run_does_not_write_state(tmp_path: Path) -> None:
    """A dry run inspects the existing state but does not create one."""
    config = _minimal_config(tmp_path)
    stdout_capture = StringIO()
    with (
        patch.object(sys, "stdout", stdout_capture),
        patch.object(install_module, "detect_profile", return_value=_minimal_profile()),
        patch.object(install_module, "_find_compose_command", return_value="docker compose"),
    ):
        result = await install_module.setup_cmd(config, dry_run=True, fresh=False)
        assert result == 0

    assert not install_module._install_state_path(config).exists()
    output = stdout_capture.getvalue()
    assert "Dry run" in output or "dry run" in output.lower()


@pytest.mark.asyncio
async def test_setup_fresh_resets_state_and_runs_all_phases(tmp_path: Path) -> None:
    """--fresh clears any existing checkpoint and executes every phase."""
    config = _minimal_config(tmp_path)
    state = install_module.load_install_state(config)
    state["completed"] = ["compose", "python", "database"]
    install_module.save_install_state(state, config)

    calls: list[str] = []
    with (
        _phase_mocks(calls),
        patch.object(install_module, "_find_compose_command", return_value="docker compose"),
    ):
        result = await install_module.setup_cmd(config, dry_run=False, fresh=True)

    assert result == 0
    assert install_module._install_state_path(config).exists()
    loaded = install_module.load_install_state(config)
    assert loaded["completed"] == list(install_module.INSTALL_PHASES)
    assert calls == ["compose", "python", "database", "extensions", "models", "config"]


@pytest.mark.asyncio
async def test_setup_resume_skips_completed_phases(tmp_path: Path) -> None:
    """A normal run skips phases already recorded in the checkpoint."""
    config = _minimal_config(tmp_path)
    state = install_module.load_install_state(config)
    state["completed"] = ["compose", "python", "database", "extensions"]
    install_module.save_install_state(state, config)

    calls: list[str] = []
    with _phase_mocks(calls):
        result = await install_module.setup_cmd(config, dry_run=False, fresh=False)

    assert result == 0
    assert calls == ["models", "config"]
    loaded = install_module.load_install_state(config)
    assert loaded["completed"] == list(install_module.INSTALL_PHASES)


@pytest.mark.asyncio
async def test_setup_dry_run_shows_resume_status(tmp_path: Path) -> None:
    """Dry run reports which phases are already completed."""
    config = _minimal_config(tmp_path)
    state = install_module.load_install_state(config)
    state["completed"] = ["compose", "python"]
    install_module.save_install_state(state, config)

    stdout_capture = StringIO()
    with (
        patch.object(sys, "stdout", stdout_capture),
        patch.object(install_module, "detect_profile", return_value=_minimal_profile()),
        patch.object(install_module, "_find_compose_command", return_value="docker compose"),
    ):
        result = await install_module.setup_cmd(config, dry_run=True, fresh=False)
        assert result == 0

    output = stdout_capture.getvalue()
    assert "Checkpoint: 2/6 phases completed" in output
    assert "Completed: compose, python" in output


def _minimal_profile() -> dict:
    """Return a stub profile dict for setup_cmd tests."""
    return {
        "profile": "minimal",
        "ram_gb": 4.0,
        "vram_gb": 0.0,
        "cpu_cores": 2,
        "os": "Windows",
        "python": "3.11.0",
    }


@contextlib.contextmanager
def _phase_mocks(calls: list[str]) -> Iterator[None]:
    """Patch every setup phase so tests run without Docker/Ollama/Postgres."""
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch.object(
                install_module,
                "setup_start_compose",
                side_effect=lambda *_args, **_kw: (_record(calls, "compose"), 0)[1],
            )
        )
        stack.enter_context(
            patch.object(
                install_module,
                "install_python_deps",
                side_effect=lambda *_args, **_kw: (_record(calls, "python"), 0)[1],
            )
        )
        stack.enter_context(
            patch.object(
                install_module,
                "install_database",
                side_effect=lambda *_args, **_kw: (_record(calls, "database"), 0)[1],
            )
        )
        stack.enter_context(
            patch.object(
                install_module,
                "setup_verify_extensions",
                side_effect=lambda *_args, **_kw: (_record(calls, "extensions"), 0)[1],
            )
        )
        stack.enter_context(
            patch.object(
                install_module,
                "setup_pull_models",
                side_effect=lambda *_args, **_kw: (_record(calls, "models"), 0)[1],
            )
        )
        stack.enter_context(
            patch.object(
                install_module,
                "setup_update_config",
                side_effect=lambda *_args, **_kw: (_record(calls, "config"), 0)[1],
            )
        )
        stack.enter_context(patch.object(install_module, "confirm", return_value=True))
        stack.enter_context(patch.object(install_module, "print_next_steps"))
        yield


def _record(calls: list[str], phase: str) -> None:
    """Record that a phase was executed."""
    calls.append(phase)
