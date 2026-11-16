"""Static content checks on the app image build (docker/app/Dockerfile).

Docker is not required to run these tests: they read the Dockerfile text
and pin the wheel self-check contract the same way test_docker_compose.py
pins the compose contract. The live build runs in the ``docker-smoke``
gate (poe target, ci.yml / docker), where the self-check fails the image
build when the wheel is incomplete.
"""

from __future__ import annotations

from pathlib import Path

DOCKERFILE_PATH = Path(__file__).resolve().parent.parent / "docker" / "app" / "Dockerfile"

# The wheel self-check runs this probe over the wheel's zip namelist.
SELF_CHECK_MARKER = "names = zipfile.ZipFile"

# Package data the self-check must demand beyond the three base paths.
# 004/005 create the AGE graph and the PostgresML bindings; without them
# the extension-gated remainder of `corpus-kb migrate` silently no-ops.
GATED_MIGRATIONS = (
    "corpus_kb/migrations/004_enable_age.sql",
    "corpus_kb/migrations/005_enable_pgml.sql",
)

BASE_REQUIRED = (
    "corpus_kb/ui_dist/index.html",
    "corpus_kb/config.yaml",
    "corpus_kb/storage/schema.sql",
)


def dockerfile_text() -> str:
    """The Dockerfile source."""
    return DOCKERFILE_PATH.read_text(encoding="utf-8")


def self_check_block() -> str:
    """The RUN python -c wheel self-check block, from its first line on."""
    text = dockerfile_text()
    start = text.index(SELF_CHECK_MARKER)
    return text[start:]


def test_self_check_still_requires_the_base_paths() -> None:
    """The self-check keeps demanding ui_dist, config.yaml, and schema.sql."""
    block = self_check_block()
    for path in BASE_REQUIRED:
        assert path in block, f"wheel self-check no longer requires {path}"


def test_self_check_requires_the_extension_gated_migrations() -> None:
    """The self-check must name the two extension-gated migrations."""
    block = self_check_block()
    for path in GATED_MIGRATIONS:
        assert path in block, f"wheel self-check does not require {path}"


def test_self_check_probes_the_migrations_glob() -> None:
    """The self-check counts shipped migrations/*.sql, not just two names.

    A regression in the package-data glob could drop every migration.
    The two pinned names would then both be missing, but the explicit
    glob probe makes the failure mode self-explanatory.
    """
    block = self_check_block()
    assert "startswith('corpus_kb/migrations/')" in block
    assert "endswith('.sql')" in block


def test_self_check_failure_message_covers_migrations() -> None:
    """A missing migration set produces a missing-migrations message."""
    block = self_check_block()
    assert "corpus_kb/migrations/*.sql" in block
