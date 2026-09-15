"""Full-stack intelligent installer for Corpus-KB.

Sub-commands:
  corpus-kb doctor      - read-only system detection and recommendations
  corpus-kb install     - mutating installation (requires --apply + confirmation)

All mutating actions require explicit user confirmation via input().
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import psutil
import yaml

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_CONFIG_DIR = Path.home() / ".corpus-kb"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.yaml"

REPO_ROOT = Path(__file__).resolve().parent.parent

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

# Resumable installer phases. Order matters: each phase is idempotent and
# checkpoints to a state file on success so interrupted runs can resume.
INSTALL_PHASES: tuple[str, ...] = (
    "compose",
    "python",
    "database",
    "extensions",
    "models",
    "config",
)
INSTALL_STATE_VERSION = 1
INSTALL_STATE_FILE_NAME = "install_state.json"


def _install_state_dir(config: dict[str, Any]) -> Path:
    """Return the directory for installer checkpoint state files.

    Honors config.installer.data_dir when present, otherwise defaults to
    ~/.corpus-kb so the state is user-local and writable.
    """
    data_dir = config.get("installer", {}).get("data_dir")
    if data_dir:
        return Path(str(data_dir)).expanduser()
    return DEFAULT_CONFIG_DIR


def _install_state_path(config: dict[str, Any]) -> Path:
    """Return the path to the installer checkpoint state file."""
    return _install_state_dir(config) / INSTALL_STATE_FILE_NAME


def load_install_state(config: dict[str, Any]) -> dict[str, Any]:
    """Load persisted installer state, returning a fresh state if missing/invalid."""
    path = _install_state_path(config)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        if isinstance(data, dict) and data.get("version") == INSTALL_STATE_VERSION:
            return data
    return {
        "version": INSTALL_STATE_VERSION,
        "completed": [],
        "last_run": None,
    }


def save_install_state(state: dict[str, Any], config: dict[str, Any]) -> None:
    """Persist installer checkpoint state, creating parent directories if needed."""
    path = _install_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = datetime.datetime.now(datetime.UTC).isoformat()
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def reset_install_state(config: dict[str, Any]) -> None:
    """Remove persisted installer state so the next run starts from phase 1."""
    path = _install_state_path(config)
    if path.exists():
        path.unlink()


def _detect_gpu_vram_gb() -> float:
    """Return total GPU VRAM in GB, or 0.0 if pynvml is unavailable."""
    try:
        import pynvml  # type: ignore[import-not-found]

        pynvml.nvmlInit()
        device_count = pynvml.nvmlDeviceGetCount()
        total_bytes = 0
        for i in range(device_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            total_bytes += mem_info.total
        pynvml.nvmlShutdown()
        return total_bytes / (1024**3)
    except Exception:
        return 0.0


def detect_profile() -> dict[str, Any]:
    """Detect hardware profile and return installer profile name + details."""
    ram_gb = psutil.virtual_memory().total / (1024**3)
    vram_gb = _detect_gpu_vram_gb()
    cpu_cores = psutil.cpu_count(logical=True) or 1

    if ram_gb > 16 or vram_gb >= 8:
        profile = "performance"
    elif ram_gb >= 8 or vram_gb >= 4:
        profile = "balanced"
    else:
        profile = "minimal"

    return {
        "profile": profile,
        "ram_gb": round(ram_gb, 1),
        "vram_gb": round(vram_gb, 1),
        "cpu_cores": cpu_cores,
        "os": platform.system(),
        "python": sys.version.split()[0],
    }


def load_installer_profiles(config: dict[str, Any]) -> dict[str, Any]:
    """Load installer profiles from config, with safe defaults."""
    installer = config.get("installer", {})
    profiles = installer.get("profiles", {})
    if not isinstance(profiles, dict) or not profiles:
        profiles = {
            "minimal": {
                "ram_gb_max": 8,
                "vram_gb_max": 0,
                "model": "nomic-embed-text",
                "llm": "qwen3:0.6b",
            },
            "balanced": {
                "ram_gb_max": 16,
                "vram_gb_max": 4,
                "model": "nomic-embed-text",
                "llm": "qwen3:4b",
            },
            "performance": {
                "ram_gb_max": 999,
                "vram_gb_max": 8,
                "model": "qwen3-embedding:8b-q8_0",
                "llm": "qwen3:14b",
            },
        }
    return profiles


async def check_postgres(connection_string: str) -> tuple[bool, str]:
    """Try to connect to Postgres with a 3-second timeout."""
    try:
        conn = await asyncpg.connect(connection_string, timeout=3)
        version = await conn.fetchval("SELECT version()")
        await conn.close()
        return True, str(version)
    except Exception as exc:
        return False, str(exc)


async def check_ollama(base_url: str) -> tuple[bool, str]:
    """Check whether Ollama is reachable with a 3-second timeout."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{base_url}/api/tags")
            return response.status_code == 200, f"status {response.status_code}"
    except Exception as exc:
        return False, str(exc)


