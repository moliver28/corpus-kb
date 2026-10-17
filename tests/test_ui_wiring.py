"""UX Wave 3 wiring tests: the mount gate, the auto-open guard, the seam.

The gate tests ALWAYS run (extras-less CI included): they exercise
``mount_ui``/``load_nicegui`` with the import seam patched, never the
real nicegui package. The one mount test that touches real NiceGUI sits
behind ``pytest.importorskip`` so it simply skips without the extra.
"""

from __future__ import annotations

import pytest

from corpus_kb.errors import DependencyMissingError
from corpus_kb.server_wiring import (
    UI_OPEN_ENV_VAR,
    UiMountResult,
    browser_open_enabled,
    mount_ui,
    schedule_browser_open,
)
from corpus_kb.ui import app as ui_app
from corpus_kb.ui.app import UiDependencyError, load_nicegui

# ---------------------------------------------------------------------------
# The lazy import seam (always run — never imports nicegui for real)
# ---------------------------------------------------------------------------


def test_load_nicegui_missing_extra_is_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(name: str) -> object:
        raise ModuleNotFoundError(f"No module named '{name}'")

    monkeypatch.setattr(ui_app, "_import_module", _missing)
    with pytest.raises(DependencyMissingError) as excinfo:
        load_nicegui()
    assert isinstance(excinfo.value, UiDependencyError)
    assert excinfo.value.remediation == "pip install 'corpus-kb[ui]'"


# ---------------------------------------------------------------------------
# The startup gate: mount_ui returns disabled-with-remediation, never raises
# ---------------------------------------------------------------------------


def test_mount_ui_reports_disabled_with_remediation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate helper — no nicegui needed: the seam itself fails."""

    def _missing(name: str) -> object:
        raise ModuleNotFoundError(f"No module named '{name}'")

    monkeypatch.setattr(ui_app, "_import_module", _missing)
    result = mount_ui(object())
    assert isinstance(result, UiMountResult)
    assert result.mounted is False
    assert result.remediation == "pip install 'corpus-kb[ui]'"


def test_mount_ui_success_path(monkeypatch: pytest.MonkeyPatch) -> None:
    mounted_with: list[object] = []

    def fake_build(app: object) -> None:
        mounted_with.append(app)

    monkeypatch.setattr(ui_app, "build_ui", fake_build)
    sentinel = object()
    result = mount_ui(sentinel)
    assert result == UiMountResult(mounted=True)
    assert mounted_with == [sentinel]


def test_mount_ui_broken_ui_never_kills_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_build(app: object) -> None:
        raise RuntimeError("nicegui exploded")

    monkeypatch.setattr(ui_app, "build_ui", broken_build)
    result = mount_ui(object())
    assert result.mounted is False
    assert result.remediation == ""


def test_main_help_advertises_the_ui_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """--ui/--no-ui thread through server_wiring.main's argparse."""
    from corpus_kb import server_wiring

    with pytest.raises(SystemExit) as excinfo:
        server_wiring.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--ui" in out
    assert "--no-ui" in out


# ---------------------------------------------------------------------------
# The browser auto-open guard (always run)
# ---------------------------------------------------------------------------


def test_browser_open_enabled_by_default() -> None:
    assert browser_open_enabled({}) is True
    assert browser_open_enabled({UI_OPEN_ENV_VAR: "1"}) is True


def test_browser_open_env_zero_disables() -> None:
    assert browser_open_enabled({UI_OPEN_ENV_VAR: "0"}) is False


def test_schedule_browser_open_needs_a_mounted_ui() -> None:
    assert schedule_browser_open({}, "localhost", 8010) is None


def test_schedule_browser_open_respects_the_env_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(UI_OPEN_ENV_VAR, "0")
    assert schedule_browser_open({"ui_mounted": True}, "localhost", 8010) is None


async def test_schedule_browser_open_returns_task_when_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import corpus_kb.server_wiring as wiring

    monkeypatch.delenv(UI_OPEN_ENV_VAR, raising=False)
    opened: list[str] = []

    async def fake_open(host: str, port: int, delay: float = 0.0) -> None:
        opened.append(f"{host}:{port}")

    monkeypatch.setattr(wiring, "open_browser_when_up", fake_open)
    task = schedule_browser_open({"ui_mounted": True}, "localhost", 8010)
    assert task is not None
    await task
    assert opened == ["localhost:8010"]


# ---------------------------------------------------------------------------
# The real mount (skips cleanly without the [ui] extra — CI never has it)
# ---------------------------------------------------------------------------


def test_build_ui_mounts_nicegui_routes_into_fastapi() -> None:
    nicegui = pytest.importorskip("nicegui")
    from fastapi import FastAPI

    application = FastAPI()
    ui_app.build_ui(application)
    paths = {getattr(route, "path", "") for route in application.routes}
    assert any(path.startswith("/_nicegui") for path in paths), sorted(paths)
    assert hasattr(nicegui, "ui")  # the extra really provided the namespace
