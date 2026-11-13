"""Setup preflight: hard failures stop setup, soft checks only warn.

Hard checks (issue #353):

- engine: root found, venv present, version at or above the plugin minimum.
- postgres backend: ``psql`` on PATH, ``pgvector`` available in the server
  the superuser DSN names.
- duckdb backend: ``duckdb`` importable in the engine venv.

Soft checks warn and never stop setup; see ``soft_checks``: Ollama
reachable and the configured embedding model pulled, both read from
``corpus-kb doctor --json`` plus one GET to the Ollama tags API.

Every hard check carries a fix command. Stdlib only.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import engine_paths
import soft_checks
import study_config

PROBE_TIMEOUT_S = 30
OLLAMA_BASE = "http://127.0.0.1:11434"
# The engine version this plugin's calls need (the engine multi-study
# wave ships in engine 0.1.0). The engine versions on its own, so raise
# this constant in the same PR that needs a newer engine.
MINIMUM_ENGINE_VERSION = "0.1.0"


class PreflightError(Exception):
    """One or more hard preflight checks failed."""


def _version_tuple(raw: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", raw or "")
    return tuple(int(part) for part in parts[:4]) or (0,)


def engine_venv_python(root: Path | None = None) -> Path | None:
    """The engine venv interpreter, or None. Mirrors ``engine_cli`` order."""
    resolved = root if root is not None else engine_paths.engine_root()
    if resolved is None:
        return None
    for candidate in (
        resolved / ".venv" / "Scripts" / "python.exe",
        resolved / ".venv" / "Scripts" / "python",
        resolved / ".venv" / "bin" / "python",
    ):
        if candidate.is_file():
            return candidate
    return None


def _run(command: list[str], timeout: int = PROBE_TIMEOUT_S) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, check=False
    )


def engine_version(cli: str) -> str:
    """The engine version from ``corpus-kb --version``, or ""."""
    try:
        result = _run([cli, "--version"], timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    match = re.search(r"\d+\.\d+\.\d+", result.stdout or result.stderr or "")
    return match.group(0) if match else ""


def pgvector_available() -> bool:
    """True when the server behind the superuser DSN lists pgvector."""
    psql = shutil.which("psql")
    if psql is None:
        return False
    try:
        result = _run(
            [
                psql,
                study_config.superuser_dsn(),
                "-tAc",
                "SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'",
            ]
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "1" in (result.stdout or "")


def duckdb_importable(venv_python: Path | None) -> bool:
    """True when the engine venv can import duckdb."""
    if venv_python is None:
        return False
    try:
        result = _run([str(venv_python), "-c", "import duckdb"])
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


# ---------------------------------------------------------------------------
# The check list
# ---------------------------------------------------------------------------


def run(backend: str | None = None) -> dict:
    """Every preflight check. Returns {"hard": [...], "soft": [...]}.

    Each row: ``{"name", "ok", "fix", "detail"}``. ``backend`` (postgres or
    duckdb) adds the backend-specific checks; None runs both.
    """
    hard: list[dict] = []
    soft: list[dict] = []
    root = engine_paths.engine_root()
    hard.append(
        _row(
            "engine root",
            root is not None,
            "Install the engine, or set CORPUS_KB_REPO. See docs/INSTALL.md.",
            str(root) if root else "not found",
        )
    )
    cli = engine_paths.engine_cli(root) if root else None
    hard.append(
        _row(
            "engine venv",
            cli is not None,
            "Run `uv sync --frozen --extra dev` inside the engine checkout.",
            cli or "no corpus-kb command",
        )
    )
    version = engine_version(cli) if cli else ""
    minimum = MINIMUM_ENGINE_VERSION
    ok = bool(version) and _version_tuple(version) >= _version_tuple(minimum)
    hard.append(
        _row(
            "engine version",
            ok,
            f"Update the engine to {minimum} or newer, then run: corpus-rag engine install",
            f"{version or 'unknown'} (minimum {minimum})",
        )
    )
    wanted = backend or "both"
    if wanted in ("postgres", "both"):
        _postgres_checks(hard)
    if wanted in ("duckdb", "both"):
        importable = duckdb_importable(engine_venv_python(root))
        hard.append(
            _row(
                "duckdb",
                importable,
                "Add the duckdb extra to the engine venv: uv sync --extra duckdb",
                "importable" if importable else "missing",
            )
        )
    soft_checks.soft_checks(cli, soft)
    return {"hard": hard, "soft": soft}


def _postgres_checks(hard: list[dict]) -> None:
    psql = shutil.which("psql")
    hard.append(
        _row(
            "psql",
            psql is not None,
            "Install the PostgreSQL client tools so `psql` is on PATH.",
            psql or "missing",
        )
    )
    vector = psql is not None and pgvector_available()
    hard.append(
        _row(
            "pgvector",
            vector,
            "Install the pgvector extension in the Postgres server, then re-run.",
            "available" if vector else "missing",
        )
    )


def _row(name: str, ok: bool, fix: str, detail: str) -> dict:
    return {"name": name, "ok": bool(ok), "fix": fix, "detail": detail}


def hard_failures(report: dict) -> list[dict]:
    return [row for row in report.get("hard", []) if not row.get("ok")]


def assert_passes(report: dict) -> None:
    """Raise ``PreflightError`` with the fix commands when a hard check fails."""
    failed = hard_failures(report)
    if not failed:
        return
    lines = ["Setup preflight failed:"]
    for row in failed:
        lines.append(f"- {row['name']}: {row['fix']}")
    raise PreflightError("\n".join(lines))
