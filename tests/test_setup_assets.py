"""Tests for the runtime asset resolver (install-ease W2).

The resolver finds non-Python setup files in both install layouts:
package data first (wheel and pipx installs), then the repository
checkout root (clones and editable installs).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_kb._setup import setup_assets
from corpus_kb._setup.setup_assets import (
    COMPOSE_BUILD_LOCAL_FILE,
    COMPOSE_FILE,
    resolve_setup_asset,
)

REPO = Path(__file__).resolve().parent.parent


def _point_at_layouts(
    monkeypatch: pytest.MonkeyPatch,
    package_root: Path | None,
    repo_root: Path | None,
) -> None:
    """Point the resolver at fake layouts (None leaves one unpatched)."""
    if package_root is not None:

        def fake_packaged(name: str) -> Path | None:
            candidate = package_root / name
            return candidate if candidate.is_file() else None

        monkeypatch.setattr(setup_assets, "_packaged_asset", fake_packaged)
    if repo_root is not None:
        monkeypatch.setattr(setup_assets, "_repo_root", lambda: repo_root)


def test_packaged_copy_is_found_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A copy inside the installed package wins over a checkout copy."""
    package_root = tmp_path / "site-packages" / "corpus_kb"
    package_root.mkdir(parents=True)
    (package_root / COMPOSE_FILE).write_text("services: packaged\n", encoding="utf-8")
    repo_root = tmp_path / "checkout"
    repo_root.mkdir()
    (repo_root / COMPOSE_FILE).write_text("services: checkout\n", encoding="utf-8")
    _point_at_layouts(monkeypatch, package_root, repo_root)

    found = resolve_setup_asset(COMPOSE_FILE)

    assert found == package_root / COMPOSE_FILE


def test_checkout_copy_is_the_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a packaged copy, the checkout root file is returned."""
    package_root = tmp_path / "site-packages" / "corpus_kb"
    package_root.mkdir(parents=True)
    repo_root = tmp_path / "checkout"
    repo_root.mkdir()
    (repo_root / COMPOSE_FILE).write_text("services: checkout\n", encoding="utf-8")
    _point_at_layouts(monkeypatch, package_root, repo_root)

    found = resolve_setup_asset(COMPOSE_FILE)

    assert found == repo_root / COMPOSE_FILE


def test_missing_asset_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No copy in either layout yields None (callers print their own error)."""
    package_root = tmp_path / "site-packages" / "corpus_kb"
    package_root.mkdir(parents=True)
    repo_root = tmp_path / "checkout"
    repo_root.mkdir()
    _point_at_layouts(monkeypatch, package_root, repo_root)

    assert resolve_setup_asset(COMPOSE_FILE) is None


def test_build_local_override_resolves_like_the_base_file() -> None:
    """The build-local override resolves through the same two layouts."""
    found = resolve_setup_asset(COMPOSE_BUILD_LOCAL_FILE)
    assert found is not None
    assert found.name == COMPOSE_BUILD_LOCAL_FILE
    assert found.is_file()


def test_real_repo_resolves_the_compose_file() -> None:
    """Smoke: the base compose file resolves to an existing file."""
    found = resolve_setup_asset(COMPOSE_FILE)
    assert found is not None
    assert found.name == COMPOSE_FILE
    assert found.is_file()


def test_wiring_setup_start_compose_uses_the_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """setup_start_compose runs docker compose with the resolved path."""
    from corpus_kb._setup import setup_stack

    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> int:
        commands.append(cmd)
        return 0

    monkeypatch.setattr(setup_assets, "resolve_setup_asset", lambda _name: compose_file)
    monkeypatch.setattr(setup_stack, "_run_cmd", fake_run)
    monkeypatch.setattr(setup_stack, "_confirm_fn", lambda: lambda _action: True)

    exit_code = setup_stack.setup_start_compose("docker compose", apply=True, build_local=False)

    assert exit_code == 0
    assert commands and commands[0][:2] == ["docker", "compose"]
    assert str(compose_file) in commands[0]
