"""Route tests for the UI SPA Task 5a demo-critical routes.

Three routes, ONE error envelope, no database:

- POST /api/demo/load     — the bundled demo corpus loader (house receipt).
- POST /api/ingest/upload — multipart upload with the ui.spa.max_upload_mb
  cap enforced BEFORE the body is read (header) and DURING the stream.
- GET  /api/doctor        — read-only diagnostics over the CLI probes.

Every dependency seam is stubbed (test_http_route_smoke precedent), so the
suite stays offline and fast. The multipart happy path needs the real
form parser; it skips when the ``python-multipart`` extra is absent, the
same degrade contract as the rest of the suite.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from corpus_kb.api.http import create_http_app

REPO = Path(__file__).resolve().parent.parent


class _FakePool:
    """Identity-only asyncpg pool stub; stubbed handlers never use it."""


@dataclass
class _FakeUpload:
    """The UploadSource shape (starlette UploadFile stand-in)."""

    filename: str | None
    content_type: str | None = "text/markdown"
    size: int | None = None
    payload: bytes = b""

    async def read(self, size: int = -1) -> bytes:
        del size
        return self.payload


def _demo_receipt() -> dict[str, Any]:
    return {
        "status": "success",
        "result": {
            "ingested": ["welcome.md"],
            "skipped": [],
            "chunk_counts": {"welcome.md": 3},
        },
    }


# ---------------------------------------------------------------------------
# POST /api/demo/load
# ---------------------------------------------------------------------------


def test_demo_load_returns_the_house_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The route calls load_demo_kb with the shared pool and returns it raw."""
    import corpus_kb.api.research_http as research_http
    import corpus_kb.demo_kb as demo_kb

    captured: dict[str, object] = {}

    async def stub(config: object, pool: object) -> dict[str, Any]:
        captured["pool"] = pool
        return _demo_receipt()

    monkeypatch.setattr(demo_kb, "load_demo_kb", stub)
    pool = _FakePool()
    monkeypatch.setattr(research_http, "_pool", pool)

    client = TestClient(create_http_app())
    response = client.post("/api/demo/load")

    assert response.status_code == 200
    assert response.json() == _demo_receipt()
    assert captured["pool"] is pool


def test_demo_load_missing_corpus_is_a_typed_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing corpus folder answers dependency_missing, never a 500."""
    import corpus_kb.api.research_http as research_http
    import corpus_kb.demo_kb as demo_kb
    from corpus_kb.demo_kb import DemoCorpusMissingError

    async def stub(config: object, pool: object) -> dict[str, Any]:
        del config, pool
        raise DemoCorpusMissingError("The sample knowledge folder is missing.")

    monkeypatch.setattr(demo_kb, "load_demo_kb", stub)
    monkeypatch.setattr(research_http, "_pool", _FakePool())

    client = TestClient(create_http_app())
    response = client.post("/api/demo/load")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["error_type"] == "dependency_missing"


# ---------------------------------------------------------------------------
# POST /api/ingest/upload
# ---------------------------------------------------------------------------


def test_upload_rejects_non_multipart_requests() -> None:
    """A JSON body is a typed 415, never a bare 400."""
    client = TestClient(create_http_app())
    response = client.post("/api/ingest/upload", json={"file_path": "x"})
    assert response.status_code == 415
    body = response.json()
    assert body["status"] == "error"
    assert body["error_type"] == "unsupported_media_type"


def test_upload_missing_parser_is_a_typed_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without python-multipart the route names the extra (typed 503)."""
    import corpus_kb.tools.ingest_upload_tools as upload_tools

    real_find = importlib.util.find_spec
    monkeypatch.setattr(
        upload_tools.importlib.util,
        "find_spec",
        lambda name: None if name == "python_multipart" else real_find(name),
    )
    client = TestClient(create_http_app())
    response = client.post(
        "/api/ingest/upload",
        files={"file": ("notes.md", b"# hello", "text/markdown")},
    )
    assert response.status_code == 503
    assert response.json()["error_type"] == "dependency_missing"


def test_upload_missing_file_field_is_a_typed_400() -> None:
    """A multipart body without a file field is validation_error."""
    pytest.importorskip("python_multipart")
    client = TestClient(create_http_app())
    response = client.post(
        "/api/ingest/upload",
        data={"source_type": "text"},
        files={"not_file": ("a.txt", b"x", "text/plain")},
    )
    assert response.status_code == 400
    assert response.json()["error_type"] == "validation_error"


