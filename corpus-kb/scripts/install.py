"""Full-stack intelligent installer for Corpus-KB.

Sub-commands:
  corpus-kb doctor      - read-only system detection and recommendations
  corpus-kb install     - mutating installation (requires --apply + confirmation)

All mutating actions require explicit user confirmation via input().
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import platform
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


def print_doctor_report(info: dict[str, Any]) -> None:
    """Print read-only diagnostic report."""
    print("\n=== Corpus-KB Doctor ===")
    print(f"OS:           {info['os']}")
    print(f"Python:       {info['python']}")
    print(f"CPU cores:    {info['cpu_cores']}")
    print(f"RAM:          {info['ram_gb']} GB")
    print(f"GPU VRAM:     {info['vram_gb']} GB")
    print(f"Profile:      {info['profile']}")
    print(f"Postgres:     {'OK' if info['postgres_ok'] else 'UNREACHABLE'} ({info['postgres_msg']})")
    print(f"Ollama:       {'OK' if info['ollama_ok'] else 'UNREACHABLE'} ({info['ollama_msg']})")
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


async def install_database(conn_str: str, apply: bool) -> int:
    """Create database and run migrations if confirmed."""
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
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", dbname
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{dbname}"')
            print(f"  Created database {dbname}")
        else:
            print(f"  Database {dbname} already exists")
        await conn.close()
    except Exception as exc:
        print(f"  Database creation step failed (continuing): {exc}")

    # Run migrations.
    from migrate import run_migrations

    await run_migrations(conn_str)
    return 0


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


def main() -> int:
    """CLI entry point for the installer."""
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

    args = parser.parse_args()
    config = load_config()

    if args.command == "doctor":
        return asyncio.run(doctor_cmd(config))
    if args.command == "install":
        return asyncio.run(install_cmd(config, args.apply, args.force))
    return 1


if __name__ == "__main__":
    sys.exit(main())
