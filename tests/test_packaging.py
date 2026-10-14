"""Distribution sanity tests (zero-drift Wave 4B, PyPI readiness, issue #2).

Light, hermetic checks over the metadata a PyPI install would see. The whole
module is skipped when the active ``corpus-kb`` distribution is a pip
editable install — the normal dev/CI checkout — so these assertions only
gate real wheel installs. The ``dist/`` wheel check reads the built artifact
directly (zipfile) and never imports the repo source, so it stays hermetic.
"""

from __future__ import annotations

import json
import zipfile
from importlib.metadata import PackageNotFoundError, distribution, entry_points
from pathlib import Path

import pytest
from packaging.version import InvalidVersion, Version

REPO_ROOT = Path(__file__).resolve().parents[1]


def _skip_packaging_assertions() -> bool:
    """True when there is no non-editable ``corpus-kb`` install to assert on."""
    try:
        dist = distribution("corpus-kb")
    except PackageNotFoundError:
        # Raw source checkout without any install: no dist metadata at all.
        return True
    raw = dist.read_text("direct_url.json")
    if raw is None:
        return False
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        return False
    # pip writes dir_info.editable=true for ``pip install -e`` installs.
    return bool(info.get("dir_info", {}).get("editable", False))


pytestmark = pytest.mark.skipif(
    _skip_packaging_assertions(),
    reason="corpus-kb is an editable/source install; packaging assertions gate wheel installs",
)


def test_version_is_valid_pep440() -> None:
    """``corpus_kb.__version__`` parses as a PEP 440 version string."""
    from corpus_kb import __version__

    try:
        parsed = Version(__version__)
    except InvalidVersion:
        pytest.fail(f"corpus_kb.__version__ is not a valid version: {__version__!r}")
    assert parsed.public != ""


def test_console_entry_point_resolves() -> None:
    """The ``corpus-kb`` console script maps to the Typer app factory."""
    scripts = {ep.name: ep for ep in entry_points(group="console_scripts")}
    assert "corpus-kb" in scripts
    assert scripts["corpus-kb"].value == "corpus_kb.cli:app"


def test_wheel_carries_package_data() -> None:
    """The newest dist/ wheel ships config data files and the SQL migrations."""
    wheels = sorted((REPO_ROOT / "dist").glob("corpus_kb-*.whl"))
    if not wheels:
        pytest.skip("no built wheel under dist/; run python -m build first")
    wheel = max(wheels, key=lambda path: path.stat().st_mtime)
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert "corpus_kb/config.yaml" in names
    assert "corpus_kb/config/ontology.yaml" in names
    in_migrations = {n for n in names if n.startswith("corpus_kb/migrations/")}
    sql = {n for n in in_migrations if n.endswith(".sql")}
    assert sql, f"{wheel.name} ships no migrations/*.sql"
    assert sql, f"{wheel.name} ships no migrations/*.sql"
