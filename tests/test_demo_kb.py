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
PACKAGE_CORPUS = REPO / "src" / "corpus_kb" / "demo_corpus"

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
# Corpus resolution across install layouts (install-ease W2)
# ---------------------------------------------------------------------------


def test_packaged_corpus_is_found_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A corpus shipped inside the package wins over the checkout copy."""
    packaged = tmp_path / "site-packages" / "corpus_kb" / "demo_corpus"
    packaged.mkdir(parents=True)
    (packaged / "welcome.md").write_text("packaged corpus\n", encoding="utf-8")
    monkeypatch.setattr(demo_kb, "_packaged_corpus_root", lambda: packaged)

    files = demo_kb._demo_files()

    assert [path.name for path in files] == ["welcome.md"]


def test_checkout_corpus_is_the_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without a packaged copy, the checkout corpus at docs/demo-kb is used."""
    monkeypatch.setattr(demo_kb, "_packaged_corpus_root", lambda: None)

    files = demo_kb._demo_files()

    assert files == sorted(DEMO_KB_ROOT.glob("*.md"))


def test_missing_corpus_raises_the_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """No corpus in either layout raises DemoCorpusMissingError."""
    monkeypatch.setattr(demo_kb, "_packaged_corpus_root", lambda: None)
    monkeypatch.setattr(demo_kb, "DEMO_KB_ROOT", Path("does-not-exist-placeholder"))

    with pytest.raises(DemoCorpusMissingError):
        demo_kb._demo_files()


def test_packaged_corpus_dir_exists_in_this_repo() -> None:
    """The shipped corpus mirror exists beside the package code."""
    assert PACKAGE_CORPUS.is_dir()
    assert sorted(path.name for path in PACKAGE_CORPUS.glob("*.md")) == list(EXPECTED_FILES)


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


async def test_loader_flips_to_partial_with_failures_in_the_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Partial runs keep the top-level shape: failures live in result."""

    async def fake_ingest(path: str, pool: object, config: object) -> dict[str, object]:
        if Path(path).name == "glossary.md":
            return {"status": "error", "message": "database unreachable"}
        return _success(Path(path).name, 1)

    monkeypatch.setattr(demo_kb, "ingest_file", fake_ingest)
    envelope = await load_demo_kb({}, pool=object())
    assert envelope["status"] == "partial"
    # The declared envelope: exactly status + result on every run.
    assert set(envelope) == {"status", "result"}
    result = cast(dict[str, object], envelope["result"])
    assert set(result) == {"ingested", "skipped", "chunk_counts", "failures"}
    failures = cast(dict[str, str], result["failures"])
    assert failures == {"glossary.md": "database unreachable"}


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
# The both-layouts-missing case: the typed missing-corpus error
# ---------------------------------------------------------------------------


def test_missing_folder_raises_the_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demo_kb, "_packaged_corpus_root", lambda: None)
    monkeypatch.setattr(demo_kb, "DEMO_KB_ROOT", tmp_path / "no-such-folder")
    with pytest.raises(DemoCorpusMissingError) as excinfo:
        demo_kb._demo_files()
    assert isinstance(excinfo.value, DependencyMissingError)
    assert "docs/demo-kb" in excinfo.value.remediation


def test_empty_folder_raises_the_typed_error_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demo_kb, "_packaged_corpus_root", lambda: None)
    monkeypatch.setattr(demo_kb, "DEMO_KB_ROOT", tmp_path)
    with pytest.raises(DemoCorpusMissingError):
        demo_kb._demo_files()


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

    from corpus_kb.cli import cli_demo_kb

    monkeypatch.setattr(demo_kb, "_packaged_corpus_root", lambda: None)
    monkeypatch.setattr(demo_kb, "DEMO_KB_ROOT", tmp_path / "absent")
    with pytest.raises(typer.Exit) as excinfo:
        cli_demo_kb.demo_kb(json_output=False)
    assert excinfo.value.exit_code == 1
    assert "docs/demo-kb" in capsys.readouterr().out


def test_packaged_corpus_root_is_none_without_the_package_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wheel layout without demo_corpus inside resolves to None."""
    import importlib.resources

    class _NoCorpus:
        def __truediv__(self, _name: str) -> _NoCorpus:
            return self

        def is_dir(self) -> bool:
            return False

    monkeypatch.setattr(importlib.resources, "files", lambda _pkg: _NoCorpus())
    assert demo_kb._packaged_corpus_root() is None
