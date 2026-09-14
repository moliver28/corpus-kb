"""Tests for the full-stack installer (read-only doctor command)."""

from __future__ import annotations

import importlib.util
import sys
from io import StringIO
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest


def _load_install_module() -> ModuleType:
    """Load scripts/install.py as a module without adding it to sys.path."""
    install_path = Path(__file__).parent.parent / "scripts" / "install.py"
    spec = importlib.util.spec_from_file_location("install", install_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["install"] = module
    spec.loader.exec_module(module)
    return module


install = _load_install_module()


@pytest.mark.asyncio
async def test_doctor_cpu_only() -> None:
    """A low-RAM, no-GPU machine is recommended the minimal profile."""
    stdout_capture = StringIO()
    with (
        patch.object(sys, "stdout", stdout_capture),
        patch.object(
            install.psutil, "virtual_memory", return_value=_Mem(total=4 * 1024**3)
        ),
        patch.object(install.psutil, "cpu_count", return_value=2),
        patch.object(install, "_detect_gpu_vram_gb", return_value=0.0),
        patch.object(install, "check_postgres", return_value=(False, "no server")),
        patch.object(install, "check_ollama", return_value=(False, "no server")),
    ):
        result = await install.doctor_cmd({"installer": {}})
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
            install.psutil, "virtual_memory", return_value=_Mem(total=32 * 1024**3)
        ),
        patch.object(install.psutil, "cpu_count", return_value=16),
        patch.object(install, "_detect_gpu_vram_gb", return_value=8.0),
        patch.object(install, "check_postgres", return_value=(True, "PostgreSQL 17")),
        patch.object(install, "check_ollama", return_value=(True, "status 200")),
    ):
        result = await install.doctor_cmd(
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
        patch.object(
            install.psutil, "virtual_memory", return_value=_Mem(total=4 * 1024**3)
        ),
        patch.object(install.psutil, "cpu_count", return_value=2),
        patch.object(install, "_detect_gpu_vram_gb", return_value=0.0),
        patch.object(install, "check_ollama", return_value=(False, "no server")),
        patch.object(install, "check_extensions", return_value=extensions),
    )


@pytest.mark.asyncio
async def test_doctor_reports_extensions_installed() -> None:
    """Doctor prints AGE + pgml with versions when both extensions are installed."""
    stdout_capture = StringIO()
    patches = _doctor_patches(
        stdout_capture, {"age": (True, "1.5.0"), "pgml": (True, "2.10.0")}
    )
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
        patch.object(install, "check_postgres", return_value=(True, "PostgreSQL 17")),
    ):
        result = await install.doctor_cmd(config)
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
        patch.object(install, "check_postgres", return_value=(True, "PostgreSQL 17")),
    ):
        result = await install.doctor_cmd(config)
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
        patch.object(install, "check_postgres", return_value=(False, "no server")),
    ):
        result = await install.doctor_cmd({"installer": {}})
        assert result == 0

    output = stdout_capture.getvalue()
    lowered = output.lower()
    assert "unknown" in lowered
    age_lines = [
        line
        for line in output.splitlines()
        if "age" in line.lower() or "pgml" in line.lower()
    ]
    assert age_lines, "doctor must still print extension status lines"
    assert not any("OK" in line for line in age_lines)


def test_install_extension_packages_windows_errors_clearly() -> None:
    """On Windows, extension install points at docker-compose and returns non-zero."""
    stdout_capture = StringIO()
    with (
        patch.object(sys, "stdout", stdout_capture),
        patch.object(install.platform, "system", return_value="Windows"),
    ):
        rc = install.install_extension_packages(apply=True)

    assert rc != 0
    output = stdout_capture.getvalue().lower()
    assert "age" in output and "pgml" in output
    assert "docker" in output
