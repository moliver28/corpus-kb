"""UX Wave 6 wiring tests: the marimo seam, the mount gate, the startup flag.

The gate tests ALWAYS run (extras-less CI included): they exercise
``mount_notebooks``/``load_marimo`` with the import seam patched or a
fake marimo module, never the real package. The one mount test that
touches real marimo sits behind ``pytest.importorskip`` so it simply
skips without the ``[notebook]`` extra.
"""

from __future__ import annotations

from types import ModuleType, SimpleNamespace

import pytest

from corpus_kb.errors import DependencyMissingError
from corpus_kb.notebooks import console
from corpus_kb.notebooks.console import (
    NOTEBOOKS_MOUNT_PATH,
    MarimoDependencyError,
    load_marimo,
    mount_notebooks,
)
from corpus_kb.server_wiring import wire_notebooks


def _missing_import(name: str) -> ModuleType:
    raise ModuleNotFoundError(f"No module named '{name}'")


# ---------------------------------------------------------------------------
# The lazy import seam (always run — never imports marimo for real)
# ---------------------------------------------------------------------------


def test_load_marimo_missing_extra_is_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(console, "_import_module", _missing_import)
    with pytest.raises(DependencyMissingError) as excinfo:
        load_marimo()
    assert isinstance(excinfo.value, MarimoDependencyError)
    assert excinfo.value.remediation == "pip install 'corpus-kb[notebook]'"


# ---------------------------------------------------------------------------
# The mount gate: False with an honest reason, never a raise
# ---------------------------------------------------------------------------


def test_mount_reports_false_without_the_extra(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(console, "_import_module", _missing_import)
    with caplog.at_level("INFO"):
        assert mount_notebooks(SimpleNamespace(), "notebooks") is False
    assert "corpus-kb[notebook]" in caplog.text


def test_mount_reports_false_when_directory_missing() -> None:
    assert mount_notebooks(SimpleNamespace(), "no/such/directory") is False


def test_mount_reports_false_when_marimo_is_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_builder() -> object:
        raise RuntimeError("marimo exploded")

    monkeypatch.setattr(
        console, "_import_module", lambda name: SimpleNamespace(create_asgi_app=broken_builder)
    )
    assert mount_notebooks(SimpleNamespace(), "notebooks") is False


def test_mount_never_kills_the_server_when_app_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def build() -> object:
        return object()

    builder = SimpleNamespace(with_dynamic_directory=lambda _d: SimpleNamespace(build=build))
    monkeypatch.setattr(
        console, "_import_module", lambda name: SimpleNamespace(create_asgi_app=lambda: builder)
    )
    app = SimpleNamespace(mount=lambda _p, _a: (_ for _ in ()).throw(RuntimeError("no mount")))
    assert mount_notebooks(app, "notebooks") is False


def test_mount_success_path_mounts_the_marimo_app(monkeypatch: pytest.MonkeyPatch) -> None:
    mounted: list[tuple[str, object]] = []
    marimo_app = object()

    def build() -> object:
        return marimo_app

    builder = SimpleNamespace(with_dynamic_directory=lambda _d: SimpleNamespace(build=build))
    monkeypatch.setattr(
        console, "_import_module", lambda name: SimpleNamespace(create_asgi_app=lambda: builder)
    )
    app = SimpleNamespace(mount=lambda path, asgi: mounted.append((path, asgi)))
    assert mount_notebooks(app, "notebooks") is True
    assert mounted == [(NOTEBOOKS_MOUNT_PATH, marimo_app)]


# ---------------------------------------------------------------------------
# The startup gate: the config flag decides, absence never raises
# ---------------------------------------------------------------------------


def test_wire_notebooks_gate_off_never_mounts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def spy(app: object, directory: object) -> bool:
        calls.append("mount")
        return True

    monkeypatch.setattr(console, "mount_notebooks", spy)
    assert wire_notebooks(SimpleNamespace(), {"notebooks": {"enabled": False}}) is False
    assert calls == []


def test_wire_notebooks_gate_on_mounts_the_configured_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def spy(app: object, directory: object) -> bool:
        seen["directory"] = directory
        return True

    monkeypatch.setattr(console, "mount_notebooks", spy)
    assert wire_notebooks(SimpleNamespace(), {"notebooks": {"enabled": True}}) is True
    assert seen["directory"] == "notebooks"


def test_wire_notebooks_invalid_values_raise_at_load() -> None:
    with pytest.raises(ValueError, match=r"notebooks\.enabled"):
        wire_notebooks(SimpleNamespace(), {"notebooks": {"enabled": "yes"}})


# ---------------------------------------------------------------------------
# The real mount (skips cleanly without the [notebook] extra — CI lacks it)
# ---------------------------------------------------------------------------


def test_mount_notebooks_serves_the_dynamic_directory() -> None:
    pytest.importorskip("marimo")
    from fastapi import FastAPI

    application = FastAPI()
    assert mount_notebooks(application, "notebooks") is True
    paths = {getattr(route, "path", "") for route in application.routes}
    assert any(path.startswith(NOTEBOOKS_MOUNT_PATH) for path in paths), sorted(paths)
