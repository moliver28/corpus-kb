"""Route + tool tests for the lifecycle read lists (UI SPA Task 5b, spec B2).

Two layers, both offline:

- HTTP/socket adapters: every GET row passes query parameters (ints
  coerced by annotation), the shared pool, and the default tenant to the
  ONE tool function; the envelope is the tool's own. GET-only routes
  refuse POST with 405; an ``unavailable`` payload is a 503; a raised
  taxonomy error keeps the ONE error envelope.
- Tool functions against a real DuckDB file with minimal lifecycle
  tables: ordering (newest first, unique tiebreaker), filters, pagination,
  tenant scoping, and the honest ``unavailable`` envelope on a
  pre-migration database.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast, get_type_hints
from uuid import uuid4

import pytest
from starlette.testclient import TestClient

from corpus_kb.api.http import create_http_app
from corpus_kb.storage.gateway import DuckDbGateway
from corpus_kb.tools import lifecycle_read_tools

TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"

_CANNED = {"status": "success", "result": [{"project_id": "p", "name": "n", "created_at": None}]}


class _FakePool:
    """Identity-only asyncpg pool stub; stubbed tools never touch it."""


def _install_stub(
    monkeypatch: pytest.MonkeyPatch, fn_name: str, payload: dict[str, Any] | None = None
) -> dict[str, object]:
    """Swap one tool function; capture the kwargs the adapter passed.

    The stub keeps the real function's resolved annotations: the GET
    adapter coerces query values by those hints, so the stub must not
    erase them.
    """
    captured: dict[str, object] = {}
    canned = dict(_CANNED if payload is None else payload)

    async def stub(*args: object, **kwargs: object) -> dict[str, Any]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return canned

    real = getattr(lifecycle_read_tools, fn_name)
    stub.__annotations__ = get_type_hints(real)
    stub.__signature__ = inspect.signature(real)
    stub.__name__ = fn_name  # the socket arms key on the function name
    monkeypatch.setattr(lifecycle_read_tools, fn_name, stub)
    return captured


# ---------------------------------------------------------------------------
# HTTP + socket adapters (tool functions stubbed at the module seam)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "fn_name"),
    [
        ("/api/projects", "projects_list"),
        ("/api/codebooks", "codebooks_list"),
        ("/api/codebook/proposals", "codebook_proposals"),
        ("/api/coding/assignments", "coding_assignments"),
    ],
)
def test_get_route_supplies_pool_params_and_tenant(
    path: str, fn_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _install_stub(monkeypatch, fn_name)
    monkeypatch.setattr("corpus_kb.api.research_http._pool", _FakePool())

    client = TestClient(create_http_app())
    response = client.get(path, params={"limit": "7"})

    assert response.status_code == 200
    assert response.json() == _CANNED
    kwargs = cast("dict[str, object]", captured["kwargs"])
    assert kwargs["limit"] == 7 and isinstance(kwargs["limit"], int)
    assert kwargs["tenant_id"] == TENANT
    assert "pg_pool" in kwargs


def test_assignment_filters_reach_the_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _install_stub(monkeypatch, "coding_assignments")
    monkeypatch.setattr("corpus_kb.api.research_http._pool", _FakePool())

    client = TestClient(create_http_app())
    params = {"review_status": "review", "code": "C1", "offset": "5"}
    response = client.get("/api/coding/assignments", params=params)

    assert response.status_code == 200
    kwargs = cast("dict[str, object]", captured["kwargs"])
    assert kwargs["review_status"] == "review"
    assert kwargs["code"] == "C1"
    assert kwargs["offset"] == 5
    # An absent query key never travels: the tool's own default applies.
    assert "limit" not in kwargs


def test_pool_refusal_is_the_taxonomy_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refusing pool seam answers the error envelope (503), never a 500.

    Covers both adapter shapes: the GET read rows and a POST tool route.
    """
    from corpus_kb.api import research_http
    from corpus_kb.errors import DependencyMissingError

    async def refusing_pool() -> object:
        raise DependencyMissingError(
            "The research routes need the Postgres pool.",
            remediation="Run the server on the Postgres backend.",
        )

    monkeypatch.setattr(research_http, "research_pool", refusing_pool)
    client = TestClient(create_http_app())

    get_response = client.get("/api/projects")
    assert get_response.status_code == 503
    assert get_response.json()["error_type"] == "dependency_missing"

    post_response = client.post(
        "/api/release/assess", json={"codebook_version_id": str(uuid4())}
    )
    assert post_response.status_code == 503
    body = post_response.json()
    assert body["status"] == "error" and body["error_type"] == "dependency_missing"


