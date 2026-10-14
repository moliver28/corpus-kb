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
