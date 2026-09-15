"""Validate the repo-root compose files.

Docker is not required to run these tests. We use PyYAML to verify that the
compose files are well-formed and contain the expected services/volumes. Live
``docker compose up`` verification is deferred to the manual QA phase (F3).
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


def test_ollama_service_not_in_default_compose(compose_path: Path) -> None:
    """Ollama must not be part of the default compose stack."""
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = data["services"]
    assert "ollama" not in services


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
    """Host ports used by services do not overlap."""
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    host_ports: set[int] = set()
    for svc in data["services"].values():
        for port in svc.get("ports", []):
            parts = str(port).split(":")
            # Accept 5433 or 127.0.0.1:5433:5432
            host_port = parts[-2] if len(parts) > 1 else parts[0]
            if host_port.isdigit():
                host_ports.add(int(host_port))
    assert len(host_ports) == 1, f"unexpected host ports: {host_ports}"
    assert 5433 in host_ports
