"""The layout-aware extra-install command (issue #464 follow-up).

Momus review of PR #517 (P2 + P3): the editable ``pip install -e`` form
is clone-only advice, so every extra-install hint must route through
``corpus_kb.install_layout``. These tests pin the real detector (no
monkeypatched ``_clone_layout`` seam): an installed-layout simulation
for both branches, plus the package-data absence a wheel install
relies on.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

import pytest

from corpus_kb import install_layout
from corpus_kb._setup import setup_assets
from corpus_kb.rag.embedders import LateChunkDependencyError


def test_pyproject_toml_is_not_package_data() -> None:
    """The wheel ships no pyproject.toml, or wheel installs read as clones.

    The detector reads a packaged pyproject.toml as "clone layout"
    (packaged copy wins, the setup_assets rule). Someone adding
    pyproject.toml to the package-data globs would flip every wheel and
    pipx install to clone advice; this test fails first.
    """
    ref = importlib.resources.files("corpus_kb").joinpath("pyproject.toml")
    assert not ref.is_file()


def test_is_clone_layout_true_with_a_checkout_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A checkout root holding pyproject.toml reads as a clone."""
    monkeypatch.setattr(setup_assets, "_packaged_asset", lambda name: None)
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    monkeypatch.setattr(setup_assets, "_repo_root", lambda: tmp_path)
    assert install_layout.is_clone_layout() is True


def test_is_clone_layout_false_for_an_installed_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No packaged copy and no checkout root reads as a wheel/pipx install."""
    monkeypatch.setattr(setup_assets, "_packaged_asset", lambda name: None)
    empty = tmp_path / "site-packages"
    empty.mkdir()
    monkeypatch.setattr(setup_assets, "_repo_root", lambda: empty)
    assert install_layout.is_clone_layout() is False


def test_extra_install_command_clone_form(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A clone layout gets the editable form."""
    monkeypatch.setattr(setup_assets, "_packaged_asset", lambda name: None)
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    monkeypatch.setattr(setup_assets, "_repo_root", lambda: tmp_path)
    assert install_layout.extra_install_command("inductive") == 'pip install -e ".[inductive]"'


def test_extra_install_command_package_form(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An installed layout gets the package-name form (pipx-safe)."""
    monkeypatch.setattr(setup_assets, "_packaged_asset", lambda name: None)
    empty = tmp_path / "site-packages"
    empty.mkdir()
    monkeypatch.setattr(setup_assets, "_repo_root", lambda: empty)
    assert install_layout.extra_install_command("latechunk") == 'pip install "corpus-kb[latechunk]"'


def test_extra_install_command_matches_this_layout() -> None:
    """The live command agrees with the detector on this environment."""
    command = install_layout.extra_install_command("inductive")
    if install_layout.is_clone_layout():
        assert command == 'pip install -e ".[inductive]"'
    else:
        assert command == 'pip install "corpus-kb[inductive]"'


def test_latechunk_install_hint_routes_through_install_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The latechunk error hint carries the layout-aware pip form (issue #526).

    The sixth clone-only site (``rag/embedders/_latechunk_types.py``) is
    computed at raise time from install_layout, so this installed-layout
    simulation pins the routing in every environment, including a clone.
    """
    from corpus_kb.rag.embedders import _latechunk_types as shim

    monkeypatch.setattr(setup_assets, "_packaged_asset", lambda name: None)
    empty = tmp_path / "site-packages"
    empty.mkdir()
    monkeypatch.setattr(setup_assets, "_repo_root", lambda: empty)

    def _absent(name: str) -> object:
        raise ModuleNotFoundError(f"No module named {name!r}")

    monkeypatch.setattr(shim.importlib, "import_module", _absent)
    with pytest.raises(LateChunkDependencyError, match=r'pip install "corpus-kb\[latechunk\]"'):
        shim.import_or_raise("sentence_transformers")
