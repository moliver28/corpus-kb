"""Doctor configuration-truth checks: env vars + embedding-dimension guard.

Hermetic: connection doubles, no database. The dimension check pins the
empirical pgvector contract (typmod width is enforced exactly; the shipped
vector(4096) column rejects every non-4096 insert).
"""

from __future__ import annotations

from typing import Any

from corpus_kb._setup.doctor_env import (
    dimension_guard_check,
    env_var_check,
    unknown_env_vars,
)
from corpus_kb._setup.doctor_research import STATUS_FAIL, STATUS_OK, STATUS_WARN

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


class _Tx:
    async def __aenter__(self) -> _HistConn:
        return self._conn

    async def __aexit__(self, exc_type: object, *exc: object) -> bool:
        return False

    def __init__(self, conn: _HistConn) -> None:
        self._conn = conn


class _HistConn:
    """Connection double: catalog probe + dimensions histogram answers."""

    def __init__(
        self,
        typmod_row: dict[str, object] | None,
        histogram: list[dict[str, object]] | None = None,
    ) -> None:
        self.typmod_row = typmod_row
        self.histogram = histogram or []
        self.guc_set = False

    async def execute(self, sql: str, *args: object) -> str:
        if "set_config" in sql:
            self.guc_set = True
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        if "atttypmod" in sql:
            return self.typmod_row
        return None

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        if "GROUP BY dimensions" in sql:
            return self.histogram
        return []

    def transaction(self) -> _Tx:
        return _Tx(self)


# --- env-var truth check -----------------------------------------------------


def test_unknown_env_vars_finds_unmapped_names() -> None:
    env = {
        "CORPUS_KB_STORAGE_PATH": "/tmp",  # removed mapping: must surface
        "CORPUS_KB_DATABASE_URL": "postgres://x",  # known: never surfaces
        "CORPUS_KB_SNAPSHOT_PERIOD": "100",  # direct reader: known
        "PATH": "/usr/bin",  # different prefix: ignored
    }
    assert unknown_env_vars(env) == ["CORPUS_KB_STORAGE_PATH"]


def test_env_var_check_ok_when_clean() -> None:
    check = env_var_check({"CORPUS_KB_PORT": "9000"})
    assert check.status == STATUS_OK


def test_env_var_check_warns_with_names_and_fix() -> None:
    env = {"CORPUS_KB_INSTALL_PROFILE": "performance", "CORPUS_KB_GRAPH_PATH": "/x"}
    check = env_var_check(env)
    assert check.status == STATUS_WARN
    assert "CORPUS_KB_INSTALL_PROFILE" in check.detail
    assert "CORPUS_KB_GRAPH_PATH" in check.detail
    assert "docs/ADMIN.md" in check.fix


# --- dimension guard check ---------------------------------------------------


async def test_dimension_guard_fails_on_width_mismatch() -> None:
    conn = _HistConn({"width": 4096, "typ": "vector(4096)"})
    config: dict[str, Any] = {"embedding": {"model": "nomic-embed-text", "dimensions": 768}}
    check = await dimension_guard_check(conn, config)
    assert check.status == STATUS_FAIL
    assert "4096" in check.detail and "768" in check.detail
    assert "embedding.model" in check.fix
    assert "embedding.dimensions" in check.fix


async def test_dimension_guard_ok_when_matching() -> None:
    conn = _HistConn({"width": 768, "typ": "vector(768)"}, [{"dimensions": 768, "n": 3}])
    config: dict[str, Any] = {"embedding": {"model": "nomic-embed-text", "dimensions": 768}}
    check = await dimension_guard_check(conn, config)
    assert check.status == STATUS_OK
    assert conn.guc_set  # histogram read ran under the tenant GUC


async def test_dimension_guard_warns_on_stale_rows() -> None:
    conn = _HistConn(
        {"width": 4096, "typ": "vector(4096)"},
        [{"dimensions": 4096, "n": 5}, {"dimensions": 768, "n": 2}],
    )
    config: dict[str, Any] = {"embedding": {"model": "qwen3-embedding", "dimensions": 4096}}
    check = await dimension_guard_check(conn, config)
    assert check.status == STATUS_WARN
    assert "--reembed" in check.fix


async def test_dimension_guard_warns_when_column_missing() -> None:
    conn = _HistConn(None)
    check = await dimension_guard_check(conn, {})
    assert check.status == STATUS_WARN
    assert "not found" in check.detail


async def test_dimension_guard_warns_on_untypmodded_column() -> None:
    conn = _HistConn({"width": -1, "typ": "vector"})
    check = await dimension_guard_check(conn, {"embedding": {"dimensions": 768}})
    assert check.status == STATUS_WARN
    assert "no fixed width" in check.detail


async def test_dimension_guard_survives_histogram_failure() -> None:
    """A broken histogram read degrades to the plain typmod verdict."""

    class _BrokenHistConn(_HistConn):
        async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
            raise RuntimeError("histogram unavailable")

    conn = _BrokenHistConn({"width": 768, "typ": "vector(768)"})
    check = await dimension_guard_check(conn, {"embedding": {"dimensions": 768}})
    assert check.status == STATUS_OK
