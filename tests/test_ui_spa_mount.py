"""SPA mount tests (UI SPA program, Task 3).

The lifecycle SPA is mounted at ``/app`` by ``corpus_kb.ui.spa.mount_spa``.
These tests ALWAYS run (extras-less CI included): they never import
nicegui — the SPA is static files plus one JSON route. The dist fixture
is a throwaway directory built per test, so no build step is needed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient

from corpus_kb import __version__
from corpus_kb.config import VERIFY_FULL_GROUNDEDNESS, get_default_config
from corpus_kb.ui.spa import (
    DIST_DIR,
    mount_spa,
    runtime_config_payload,
    spa_settings,
)

SPA_MISSING_LOG = "[ui] SPA build not found - run: poe ui-build"

REPO = Path(__file__).resolve().parent.parent


def _fake_dist(root: Path) -> Path:
    """A dist directory that looks like a Vite build output."""
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        "<!doctype html><title>Corpus-KB</title><script src=/app/assets/app.js>"
    )
    (root / "assets" / "app.js").write_text("console.log('spa');")
    return root


def _client(app: FastAPI) -> TestClient:
    return TestClient(app)


async def _ping(_: Request) -> Response:
    return JSONResponse({"status": "ok"})


def _app_with_api() -> FastAPI:
    """A real app-shaped object: one API route, no UI."""
    app = FastAPI()
    app.add_route("/api/ping", _ping, methods=["GET"])
    return app


# ---------------------------------------------------------------------------
# Dist present: the SPA serves, runtime-config matches the config
# ---------------------------------------------------------------------------


def test_dist_present_mounts_and_serves_index(tmp_path: Path) -> None:
    app = _app_with_api()
    assert mount_spa(app, get_default_config(), dist=_fake_dist(tmp_path)) is True
    response = _client(app).get("/app/")
    assert response.status_code == 200
    assert "Corpus-KB" in response.text
    # Stale-SPA defense: the index must always revalidate.
    assert response.headers["cache-control"] == "no-cache"


def test_runtime_config_matches_config(tmp_path: Path) -> None:
    config = get_default_config()
    app = _app_with_api()
    mount_spa(app, config, dist=_fake_dist(tmp_path))
    response = _client(app).get("/app/runtime-config.json")
    assert response.status_code == 200
    assert response.json() == runtime_config_payload(config)


def test_runtime_config_carries_only_tunable_keys(tmp_path: Path) -> None:
    payload = runtime_config_payload(get_default_config())
    assert set(payload) == {
        "poll_ms",
        "request_timeout_s",
        "max_upload_mb",
        "server_version",
    }
    assert payload["server_version"] == __version__
    # Policy constants never travel over the wire.
    assert "verify_full_groundedness" not in payload


def test_runtime_config_drops_unknown_spa_keys(tmp_path: Path) -> None:
    """The payload key list is closed: junk under ui.spa never ships."""
    config = get_default_config()
    config["ui"]["spa"]["injected"] = "value"
    payload = runtime_config_payload(config)
    assert "injected" not in payload
    app = _app_with_api()
    mount_spa(app, config, dist=_fake_dist(tmp_path))
    response = _client(app).get("/app/runtime-config.json")
    assert "injected" not in response.json()


def test_spa_settings_reads_the_ui_spa_block() -> None:
    config = get_default_config()
    settings = spa_settings(config)
    assert settings == config["ui"]["spa"]
    # A missing ui block degrades to empty settings, never a crash.
    assert spa_settings({}) == {}


def test_root_redirects_to_app_when_dist_exists(tmp_path: Path) -> None:
    app = _app_with_api()
    mount_spa(app, get_default_config(), dist=_fake_dist(tmp_path))
    response = _client(app).get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/app/"


def test_runtime_config_route_registered_before_static_mount(tmp_path: Path) -> None:
    """Review Focus 3: the JSON route must precede the /app mount."""
    app = _app_with_api()
    mount_spa(app, get_default_config(), dist=_fake_dist(tmp_path))
    routes = app.router.routes
    json_index = next(
        i for i, r in enumerate(routes) if getattr(r, "path", "") == "/app/runtime-config.json"
    )
    mount_index = next(
        i for i, r in enumerate(routes) if isinstance(r, Mount) and getattr(r, "path", "") == "/app"
    )
    assert json_index < mount_index


def test_spa_route_wins_over_a_later_root_mount(tmp_path: Path) -> None:
    """Mount order: a root mount added after the SPA cannot shadow it."""
    app = _app_with_api()
    mount_spa(app, get_default_config(), dist=_fake_dist(tmp_path))
    other = tmp_path / "other"
    other.mkdir()
    (other / "index.html").write_text("root app")
    app.mount("/", StaticFiles(directory=str(other), html=True), name="root")
    response = _client(app).get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/app/"


def test_dist_dir_points_at_the_package_ui_dist() -> None:
    assert DIST_DIR.name == "ui_dist"
    assert DIST_DIR.parent.name == "corpus_kb"


# ---------------------------------------------------------------------------
# Dist missing: ONE honest log line, API still works (Review Focus 4)
# ---------------------------------------------------------------------------


def test_dist_missing_logs_exactly_one_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app = _app_with_api()
    with caplog.at_level(logging.WARNING, logger="corpus_kb.ui.spa"):
        mounted = mount_spa(app, get_default_config(), dist=tmp_path / "nowhere")
    assert mounted is False
    messages = [rec.message for rec in caplog.records]
    assert messages.count(SPA_MISSING_LOG) == 1


def test_dist_missing_keeps_the_api_working(tmp_path: Path) -> None:
    app = _app_with_api()
    mount_spa(app, get_default_config(), dist=tmp_path / "nowhere")
    response = _client(app).get("/api/ping")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert _client(app).get("/app/").status_code == 404


def test_missing_index_but_present_dir_is_still_missing(tmp_path: Path) -> None:
    empty = tmp_path / "half-built"
    empty.mkdir()
    assert mount_spa(_app_with_api(), get_default_config(), dist=empty) is False


# ---------------------------------------------------------------------------
# Config: the hoisted threshold + the ui.spa.* validation
# ---------------------------------------------------------------------------


def test_verify_threshold_hoisted_into_config_defaults() -> None:
    config = get_default_config()
    # The policy constant lives as a module constant (codegen source for
    # Task 4), never as a config-tree key.
    assert "verify_full_groundedness" not in config["ui"]
    assert VERIFY_FULL_GROUNDEDNESS == 0.99


@pytest.mark.parametrize(
    ("key", "bad_value"),
    [
        ("poll_ms", 0),
        ("poll_ms", 10),
        ("poll_ms", "fast"),
        ("poll_ms", True),
        ("poll_ms", 60001),
        ("request_timeout_s", 0),
        ("request_timeout_s", -1),
        ("request_timeout_s", 601),
        ("request_timeout_s", True),
        ("max_upload_mb", 0),
        ("max_upload_mb", 4096),
    ],
)
def test_bad_spa_values_refuse_at_load(tmp_path: Path, key: str, bad_value: Any) -> None:
    from corpus_kb.config import load_config

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"ui:\n  spa:\n    {key}: {bad_value!r}\n")
    with pytest.raises(ValueError) as excinfo:
        load_config(str(config_file))
    assert f"ui.spa.{key}" in str(excinfo.value)


def test_bad_spa_block_shape_refuses_at_load(tmp_path: Path) -> None:
    from corpus_kb.config import load_config

    config_file = tmp_path / "config.yaml"
    config_file.write_text("ui:\n  spa: broken\n")
    with pytest.raises(ValueError, match=r"ui\.spa"):
        load_config(str(config_file))


def test_good_spa_values_load(tmp_path: Path) -> None:
    from corpus_kb.config import load_config

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "ui:\n  spa:\n    poll_ms: 500\n    request_timeout_s: 30\n    max_upload_mb: 10\n"
    )
    config = load_config(str(config_file))
    assert config["ui"]["spa"] == {
        "poll_ms": 500,
        "request_timeout_s": 30,
        "max_upload_mb": 10,
    }


# ---------------------------------------------------------------------------
# Shipped-contract lockstep: defaults == both config.yaml files, and the
# validate_configs rows (moved here from test_ui_settings.py with ui.spa)
# ---------------------------------------------------------------------------


def _shipped_yaml() -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load((REPO / "config.yaml").read_text(encoding="utf-8"))
    return raw


def test_spa_defaults_in_lockstep_across_three_ways() -> None:
    defaults = get_default_config()["ui"]["spa"]
    for name in ("config.yaml", "src/corpus_kb/config.yaml"):
        raw: dict[str, Any] = yaml.safe_load((REPO / name).read_text(encoding="utf-8"))
        assert raw["ui"]["spa"] == defaults


def test_runtime_config_keys_pin_the_spa_keys() -> None:
    from corpus_kb._setup.validate_configs import RUNTIME_CONFIG_KEYS

    assert RUNTIME_CONFIG_KEYS["ui.spa.poll_ms"] is int
    assert RUNTIME_CONFIG_KEYS["ui.spa.request_timeout_s"] is float
    assert RUNTIME_CONFIG_KEYS["ui.spa.max_upload_mb"] is int


def test_validate_runtime_config_rejects_bad_spa_type() -> None:
    from corpus_kb._setup.validate_configs import validate_runtime_config

    cfg = _shipped_yaml()
    spa = cfg["ui"]["spa"]
    assert isinstance(spa, dict)
    spa["poll_ms"] = "fast"
    errors = validate_runtime_config(cfg)
    assert any("ui.spa.poll_ms" in error for error in errors)


def test_validate_runtime_config_accepts_the_shipped_block() -> None:
    from corpus_kb._setup.validate_configs import validate_runtime_config

    assert validate_runtime_config(_shipped_yaml()) == []


# ---------------------------------------------------------------------------
# DuckDB parity (issue #172): the single-user backend mounts the same SPA
# through the same mount_spa seam — one UI, one registration order.
# ---------------------------------------------------------------------------


class _FakeDuckDbGateway:
    """Connect-shaped gateway stub: startup never touches real files."""

    @classmethod
    async def connect(cls, conn_str: str) -> _FakeDuckDbGateway:
        del conn_str
        return cls()


async def test_duckdb_startup_mounts_the_spa_through_the_same_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """startup_duckdb reaches mount_spa exactly like the Postgres path."""
    from types import SimpleNamespace

    import corpus_kb.domain.application as domain_app
    import corpus_kb.rag.embedder as embedder_module
    import corpus_kb.storage.gateway as gateway_module
    import corpus_kb.ui.spa as spa_module
    from corpus_kb.server_wiring_duckdb import startup_duckdb

    calls: list[tuple[object, object]] = []

    def fake_mount_spa(app: FastAPI, config: dict[str, Any], dist: Path = DIST_DIR) -> bool:
        del dist
        calls.append((app, config))
        return True

    monkeypatch.setattr(gateway_module, "DuckDbGateway", _FakeDuckDbGateway)
    monkeypatch.setattr(
        domain_app,
        "get_app",
        lambda _conn_str, backend: SimpleNamespace(mapper=object(), recorder=object()),
    )
    monkeypatch.setattr(domain_app, "events_table_name", lambda _app: "events")
    monkeypatch.setattr(embedder_module, "create_embedder", lambda _cfg, _pool: object())
    monkeypatch.setattr(spa_module, "mount_spa", fake_mount_spa)

    cfg: dict[str, Any] = {"database": {"backend": "duckdb"}}
    services = await startup_duckdb(cfg, str(tmp_path / "single.duckdb"))

    assert len(calls) == 1, "the DuckDB path must route through mount_spa"
    mounted_app, mounted_cfg = calls[0]
    assert mounted_app is services["http_app"]
    assert mounted_cfg is cfg
    assert services["ui_mounted"] is True
    assert services["pool"] is None  # the DuckDB contract stays pool-free
