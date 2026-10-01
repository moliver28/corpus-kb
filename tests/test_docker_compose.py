"""Validate the repo-root docker-compose.yml file.

Docker is not required to run these tests. We use PyYAML to verify that the
compose file is well-formed and contains the expected services/volumes. Live
`docker compose up` verification is deferred to the manual QA phase (F3).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def compose_path() -> Path:
    """Return the absolute path to the repo-root docker-compose.yml.

    Path layout:
        repo-root/docker-compose.yml
        repo-root/tests/test_docker_compose.py
    So the compose file is two levels above this test file.
    """
    return Path(__file__).resolve().parent.parent / "docker-compose.yml"


def test_compose_file_exists(compose_path: Path) -> None:
    """The docker-compose.yml must exist at the repo root."""
    assert compose_path.exists(), f"docker-compose.yml not found at {compose_path}"


def test_compose_file_parses(compose_path: Path) -> None:
    """The compose file must be valid YAML with a services block."""
    raw = compose_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    assert isinstance(data, dict)
    assert "services" in data
    assert "volumes" in data


def test_postgres_service_configured(compose_path: Path) -> None:
    """Postgres service uses the project default credentials and port."""
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = data["services"]
    assert "postgres" in services
    pg = services["postgres"]
    assert pg["build"]["context"] == "docker/postgres"
    env = pg["environment"]
    assert env["POSTGRES_USER"] == "corpus_user"
    assert env["POSTGRES_PASSWORD"] == "corpus_pass"
    assert env["POSTGRES_DB"] == "corpus_kb"
    assert any("5433:5432" in str(p) for p in pg["ports"])
    assert "healthcheck" in pg


def test_ollama_service_configured(compose_path: Path) -> None:
    """Ollama service exposes the default port and has a healthcheck."""
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = data["services"]
    assert "ollama" in services
    ollama = services["ollama"]
    assert "ollama/ollama:latest" in ollama["image"]
    assert any("11434:11434" in str(p) for p in ollama["ports"])
    assert "healthcheck" in ollama


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
    assert len(host_ports) == 2, f"unexpected host ports: {host_ports}"
    assert 5433 in host_ports
    assert 11434 in host_ports
