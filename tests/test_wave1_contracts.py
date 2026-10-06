"""Wave-1 integration contracts for the coding subsystem.

Pins the three re-derived integration points end to end, fully offline
(no live Postgres, no live Ollama):

(a) Route contract — representative request/response SHAPES for
    POST /api/embed (plain + instructed), POST /api/coding/calibrate-floors,
    POST /api/coding/saturation, derived from api/http.py and the ported
    handler modules. The routes parse plain dicts and wrap any parse error
    in a 400 JSONResponse; the calibrate-floors backend ValueError path is
    deliberately a 200 with {"status": "error", ...} (see
    CodingHandler.handle_calibrate_floors).
(b) tenant_conn contract — every coding DB path acquires its connection
    through corpus_kb.storage.tenant_conn.tenant_connection (identity check
    on each module's bound symbol, plus spies on the live call sites).
(c) Embedder Protocol contract — the instructed embed paths plumb the
    exact QUERY_INSTRUCTION_PREFIX to whatever embedder sits behind them.
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from starlette.testclient import TestClient

from corpus_kb.api.http import create_http_app
from corpus_kb.coding import floors_calibration, pooling, saturation_query
from corpus_kb.coding.floors import calibrate as calibrate_floors_stats
from corpus_kb.coding.saturation import isr, run_stop
from corpus_kb.handlers import coding_handler as coding_handler_module
from corpus_kb.handlers.coding_handler import (
    CodingHandler,
    reset_coding_handler,
    set_coding_handler,
)
from corpus_kb.rag import embedder as embedder_module
from corpus_kb.rag.embedder import QUERY_INSTRUCTION_PREFIX
from corpus_kb.storage import tenant_conn as tenant_conn_module

_TENANT = "00000000-0000-0000-0000-000000000001"

# Real success-shape key sets, derived from the return literals in
# floors_calibration.calibrate_code_floors and saturation_query.compute_saturation.
_CALIBRATE_KEYS = {
    "status",
    "code_id",
    "pool_floor",
    "residual_floor",
    "n_positive_used",
    "n_negative_used",
    "n_positive_requested",
    "n_negative_requested",
    "fallback_applied",
}
_SATURATION_KEYS = {
    "status",
    "unique_codes",
    "total_applications",
    "isr",
    "batch_id",
    "new_codes",
    "base_unique",
    "should_stop",
}

# Every module with a coding DB path must bind THE master tenant_connection.
_CODING_DB_MODULES = (
    "corpus_kb.coding.codebook_loader",
    "corpus_kb.coding.coder_dispatch",
    "corpus_kb.coding.floors_calibration",
    "corpus_kb.coding.keyword_governance_report",
    "corpus_kb.coding.pooling",
    "corpus_kb.coding.saturation_query",
    "corpus_kb.coding.speaker_role_projection",
    "corpus_kb.handlers.coding_handler",
)


# ============================================================================
# Test doubles
# ============================================================================


class _FakeConn:
    """Asyncpg connection double: queued results, executed-SQL recording."""

    def __init__(
        self,
        fetchval_results: list[object] | None = None,
        fetch_results: list[list[dict[str, object]]] | None = None,
        fetchrow_results: list[dict[str, object] | None] | None = None,
    ) -> None:
        self.fetchval_results = list(fetchval_results or [])
        self.fetch_results = list(fetch_results or [])
        self.fetchrow_results = list(fetchrow_results or [])
        self.executed: list[str] = []

    async def execute(self, sql: str, *args: object) -> str:
        self.executed.append(sql)
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        return self.fetch_results.pop(0) if self.fetch_results else []

    async def fetchval(self, sql: str, *args: object) -> object:
        return self.fetchval_results.pop(0) if self.fetchval_results else None

    async def fetchrow(self, sql: str, *args: object) -> object:
        return self.fetchrow_results.pop(0) if self.fetchrow_results else None


def _spy_tenant_connection(calls: list[tuple[object, UUID]], conn: _FakeConn) -> object:
    """A tenant_connection stand-in that records (pool, tenant_id) per use."""

    @asynccontextmanager
    async def fake(pool: object, tenant_id: UUID) -> AsyncIterator[_FakeConn]:
        calls.append((pool, tenant_id))
        yield conn

    return fake


class _BackendSpy:
    """Stands in for a coding backend function; returns/raises on call."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


