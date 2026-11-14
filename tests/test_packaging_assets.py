"""Packaging-content gate: non-Python assets ship inside the wheel (W2).

Fails when the compose files or the demo corpus drop out of the
package-data config, or when a shipped copy drifts from its canonical
repository file. Same lockstep pattern as the packaged config.yaml
(issue #246).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "src" / "corpus_kb"

# (shipped name inside the package, canonical file at the checkout root)
SHIPPED_COMPOSE_FILES = (
    ("compose.yaml", REPO / "compose.yaml"),
    ("compose.build-local.yaml", REPO / "compose.build-local.yaml"),
)

DEMO_CORPUS_PACKAGE_DIR = PACKAGE / "demo_corpus"
DEMO_CORPUS_REPO_DIR = REPO / "docs" / "demo-kb"


def _package_data_patterns() -> list[str]:
    """The corpus_kb package-data patterns from pyproject.toml."""
    with (REPO / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    patterns = data["tool"]["setuptools"]["package-data"]["corpus_kb"]
    assert isinstance(patterns, list)
    return list(patterns)


def test_compose_files_are_declared_package_data() -> None:
    """The compose files must stay in the package-data list."""
    patterns = _package_data_patterns()
    assert "compose.yaml" in patterns
    assert "compose.build-local.yaml" in patterns


def test_compose_file_copies_exist_inside_the_package() -> None:
    """The declared files must exist inside the package at build time."""
    for name, _canonical in SHIPPED_COMPOSE_FILES:
        assert (PACKAGE / name).is_file(), f"src/corpus_kb/{name} is missing"


def test_shipped_compose_copies_match_the_checkout_files() -> None:
    """Shipped copies must stay byte-equal to the checkout-root files."""
    for name, canonical in SHIPPED_COMPOSE_FILES:
        assert canonical.is_file(), name
        assert (PACKAGE / name).read_bytes() == canonical.read_bytes(), (
            f"src/corpus_kb/{name} differs from {canonical}"
        )


def test_demo_corpus_is_declared_package_data() -> None:
    """The demo corpus glob must stay in the package-data list."""
    assert "demo_corpus/*.md" in _package_data_patterns()


def test_demo_corpus_copies_match_the_checkout_corpus() -> None:
    """Shipped corpus files must mirror docs/demo-kb name-for-name."""
    shipped = sorted(path.name for path in DEMO_CORPUS_PACKAGE_DIR.glob("*.md"))
    canonical = sorted(path.name for path in DEMO_CORPUS_REPO_DIR.glob("*.md"))
    assert canonical, "docs/demo-kb must hold the canonical demo files"
    assert shipped == canonical
    for name in canonical:
        shipped_bytes = (DEMO_CORPUS_PACKAGE_DIR / name).read_bytes()
        canonical_bytes = (DEMO_CORPUS_REPO_DIR / name).read_bytes()
        assert shipped_bytes == canonical_bytes, name
