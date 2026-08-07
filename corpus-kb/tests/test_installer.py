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


def test_load_config_applies_database_url_env_var_override(monkeypatch, tmp_path):
    """CORPUS_KB_DATABASE_URL must win even when config.yaml already has a value."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "database:\n  connection_string: postgresql://wrong:wrong@localhost:5433/wrong\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "CORPUS_KB_DATABASE_URL",
        "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb",
    )

    config = install.load_config()

    assert (
        config["database"]["connection_string"]
        == "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"
    )


class _Mem:
    """Tiny psutil virtual_memory stub."""

    def __init__(self, total: int) -> None:
        self.total = total