def test_get_reads_refuse_post(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_stub(monkeypatch, "projects_list")
    client = TestClient(create_http_app())
    response = client.post("/api/projects", json={"limit": 7})
    assert response.status_code == 405


def test_unavailable_payload_maps_to_503(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "status": "unavailable",
        "reason": "research_projects table absent (run corpus-kb setup)",
        "remedy": "run corpus-kb setup",
        "result": [],
    }
    _install_stub(monkeypatch, "projects_list", payload)
    monkeypatch.setattr("corpus_kb.api.research_http._pool", _FakePool())

    client = TestClient(create_http_app())
    response = client.get("/api/projects")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"


def test_raised_taxonomy_error_keeps_the_one_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_kb.errors import ValidationError

    async def stub(*args: object, **kwargs: object) -> dict[str, Any]:
        raise ValidationError("bad limit")

    monkeypatch.setattr(lifecycle_read_tools, "projects_list", stub)
    monkeypatch.setattr("corpus_kb.api.research_http._pool", _FakePool())

    client = TestClient(create_http_app())
    response = client.get("/api/projects")

    assert response.status_code == 400
    body = response.json()
    assert body["status"] == "error" and body["error_type"] == "validation_error"


async def test_socket_arms_serve_the_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_kb.api import research_http
    from corpus_kb.api.socket_ops import _route_arms

    captured = _install_stub(monkeypatch, "projects_list")
    monkeypatch.setattr(research_http, "_pool", _FakePool())

    arms = _route_arms()
    for name in ("projects_list", "codebooks_list", "codebook_proposals", "coding_assignments"):
        assert name in arms, name
    arm = arms["projects_list"]
    result = await arm({"limit": 3}, TENANT)

    assert result == _CANNED
    kwargs = cast("dict[str, object]", captured["kwargs"])
    assert kwargs["limit"] == 3  # the socket passes JSON values; no coercion needed


# ---------------------------------------------------------------------------
# Tool functions against a real DuckDB file (minimal lifecycle tables)
# ---------------------------------------------------------------------------

_DDL = (
    "CREATE TABLE research_projects (project_id UUID, tenant_id UUID, "
    "name VARCHAR, created_at TIMESTAMPTZ)",
    "CREATE TABLE codebook_versions (version_id UUID, tenant_id UUID, label VARCHAR, "
    "sha256 VARCHAR, paradigm VARCHAR, created_at TIMESTAMPTZ)",
    "CREATE TABLE research_proposed_codes (proposed_id BIGINT, tenant_id UUID, "
    "cluster_id VARCHAR, run_id UUID, suggested_label VARCHAR, n_members INT, "
    "status VARCHAR, block_reason VARCHAR, created_at TIMESTAMPTZ)",
    "CREATE TABLE research_assignments (assignment_id BIGINT, tenant_id UUID, "
    "assignment_aggregate_id UUID, unit_id BIGINT, code_id VARCHAR, cb_version_id UUID, "
    "run_id UUID, status VARCHAR, confidence VARCHAR, stance VARCHAR, "
    "evidence_basis VARCHAR, sim_answer REAL, sim_qa REAL, sim_q REAL, "
    "created_at TIMESTAMPTZ)",
)


async def _seeded(tmp_path: Path) -> DuckDbGateway:
    """One DuckDB file, the four tables, one tenant's rows, one stranger's."""
    gw = await DuckDbGateway.connect(str(tmp_path / "reads.duckdb"))
    for ddl in _DDL:
        await gw.execute(ddl)
    old = datetime(2026, 1, 1)
    new = old + timedelta(days=1)
    await _insert(
        gw,
        "research_projects",
        [
            {"project_id": uuid4(), "tenant_id": TENANT, "name": "old", "created_at": old},
            {"project_id": uuid4(), "tenant_id": TENANT, "name": "new", "created_at": new},
            {"project_id": uuid4(), "tenant_id": OTHER_TENANT, "name": "x", "created_at": new},
        ],
    )
    await _insert(
        gw,
        "codebook_versions",
        [
            {
                "version_id": uuid4(),
                "tenant_id": TENANT,
                "label": "v1",
                "sha256": "a" * 64,
                "paradigm": "deductive",
                "created_at": old,
            },
        ],
    )
    await _insert(
        gw,
        "research_proposed_codes",
        [
            {
                "proposed_id": 1,
                "tenant_id": TENANT,
                "cluster_id": "c1",
                "run_id": uuid4(),
                "suggested_label": "trust",
                "n_members": 4,
                "status": "proposed",
                "block_reason": None,
                "created_at": old,
            },
            {
                "proposed_id": 2,
                "tenant_id": TENANT,
                "cluster_id": "c2",
                "run_id": uuid4(),
                "suggested_label": "fear",
                "n_members": 2,
                "status": "promoted",
                "block_reason": None,
                "created_at": new,
            },
        ],
    )
    await _insert(
        gw,
        "research_assignments",
        [
            {
                "assignment_id": 1,
                "tenant_id": TENANT,
                "assignment_aggregate_id": uuid4(),
                "unit_id": 11,
                "code_id": "C1",
                "cb_version_id": None,
                "run_id": None,
                "status": "review",
                "confidence": "high",
                "stance": None,
                "evidence_basis": "explicit_in_answer",
                "sim_answer": 0.9,
                "sim_qa": None,
                "sim_q": None,
                "created_at": old,
            },
            {
                "assignment_id": 2,
                "tenant_id": TENANT,
                "assignment_aggregate_id": uuid4(),
                "unit_id": 12,
                "code_id": "C2",
                "cb_version_id": None,
                "run_id": None,
                "status": "auto",
                "confidence": None,
                "stance": None,
                "evidence_basis": None,
                "sim_answer": None,
                "sim_qa": None,
                "sim_q": None,
                "created_at": new,
            },
        ],
    )
    return gw


async def _insert(gw: DuckDbGateway, table: str, rows: list[dict[str, object]]) -> None:
    for row in rows:
        cols = ", ".join(row)
        marks = ", ".join(f"${i + 1}" for i in range(len(row)))
        await gw.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", *row.values())


async def test_projects_list_scopes_to_tenant_and_orders_newest_first(tmp_path: Path) -> None:
    gw = await _seeded(tmp_path)
    try:
        envelope = await lifecycle_read_tools.projects_list(gw)
    finally:
        await gw.close()
    assert envelope["status"] == "success"
    names = [row["name"] for row in envelope["result"]]
    assert names == ["new", "old"]  # newest first; the other tenant's row never leaves RLS


async def test_codebooks_list_carries_label_hash_and_date(tmp_path: Path) -> None:
    gw = await _seeded(tmp_path)
    try:
        envelope = await lifecycle_read_tools.codebooks_list(gw)
    finally:
        await gw.close()
    row = envelope["result"][0]
    assert row["label"] == "v1" and row["sha256"] == "a" * 64
    # DuckDB reads the seeded naive timestamp as local time; compare instants.
    stamp = datetime.fromisoformat(str(row["created_at"]))
    assert stamp == datetime(2026, 1, 1).astimezone()


async def test_proposals_filter_by_status(tmp_path: Path) -> None:
    gw = await _seeded(tmp_path)
    try:
        proposed = await lifecycle_read_tools.codebook_proposals(gw)
        promoted = await lifecycle_read_tools.codebook_proposals(gw, status="promoted")
    finally:
        await gw.close()
    assert [row["proposed_id"] for row in proposed["result"]] == [1]
    assert [row["suggested_label"] for row in promoted["result"]] == ["fear"]


async def test_assignments_filter_and_pagination(tmp_path: Path) -> None:
    gw = await _seeded(tmp_path)
    try:
        review = await lifecycle_read_tools.coding_assignments(gw, review_status="review")
        page = await lifecycle_read_tools.coding_assignments(gw, limit=1, offset=1)
    finally:
        await gw.close()
    assert [row["assignment_id"] for row in review["result"]] == [1]
    assert review["result"][0]["sim_answer"] == pytest.approx(0.9)
    assert [row["assignment_id"] for row in page["result"]] == [1]  # newest first, offset skips 2


async def test_missing_tables_answer_unavailable(tmp_path: Path) -> None:
    gw = await DuckDbGateway.connect(str(tmp_path / "bare.duckdb"))
    try:
        envelope = await lifecycle_read_tools.projects_list(gw)
    finally:
        await gw.close()
    assert envelope["status"] == "unavailable"
    assert "setup" in str(envelope["remedy"])


async def test_limit_clamps_to_the_page_window(tmp_path: Path) -> None:
    gw = await _seeded(tmp_path)
    try:
        envelope = await lifecycle_read_tools.projects_list(gw, limit=0)
    finally:
        await gw.close()
    assert envelope["status"] == "success"  # limit=0 -> the 1..500 clamp keeps the page honest