async def check_extensions(
    connection_string: str,
) -> dict[str, tuple[bool, str]] | None:
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


def print_doctor_report(info: dict[str, Any]) -> None:
    """Print read-only diagnostic report."""
    print("\n=== Corpus-KB Doctor ===")
    print(f"OS:           {info['os']}")
    print(f"Python:       {info['python']}")
    print(f"CPU cores:    {info['cpu_cores']}")
    print(f"RAM:          {info['ram_gb']} GB")
    print(f"GPU VRAM:     {info['vram_gb']} GB")
    print(f"Profile:      {info['profile']}")
    print(
        f"Postgres:     {'OK' if info['postgres_ok'] else 'UNREACHABLE'} ({info['postgres_msg']})"
    )
    print(f"Ollama:       {'OK' if info['ollama_ok'] else 'UNREACHABLE'} ({info['ollama_msg']})")
    print("Extensions:")
    extensions = info.get("extensions")
    for name, label in EXTENSIONS:
        if extensions is None:
            print(f"  {label} ({name}): UNKNOWN (Postgres unreachable; cannot verify)")
        else:
            installed, version = extensions[name]
            if installed:
                print(f"  {label} ({name}): OK v{version}")
            else:
                print(f"  {label} ({name}): MISSING - {EXTENSION_REMEDIATION}")
    print("\nRecommended commands (run with --apply to execute):")
    print("  1. pip install -e .[dev]")
    print("  2. corpus-kb install --apply")
    print(f"  3. ollama pull {info['recommended_model']}")
    print("========================\n")


async def doctor_cmd(config: dict[str, Any]) -> int:
    """Read-only diagnostic command."""
    info = detect_profile()
    profiles = load_installer_profiles(config)
    profile = profiles.get(info["profile"], {})
    info["recommended_model"] = profile.get("model", "nomic-embed-text")

    db_cfg = config.get("database", {})
    conn_str = str(db_cfg.get("connection_string", "")) or os.environ.get(
        "CORPUS_KB_DATABASE_URL", ""
    )
    info["postgres_ok"], info["postgres_msg"] = (
        await check_postgres(conn_str) if conn_str else (False, "no connection string")
    )
    info["extensions"] = await check_extensions(conn_str) if info["postgres_ok"] else None

    emb_cfg = config.get("embedding", {})
    ollama_url = str(emb_cfg.get("base_url", DEFAULT_OLLAMA_URL))
    info["ollama_ok"], info["ollama_msg"] = await check_ollama(ollama_url)

    print_doctor_report(info)
    return 0


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
    if not apply or not confirm("Create database (if needed) and run migrations."):
        print("  Skipped.")
        return 0

    # Create database if it does not exist (connect to postgres maintenance db).
    try:
        parsed = asyncpg.connection._parse_connstring(conn_str)
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
        print(f"  Database creation step failed (continuing): {exc}")

    # Install AGE + pgml server packages before migrations 004/005 need them.
    ext_rc = install_extension_packages(apply)

    # Run migrations.
    from corpus_kb._setup.migrate import run_migrations

    try:
        await run_migrations(conn_str)
    except Exception as exc:
        print(f"  ERROR: migrations failed: {exc}")
        print(f"  If AGE or pgml are missing on this server: {EXTENSION_REMEDIATION}")
        return 1
    return ext_rc


