"""Validate the repo-root compose files.

Docker is not required to run these tests. We use PyYAML to verify that the
compose files are well-formed and contain the expected services/volumes.
Live ``docker compose up`` verification runs in CI through the
``docker-smoke`` gate (poe target, ci.yml / docker): it builds the app
image, boots the ``app`` profile stack, and exercises the server.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def compose_path() -> Path:
    """Return the absolute path to the repo-root compose.yaml.

    Path layout:
        repo-root/compose.yaml
        repo-root/tests/test_docker_compose.py
    So the compose file is two levels above this test file.
    """
    return Path(__file__).resolve().parent.parent / "compose.yaml"


@pytest.fixture
def build_local_path() -> Path:
    """Return the absolute path to the build-local compose override."""
    return Path(__file__).resolve().parent.parent / "compose.build-local.yaml"


def test_compose_file_exists(compose_path: Path) -> None:
    """compose.yaml must exist at the repo root."""
    assert compose_path.exists(), f"compose.yaml not found at {compose_path}"


def test_legacy_docker_compose_yml_removed(compose_path: Path) -> None:
    """The old docker-compose.yml name must not be present."""
    legacy_path = compose_path.parent / "docker-compose.yml"
    assert not legacy_path.exists(), f"legacy docker-compose.yml still exists at {legacy_path}"


def test_compose_file_parses(compose_path: Path) -> None:
    """The compose file must be valid YAML with a services block."""
    raw = compose_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    assert isinstance(data, dict)
    assert "services" in data
    assert "volumes" in data


def test_postgres_service_uses_prebuilt_image(compose_path: Path) -> None:
    """Postgres service uses the pinned pre-built image by default."""
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = data["services"]
    assert "postgres" in services
    pg = services["postgres"]
    assert "build" not in pg, "default compose must use a pre-built image, not build"
    assert pg["image"] == "ghcr.io/moliver28/corpus-kb-postgres:0.1.0-pg17"
    env = pg["environment"]
    assert env["POSTGRES_USER"] == "corpus_user"
    assert env["POSTGRES_PASSWORD"] == "corpus_pass"
    assert env["POSTGRES_DB"] == "corpus_kb"
    assert any("5433:5432" in str(p) for p in pg["ports"])
    assert "healthcheck" in pg


def test_default_stack_is_postgres_only(compose_path: Path) -> None:
    """Only Postgres runs without a profile.

    Ollama and the app server are opt-in services (``ollama`` / ``app``
    profiles), so a bare ``docker compose up`` keeps the historical
    Postgres-only behavior byte-for-byte.
    """
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = data["services"]
    assert "postgres" in services
    assert "postgres" not in (services["postgres"].get("profiles") or [])
    default_active = {name for name, svc in services.items() if not svc.get("profiles")}
    assert default_active == {"postgres"}


def test_app_service_builds_behind_profile(compose_path: Path) -> None:
    """The app service builds the repo image behind the ``app`` profile."""
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    app = data["services"]["app"]
    assert app["profiles"] == ["app"]
    assert app["build"]["context"] == "."
    assert app["build"]["dockerfile"] == "docker/app/Dockerfile"
    assert app["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert any(str(p).endswith(":8010") for p in app["ports"])
    assert app["restart"] == "unless-stopped"


def test_app_publish_bind_defaults_to_loopback(compose_path: Path) -> None:
    """The app port publishes on host loopback unless CORPUS_KB_PUBLISH_BIND says otherwise.

    The HTTP API has no login, so the default publish must not listen on
    all interfaces. LAN access (a NAS serving ``http://<nas-ip>:8010``)
    is an explicit act: set ``CORPUS_KB_PUBLISH_BIND=0.0.0.0``.
    """
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    app = data["services"]["app"]
    assert "${CORPUS_KB_PUBLISH_BIND:-127.0.0.1}:8010:8010" in [str(p) for p in app["ports"]]


def test_app_environment_wires_the_host_override(compose_path: Path) -> None:
    """CORPUS_KB_HOST passes into the app container as a registered variable.

    The entrypoint reads it for the ``--host`` bind. Without the
    environment row a project-level variable never reaches the container.
    """
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    env = data["services"]["app"]["environment"]
    assert env["CORPUS_KB_HOST"] == "${CORPUS_KB_HOST:-0.0.0.0}"


def test_ollama_service_profile_only_no_host_port(compose_path: Path) -> None:
    """The ollama service is opt-in and stays inside the compose network."""
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    ollama = data["services"]["ollama"]
    assert ollama["profiles"] == ["ollama"]
    assert "ports" not in ollama
    assert ollama["image"].startswith("ollama/ollama:")


def test_build_local_override_exists(build_local_path: Path) -> None:
    """The build-local override file must exist at the repo root."""
    assert build_local_path.exists(), f"compose.build-local.yaml not found at {build_local_path}"


def test_build_local_override_parses(build_local_path: Path) -> None:
    """The build-local override must be valid YAML with a postgres service."""
    data = yaml.safe_load(build_local_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "services" in data
    assert "postgres" in data["services"]


def test_build_local_override_uses_local_dockerfile(build_local_path: Path) -> None:
    """The build-local override builds from docker/postgres/Dockerfile."""
    data = yaml.safe_load(build_local_path.read_text(encoding="utf-8"))
    pg = data["services"]["postgres"]
    assert "build" in pg
    assert pg["build"]["context"] == "docker/postgres"
    assert pg["build"]["dockerfile"] == "Dockerfile"


def test_no_port_conflicts(compose_path: Path) -> None:
    """Host ports used by services are exactly the two declared ones.

    Postgres stays on loopback 5433 (the compose-contract port); the app
    server serves 8010 on host loopback (see the publish-bind test).
    """
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    host_ports: set[int] = set()
    for svc in data["services"].values():
        for port in svc.get("ports", []):
            # The app publish bind is templated; resolve its loopback
            # default so the effective host-port set stays exact.
            text = str(port).replace("${CORPUS_KB_PUBLISH_BIND:-127.0.0.1}", "127.0.0.1")
            parts = text.split(":")
            # Accept 5433, 8010:8010, or 127.0.0.1:5433:5432
            host_port = parts[-2] if len(parts) > 1 else parts[0]
            if host_port.isdigit():
                host_ports.add(int(host_port))
    assert host_ports == {5433, 8010}, f"unexpected host ports: {host_ports}"
