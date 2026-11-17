"""Install-flavor detection for ``corpus-kb update`` (W5-a).

Four install flavors, one detector, checked in a fixed order:

1. compose  — a container marker file exists: this process runs INSIDE
   the app container, so the update surface is ``docker compose`` on
   the host, not this process.
2. editable — the installed ``corpus-kb`` distribution records a PEP 610
   ``direct_url.json`` with ``dir_info.editable`` (the checkout layout,
   ``pip install -e .``).
3. pipx     — PIPX environment variables, or the interpreter lives in a
   pipx venv (``.../pipx/venvs/<pkg>/...``).
4. pip      — a plain wheel install; the upgrade surface is PyPI.

Every probe is injectable so the detection table runs hermetically
(``tests/test_update_flavor.py``).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path

_PACKAGE_NAME = "corpus-kb"

#: pipx leaves these set in the venv it builds and the shells it manages.
_PIPX_ENV_VARS = ("PIPX_HOME", "PIPX_DEFAULT_PYTHON")

#: Marker files a container runtime creates at the filesystem root.
_DEFAULT_CONTAINER_MARKERS = (Path("/.dockerenv"), Path("/.containerenv"))


class InstallFlavor(StrEnum):
    """How this Corpus-KB install was put on the machine."""

    EDITABLE = "editable"
    PIPX = "pipx"
    PIP = "pip"
    COMPOSE = "compose"


def default_editable_probe() -> bool | None:
    """Read PEP 610 ``direct_url.json`` from the installed distribution.

    True = editable install, False = a wheel/sdist install, None = the
    answer is unknown (no metadata, or the file does not parse).
    """
    from importlib.metadata import distribution

    try:
        raw = distribution(_PACKAGE_NAME).read_text("direct_url.json")
    except Exception:
        # Any metadata trouble (not installed, unreadable site-packages)
        # means this probe cannot answer; the later rules decide.
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    dir_info = data.get("dir_info")
    return bool(isinstance(dir_info, dict) and dir_info.get("editable"))


def detect_flavor(
    *,
    container_markers: Sequence[Path] | None = None,
    editable_probe: Callable[[], bool | None] | None = None,
    executable: str | None = None,
    env: Mapping[str, str] | None = None,
) -> InstallFlavor:
    """Answer the install flavor, checking the rules in the fixed order.

    The defaults read the live process (container markers, the installed
    distribution's metadata, ``sys.executable``, ``os.environ``); the
    keyword arguments let tests inject every answer.
    """
    markers = _DEFAULT_CONTAINER_MARKERS if container_markers is None else container_markers
    if any(marker.exists() for marker in markers):
        return InstallFlavor.COMPOSE
    probe = editable_probe if editable_probe is not None else default_editable_probe
    if probe() is True:
        return InstallFlavor.EDITABLE
    env_map: Mapping[str, str] = {} if env is None else env
    if any(name in env_map for name in _PIPX_ENV_VARS):
        return InstallFlavor.PIPX
    exe = executable if executable is not None else sys.executable
    if "pipx" in Path(exe).parts:
        return InstallFlavor.PIPX
    return InstallFlavor.PIP


__all__ = ["InstallFlavor", "default_editable_probe", "detect_flavor"]
