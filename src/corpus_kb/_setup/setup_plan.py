"""Setup dry-run plan printer (zero-drift split of _setup/install.py).

Renders the exact steps ``corpus-kb setup --dry-run`` prints. Pure output:
no mutation, no subprocess, no database access.
"""

from __future__ import annotations

from typing import Any

from corpus_kb._setup.install_state import DEFAULT_CONFIG_PATH, INSTALL_PHASES
from corpus_kb._setup.setup_steps import SETUP_REQUIRED_MODELS


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


def _print_pull_steps(
    total: int,
    completed: list[str],
    compose_pull_cmd: str | None,
    build_local: bool,
) -> None:
    """Steps 1-2: the concurrent image and model pull plan lines."""
    _step(
        1,
        total,
        "Pull the Docker stack images",
        False,
        f"run: {compose_pull_cmd}" if compose_pull_cmd else None,
    )
    if "pull" in completed:
        print("  status: already completed; will be skipped")
    elif build_local:
        print("  skipped with --build-local: the image builds at compose up")
    elif compose_pull_cmd:
        print(f"  command: {compose_pull_cmd}")
        print("  concurrent: runs together with step 2")
    else:
        print("  command: not found; install Docker to proceed")
        print("  Or run: corpus-kb setup --backend duckdb for a no-Docker setup.")

    _step(
        2,
        total,
        "Pull required Ollama models",
        False,
        f"run: ollama pull {', '.join(SETUP_REQUIRED_MODELS)}",
    )
    if "models" in completed:
        print("  status: already completed; will be skipped")
    else:
        print("  concurrent: runs together with step 1")


def _print_stack_step(
    total: int,
    completed: list[str],
    compose_up_cmd: str | None,
    conn_str: str,
    build_local: bool,
) -> None:
    """Step 3: the compose stack start plan lines."""
    _step(
        3,
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


def _print_phase_steps(total: int, completed: list[str], conn_str: str, profile: str) -> None:
    """Steps 4-8: deps, migrations, extensions, weights, config."""
    _step(4, total, "Install Python dependencies", False, "run: pip install -e .[dev]")
    if "python" in completed:
        print("  status: already completed; will be skipped")

    _step(
        5,
        total,
        "Create database/user and apply migrations",
        False,
        f"run migrations on {conn_str}",
    )
    if "database" in completed:
        print("  status: already completed; will be skipped")

    _step(
        6,
        total,
        "Verify AGE + pgml extensions are installed",
        False,
        "query pg_extension for age, pgml, vector",
    )
    if "extensions" in completed:
        print("  status: already completed; will be skipped")

    _step(
        7,
        total,
        "Pre-pull extraction model weights",
        False,
        "run: huggingface snapshot download (GLiNER2 + Relex)",
    )
    if "weights" in completed:
        print("  status: already completed; will be skipped")

    _step(
        8,
        total,
        "Write/update user config file",
        False,
        f"write {DEFAULT_CONFIG_PATH} (profile: {profile})",
    )
    if "config" in completed:
        print("  status: already completed; will be skipped")


def setup_print_dry_run_steps(
    compose_cmd: str | None,
    conn_str: str,
    profile: str,
    build_local: bool = False,
    state: dict[str, Any] | None = None,
) -> None:
    """Print the exact steps the setup command would execute.

    The plan mirrors the real run order: the image pull (step 1) and the
    model pulls (step 2) run concurrently before compose up.
    """
    if state is None:
        state = {"completed": []}
    total = len(INSTALL_PHASES)
    completed = state.get("completed", [])

    compose_files = ["compose.yaml"]
    if build_local:
        compose_files.append("compose.build-local.yaml")
    file_flags = " ".join(f"-f {name}" for name in compose_files)
    build_flag = " --build" if build_local else ""
    compose_pull_cmd = f"{compose_cmd} {file_flags} pull" if compose_cmd else None
    compose_up_cmd = f"{compose_cmd} {file_flags} up -d{build_flag}" if compose_cmd else None

    _print_pull_steps(total, completed, compose_pull_cmd, build_local)
    _print_stack_step(total, completed, compose_up_cmd, conn_str, build_local)
    _print_phase_steps(total, completed, conn_str, profile)

    print("\nThis was a dry run. Re-run without --dry-run to make changes.")
    print("========================\n")
