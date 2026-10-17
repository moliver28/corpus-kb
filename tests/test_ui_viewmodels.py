"""Pure view-model tests for the browser UI (UX program Wave 3).

No NiceGUI import anywhere: the tool seams (``view_models.ask`` etc.)
are monkeypatched with fakes, and the doctor probe seams are patched at
their owning modules. These tests always run — extras-less CI included.
"""

from __future__ import annotations

from typing import cast

import pytest

from corpus_kb._setup.doctor_research import STATUS_OK, ResearchCheck
from corpus_kb.ui import health, render, view_models

# ---------------------------------------------------------------------------
# The verify badge (abstain -> "not verified"; >= 0.99 -> "verified")
# ---------------------------------------------------------------------------


def test_badge_full_groundedness_is_verified() -> None:
    badge = view_models.verify_badge({"groundedness": 0.99, "abstained": False})
    assert badge["label"] == "verified"
    assert badge["groundedness"] == pytest.approx(0.99)


def test_badge_partial_groundedness_is_partial() -> None:
    badge = view_models.verify_badge({"groundedness": 0.5, "abstained": False})
    assert badge["label"] == "partial"


def test_badge_abstention_is_not_verified() -> None:
    badge = view_models.verify_badge({"groundedness": 0.0, "verdicts": [], "abstained": True})
    assert badge["label"] == "not verified"


def test_badge_missing_record_is_not_verified() -> None:
    """No verify record at all never masquerades as a pass."""
    assert view_models.verify_badge(None)["label"] == "not verified"
    assert view_models.verify_badge({})["label"] == "not verified"


# ---------------------------------------------------------------------------
# ask_view over the tool seam
# ---------------------------------------------------------------------------


def _ask_payload(status: str) -> dict[str, object]:
    return {
        "status": status,
        "result": {
            "answer": "Answer text [1].",
            "sentences": [{"text": "Answer text [1].", "citations": [1]}],
            "sources": [{"source": "notes.md", "doc_id": "d1"}],
            "verify": {"groundedness": 1.0, "verdicts": [], "abstained": False},
            "model": "fake",
            "note": None,
        },
        "degraded_excluded": {"count": 2},
    }


async def test_ask_view_shapes_the_success_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_ask(query: str, **kwargs: object) -> dict[str, object]:
        seen["query"] = query
        seen.update(kwargs)
        return _ask_payload("success")

    monkeypatch.setattr(view_models, "ask", fake_ask)
    view = await view_models.ask_view("what is X?", pg_pool=None, k=5)
    assert seen["query"] == "what is X?"
    assert seen["k"] == 5
    assert view["status"] == "success"
    assert view["sentences"][0]["text"] == "Answer text [1]."
    assert view["sources"][0]["source"] == "notes.md"
    assert view["verify_badge"]["label"] == "verified"
    assert view["note"] == ""
    assert view["degraded_excluded"] == {"count": 2}


async def test_ask_view_empty_status_carries_the_honest_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ask(query: str, **kwargs: object) -> dict[str, object]:
        return {"status": "empty", "result": {}, "degraded_excluded": {}}

    monkeypatch.setattr(view_models, "ask", fake_ask)
    view = await view_models.ask_view("nothing matches")
    assert view["status"] == "empty"
    assert "No evidence" in str(view["note"])
    assert view["verify_badge"]["label"] == "not verified"


async def test_ask_view_degraded_status_notes_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ask(query: str, **kwargs: object) -> dict[str, object]:
        payload = _ask_payload("degraded")
        result = cast(dict[str, object], payload["result"])
        result["verify"] = {"groundedness": 0.0, "abstained": True}
        return payload

    monkeypatch.setattr(view_models, "ask", fake_ask)
    view = await view_models.ask_view("anything")
    assert view["status"] == "degraded"
    assert "without an answer" in str(view["note"])
    assert view["verify_badge"]["label"] == "not verified"


# ---------------------------------------------------------------------------
# library_rows over the read seams
# ---------------------------------------------------------------------------


async def test_library_rows_shape_documents_and_topics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_docs(pool: object, config: object, limit: int = 100) -> dict[str, object]:
        return {
            "status": "success",
            "result": [
                {"doc_id": "d1", "source": "notes.md", "source_type": "text", "chunk_count": 3}
            ],
        }

    async def fake_topics(pool: object, config: object, limit: int = 100) -> dict[str, object]:
        return {
            "status": "success",
            "topic_count": 1,
            "topics": [{"topic_id": "t1", "label": "fonts", "reduced_label": None, "size": 7}],
        }

    monkeypatch.setattr(view_models, "list_documents", fake_docs)
    monkeypatch.setattr(view_models, "topics_list", fake_topics)
    data = await view_models.library_rows({}, pool=object(), limit=50)
    assert data["documents"] == [{"id": "d1", "title": "notes.md", "type": "text", "chunks": 3}]
    assert data["topics"] == [{"id": "t1", "label": "fonts", "size": 7}]
    markdown = render.library_markdown(
        cast(list[dict[str, object]], data["documents"]),
        cast(list[dict[str, object]], data["topics"]),
    )
    assert "notes.md" in markdown
    assert "fonts" in markdown


def test_library_markdown_empty_library_is_honest() -> None:
    markdown = render.library_markdown([], [])
    assert "No documents yet" in markdown


# ---------------------------------------------------------------------------
# overview_checks over the doctor seams
# ---------------------------------------------------------------------------


