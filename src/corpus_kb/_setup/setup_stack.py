"""Docker-stack and model setup steps (zero-drift split of _setup/install.py).

Compose-stack start, Postgres image signature verification, the bounded
subprocess runner, extension verification, and the required-model pulls.
``install.py`` re-exports these so its orchestrators (and the
``install_module.*`` test patches) keep working.

Patch seam: the tests patch ``corpus_kb._setup.install.confirm`` /
``install.check_extensions`` and then call some of these functions
DIRECTLY (tests/test_f3_fixes.py). Consumers therefore resolve those two
collaborators through the install module at CALL time.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path

from corpus_kb._setup.setup_steps import (
    EXTENSION_REMEDIATION,
    EXTENSIONS,
    SETUP_REQUIRED_MODELS,
    ExtensionMap,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_POSTGRES_IMAGE = "ghcr.io/moliver28/corpus-kb-postgres:0.1.0-pg17"


def _confirm_fn() -> Callable[[str], bool]:
    """Resolve ``confirm`` from the install module (test patch seam)."""
    from corpus_kb._setup import install

    return install.confirm


def _check_extensions_fn() -> Callable[[str], Awaitable[ExtensionMap | None]]:
    """Resolve ``check_extensions`` from the install module (test patch seam)."""
    from corpus_kb._setup import install

    return install.check_extensions


def _find_compose_command() -> str | None:
    """Return 'docker compose' or 'docker-compose' if available, else None."""
    if shutil.which("docker"):
        # Prefer the plugin form. shutil.which("docker") being truthy is enough
        # to try the plugin syntax; the legacy docker-compose binary is separate.
        return "docker compose"
    if shutil.which("docker-compose"):
        return "docker-compose"
    return None


def _verify_postgres_image(image: str, skip_verify: bool) -> None:
    """Verify the Postgres container image signature using cosign.

    Args:
        image: Container image reference to verify.
        skip_verify: When True, verification is skipped and the function returns
            without invoking cosign.

    Raises:
        RuntimeError: If cosign is not installed or if verification fails.
    """
    if skip_verify:
        print("  Skipping Postgres image signature verification.")
        return

    if not shutil.which("cosign"):
        raise RuntimeError(
            "cosign is required for Postgres image signature verification. "
            "Install cosign (https://docs.sigstore.dev/cosign/installation/) "
            "or disable verification with --skip-image-verify or "
            "installer.verify_image_signature: false in config.yaml."
        )

    print(f"  Verifying signature for {image}...")
    try:
        subprocess.run(
            [
                "cosign",
                "verify",
                "--certificate-identity-regexp",
                r"^https://github\.com/moliver28/corpus-kb/",
                image,
            ],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Postgres image signature verification failed for {image}. "
            "The image may have been tampered with or was not signed by the "
            "expected identity. Disable verification only if you trust the image source."
        ) from exc


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
    if not _confirm_fn()(action):
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

    extensions = await _check_extensions_fn()(conn_str)
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
        if not _confirm_fn()(f"Pull Ollama model '{model}'."):
            print(f"  Skipped {model}.")
            continue
        exit_code |= _run_cmd(["ollama", "pull", model], timeout=600)
    return exit_code


def setup_pull_weights(apply: bool) -> int:
    """Pre-pull extraction model weights for the default ``ml`` backend.

    A declined prompt or a failed download only prints a note — the
    extraction fallback chain covers a missing model at ingest time.
    """
    from .setup_steps import install_extraction_weights

    return install_extraction_weights(apply)