def test_upload_happy_path_runs_the_standard_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The upload lands in a temp file and ingest_file's envelope passes through."""
    import corpus_kb.api.research_http as research_http
    import corpus_kb.tools.ingest_upload_tools as upload_tools

    seen: dict[str, object] = {}

    async def stub_ingest_file(
        file_path: str, pool: object, config: object, **kwargs: object
    ) -> dict[str, Any]:
        seen["pool"] = pool
        seen["suffix"] = Path(file_path).suffix
        seen["path"] = file_path
        seen["body"] = Path(file_path).read_text(encoding="utf-8")
        return {"status": "success", "chunk_count": 2}

    monkeypatch.setattr(upload_tools, "ingest_file", stub_ingest_file)
    pool = _FakePool()
    monkeypatch.setattr(research_http, "_pool", pool)

    client = TestClient(create_http_app())
    response = client.post(
        "/api/ingest/upload",
        files={"file": ("notes.md", b"# hello world", "text/markdown")},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "chunk_count": 2}
    assert seen["pool"] is pool
    assert seen["suffix"] == ".md"
    assert seen["body"] == "# hello world"
    assert not Path(str(seen["path"])).exists(), "the temp upload file must be removed"


def test_upload_stream_cap_answers_413(monkeypatch: pytest.MonkeyPatch) -> None:
    """A body over the cap is refused before the ingest tool ever runs."""
    import corpus_kb.api.research_http as research_http
    import corpus_kb.tools.ingest_upload_tools as upload_tools

    monkeypatch.setattr(upload_tools, "upload_cap_mb", lambda config: 1)

    async def stub_ingest_file(*args: object, **kwargs: object) -> dict[str, Any]:
        raise AssertionError("ingest_file must not run for an oversized upload")

    monkeypatch.setattr(upload_tools, "ingest_file", stub_ingest_file)
    monkeypatch.setattr(research_http, "_pool", _FakePool())

    client = TestClient(create_http_app())
    response = client.post(
        "/api/ingest/upload",
        files={"file": ("big.md", b"x" * (1024 * 1024 + 1), "text/markdown")},
    )
    assert response.status_code == 413
    assert response.json()["error_type"] == "payload_too_large"