def install_ollama_model(model: str, apply: bool) -> int:
    """Pull recommended Ollama model if confirmed."""
    print(f"\n[Step 3/5] Ollama model: {model}")
    if not apply or not confirm(f"Pull Ollama model '{model}'."):
        print("  Skipped.")
        return 0

    result = subprocess.run(["ollama", "pull", model])
    return result.returncode


def write_config(profile: str, config: dict[str, Any], force: bool) -> int:
    """Write ~/.corpus-kb/config.yaml from detected profile if confirmed."""
    print("\n[Step 4/5] Writing user config file...")
    if DEFAULT_CONFIG_PATH.exists() and not force:
        print(f"  {DEFAULT_CONFIG_PATH} already exists (use --force to overwrite).")
        return 0

    profiles = load_installer_profiles(config)
    profile_data = profiles.get(profile, {})
    output = {
        "server": config.get("server", {}),
        "embedding": {
            **config.get("embedding", {}),
            "model": profile_data.get("model", "nomic-embed-text"),
        },
        "chunking": config.get("chunking", {}),
        "search": config.get("search", {}),
        "graph": config.get("graph", {}),
        "llamaindex": config.get("llamaindex", {}),
        "database": config.get("database", {}),
    }

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


async def install_cmd(config: dict[str, Any], apply: bool, force: bool) -> int:
    """Mutating installation command."""
    info = detect_profile()
    profiles = load_installer_profiles(config)
    profile_cfg = profiles.get(info["profile"], {})
    model = profile_cfg.get("model", "nomic-embed-text")

    conn_str = str(config.get("database", {}).get("connection_string", "")) or os.environ.get(
        "CORPUS_KB_DATABASE_URL", ""
    )

    print("\n=== Corpus-KB Installer ===")
    print(f"Detected profile: {info['profile']}")
    print(f"Recommended model: {model}")
    if not apply:
        print("\nThis was a dry run. Re-run with --apply to make changes.")
        print("========================\n")
        return 0

    print("\nWARNING: --apply will modify this machine's Python packages,")
    print("database, and Ollama models. Each step requires confirmation.\n")

    exit_code = 0
    exit_code |= install_python_deps()
    if conn_str:
        exit_code |= await install_database(conn_str, apply)
    else:
        print("\n[Step 2/5] No database connection string; skipping database setup.")
    exit_code |= install_ollama_model(model, apply)
    exit_code |= write_config(info["profile"], config, force)
    print_next_steps()
    return exit_code


def _find_compose_command() -> str | None:
    """Return 'docker compose' or 'docker-compose' if available, else None."""
    if shutil.which("docker"):
        # Prefer the plugin form. shutil.which("docker") being truthy is enough
        # to try the plugin syntax; the legacy docker-compose binary is separate.
        return "docker compose"
    if shutil.which("docker-compose"):
        return "docker-compose"
    return None


def _step(
    number: int,
    total: int,
    description: str,
    apply: bool,
    action: str | None = None,
) -> None:
    """Print a setup step header and its dry-run action line."""
    print(f"\n[Step {number}/{total}] {description}")
    if not apply and action:
        print(f"  (dry run) {action}")


def _phase_status(state: dict[str, Any], phase: str) -> str:
    """Return a short marker showing whether a phase is already completed."""
    completed = state.get("completed", [])
    return "(completed)" if phase in completed else ""