async def test_overview_checks_shape_the_doctor_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from corpus_kb._setup import doctor_env, setup_steps

    monkeypatch.setattr(
        doctor_env,
        "env_var_check",
        lambda: ResearchCheck(name="CORPUS_KB env vars", status=STATUS_OK, detail="clean"),
    )

    async def fake_postgres(conn_str: str) -> tuple[bool, str]:
        return True, "PostgreSQL 16.4"

    async def fake_ollama(base_url: str) -> tuple[bool, str]:
        return False, "status 000"

    monkeypatch.setattr(setup_steps, "check_postgres", fake_postgres)
    monkeypatch.setattr(setup_steps, "check_ollama", fake_ollama)
    cfg: dict[str, object] = {
        "database": {"connection_string": "postgresql://localhost/x"},
        "embedding": {"base_url": "http://localhost:11434"},
    }
    checks = await health.overview_checks(cfg)
    assert [str(check["name"]) for check in checks] == ["Configuration", "Database", "Models"]
    assert [bool(check["ok"]) for check in checks] == [True, True, False]
    assert str(checks[2]["fix"]) == "Run: corpus-kb setup (installs the models)"
    line = health.check_line(checks[2])
    assert "Needs attention" in line
    assert "Fix:" in line


async def test_overview_checks_report_a_missing_connection_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from corpus_kb._setup import doctor_env, setup_steps

    # Env wins over config (the doctor's documented precedence): the env
    # var must be gone, or CI's exported DSN would make the probe run.
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    monkeypatch.setattr(
        doctor_env,
        "env_var_check",
        lambda: ResearchCheck(name="CORPUS_KB env vars", status=STATUS_OK, detail="clean"),
    )

    async def fake_postgres(conn_str: str) -> tuple[bool, str]:
        raise AssertionError("no probe should run without a connection string")

    async def fake_ollama(base_url: str) -> tuple[bool, str]:
        return True, "status 200"

    monkeypatch.setattr(setup_steps, "check_postgres", fake_postgres)
    monkeypatch.setattr(setup_steps, "check_ollama", fake_ollama)
    checks = await health.overview_checks({})
    assert checks[1]["ok"] is False
    assert "no connection string" in str(checks[1]["detail"])


# ---------------------------------------------------------------------------
# Add receipts (file + URL + demo)
# ---------------------------------------------------------------------------


async def test_add_via_path_success_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_ingest(
        file_path: str,
        pool: object,
        config: object,
        tenant_id: str,
        source_type: str | None,
    ) -> dict[str, object]:
        seen.update(path=file_path, tenant=tenant_id, source_type=source_type)
        return {"status": "success", "document_id": "abc", "chunk_count": 4, "entity_count": 2}

    monkeypatch.setattr(view_models, "ingest_file", fake_ingest)
    receipt = await view_models.add_via_path("/tmp/a.md", pool=object(), source_type=None)
    assert str(seen["path"]) == "/tmp/a.md"
    assert receipt["ok"] is True
    assert receipt["chunks"] == 4
    assert receipt["entities"] == 2
    assert render.receipt_line("a.md", receipt) == (
        "**a.md** added: 4 chunks, 2 connections found."
    )


async def test_add_via_path_error_carries_the_message(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_ingest(
        file_path: str,
        pool: object,
        config: object,
        tenant_id: str,
        source_type: str | None,
    ) -> dict[str, object]:
        return {"status": "error", "message": "File not found: /tmp/a.md"}

    monkeypatch.setattr(view_models, "ingest_file", fake_ingest)
    receipt = await view_models.add_via_path("/tmp/a.md", pool=object())
    assert receipt["ok"] is False
    assert "File not found" in str(receipt["message"])
    line = render.receipt_line("a.md", receipt)
    assert "failed" in line
    assert "Fix:" not in line  # no remediation key -> no invented fix


async def test_add_via_url_counts_pages_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ingest_url(
        url: str, pool: object, config: object, tenant_id: str
    ) -> dict[str, object]:
        return {
            "status": "success",
            "pages": [{"status": "success"}, {"status": "error", "message": "timeout"}],
            "total_documents": 1,
        }

    monkeypatch.setattr(view_models, "ingest_url", fake_ingest_url)
    receipt = await view_models.add_via_url("https://example.com", pool=object())
    assert receipt["ok"] is True
    assert receipt["pages"] == 2
    assert receipt["documents"] == 1
    assert receipt["failed_pages"] == 1
    line = render.url_receipt_line("https://example.com", receipt)
    assert "1 page(s) failed" in line


async def test_demo_receipt_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_demo(config: object) -> dict[str, object]:
        return {"status": "completed", "exit_code": 0, "narration": "step one"}

    monkeypatch.setattr(view_models, "research_demo", fake_demo)
    receipt = await view_models.demo_receipt()
    assert receipt["ok"] is True
    assert receipt["narration"] == "step one"


async def test_demo_receipt_unavailable_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_demo(config: object) -> dict[str, object]:
        return {"status": "unavailable", "exit_code": 2, "narration": "no embeddings"}

    monkeypatch.setattr(view_models, "research_demo", fake_demo)
    receipt = await view_models.demo_receipt()
    assert receipt["ok"] is False
    assert "did not finish" in str(receipt["message"])
    assert receipt["narration"] == "no embeddings"


async def test_demo_receipt_never_crashes_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_demo(config: object) -> dict[str, object]:
        raise FileNotFoundError("demo corpus directory not found")

    monkeypatch.setattr(view_models, "research_demo", fake_demo)
    receipt = await view_models.demo_receipt()
    assert receipt["ok"] is False
    assert "could not start" in str(receipt["message"])
    assert "demo corpus" in str(receipt["message"])