async def test_upload_declared_size_cap_answers_413(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A declared size over the cap aborts before a byte is spooled."""
    import corpus_kb.tools.ingest_upload_tools as upload_tools
    from corpus_kb.errors import PayloadTooLargeError

    async def stub_ingest_file(*args: object, **kwargs: object) -> dict[str, Any]:
        raise AssertionError("ingest_file must not run for an oversized upload")

    monkeypatch.setattr(upload_tools, "ingest_file", stub_ingest_file)
    source = _FakeUpload(filename="big.md", size=10 * 1024 * 1024, payload=b"x")
    with pytest.raises(PayloadTooLargeError):
        await upload_tools.ingest_upload(source, _FakePool(), max_upload_mb=1)


def test_upload_tool_helpers_are_strict() -> None:
    """Junk filenames are a typed 400; the cap helper fills the default."""
    import corpus_kb.tools.ingest_upload_tools as upload_tools
    from corpus_kb.errors import ValidationError

    with pytest.raises(ValidationError):
        upload_tools.sanitize_filename(None)
    with pytest.raises(ValidationError):
        upload_tools.sanitize_filename("../..")
    assert upload_tools.upload_cap_mb({"ui": {"spa": {"max_upload_mb": 7}}}) == 7
    assert upload_tools.upload_cap_mb({}) >= 1


# ---------------------------------------------------------------------------
# GET /api/doctor
# ---------------------------------------------------------------------------


@dataclass
class _Check:
    """The ResearchCheck shape without the asyncpg-flavored module import."""

    name: str
    status: str
    detail: str
    fix: str = ""
    doc: str = ""


@dataclass
class _Env:
    """The env_var_check return shape (a ResearchCheck)."""

    name: str = "env vars"
    status: str = "ok"
    detail: str = "none set"
    fix: str = ""
    doc: str = ""


def _patch_doctor_probes(monkeypatch: pytest.MonkeyPatch, postgres_ok: bool) -> None:
    """Stub the SAME probe functions the CLI doctor calls (source modules).

    The snapshot imports the probes lazily from their own modules, so the
    patches pin the real seams — not wrappers the handler owns.
    """
    import corpus_kb._setup.install as install
    import corpus_kb._setup.setup_steps as setup_steps
    import corpus_kb.tools.doctor_tools as doctor_tools

    def fake_profile() -> dict[str, Any]:
        return {
            "os": "test-os",
            "python": "3.11",
            "cpu_cores": 2,
            "ram_gb": 8,
            "vram_detected": False,
            "profile": "cpu",
        }

    async def fake_postgres(conn_str: str) -> tuple[bool, str]:
        return postgres_ok, "ok" if postgres_ok else "unreachable"

    async def fake_extensions(conn_str: str) -> dict[str, tuple[bool, str]]:
        return {"vector": (True, "0.7.0")}

    async def fake_ollama(url: str) -> tuple[bool, str]:
        return False, "down"

    async def fake_research(
        conn_str: str, config: object, extensions: object, ollama_ok: object = None
    ) -> list[_Check]:
        return [_Check("surface parity", "ok", "green")]

    async def fake_storage(config: object, conn_str: str, postgres_ok: bool) -> list[_Check]:
        return [_Check("engine", "info", "postgres")]

    monkeypatch.setattr(
        doctor_tools,
        "load_config_or_pass",
        lambda _c: {"database": {"connection_string": "postgresql://doctor-test"}},
    )
    monkeypatch.setattr(install, "detect_profile", fake_profile)
    monkeypatch.setattr(install, "load_installer_profiles", lambda _c: {"cpu": {"model": "m"}})
    monkeypatch.setattr(setup_steps, "check_postgres", fake_postgres)
    monkeypatch.setattr(setup_steps, "check_extensions", fake_extensions)
    monkeypatch.setattr(setup_steps, "check_ollama", fake_ollama)
    # research/storage/env names are bound at doctor_tools module top, so
    # the patches target that binding, not the defining module.
    monkeypatch.setattr(doctor_tools, "research_checks", fake_research)
    monkeypatch.setattr(doctor_tools, "storage_checks", fake_storage)
    monkeypatch.setattr(doctor_tools, "env_var_check", lambda: _Env())


def test_doctor_snapshot_reuses_the_cli_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The handler assembles the SAME probes the CLI doctor runs."""

    _patch_doctor_probes(monkeypatch, postgres_ok=True)

    client = TestClient(create_http_app())
    response = client.get("/api/doctor")

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["postgres"] == {
        "reachable": True,
        "message": "ok",
        "extensions": {"vector": {"installed": True, "version": "0.7.0"}},
    }
    assert result["ollama"] == {"reachable": False, "message": "down"}
    assert result["profile"]["recommended_model"] == "m"
    assert result["research_checks"] == [
        {"name": "surface parity", "status": "ok", "detail": "green", "fix": "", "doc": ""}
    ]
    assert result["storage_checks"][0]["name"] == "engine"
    assert result["env"]["status"] == "ok"


def test_doctor_skips_research_checks_without_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With Postgres unreachable the research section stays empty, not broken."""

    _patch_doctor_probes(monkeypatch, postgres_ok=False)

    client = TestClient(create_http_app())
    response = client.get("/api/doctor")

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["postgres"]["reachable"] is False
    assert result["research_checks"] == []


# ---------------------------------------------------------------------------
# scripts/gen_demo_corpus.py — determinism
# ---------------------------------------------------------------------------


def _load_gen_demo_corpus() -> Any:
    """Load the generator by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "gen_demo_corpus", REPO / "scripts" / "gen_demo_corpus.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gen_demo_corpus_is_deterministic(tmp_path: Path) -> None:
    """Two runs with the same seed and count write identical bytes."""
    gen = _load_gen_demo_corpus()
    first, second = tmp_path / "a", tmp_path / "b"
    gen.generate(first, 12, 42)
    gen.generate(second, 12, 42)
    files_a = sorted(p.name for p in first.glob("*.md"))
    files_b = sorted(p.name for p in second.glob("*.md"))
    assert files_a == files_b
    assert len(files_a) == 12
    for name in files_a:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_gen_demo_corpus_documents_are_stable_shapes(tmp_path: Path) -> None:
    """Every document starts with one heading and ends with the footer."""
    gen = _load_gen_demo_corpus()
    out = tmp_path / "corpus"
    gen.generate(out, 8, 7)
    for path in sorted(out.glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines[0].startswith("# "), path.name
        index = int(path.stem.split("-")[1])
        assert lines[-1] == f"Synthetic demo document {index} of 8.", path.name
