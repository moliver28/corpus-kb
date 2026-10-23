"""Setup step implementations (zero-drift split of _setup/install.py).

Subprocess/package/database/model steps, the confirmation prompt, the
service probes, and the user-config writers. ``install.py`` re-exports
these so its orchestrators (and the ``install_module.*`` test patches)
keep working.

Patch seam: the tests patch ``corpus_kb._setup.install.confirm`` /
``install.check_extensions`` and then call some of these functions
DIRECTLY (tests/test_f3_fixes.py). Consumers therefore resolve those two
collaborators through the install module at CALL time (see
``_confirm_fn`` / ``_check_extensions_fn``), never at import time.
"""

from __future__ import annotations

import importlib
import platform
import subprocess
import sys
from collections.abc import Awaitable, Callable
from typing import Any, cast
from urllib.parse import urlsplit

import asyncpg
import httpx
import yaml

from corpus_kb._setup.install_state import DEFAULT_CONFIG_DIR, DEFAULT_CONFIG_PATH
from corpus_kb._setup.profiles import load_installer_profiles

# PostgreSQL server extensions required by migrations 004/005.
EXTENSIONS: tuple[tuple[str, str], ...] = (
    ("age", "Apache AGE"),
    ("pgml", "PostgresML"),
)
EXTENSION_REMEDIATION = (
    "use the Corpus-KB docker-compose stack (corpus-kb setup) which bundles "
    "both extensions, or install manually: https://age.apache.org/ and "
    "https://github.com/postgresml/postgresml"
)

# Best-effort native package candidates per platform: {os: (manager, [packages])}.
_EXTENSION_PACKAGES: dict[str, tuple[str, list[str]]] = {
    "Linux": ("apt", ["postgresql-17-age", "postgresql-17-pgml"]),
    "Darwin": ("brew", ["apache-age", "postgresml"]),
}

SETUP_REQUIRED_MODELS = ("nomic-embed-text", "qwen3:4b")

ExtensionMap = dict[str, tuple[bool, str]]


def _confirm_fn() -> Callable[[str], bool]:
    """Resolve ``confirm`` from the install module (test patch seam)."""
    from corpus_kb._setup import install

    return install.confirm


def _check_extensions_fn() -> Callable[[str], Awaitable[ExtensionMap | None]]:
    """Resolve ``check_extensions`` from the install module (test patch seam)."""
    from corpus_kb._setup import install

    return install.check_extensions


async def check_postgres(connection_string: str) -> tuple[bool, str]:
    """Try to connect to Postgres with a 3-second timeout."""
    try:
        conn = await asyncpg.connect(connection_string, timeout=3)
        version = await conn.fetchval("SELECT version()")
        await conn.close()
        return True, str(version)
    except Exception as exc:
        detail = _error_detail(exc)
        target = _dsn_target(connection_string)
        if target:
            return False, f"Cannot reach Postgres at {target}: {detail}"
        return False, f"Cannot reach Postgres: {detail}"


def _error_detail(exc: Exception) -> str:
    """Non-empty reason for a failed probe (asyncpg timeouts str() empty)."""
    text = str(exc).strip()
    if text:
        return text
    if isinstance(exc, TimeoutError):
        return "the connection timed out"
    return type(exc).__name__


def _dsn_target(connection_string: str) -> str | None:
    """Host:port from a postgres DSN, or None when it does not parse."""
    try:
        parts = urlsplit(connection_string)
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return f"{parts.hostname}:{parts.port or 5432}"