def setup_print_dry_run_steps(
    compose_cmd: str | None,
    conn_str: str,
    profile: str,
    build_local: bool = False,
    state: dict[str, Any] | None = None,
) -> None:
    """Print the exact steps the setup command would execute."""
    if state is None:
        state = {"completed": []}
    total = len(INSTALL_PHASES)
    completed = state.get("completed", [])

    compose_files = ["compose.yaml"]
    if build_local:
        compose_files.append("compose.build-local.yaml")
    file_flags = " ".join(f"-f {name}" for name in compose_files)
    build_flag = " --build" if build_local else ""
    compose_up_cmd = f"{compose_cmd} {file_flags} up -d{build_flag}" if compose_cmd else None

    _step(
        1,
        total,
        "Start the Docker compose stack",
        False,
        f"run: {compose_up_cmd}" if compose_up_cmd else None,
    )
    if "compose" in completed:
        print("  status: already completed; will be skipped")
    elif compose_up_cmd:
        print(f"  command: {compose_up_cmd}")
        if build_local:
            print("  build: local docker/postgres/Dockerfile")
        else:
            print("  image: ghcr.io/moliver28/corpus-kb-postgres:0.1.0-pg17")
    else:
        print("  command: not found; install Docker to proceed")
    print(f"  services: postgres ({conn_str})")

    _step(2, total, "Install Python dependencies", False, "run: pip install -e .[dev]")
    if "python" in completed:
        print("  status: already completed; will be skipped")

    _step(
        3,
        total,
        "Create database/user and apply migrations",
        False,
        f"run migrations on {conn_str}",
    )
    if "database" in completed:
        print("  status: already completed; will be skipped")

    _step(
        4,
        total,
        "Verify AGE + pgml extensions are installed",
        False,
        "query pg_extension for age, pgml, vector",
    )
    if "extensions" in completed:
        print("  status: already completed; will be skipped")

    _step(
        5,
        total,
        "Pull required Ollama models",
        False,
        f"run: ollama pull {', '.join(SETUP_REQUIRED_MODELS)}",
    )
    if "models" in completed:
        print("  status: already completed; will be skipped")

    _step(
        6,
        total,
        "Write/update user config file",
        False,
        f"write {DEFAULT_CONFIG_PATH} (profile: {profile})",
    )
    if "config" in completed:
        print("  status: already completed; will be skipped")

    print("\nThis was a dry run. Re-run without --dry-run to make changes.")
    print("========================\n")


