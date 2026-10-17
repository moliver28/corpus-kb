"""UX Wave 6 demo-kb view-model tests (offline — the loader seam is fake).

Same style as tests/test_ui_viewmodels.py: no NiceGUI import anywhere;
``demo_kb_vm.load_demo_kb`` is the monkeypatched seam. The render tests
pin the Add page's receipt strings exactly.
"""

from __future__ import annotations

import pytest

from corpus_kb.ui import demo_kb_vm, render


def _envelope(
    status: str, ingested: list[str], skipped: list[str], counts: dict[str, int]
) -> dict[str, object]:
    return {
        "status": status,
        "result": {"ingested": ingested, "skipped": skipped, "chunk_counts": counts},
        **(
            {"message": "Some sample files failed to ingest. x: boom"}
            if status == "partial"
            else {}
        ),
    }


async def test_view_shapes_the_success_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_loader(config: object, pool: object) -> dict[str, object]:
        seen["config"] = config
        seen["pool"] = pool
        return _envelope("success", ["faq.md", "welcome.md"], [], {"faq.md": 2, "welcome.md": 3})

    seen_pool = object()
    monkeypatch.setattr(demo_kb_vm, "load_demo_kb", fake_loader)
    view = await demo_kb_vm.demo_kb_view({"cfg": 1}, seen_pool)  # type: ignore[arg-type]
    assert seen["config"] == {"cfg": 1}
    assert seen["pool"] is seen_pool
    assert view["ok"] is True
    assert view["status"] == "success"
    assert view["ingested"] == ["faq.md", "welcome.md"]
    assert view["skipped"] == []
    assert view["chunks"] == 5
    assert view["message"] == ""


async def test_view_counts_unchanged_files_as_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_loader(config: object, pool: object) -> dict[str, object]:
        return _envelope("success", ["welcome.md"], ["faq.md"], {"welcome.md": 4})

    monkeypatch.setattr(demo_kb_vm, "load_demo_kb", fake_loader)
    view = await demo_kb_vm.demo_kb_view({}, object())
    assert view["ok"] is True
    assert view["skipped"] == ["faq.md"]
    assert view["chunks"] == 4


async def test_view_partial_status_is_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_loader(config: object, pool: object) -> dict[str, object]:
        return _envelope("partial", ["faq.md"], [], {"faq.md": 1})

    monkeypatch.setattr(demo_kb_vm, "load_demo_kb", fake_loader)
    view = await demo_kb_vm.demo_kb_view({}, object())
    assert view["ok"] is False
    assert view["status"] == "partial"
    assert "failed" in str(view["message"])


async def test_view_missing_corpus_carries_the_remediation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from corpus_kb.demo_kb import DemoCorpusMissingError

    async def fake_loader(config: object, pool: object) -> dict[str, object]:
        raise DemoCorpusMissingError("The sample knowledge folder is missing.")

    monkeypatch.setattr(demo_kb_vm, "load_demo_kb", fake_loader)
    view = await demo_kb_vm.demo_kb_view({}, object())
    assert view["ok"] is False
    assert view["status"] == "missing"
    assert "docs/demo-kb" in str(view["remediation"])


async def test_view_loader_crash_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_loader(config: object, pool: object) -> dict[str, object]:
        raise RuntimeError("boom")

    monkeypatch.setattr(demo_kb_vm, "load_demo_kb", fake_loader)
    view = await demo_kb_vm.demo_kb_view({}, object())
    assert view["ok"] is False
    assert view["status"] == "failed"
    assert "boom" in str(view["message"])


async def test_view_without_a_pool_says_how_to_start() -> None:
    view = await demo_kb_vm.demo_kb_view({}, None)
    assert view["ok"] is False
    assert "corpus-kb start" in str(view["message"])


# ---------------------------------------------------------------------------
# The Add page's receipt strings
# ---------------------------------------------------------------------------


def test_render_success_lists_files_and_chunks() -> None:
    view = {
        "ok": True,
        "ingested": ["faq.md", "welcome.md"],
        "chunks": 5,
    }
    text = render.demo_kb_markdown(view)
    assert "5 chunks" in text
    assert "faq.md" in text and "welcome.md" in text


def test_render_missing_corpus_names_the_fix() -> None:
    view = {
        "ok": False,
        "message": "The sample knowledge folder is missing.",
        "remediation": "Run from a repository clone.",
        "skipped": [],
    }
    text = render.demo_kb_markdown(view)
    assert "folder is missing" in text
    assert "Fix: Run from a repository clone." in text


def test_render_partial_lists_unchanged_files() -> None:
    view = {
        "ok": False,
        "message": "Some sample files failed to ingest.",
        "remediation": "",
        "skipped": ["welcome.md"],
    }
    text = render.demo_kb_markdown(view)
    assert "failed to ingest" in text
    assert "welcome.md" in text
    assert "Fix:" not in text
