"""The ``corpus-kb update`` flavor detection table (W5-a).

Four install flavors, one deterministic detector:

=============== ===================== ==========================================
Flavor          Signal                Update surface
=============== ===================== ==========================================
compose         container marker      ``docker compose pull`` (pin stays owner's)
editable        direct_url.json       ``git pull`` + ``pip install -e .``
pipx            PIPX env / venv path  ``pipx upgrade corpus-kb``
pip             wheel, none of the up ``pip install --upgrade corpus-kb``
=============== ===================== ==========================================

The probes are injected so the table never touches the live interpreter.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from corpus_kb._setup.update_flavor import InstallFlavor, detect_flavor


def _no_markers() -> list[Path]:
    return []


def _probe(editable: bool | None) -> Callable[[], bool | None]:
    return lambda: editable


def test_container_marker_detects_compose(tmp_path: Path) -> None:
    marker = tmp_path / ".dockerenv"
    marker.write_text("")
    assert detect_flavor(container_markers=[marker]) is InstallFlavor.COMPOSE


def test_both_container_markers_missing_answers_not_compose(tmp_path: Path) -> None:
    markers = [tmp_path / ".dockerenv", tmp_path / ".containerenv"]
    assert (
        detect_flavor(container_markers=markers, editable_probe=_probe(False)) is InstallFlavor.PIP
    )


def test_editable_direct_url_detects_editable() -> None:
    assert (
        detect_flavor(container_markers=_no_markers(), editable_probe=_probe(True))
        is InstallFlavor.EDITABLE
    )


def test_pipx_env_detects_pipx() -> None:
    assert (
        detect_flavor(
            container_markers=_no_markers(),
            editable_probe=_probe(False),
            env={"PIPX_HOME": r"C:\Users\me\.pipx"},
        )
        is InstallFlavor.PIPX
    )


def test_pipx_default_python_env_detects_pipx() -> None:
    assert (
        detect_flavor(
            container_markers=_no_markers(),
            editable_probe=_probe(False),
            env={"PIPX_DEFAULT_PYTHON": r"C:\Python311\python.exe"},
        )
        is InstallFlavor.PIPX
    )


@pytest.mark.parametrize(
    "executable",
    [
        r"C:\Users\me\.local\pipx\venvs\corpus-kb\Scripts\python.exe",
        "/home/me/.local/pipx/venvs/corpus-kb/bin/python",
    ],
    ids=["windows-venv", "posix-venv"],
)
def test_pipx_venv_path_detects_pipx(executable: str) -> None:
    assert (
        detect_flavor(
            container_markers=_no_markers(),
            editable_probe=_probe(False),
            executable=executable,
        )
        is InstallFlavor.PIPX
    )


def test_plain_wheel_answers_pip() -> None:
    assert (
        detect_flavor(
            container_markers=_no_markers(),
            editable_probe=_probe(False),
            executable=r"C:\Python311\python.exe",
            env={},
        )
        is InstallFlavor.PIP
    )


def test_unknown_editable_answer_falls_through() -> None:
    """A probe that cannot answer (None) falls through to the later rules."""
    assert (
        detect_flavor(container_markers=_no_markers(), editable_probe=_probe(None), env={})
        is InstallFlavor.PIP
    )


def test_compose_wins_over_everything(tmp_path: Path) -> None:
    marker = tmp_path / ".containerenv"
    marker.write_text("")
    assert (
        detect_flavor(
            container_markers=[marker],
            editable_probe=_probe(True),
            executable="/home/me/.local/pipx/venvs/corpus-kb/bin/python",
            env={"PIPX_HOME": "/home/me/.local/pipx"},
        )
        is InstallFlavor.COMPOSE
    )


def test_editable_wins_over_pipx() -> None:
    assert (
        detect_flavor(
            container_markers=_no_markers(),
            editable_probe=_probe(True),
            env={"PIPX_HOME": "/home/me/.local/pipx"},
        )
        is InstallFlavor.EDITABLE
    )


def test_default_probe_reads_the_installed_distribution() -> None:
    """The default probe answers from this checkout's own metadata.

    The tests run against ``pip install -e .`` of this repo, so the
    honest default answer here is True — but the contract under test is
    only that the default probe exists and answers bool or None.
    """
    from corpus_kb._setup.update_flavor import default_editable_probe

    answer = default_editable_probe()
    assert answer is None or isinstance(answer, bool)