_EMBED_CALLS: list[str] = []


class _RecordingEmbedder:
    """OllamaEmbedder double for the /api/embed route: records payloads."""

    dimensions = 4

    def embed(self, text: str) -> list[float]:
        _EMBED_CALLS.append(text)
        return [0.5, -0.5, 0.25, 0.75]


class _CapturingEmbedder:
    """Handler-side embedder double that records the exact text embedded."""

    def __init__(self) -> None:
        self.embedded: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.embedded.append(text)
        return [0.25, 0.5, 0.75, 1.0]


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def embed_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App whose OllamaEmbedder construction is replaced by a recorder."""
    _EMBED_CALLS.clear()
    monkeypatch.setattr(embedder_module, "OllamaEmbedder", _RecordingEmbedder)
    return TestClient(create_http_app())


@pytest.fixture
def coding_client() -> TestClient:
    """App with the real coding-handler singleton set (no DB is touched)."""
    set_coding_handler(CodingHandler(pool=object(), embedder=None))
    try:
        yield TestClient(create_http_app())
    finally:
        reset_coding_handler()


# ============================================================================
# (a) Route contract — POST /api/embed
# ============================================================================


class TestEmbedRouteContract:
    def test_plain_mode_response_shape(self, embed_client: TestClient) -> None:
        response = embed_client.post("/api/embed", json={"text": "hello world"})
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"vector", "instructed", "dimensions"}
        assert body["instructed"] is False
        assert body["dimensions"] == len(body["vector"]) == 4
        assert all(isinstance(v, float) for v in body["vector"])
        assert _EMBED_CALLS == ["hello world"]

    def test_instructed_mode_plumbs_prefix_to_embedder(self, embed_client: TestClient) -> None:
        query = "why do teams underreport incidents"
        response = embed_client.post("/api/embed", json={"text": query, "instructed": True})
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"vector", "instructed", "dimensions"}
        assert body["instructed"] is True
        assert len(_EMBED_CALLS) == 1
        assert _EMBED_CALLS[0] == QUERY_INSTRUCTION_PREFIX + query

    def test_missing_text_is_400(self, embed_client: TestClient) -> None:
        response = embed_client.post("/api/embed", json={})
        assert response.status_code == 400
        assert response.json() == {"error": "text is required"}
        assert _EMBED_CALLS == []


# ============================================================================
# (a) Route contract — POST /api/coding/calibrate-floors
# ============================================================================


class TestCalibrateFloorsRouteContract:
    def test_valid_payload_returns_success_shape(
        self, coding_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = _BackendSpy(
            {
                "status": "success",
                "code_id": "trust_in_management",
                "pool_floor": 0.62,
                "residual_floor": 0.31,
                "n_positive_used": 12,
                "n_negative_used": 8,
                "n_positive_requested": 12,
                "n_negative_requested": 8,
                "fallback_applied": False,
            }
        )
        monkeypatch.setattr(coding_handler_module, "calibrate_code_floors", spy)
        version_id, chunk_id = uuid4(), uuid4()
        response = coding_client.post(
            "/api/coding/calibrate-floors",
            json={
                "tenant_id": _TENANT,
                "code_id": "trust_in_management",
                "codebook_version_id": str(version_id),
                "positive_chunk_ids": [str(chunk_id)],
                "negative_chunk_ids": [],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body) == _CALIBRATE_KEYS
        assert body["status"] == "success"
        assert body["code_id"] == "trust_in_management"
        # The route must parse ids into UUIDs before the handler sees them.
        args, kwargs = spy.calls[0]
        assert args[1] == UUID(_TENANT)
        assert args[2] == "trust_in_management"
        assert args[3] == version_id
        assert args[4] == [chunk_id]
        assert args[5] == []
        assert kwargs == {"min_pos": 10, "global_fallback": None}

    def test_missing_code_id_is_400(self, coding_client: TestClient) -> None:
        response = coding_client.post(
            "/api/coding/calibrate-floors",
            json={"codebook_version_id": str(uuid4())},
        )
        assert response.status_code == 400
        body = response.json()
        assert body["status"] == "error"
        assert "code_id" in body["error"]

    def test_backend_valueerror_is_200_error_body(
        self, coding_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            coding_handler_module,
            "calibrate_code_floors",
            _BackendSpy(ValueError("code_id 'x' has no probe_vector")),
        )
        response = coding_client.post(
            "/api/coding/calibrate-floors",
            json={
                "code_id": "x",
                "codebook_version_id": str(uuid4()),
                "positive_chunk_ids": [],
                "negative_chunk_ids": [],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body == {
            "status": "error",
            "error": "code_id 'x' has no probe_vector",
            "code_id": "x",
        }


# ============================================================================
# (a) Route contract — POST /api/coding/saturation
# ============================================================================


class TestSaturationRouteContract:
    def test_valid_payload_returns_success_shape(
        self, coding_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = _BackendSpy(
            {
                "status": "success",
                "unique_codes": 7,
                "total_applications": 120,
                "isr": 0.12,
                "batch_id": "run-1",
                "new_codes": 2,
                "base_unique": 5,
                "should_stop": False,
            }
        )
        monkeypatch.setattr(coding_handler_module, "compute_saturation", spy)
        response = coding_client.post(
            "/api/coding/saturation",
            json={"batch_id": "run-1", "threshold": 0.05, "min_samples": 50},
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body) == _SATURATION_KEYS
        assert body["status"] == "success"
        assert body["batch_id"] == "run-1"
        args, kwargs = spy.calls[0]
        assert args[1] == UUID(_TENANT)
        assert kwargs == {"batch_id": "run-1", "threshold": 0.05, "min_samples": 50}

    @pytest.mark.parametrize(
        "payload",
        [
            {"threshold": "not-a-number"},
            {"min_samples": "fifty"},
            {"tenant_id": "not-a-uuid"},
        ],
    )
    def test_malformed_payload_is_400(
        self, coding_client: TestClient, payload: dict[str, object]
    ) -> None:
        response = coding_client.post("/api/coding/saturation", json=payload)
        assert response.status_code == 400
        assert response.json()["status"] == "error"


# ============================================================================
# (b) tenant_conn contract — live call sites route through the master helper
# ============================================================================


class TestTenantConnBindingContract:
    @pytest.mark.parametrize("module_name", _CODING_DB_MODULES)
    def test_module_binds_master_tenant_connection(self, module_name: str) -> None:
        module = importlib.import_module(module_name)
        assert hasattr(module, "tenant_connection"), (
            f"{module_name} does not bind tenant_connection"
        )
        assert module.tenant_connection is tenant_conn_module.tenant_connection, (
            f"{module_name} binds a copy, not master's tenant_connection"
        )


class TestSaturationModuleTenantConn:
    async def test_routes_through_tenant_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[tuple[object, UUID]] = []
        conn = _FakeConn(fetchval_results=[7, 120])
        monkeypatch.setattr(
            saturation_query, "tenant_connection", _spy_tenant_connection(calls, conn)
        )
        pool, tenant = object(), uuid4()
        result = await saturation_query.compute_saturation(pool, tenant)
        assert len(calls) == 1 and calls[0][0] is pool and calls[0][1] is tenant
        assert set(result) == _SATURATION_KEYS
        assert result["status"] == "success"
        assert result["unique_codes"] == 7
        assert result["total_applications"] == 120
        assert result["isr"] == isr(7, 120)
        assert result["batch_id"] is None
        assert result["new_codes"] is None
        assert result["base_unique"] is None
        assert result["should_stop"] is None

    async def test_batch_window_stop_rule_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[tuple[object, UUID]] = []
        window = {
            "start": datetime(2026, 1, 1),
            "stop": datetime(2026, 1, 2),
        }
        conn = _FakeConn(fetchval_results=[7, 120, 2], fetchrow_results=[window])
        monkeypatch.setattr(
            saturation_query, "tenant_connection", _spy_tenant_connection(calls, conn)
        )
        result = await saturation_query.compute_saturation(
            object(), uuid4(), batch_id="run-1", threshold=0.05, min_samples=50
        )
        assert result["batch_id"] == "run-1"
        assert result["new_codes"] == 2
        assert result["base_unique"] == 5
        assert result["should_stop"] == run_stop(
            new_codes=2, base_unique=5, threshold=0.05, min_samples=50
        )


class TestFloorsCalibrationModuleTenantConn:
    async def test_routes_through_tenant_connection_and_real_shape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[object, UUID]] = []
        chunk_id = uuid4()
        conn = _FakeConn(
            fetchval_results=[[1.0, 0.0]],
            fetch_results=[[{"chunk_id": chunk_id, "vector": [1.0, 0.0]}]],
        )
        monkeypatch.setattr(
            floors_calibration, "tenant_connection", _spy_tenant_connection(calls, conn)
        )
        pool, tenant, version = object(), uuid4(), uuid4()
        result = await floors_calibration.calibrate_code_floors(
            pool,
            tenant,
            "trust_in_management",
            version,
            [chunk_id],
            [],
            min_pos=1,
        )
        assert len(calls) == 1 and calls[0][0] is pool and calls[0][1] is tenant
        assert set(result) == _CALIBRATE_KEYS
        assert result["status"] == "success"
        assert result["code_id"] == "trust_in_management"
        assert result["n_positive_used"] == 1
        assert result["n_negative_used"] == 0
        assert result["fallback_applied"] is False
        expected = calibrate_floors_stats([1.0], [], min_pos=1, global_fallback=None)
        assert (result["pool_floor"], result["residual_floor"]) == expected
        assert conn.executed, "the floors UPDATE must run inside the scoped txn"

    async def test_missing_probe_vector_raises_valueerror(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[object, UUID]] = []
        conn = _FakeConn(fetchval_results=[None])
        monkeypatch.setattr(
            floors_calibration, "tenant_connection", _spy_tenant_connection(calls, conn)
        )
        with pytest.raises(ValueError, match="probe_vector"):
            await floors_calibration.calibrate_code_floors(object(), uuid4(), "x", uuid4(), [], [])


class TestPoolingModuleTenantConn:
    async def test_all_pooling_paths_route_through_tenant_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[object, UUID]] = []
        conn = _FakeConn(fetchval_results=[0, 0])
        monkeypatch.setattr(pooling, "tenant_connection", _spy_tenant_connection(calls, conn))
        pool, tenant, version = object(), uuid4(), uuid4()
        keyword_summary = await pooling.materialize_chunk_keyword_hits(pool, tenant, version)
        similarity_summary = await pooling.run_similarity_pooling(pool, tenant, version)
        coverage = await pooling.reconcile_coverage(pool, tenant)
        assert len(calls) == 3
        assert all(p is pool and t is tenant for p, t in calls)
        assert keyword_summary == {"keyword_hits_written": 0, "status": "success"}
        assert similarity_summary == {"chunk_signals_written": 0, "status": "success"}
        assert coverage == {
            "total_corpus_chunks": 0,
            "pooled_chunks": 0,
            "never_pooled_chunks": 0,
            "coverage_reconciled": True,
        }


class TestHandlerReliabilityTenantConn:
    async def test_reliability_routes_through_tenant_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[object, UUID]] = []
        conn = _FakeConn()
        monkeypatch.setattr(
            coding_handler_module, "tenant_connection", _spy_tenant_connection(calls, conn)
        )
        pool, tenant = object(), uuid4()
        handler = CodingHandler(pool=pool, embedder=None)
        result = await handler.handle_reliability(tenant)
        assert len(calls) == 1 and calls[0][0] is pool and calls[0][1] is tenant
        assert result["status"] == "success"
        assert result["coders"] == []
        assert result["insufficient_sample"] is True


# ============================================================================
# (c) Embedder Protocol contract — handler-side instructed embed plumbing
# ============================================================================


class TestHandlerEmbedPathContract:
    async def test_instructed_embed_receives_prefixed_text(self) -> None:
        embedder = _CapturingEmbedder()
        handler = CodingHandler(pool=object(), embedder=embedder)
        embed_fn = handler._default_embed_fn()
        query = "why do teams underreport incidents"
        result = await embed_fn(query, instructed=True)
        assert embedder.embedded == [QUERY_INSTRUCTION_PREFIX + query]
        assert result == "[0.25,0.5,0.75,1.0]"

    async def test_plain_embed_receives_unprefixed_text(self) -> None:
        embedder = _CapturingEmbedder()
        handler = CodingHandler(pool=object(), embedder=embedder)
        embed_fn = handler._default_embed_fn()
        await embed_fn("plain text", instructed=False)
        assert embedder.embedded == ["plain text"]
