"""Tests for the session preflight guards in tests/conftest.py.

The guards decide skip versus run for capability-gated tests. Each probe
is cached, so these tests clear the cache around their own scenario.
"""

from __future__ import annotations

import pytest

from tests import conftest


@pytest.fixture(autouse=True)
def _clean_caches() -> None:
    conftest._postgres_superuser_reason.cache_clear()
    yield
    conftest._postgres_superuser_reason.cache_clear()


def test_superuser_probe_none_on_this_host_when_postgres_works() -> None:
    """The admin role connects on a host that matches the harness contract."""
    if not conftest._postgres_reachable():
        pytest.skip("Postgres not reachable on this host")
    assert conftest._postgres_superuser_reason() is None


def test_superuser_probe_names_the_env_vars_when_the_role_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed admin connect yields a reason that guides setup (issue #358)."""
    import research_db

    monkeypatch.setattr(research_db, "SUPER_DSN", "postgresql://nobody@localhost:9/nope")
    reason = conftest._postgres_superuser_reason()
    assert reason is not None
    assert "POSTGRES_SUPERUSER" in reason
    assert "CORPUS_KB_DATABASE_URL" in reason
    assert "docs/DEVELOPMENT.md" in reason