def _run_cmd(cmd: list[str], *, cwd: Path | None = None, timeout: int = 300) -> int:
    """Run a command and return its exit code, with a bounded timeout."""
    print(f"  Running: {' '.join(cmd)}")
    try:
        return subprocess.run(cmd, cwd=cwd, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        print(f"  ERROR: command timed out after {timeout}s: {' '.join(cmd)}")
        return 1
    except FileNotFoundError as exc:
        print(f"  ERROR: command not found: {exc}")
        return 1


def setup_start_compose(compose_cmd: str, apply: bool, build_local: bool = False) -> int:
    """Bring the docker-compose stack up if available and confirmed."""
    repo_root = REPO_ROOT
    compose_file = repo_root.parent / "compose.yaml"
    if not compose_file.exists():
        print("  ERROR: compose.yaml not found at repo root.")
        print(f"  Looked for: {compose_file}")
        return 1

    compose_files = [repo_root.parent / "compose.yaml"]
    if build_local:
        build_local_file = repo_root.parent / "compose.build-local.yaml"
        if not build_local_file.exists():
            print("  ERROR: compose.build-local.yaml not found at repo root.")
            print(f"  Looked for: {build_local_file}")
            return 1
        compose_files.append(build_local_file)

    if not apply:
        return 0

    if build_local:
        action = "Build the Postgres image locally and start the docker-compose stack (postgres)."
    else:
        action = "Start the docker-compose stack (postgres)."
    if not confirm(action):
        print("  Skipped.")
        return 0

    parts = compose_cmd.split()
    file_args: list[str] = []
    for file in compose_files:
        file_args.extend(["-f", str(file)])
    cmd = [*parts, *file_args, "up", "-d"]
    if build_local:
        cmd.append("--build")
    return _run_cmd(cmd, cwd=repo_root.parent, timeout=600)


async def setup_verify_extensions(conn_str: str, apply: bool) -> int:
    """Verify AGE + pgml extensions are present in Postgres.

    This step is purely diagnostic: migrations are the real gate for whether
    the extensions are usable. Re-running is a no-op.
    """
    print("\n[Step 4/6] Verifying PostgreSQL extensions...")
    if not conn_str:
        print("  No database connection string; skipping extension check.")
        return 0

    extensions = await check_extensions(conn_str)
    if extensions is None:
        print("  Could not connect to Postgres; skipping extension check.")
        return 0

    for name, label in EXTENSIONS:
        installed, version = extensions[name]
        if installed:
            print(f"  {label} ({name}): OK v{version}")
        else:
            print(f"  {label} ({name}): MISSING - {EXTENSION_REMEDIATION}")
    return 0


def setup_pull_models(apply: bool) -> int:
    """Pull the models required by the default configuration."""
    exit_code = 0
    for model in SETUP_REQUIRED_MODELS:
        if not apply:
            print(f"  (dry run) ollama pull {model}")
            continue
        if not confirm(f"Pull Ollama model '{model}'."):
            print(f"  Skipped {model}.")
            continue
        exit_code |= _run_cmd(["ollama", "pull", model], timeout=600)
    return exit_code


def setup_update_config(config: dict[str, Any], profile: str, apply: bool) -> int:
    """Update or write ~/.corpus-kb/config.yaml idempotently.

    If a config already exists, merge in the docker-compose connection string and
    defaults without overwriting profile-specific choices unless --force is used.
    """
    print("\n[Step 6/6] Updating user config file...")
    output: dict[str, Any] = {
        "server": config.get("server", {}),
        "embedding": config.get("embedding", {}),
        "chunking": config.get("chunking", {}),
        "search": config.get("search", {}),
        "graph": config.get("graph", {}),
        "llamaindex": config.get("llamaindex", {}),
        "database": config.get("database", {}),
        "installer": config.get("installer", {}),
        "llm": config.get("llm", {}),
    }

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


async def setup_cmd(
    config: dict[str, Any], dry_run: bool, fresh: bool, build_local: bool = False
) -> int:
    """One-flow guided setup for the docker-compose stack.

    Steps:
      1. docker compose up -d
      2. pip install -e .[dev]
      3. create database/user and run migrations
      4. verify AGE + pgml extensions
      5. pull required Ollama models
      6. write/update user config

    Completed phases are persisted so interrupted runs can resume with
    ``corpus-kb setup``. Use ``--fresh`` to reset the checkpoint.
    """
    info = detect_profile()
    profiles = load_installer_profiles(config)
    profile = info["profile"]
    profile_cfg = profiles.get(profile, {})
    recommended_model = profile_cfg.get("model", "nomic-embed-text")

    conn_str = str(config.get("database", {}).get("connection_string", "")) or os.environ.get(
        "CORPUS_KB_DATABASE_URL", ""
    )
    if not conn_str:
        conn_str = "postgresql://corpus_user:corpus_pass@localhost:5433/corpus_kb"

    compose_cmd = _find_compose_command()

    if fresh:
        reset_install_state(config)

    state = load_install_state(config)

    print("\n=== Corpus-KB One-Line Setup ===")
    print(f"Detected profile: {profile}")
    print(f"Recommended embedding model: {recommended_model}")
    print(f"Database connection string: {conn_str}")
    completed = state.get("completed", [])
    if completed:
        print(f"Checkpoint: {len(completed)}/{len(INSTALL_PHASES)} phases completed")
        print(f"  Completed: {', '.join(completed)}")

    if dry_run:
        setup_print_dry_run_steps(
            compose_cmd, conn_str, profile, build_local=build_local, state=state
        )
        return 0

    print("\nWARNING: setup will start Docker containers, modify Python packages,")
    print("create database objects, and pull Ollama models. Each step requires")
    print("confirmation.\n")

    exit_code = 0

    # Phase 1: docker compose stack.
    if "compose" in completed:
        print("\n[Step 1/6] Docker compose stack already started; skipping.")
    elif compose_cmd:
        exit_code |= setup_start_compose(compose_cmd, apply=True, build_local=build_local)
        if not exit_code:
            completed.append("compose")
            save_install_state(state, config)
    else:
        print("\n[Step 1/6] docker / docker-compose not found; skipping stack start.")
        print("  Install Docker, then re-run setup.")

    # Phase 2: Python dependencies.
    if "python" in completed:
        print("\n[Step 2/6] Python dependencies already installed; skipping.")
    else:
        exit_code |= install_python_deps()
        if not exit_code:
            completed.append("python")
            save_install_state(state, config)

    # Phase 3: database and migrations.
    if "database" in completed:
        print("\n[Step 3/6] Database already set up; skipping.")
    elif conn_str:
        exit_code |= await install_database(conn_str, apply=True)
        if not exit_code:
            completed.append("database")
            save_install_state(state, config)
    else:
        print("\n[Step 3/6] No database connection string; skipping database setup.")

    # Phase 4: extension verification.
    if "extensions" in completed:
        print("\n[Step 4/6] Extensions already verified; skipping.")
    else:
        exit_code |= await setup_verify_extensions(conn_str, apply=True)
        if not exit_code:
            completed.append("extensions")
            save_install_state(state, config)

    # Phase 5: Ollama models.
    if "models" in completed:
        print("\n[Step 5/6] Ollama models already pulled; skipping.")
    else:
        exit_code |= setup_pull_models(apply=True)
        if not exit_code:
            completed.append("models")
            save_install_state(state, config)

    # Phase 6: user config.
    if "config" in completed:
        print("\n[Step 6/6] User config already written; skipping.")
    else:
        exit_code |= setup_update_config(config, profile, apply=True)
        if not exit_code:
            completed.append("config")
            save_install_state(state, config)

    print_next_steps()
    return exit_code


def load_config() -> dict[str, Any]:
    """Load config.yaml from repo root or user home."""
    candidates = [
        Path("config.yaml"),
        DEFAULT_CONFIG_PATH,
    ]
    for path in candidates:
        if path.exists():
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the installer.

    Args:
        argv: Optional argument list. When omitted, ``sys.argv`` is used.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Corpus-KB installer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Read-only system diagnostics")

    install_parser = subparsers.add_parser("install", help="Install Corpus-KB")
    install_parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute mutating steps (requires confirmation per step)",
    )
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing ~/.corpus-kb/config.yaml",
    )

    setup_parser = subparsers.add_parser(
        "setup",
        help="One-line docker-compose + database + migrations + models setup",
    )
    setup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print each setup step without executing",
    )
    setup_parser.add_argument(
        "--fresh",
        action="store_true",
        help="Reset the installer checkpoint and start from phase 1",
    )
    setup_parser.add_argument(
        "--build-local",
        action="store_true",
        help=(
            "Build the Postgres image from docker/postgres/Dockerfile "
            "instead of using the pre-built image"
        ),
    )

    args = parser.parse_args(argv)
    config = load_config()

    if args.command == "doctor":
        return asyncio.run(doctor_cmd(config))
    if args.command == "install":
        return asyncio.run(install_cmd(config, args.apply, args.force))
    if args.command == "setup":
        return asyncio.run(setup_cmd(config, args.dry_run, args.fresh, args.build_local))
    return 1


if __name__ == "__main__":
    sys.exit(main())
