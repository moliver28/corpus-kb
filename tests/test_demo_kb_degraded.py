"""Degraded demo-kb loader tests (issue #414; offline, fake ingest seam).

NEVER touches Postgres or Ollama: ``ingest_file`` is monkeypatched at the
owning module. The receipts copy the shape a real run returns when the
embedder is unreachable: ``status: partial``, the chunks stored, and the
real cause in ``errors`` (there is no ``message`` key on that receipt).
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from corpus_kb import demo_kb
from corpus_kb.demo_kb import load_demo_kb

EXPECTED_FILES = (
    "architecture-notes.md",
    "faq.md",
    "glossary.md",
    "sample_meeting.md",
    "welcome.md",
)

ZERO_VECTOR_ERROR = "EmbeddingError: Connection failed: OllamaEmbedder returned zero vectors"
WRITE_ERROR = "PostgresWriteError: connection lost"


def _success(name: str, chunks: int) -> dict[str, object]:
    return {"status": "success", "path": name, "chunk_count": chunks, "errors": []}


def _partial(name: str, stored: int, errors: list[str]) -> dict[str, object]:
    """The pipeline's partial receipt; ``stored`` is what reached the store."""
    return {
        "status": "partial",
        "path": name,
        "chunk_count": 9,
        "pg_chunk_count": stored,
        "pg_chunk_total": stored,
        "pg_vector_count": stored,
        "degraded": True,
        "errors": errors,
    }


def _patch_ingest(monkeypatch: pytest.MonkeyPatch, receipts: dict[str, dict[str, object]]) -> None:
    """Answer the named files from ``receipts``; every other file succeeds."""

    async def fake_ingest(path: str, pool: object, config: object) -> dict[str, object]:
        name = Path(path).name
        return receipts.get(name, _success(name, 2))

    monkeypatch.setattr(demo_kb, "ingest_file", fake_ingest)


async def _load() -> tuple[dict[str, object], dict[str, object]]:
    envelope = await load_demo_kb({}, pool=object())
    return envelope, cast("dict[str, object]", envelope["result"])


async def test_stored_chunks_without_vectors_count_as_a_degraded_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No embedder: every file is stored, so the corpus is usable."""
    _patch_ingest(
        monkeypatch, {name: _partial(name, 9, [ZERO_VECTOR_ERROR]) for name in EXPECTED_FILES}
    )
    envelope, result = await _load()
    assert envelope["status"] == "success"
    assert set(envelope) == {"status", "result"}
    assert set(result) == {"ingested", "skipped", "chunk_counts", "degraded"}
    assert result["ingested"] == list(EXPECTED_FILES)
    assert result["chunk_counts"] == dict.fromkeys(EXPECTED_FILES, 9)
    assert result["degraded"] is True


async def test_partial_receipt_with_nothing_stored_fails_with_the_real_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chunks made in memory but never stored are a failure, not a success."""
    _patch_ingest(monkeypatch, {"glossary.md": _partial("glossary.md", 0, [WRITE_ERROR])})
    envelope, result = await _load()
    assert envelope["status"] == "partial"
    assert set(result) == {"ingested", "skipped", "chunk_counts", "failures"}
    assert result["failures"] == {"glossary.md": WRITE_ERROR}
    assert "glossary.md" not in cast("list[str]", result["ingested"])
    assert "glossary.md" not in cast("dict[str, int]", result["chunk_counts"])


async def test_every_receipt_error_reaches_the_failure_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ingest(monkeypatch, {"faq.md": _partial("faq.md", 0, [WRITE_ERROR, ZERO_VECTOR_ERROR])})
    _, result = await _load()
    assert result["failures"] == {"faq.md": f"{WRITE_ERROR}; {ZERO_VECTOR_ERROR}"}


async def test_stored_count_falls_back_to_this_runs_write_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sink without the document total still reports what this run wrote."""
    receipt = _partial("faq.md", 9, [ZERO_VECTOR_ERROR])
    receipt["pg_chunk_total"] = 0
    _patch_ingest(monkeypatch, {"faq.md": receipt})
    envelope, result = await _load()
    assert envelope["status"] == "success"
    assert result["degraded"] is True


async def test_mixed_run_keeps_the_degraded_marker_and_the_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ingest(
        monkeypatch,
        {
            "faq.md": _partial("faq.md", 9, [ZERO_VECTOR_ERROR]),
            "glossary.md": {"status": "error", "message": "database unreachable"},
        },
    )
    envelope, result = await _load()
    assert envelope["status"] == "partial"
    assert set(result) == {"ingested", "skipped", "chunk_counts", "degraded", "failures"}
    assert result["degraded"] is True
    assert result["failures"] == {"glossary.md": "database unreachable"}
    assert "faq.md" in cast("list[str]", result["ingested"])


async def test_receipt_with_no_cause_keeps_the_last_resort_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ingest(monkeypatch, {"welcome.md": {"status": "error"}})
    _, result = await _load()
    assert result["failures"] == {"welcome.md": "unknown ingest failure"}


def test_cli_receipt_names_the_degraded_load(capsys: pytest.CaptureFixture[str]) -> None:
    from corpus_kb.cli import cli_demo_kb

    cli_demo_kb._print_receipt(
        {
            "status": "success",
            "result": {
                "ingested": ["faq.md"],
                "skipped": [],
                "chunk_counts": {"faq.md": 9},
                "degraded": True,
            },
        }
    )
    out = capsys.readouterr().out
    assert "[OK] faq.md: 9 chunk(s)" in out
    assert "DEGRADED" in out
    assert "corpus-kb ingest --reembed" in out


def test_cli_receipt_stays_quiet_on_a_clean_load(capsys: pytest.CaptureFixture[str]) -> None:
    from corpus_kb.cli import cli_demo_kb

    cli_demo_kb._print_receipt(
        {
            "status": "success",
            "result": {"ingested": ["faq.md"], "skipped": [], "chunk_counts": {"faq.md": 9}},
        }
    )
    assert "DEGRADED" not in capsys.readouterr().out
