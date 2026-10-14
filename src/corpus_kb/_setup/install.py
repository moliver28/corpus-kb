"""Full-stack intelligent installer for Corpus-KB.

Sub-commands:
  corpus-kb doctor      - read-only system detection and recommendations
  corpus-kb setup       - one-flow guided installer (docker-compose + db + models)
  corpus-kb install     - legacy mutating installation (requires --apply + confirmation)

All mutating actions require explicit user confirmation via input().

Zero-drift split (Wave 3): this module keeps the doctor, the setup/install
orchestrators, the dry-run plan printer, and the argparse entry point. The
moved pieces live in sibling modules and are RE-EXPORTED below so the
historical ``install.<name>`` surface (CLI wiring, tests that patch
``install_module.<name>``) keeps working byte-for-byte:

  install_state.py  checkpoint state (load/save/reset + constants)
  profiles.py       hardware/GPU detection + installer profiles
  setup_steps.py    confirm, service probes, deps/db/model steps, config write
  setup_stack.py    docker stack, image verification, extension/model steps
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import logging
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import psutil

# NOTE: platform/shutil/subprocess/psutil are imported (not used directly) because the
# installer tests patch them as attributes of THIS module
# (patch.object(install_module.shutil, "which", ...) etc.). Do not remove.
from corpus_kb._setup.install_state import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_CONFIG_PATH,
    INSTALL_PHASES,
    INSTALL_STATE_FILE_NAME,
    INSTALL_STATE_VERSION,
    _install_state_dir,
    _install_state_path,
    load_install_state,
    reset_install_state,
    save_install_state,
)
from corpus_kb._setup.profiles import (
    _detect_gpu_vram_gb,
    load_installer_profiles,
)
from corpus_kb._setup.setup_plan import setup_print_dry_run_steps
from corpus_kb._setup.setup_stack import (
    DEFAULT_POSTGRES_IMAGE,
    REPO_ROOT,
    _find_compose_command,
    _run_cmd,
    _verify_postgres_image,
    setup_pull_models,
    setup_start_compose,
    setup_verify_extensions,
)
from corpus_kb._setup.setup_steps import (
    EXTENSION_REMEDIATION,
    EXTENSIONS,
    SETUP_REQUIRED_MODELS,
    check_extensions,
    check_ollama,
    check_postgres,
    confirm,
    install_database,
    install_extension_packages,
    install_ollama_model,
    install_python_deps,
    print_next_steps,
    setup_update_config,
    write_config,
)

DEFAULT_OLLAMA_URL = "http://localhost:11434"


def detect_profile() -> dict[str, Any]:
    """Detect hardware profile (patch seam: passes this module's GPU probe)."""
    from corpus_kb._setup.profiles import detect_profile as _detect_profile

    return _detect_profile(_detect_gpu_vram_gb)


def print_doctor_report(
    info: dict[str, Any],
    research: list[Any] | None = None,
    env_check: Any | None = None,
) -> None:
    """Print read-only diagnostic report."""
    print("\n=== Corpus-KB Doctor ===")
    print(f"OS:           {info['os']}")
    print(f"Python:       {info['python']}")
    print(f"CPU cores:    {info['cpu_cores']}")
    print(f"RAM:          {info['ram_gb']} GB")
    if info.get("vram_detected"):
        print(f"GPU VRAM:     {info['vram_gb']} GB")
    else:
        print("GPU VRAM:     unknown (no NVIDIA VRAM detection available)")
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
    if env_check is not None:
        print("\nConfiguration:")
        print(f"  {env_check.name}: {env_check.status.upper()} - {env_check.detail}")
        if env_check.fix:
            print(f"    fix: {env_check.fix}")
        if env_check.doc:
            print(f"    docs: {env_check.doc}")
    if research is None:
        print("\nResearch subsystem: SKIPPED (Postgres unreachable; run corpus-kb setup)")
    else:
        from corpus_kb._setup.doctor_research import print_research_checks

        print_research_checks(research)
    print("\nRecommended commands (drop --dry-run to execute setup):")
    print("  1. pip install -e .[dev]")
    print("  2. corpus-kb setup")
    print(f"  3. ollama pull {info['recommended_model']}")
    print("========================\n")


async def doctor_cmd(config: dict[str, Any]) -> int:
    """Read-only diagnostic command."""
    info = detect_profile()
    profiles = load_installer_profiles(config)
    profile = profiles.get(info["profile"], {})
    info["recommended_model"] = profile.get("model", "nomic-embed-text")

    # Offline configuration-truth check (runs even without Postgres): warns
    # about CORPUS_KB_* variables this version does not read.
    from corpus_kb._setup.doctor_env import env_var_check

    env_check = env_var_check()

    db_cfg = config.get("database", {})
    # Runtime precedence (review F-1): env wins over config so doctor probes
    # the SAME deployment research commands do. install_cmd/setup_cmd below
    # deliberately keep config-first (the installer targets the docker stack).
    conn_str = os.environ.get("CORPUS_KB_DATABASE_URL", "") or str(
        db_cfg.get("connection_string", "")
    )
    info["postgres_ok"], info["postgres_msg"] = (
        await check_postgres(conn_str) if conn_str else (False, "no connection string")
    )
    info["extensions"] = await check_extensions(conn_str) if info["postgres_ok"] else None

    emb_cfg = config.get("embedding", {})
    ollama_url = str(emb_cfg.get("base_url", DEFAULT_OLLAMA_URL))
    info["ollama_ok"], info["ollama_msg"] = await check_ollama(ollama_url)

    # Research subsystem checks (todo-19 (a)): read-only, all guarded on
    # postgres_ok, never affecting the doctor exit code. ollama_ok gates the
    # zero-vector canary (it only embeds when Ollama is confirmed reachable).
    research = None
    if info["postgres_ok"]:
        from corpus_kb._setup.doctor_research import research_checks

        research = await research_checks(
            conn_str, config, info.get("extensions"), ollama_ok=info["ollama_ok"]
        )

    print_doctor_report(info, research, env_check=env_check)
    return 0


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


async def _phase_compose(
    state: dict[str, Any],
    config: dict[str, Any],
    completed: list[str],
    *,
    compose_cmd: list[str] | str | None,
    postgres_image: str,
    skip_image_verify: bool,
    build_local: bool,
) -> tuple[int, bool]:
    """Phase 1: docker compose stack (checkpointed).

    Returns (exit_code, fatal); fatal means the image-verify step failed and
    setup must stop (a compose START failure is not fatal — the later phases
    still run, matching the historical behavior).
    """
    if "compose" in completed:
        print("\n[Step 1/6] Docker compose stack already started; skipping.")
        return 0, False
    if compose_cmd:
        if not build_local:
            try:
                _verify_postgres_image(postgres_image, skip_image_verify)
            except RuntimeError as exc:
                print(f"\n[Step 1/6] ERROR: {exc}")
                return 1, True
        exit_code = setup_start_compose(compose_cmd, apply=True, build_local=build_local)
        if not exit_code:
            completed.append("compose")
            save_install_state(state, config)
        return exit_code, False
    print("\n[Step 1/6] docker / docker-compose not found; skipping stack start.")
    print("  Install Docker, then re-run setup.")
    return 0, False


async def _run_database_phase(conn_str: str) -> int:
    """Phase 3 body: database, user and migrations (skipped without a DSN)."""
    if not conn_str:
        print("\n[Step 3/6] No database connection string; skipping database setup.")
        return 0
    return await install_database(conn_str, apply=True)


async def _run_phase(
    state: dict[str, Any],
    config: dict[str, Any],
    completed: list[str],
    name: str,
    skip_banner: str,
    run: Callable[[], Awaitable[int] | int],
) -> int:
    """Run one checkpointed setup phase: skip when completed, else checkpoint.

    A phase that reports a nonzero code is retried on the next run (it is
    not recorded as completed).
    """
    if name in completed:
        print(skip_banner)
        return 0
    result = run()
    if inspect.isawaitable(result):
        result = await result
    exit_code = int(result)
    if not exit_code:
        completed.append(name)
        save_install_state(state, config)
    return exit_code


async def setup_cmd(
    config: dict[str, Any],
    dry_run: bool,
    fresh: bool,
    build_local: bool = False,
    skip_image_verify: bool = False,
) -> int:
    """One-flow guided setup for the docker-compose stack.

    Steps:
      1. verify Postgres image signature (cosign)
      2. docker compose up -d
      3. pip install -e .[dev]
      4. create database/user and run migrations
      5. verify AGE + pgml extensions
      6. pull required Ollama models
      7. write/update user config

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
    # Shared list object with state["completed"]: _run_phase appends into it
    # before each save_install_state, so checkpoints persist (same mechanism
    # as the pre-split inline phases).
    completed = state.get("completed", [])

    installer_cfg = config.get("installer", {})
    postgres_image = str(installer_cfg.get("postgres_image", DEFAULT_POSTGRES_IMAGE))
    verify_enabled = bool(installer_cfg.get("verify_image_signature", True))
    skip_image_verify = skip_image_verify or not verify_enabled

    print("\n=== Corpus-KB One-Line Setup ===")
    print(f"Detected profile: {profile}")
    print(f"Recommended embedding model: {recommended_model}")
    print(f"Database connection string: {conn_str}")
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

    phase_exit, fatal = await _phase_compose(
        state,
        config,
        completed,
        compose_cmd=compose_cmd,
        postgres_image=postgres_image,
        skip_image_verify=skip_image_verify,
        build_local=build_local,
    )
    exit_code = phase_exit
    if fatal:
        return exit_code

    exit_code |= await _run_phase(
        state,
        config,
        completed,
        "python",
        "\n[Step 2/6] Python dependencies already installed; skipping.",
        install_python_deps,
    )
    exit_code |= await _run_phase(
        state,
        config,
        completed,
        "database",
        "\n[Step 3/6] Database already set up; skipping.",
        lambda: _run_database_phase(conn_str),
    )
    exit_code |= await _run_phase(
        state,
        config,
        completed,
        "extensions",
        "\n[Step 4/6] Extensions already verified; skipping.",
        lambda: setup_verify_extensions(conn_str, apply=True),
    )
    exit_code |= await _run_phase(
        state,
        config,
        completed,
        "models",
        "\n[Step 5/6] Ollama models already pulled; skipping.",
        lambda: setup_pull_models(apply=True),
    )
    exit_code |= await _run_phase(
        state,
        config,
        completed,
        "config",
        "\n[Step 6/6] User config already written; skipping.",
        lambda: setup_update_config(config, profile, apply=True),
    )

    print_next_steps()
    return exit_code


def load_config() -> dict[str, Any]:
    """Load config.yaml from repo root or user home.

    DIVERGENCE (do not swap silently): this loader is CONFIG-FIRST — it
    reads the YAML file as-is and applies NO environment overrides. The
    runtime loader (``corpus_kb.config.load_config``) is ENV-FIRST: it
    merges the CORPUS_KB_* environment variables over the YAML values.
    Keeping the installer config-first is deliberate (review F-1): setup
    must read and write the docker-compose stack targets recorded in
    config.yaml even when the shell exports another CORPUS_KB_DATABASE_URL,
    while doctor's CONNECTION PROBE separately honors the env var (see
    doctor_cmd). TODO(follow-up): add a precedence test pinning BOTH
    orderings (installer config-first, runtime env-first) so a future
    unification cannot drift silently.
    """
    from pathlib import Path

    import yaml

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
    setup_parser.add_argument(
        "--skip-image-verify",
        action="store_true",
        help="Skip cosign verification of the Postgres container image",
    )

    args = parser.parse_args(argv)
    config = load_config()

    if args.command == "doctor":
        return asyncio.run(doctor_cmd(config))
    if args.command == "install":
        return asyncio.run(install_cmd(config, args.apply, args.force))
    if args.command == "setup":
        return asyncio.run(
            setup_cmd(
                config,
                args.dry_run,
                args.fresh,
                args.build_local,
                args.skip_image_verify,
            )
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_OLLAMA_URL",
    "DEFAULT_POSTGRES_IMAGE",
    "EXTENSIONS",
    "EXTENSION_REMEDIATION",
    "INSTALL_PHASES",
    "INSTALL_STATE_FILE_NAME",
    "INSTALL_STATE_VERSION",
    "REPO_ROOT",
    "SETUP_REQUIRED_MODELS",
    "_detect_gpu_vram_gb",
    "_find_compose_command",
    "_install_state_dir",
    "_install_state_path",
    "_run_cmd",
    "_verify_postgres_image",
    "check_extensions",
    "check_ollama",
    "check_postgres",
    "confirm",
    "detect_profile",
    "install_database",
    "install_extension_packages",
    "install_ollama_model",
    "install_python_deps",
    "load_install_state",
    "load_installer_profiles",
    "platform",
    "print_doctor_report",
    "print_next_steps",
    "psutil",
    "reset_install_state",
    "save_install_state",
    "setup_print_dry_run_steps",
    "setup_pull_models",
    "setup_start_compose",
    "setup_update_config",
    "setup_verify_extensions",
    "shutil",
    "subprocess",
    "write_config",
]