async def check_ollama(base_url: str) -> tuple[bool, str]:
    """Check whether Ollama is reachable with a 3-second timeout."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{base_url}/api/tags")
            return response.status_code == 200, f"status {response.status_code}"
    except Exception as exc:
        return False, f"Cannot reach Ollama at {base_url}: {_error_detail(exc)}"


async def check_extensions(connection_string: str) -> ExtensionMap | None:
    """Return {name: (installed, version)} for AGE/pgml, or None if unreachable.

    None distinguishes "could not check" from "checked and missing" so the
    doctor report never claims an extension is absent when it was never read.
    """
    try:
        conn = await asyncpg.connect(connection_string, timeout=3)
    except Exception:
        return None
    try:
        rows = await conn.fetch(
            "SELECT extname, extversion FROM pg_extension WHERE extname = ANY($1::text[])",
            [name for name, _ in EXTENSIONS],
        )
    finally:
        await conn.close()
    versions = {row["extname"]: row["extversion"] for row in rows}
    return {name: (name in versions, versions.get(name, "")) for name, _ in EXTENSIONS}


def confirm(step: str) -> bool:
    """Ask user for explicit confirmation before a mutating step."""
    answer = input(f"{step}\nProceed? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def install_python_deps() -> int:
    """Run pip install -e .[dev] and return exit code."""
    print("\n[Step 1/5] Installing Python dependencies...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", ".[dev]"],
        cwd=".",
    )
    return result.returncode


def _package_install_cmd(manager: str, package: str) -> list[str]:
    """Build the platform package-install command (non-interactive, fail-fast)."""
    if manager == "apt":
        return ["sudo", "-n", "apt-get", "install", "-y", package]
    return ["brew", "install", package]


def install_extension_packages(apply: bool) -> int:
    """Best-effort native install of the AGE + pgml Postgres server packages.

    Returns 0 on a dry run or when every package install succeeds; returns 1
    with a clear remediation message when the platform is unsupported or a
    package manager fails. Never raises and never skips silently.
    """
    print("\n  PostgreSQL server extensions (AGE + pgml):")
    system = platform.system()
    candidate = _EXTENSION_PACKAGES.get(system)
    if candidate is None:
        print(f"  ERROR: AGE and pgml have no supported native installer on {system}.")
        print(f"  Remediation: {EXTENSION_REMEDIATION}")
        return 1
    manager, packages = candidate
    if not apply:
        print(f"  (dry run) would install via {manager}: {', '.join(packages)}")
        return 0
    rc = 0
    for package in packages:
        print(f"  Installing {package} via {manager} (best-effort)...")
        try:
            result = subprocess.run(_package_install_cmd(manager, package))
        except OSError as exc:
            print(f"  ERROR: could not run {manager}: {exc}")
            print(f"  Remediation: {EXTENSION_REMEDIATION}")
            rc = 1
            continue
        if result.returncode != 0:
            print(f"  WARNING: {package} install failed (exit {result.returncode}).")
            print(f"  Remediation: {EXTENSION_REMEDIATION}")
            rc = 1
    return rc


async def install_database(conn_str: str, apply: bool) -> int:
    """Create database, install extensions, and run migrations if confirmed."""
    print("\n[Step 2/5] PostgreSQL database setup...")
    if not apply or not _confirm_fn()("Create database (if needed) and run migrations."):
        print("  Skipped.")
        return 0

    # Create database if it does not exist (connect to postgres maintenance db).
    try:
        parse_connstring = cast(
            "Callable[[str], dict[str, str]]",
            getattr(asyncpg.connection, "_parse_connstring", None),
        )
        parsed = parse_connstring(conn_str)
        dbname = parsed.get("database", "corpus_kb")
        maintenance_dsn = conn_str.replace(f"/{dbname}", "/postgres")
        conn = await asyncpg.connect(maintenance_dsn, timeout=5)
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", dbname)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{dbname}"')
            print(f"  Created database {dbname}")
        else:
            print(f"  Database {dbname} already exists")
        await conn.close()
    except Exception as exc:
        # Deliberate small behavior change (Wave 3): the old message said
        # "continuing" and let migrations fail later with a confusing error.
        # Now the failure is recorded and the next step is stated plainly.
        print(f"  ERROR: could not create the database: {exc}")
        print("  The migration step runs next and will fail if the database is missing.")
        print("  Fix the error above, then re-run corpus-kb setup.")

    # Install AGE + pgml server packages before migrations 004/005 need them.
    ext_rc = install_extension_packages(apply)

    # Run migrations.
    from corpus_kb._setup.migrate import run_migrations

    try:
        await run_migrations(conn_str)
    except Exception as exc:
        print(f"  ERROR: migrations failed: {exc}")
        if getattr(exc, "sqlstate", "") == "42501" or "permission denied" in str(exc).lower():
            from corpus_kb.research import guide_copy

            print(f"  Fix: {guide_copy.DB_PERMISSION_DENIED_HINT}")
        print(f"  If AGE or pgml are missing on this server: {EXTENSION_REMEDIATION}")
        return 1
    return ext_rc


def install_ollama_model(model: str, apply: bool) -> int:
    """Pull recommended Ollama model if confirmed."""
    print(f"\n[Step 3/5] Ollama model: {model}")
    if not apply or not _confirm_fn()(f"Pull Ollama model '{model}'."):
        print("  Skipped.")
        return 0

    result = subprocess.run(["ollama", "pull", model])
    return result.returncode


# HuggingFace weight ids pre-pulled by setup so the first `ml`-backend
# ingest never blocks on a download mid-pipeline (plan: Living Corpus W1).
# Weights ship through huggingface_hub snapshot_download.
_EXTRACTION_WEIGHTS: tuple[str, ...] = (
    "fastino/gliner2-large-v1",
    "knowledgator/gliner-relex-large-v1.0",
)


def install_extraction_weights(apply: bool) -> int:
    """Pre-pull GLiNER2 + GLiNER-Relex weights if confirmed.

    Degrades honestly: no huggingface_hub, no network, or a declined
    prompt only prints a note. The extraction fallback chain covers a
    missing model at ingest time.
    """
    print("\n[Step 3b/5] Extraction model weights (GLiNER2 + Relex)")
    # String import: huggingface_hub ships in the [ml] extra, which CI never
    # installs — importlib keeps pyright and extras-less jobs green.
    try:
        snapshot_download = importlib.import_module("huggingface_hub").snapshot_download
    except ImportError:
        print("  Skipped: huggingface_hub not installed (pip install '.[ml]').")
        return 0
    if not apply or not _confirm_fn()(
        f"Download {len(_EXTRACTION_WEIGHTS)} extraction model weight sets."
    ):
        print("  Skipped.")
        return 0
    failed = 0
    for weight_id in _EXTRACTION_WEIGHTS:
        try:
            snapshot_download(weight_id)
            print(f"  OK: {weight_id}")
        except Exception as exc:
            failed += 1
            print(f"  Failed: {weight_id}: {exc}")
    if failed:
        print(f"  {failed} weight download(s) failed; ingest will fall back honestly.")
    return 0


def write_config(profile: str, config: dict[str, Any], force: bool) -> int:
    """Write ~/.corpus-kb/config.yaml from detected profile if confirmed."""
    print("\n[Step 4/5] Writing user config file...")
    if DEFAULT_CONFIG_PATH.exists() and not force:
        print(f"  {DEFAULT_CONFIG_PATH} already exists (use --force to overwrite).")
        return 0

    profiles = load_installer_profiles(config)
    profile_data = profiles.get(profile, {})
    # Full passthrough (todo-19 (c)): a fixed whitelist silently dropped any
    # top-level block it did not name (coding:/research: included) from user
    # configs. Only the profile's embedding model is overridden.
    output = {
        **config,
        "embedding": {
            **config.get("embedding", {}),
            "model": profile_data.get("model", "nomic-embed-text"),
        },
    }

    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_CONFIG_PATH.write_text(yaml.safe_dump(output, sort_keys=False), encoding="utf-8")
    print(f"  Wrote {DEFAULT_CONFIG_PATH}")
    return 0


def setup_update_config(config: dict[str, Any], profile: str, apply: bool) -> int:
    """Update or write ~/.corpus-kb/config.yaml idempotently.

    If a config already exists, merge in the docker-compose connection string and
    defaults without overwriting profile-specific choices unless --force is used.
    """
    print("\n[Step 6/6] Updating user config file...")
    # Full passthrough (todo-19 (c)): same rationale as write_config — a fixed
    # whitelist silently dropped coding:/research: and any later block.
    output: dict[str, Any] = dict(config)

    if not apply:
        print(f"  (dry run) would write {DEFAULT_CONFIG_PATH}")
        print(f"  contents profile: {profile}")
        return 0

    if DEFAULT_CONFIG_PATH.exists():
        print(f"  Merging into existing {DEFAULT_CONFIG_PATH}")
        existing = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        if isinstance(existing, dict):
            # Preserve user values; only backfill missing top-level sections.
            for key, value in output.items():
                if key not in existing:
                    existing[key] = value
            output = existing

    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_CONFIG_PATH.write_text(yaml.safe_dump(output, sort_keys=False), encoding="utf-8")
    print(f"  Wrote {DEFAULT_CONFIG_PATH}")
    return 0


def print_next_steps() -> None:
    """Print post-install instructions."""
    print("\n[Step 5/5] Next steps:")
    print("  - Start Ollama: ollama serve")
    print("  - Start Postgres (if not running)")
    print("  - Run Corpus-KB: corpus-kb")
    print("========================\n")
