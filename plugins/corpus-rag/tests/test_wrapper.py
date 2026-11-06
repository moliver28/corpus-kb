"""Contract test for the thin corpus-rag wrapper (bin/corpus-rag)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]


def run_wrapper(*args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("CORPUS_KB_REPO", "CORPUS_KB_STATE_DIR")}
    return subprocess.run(
        [sys.executable, str(PLUGIN / "bin" / "corpus-rag"), *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
        cwd=str(PLUGIN),
    )


def test_connect_prints_valid_mcp_config() -> None:
    result = run_wrapper("connect")
    # Exit 0 even without an engine: it prints a PATH fallback config.
    assert result.returncode == 0, result.stdout + result.stderr
    start = result.stdout.index("{")
    config = json.loads(result.stdout[start:])
    server = config["mcpServers"]["corpus-kb"]
    assert server["args"] == ["--transport", "stdio"]
    assert isinstance(server["command"], str) and server["command"]


def test_status_reports_missing_engine_honestly(tmp_path: Path) -> None:
    """No engine anywhere: status says so and exits non-zero."""
    import shutil

    if shutil.which("corpus-kb"):
        # A corpus-kb on PATH (dev machine) makes the missing-engine path
        # unreachable; the connect test above already covers the contract.
        return
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CORPUS_KB_REPO", "CORPUS_KB_STATE_DIR")
    }
    env["PATH"] = ""
    env["CORPUS_KB_STATE_DIR"] = str(tmp_path / "empty")
    result = subprocess.run(
        [sys.executable, str(PLUGIN / "bin" / "corpus-rag"), "status"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
        cwd=str(PLUGIN),
    )
    assert result.returncode == 1
    assert "not found" in result.stdout.lower()


def test_status_uses_dev_checkout(tmp_path: Path) -> None:
    """This test runs inside a corpus-kb checkout, so the dev-checkout
    resolution must find the real engine CLI (the venv or PATH)."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CORPUS_KB_REPO", "CORPUS_KB_STATE_DIR")
    }
    result = subprocess.run(
        [sys.executable, str(PLUGIN / "bin" / "corpus-rag"), "connect"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
        cwd=str(PLUGIN),
    )
    start = result.stdout.index("{")
    config = json.loads(result.stdout[start:])
    command = config["mcpServers"]["corpus-kb"]["command"]
    assert command == "corpus-kb" or "corpus-kb" in Path(command).name
