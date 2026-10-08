"""Tests for the `corpus-kb setup` installer subcommand."""

from __future__ import annotations

import importlib.util
import sys
from io import StringIO
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest


def _load_install_module() -> ModuleType:
    """Load the installer module without adding it to sys.path."""
    install_path = Path(__file__).parent.parent / "src" / "corpus_kb" / "_setup" / "install.py"
    spec = importlib.util.spec_from_file_location("install", install_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["install"] = module
    spec.loader.exec_module(module)
    return module


install = _load_install_module()


class _Mem:
    """Tiny psutil virtual_memory stub."""

    def __init__(self, total: int) -> None:
        self.total = total


@pytest.mark.asyncio
async def test_setup_dry_run_lists_all_steps() -> None:
    """`setup --dry-run` prints every step and exits 0 without side effects."""
    stdout_capture = StringIO()
    config = {
        "installer": {
            "profiles": {
                "balanced": {
                    "ram_gb_max": 16,
                    "vram_gb_max": 4,
                    "model": "nomic-embed-text",
                    "llm": "qwen3:4b",
                }
            }
        },
        "database": {"connection_string": "postgresql://u:p@localhost:5433/db"},
    }
    with (
        patch.object(sys, "stdout", stdout_capture),
        patch.object(install.psutil, "virtual_memory", return_value=_Mem(8 * 1024**3)),
        patch.object(install.psutil, "cpu_count", return_value=4),
        patch.object(install, "_detect_gpu_vram_gb", return_value=(0.0, False)),
        patch.object(install, "_find_compose_command", return_value="docker compose"),
    ):
        result = await install.setup_cmd(config, dry_run=True, fresh=False)

    assert result == 0
    output = stdout_capture.getvalue()
    assert "=== Corpus-KB One-Line Setup ===" in output
    assert "docker compose -f compose.yaml up -d" in output
    assert "pip install -e .[dev]" in output
    assert "migrations" in output.lower()
    assert "AGE + pgml" in output
    assert "ollama pull nomic-embed-text" in output
    assert "qwen3:4b" in output
    assert "write" in output.lower() and "config.yaml" in output
    assert "This was a dry run" in output


@pytest.mark.asyncio
async def test_setup_dry_run_build_local_lists_local_dockerfile() -> None:
    """`setup --dry-run --build-local` shows the local Dockerfile build path."""
    stdout_capture = StringIO()
    config = {
        "installer": {
            "profiles": {
                "balanced": {
                    "ram_gb_max": 16,
                    "vram_gb_max": 4,
                    "model": "nomic-embed-text",
                    "llm": "qwen3:4b",
                }
            }
        },
        "database": {"connection_string": "postgresql://u:p@localhost:5433/db"},
    }
    with (
        patch.object(sys, "stdout", stdout_capture),
        patch.object(install.psutil, "virtual_memory", return_value=_Mem(8 * 1024**3)),
        patch.object(install.psutil, "cpu_count", return_value=4),
        patch.object(install, "_detect_gpu_vram_gb", return_value=(0.0, False)),
        patch.object(install, "_find_compose_command", return_value="docker compose"),
    ):
        result = await install.setup_cmd(config, dry_run=True, fresh=False, build_local=True)

    assert result == 0
    output = stdout_capture.getvalue()
    assert "docker compose -f compose.yaml -f compose.build-local.yaml up -d --build" in output
    assert "docker/postgres/Dockerfile" in output


@pytest.mark.asyncio
async def test_setup_dry_run_uses_default_dsn_when_missing() -> None:
    """When no connection string is provided, setup falls back to the default DSN."""
    stdout_capture = StringIO()
    with (
        patch.object(sys, "stdout", stdout_capture),
        patch.object(install.psutil, "virtual_memory", return_value=_Mem(8 * 1024**3)),
        patch.object(install.psutil, "cpu_count", return_value=4),
        patch.object(install, "_detect_gpu_vram_gb", return_value=(0.0, False)),
    ):
        result = await install.setup_cmd({"installer": {}}, dry_run=True, fresh=False)

    assert result == 0
    output = stdout_capture.getvalue()
    assert "postgresql://corpus_user:corpus_pass@localhost:5433/corpus_kb" in output


@pytest.mark.asyncio
async def test_setup_dry_run_reports_missing_docker() -> None:
    """If docker is not installed, dry-run states the fact but still exits 0."""
    stdout_capture = StringIO()
    with (
        patch.object(sys, "stdout", stdout_capture),
        patch.object(install, "_find_compose_command", return_value=None),
        patch.object(install.psutil, "virtual_memory", return_value=_Mem(8 * 1024**3)),
        patch.object(install.psutil, "cpu_count", return_value=4),
        patch.object(install, "_detect_gpu_vram_gb", return_value=(0.0, False)),
    ):
        result = await install.setup_cmd({"installer": {}}, dry_run=True, fresh=False)

    assert result == 0
    output = stdout_capture.getvalue()
    assert "not found; install Docker to proceed" in output
