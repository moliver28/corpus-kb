"""UX Wave 6 demo-kb loader tests (offline — the ingest tool seam is fake).

NEVER touches Postgres or Ollama: ``ingest_file`` and ``_open_pool`` are
monkeypatched at the owning module. These tests pin the real corpus
files, the receipt envelope's key set, the pool lifecycle, and the typed
missing-corpus error for installed wheels.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from corpus_kb import demo_kb
from corpus_kb.demo_kb import (
    DEMO_KB_ROOT,
    DemoCorpusMissingError,
    load_demo_kb,
)
from corpus_kb.errors import DependencyMissingError

REPO = Path(__file__).resolve().parent.parent

EXPECTED_FILES = (
    "architecture-notes.md",
    "faq.md",
    "glossary.md",
    "sample_meeting.md",
    "welcome.md",
)


# ---------------------------------------------------------------------------
# The bundled corpus (real files, in-repo)
# ---------------------------------------------------------------------------


def test_demo_kb_corpus_exists_repo_root_relative() -> None:
    assert DEMO_KB_ROOT == REPO / "docs" / "demo-kb"
    assert DEMO_KB_ROOT.is_dir()
    for name in EXPECTED_FILES:
        assert (DEMO_KB_ROOT / name).is_file(), name


def test_demo_kb_corpus_is_original_short_sentences() -> None:
    """Every file is small, plain-language demo content (no research corpus)."""
    import re

    for name in EXPECTED_FILES:
        text = (DEMO_KB_ROOT / name).read_text(encoding="utf-8")
        assert len(text) < 4000, name
        body = re.sub(r"```.*?```", " ", text, flags=re.S)
        body = re.sub(r"`[^`\n]*`", " code ", body)
        for sentence in re.split(r"(?<=[.!?])\s+", body):
            words = [w for w in sentence.split() if re.search(r"[A-Za-z0-9]", w)]
            assert len(words) <= 20, f"{name}: sentence over 20 words: {sentence.strip()[:80]}"


# ---------------------------------------------------------------------------
# The loader against the tool seam
# ---------------------------------------------------------------------------


def _success(name: str, chunks: int) -> dict[str, object]:
    return {"status": "success", "path": name, "chunk_count": chunks, "errors": []}


async def test_loader_ingests_every_file_and_pins_the_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_ingest(path: str, pool: object, config: object) -> dict[str, object]:
        calls.append(Path(path).name)
        return _success(Path(path).name, 3)

    monkeypatch.setattr(demo_kb, "ingest_file", fake_ingest)
    envelope = await load_demo_kb({}, pool=object())
    assert calls == list(EXPECTED_FILES)  # sorted walk, every file once
    assert envelope["status"] == "success"
    result = cast(dict[str, object], envelope["result"])
    assert set(result) == {"ingested", "skipped", "chunk_counts"}
    assert result["ingested"] == list(EXPECTED_FILES)
    assert result["skipped"] == []
    assert result["chunk_counts"] == dict.fromkeys(EXPECTED_FILES, 3)


async def test_loader_reports_unchanged_files_as_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ingest(path: str, pool: object, config: object) -> dict[str, object]:
        if Path(path).name == "faq.md":
            return {"status": "skipped", "path": path, "reason": "unchanged", "errors": []}
        return _success(Path(path).name, 2)

    monkeypatch.setattr(demo_kb, "ingest_file", fake_ingest)
    envelope = await load_demo_kb({}, pool=object())
    result = cast(dict[str, object], envelope["result"])
    assert result["skipped"] == ["faq.md"]
    assert "faq.md" not in cast(dict[str, object], result["chunk_counts"])
    assert envelope["status"] == "success"


async def test_loader_flips_to_partial_with_a_message_on_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ingest(path: str, pool: object, config: object) -> dict[str, object]:
        if Path(path).name == "glossary.md":
            return {"status": "error", "message": "database unreachable"}
        return _success(Path(path).name, 1)

    monkeypatch.setattr(demo_kb, "ingest_file", fake_ingest)
    envelope = await load_demo_kb({}, pool=object())
    assert envelope["status"] == "partial"
    assert "glossary.md" in str(envelope["message"])


async def test_loader_uses_the_callers_pool_without_closing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}
    sentinel_pool = object()

    async def fake_ingest(path: str, pool: object, config: object) -> dict[str, object]:
        seen["pool"] = pool
        return _success(Path(path).name, 1)

    monkeypatch.setattr(demo_kb, "ingest_file", fake_ingest)
    await load_demo_kb({}, pool=sentinel_pool)  # type: ignore[arg-type]
    assert seen["pool"] is sentinel_pool


async def test_loader_opens_and_closes_a_pool_when_none_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []
    owned_pool = cast(object, SimpleNamespaceWithClose(closed))

    async def fake_open_pool(config: object) -> object:
        return owned_pool

    async def fake_ingest(path: str, pool: object, config: object) -> dict[str, object]:
        assert pool is owned_pool
        return _success(Path(path).name, 1)

    monkeypatch.setattr(demo_kb, "_open_pool", fake_open_pool)
    monkeypatch.setattr(demo_kb, "ingest_file", fake_ingest)
    envelope = await load_demo_kb({})
    assert envelope["status"] == "success"
    assert closed == [True]


class SimpleNamespaceWithClose:
    """A pool stand-in that records close()."""

    def __init__(self, closed: list[bool]) -> None:
        self._closed = closed

    async def close(self) -> None:
        self._closed.append(True)


# ---------------------------------------------------------------------------
# The installed-wheel case: the typed missing-corpus error
# ---------------------------------------------------------------------------


def test_missing_folder_raises_the_typed_error(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-folder"
    original = demo_kb.DEMO_KB_ROOT
    demo_kb.DEMO_KB_ROOT = missing
    try:
        with pytest.raises(DemoCorpusMissingError) as excinfo:
            demo_kb._demo_files()
    finally:
        demo_kb.DEMO_KB_ROOT = original
    assert isinstance(excinfo.value, DependencyMissingError)
    assert "docs/demo-kb" in excinfo.value.remediation


def test_empty_folder_raises_the_typed_error_too(tmp_path: Path) -> None:
    original = demo_kb.DEMO_KB_ROOT
    demo_kb.DEMO_KB_ROOT = tmp_path
    try:
        with pytest.raises(DemoCorpusMissingError):
            demo_kb._demo_files()
    finally:
        demo_kb.DEMO_KB_ROOT = original


# ---------------------------------------------------------------------------
# The CLI front door (lifecycle command — parity gates untouched)
# ---------------------------------------------------------------------------


def test_demo_kb_command_is_registered() -> None:
    from typer.testing import CliRunner

    from corpus_kb.cli import app

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "demo-kb" in result.output


def test_demo_kb_command_missing_corpus_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import typer

    from corpus_kb import cli_demo_kb

    original = demo_kb.DEMO_KB_ROOT
    demo_kb.DEMO_KB_ROOT = tmp_path / "absent"
    try:
        with pytest.raises(typer.Exit) as excinfo:
            cli_demo_kb.demo_kb(json_output=False)
    finally:
        demo_kb.DEMO_KB_ROOT = original
    assert excinfo.value.exit_code == 1
    assert "docs/demo-kb" in capsys.readouterr().out
