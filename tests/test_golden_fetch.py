"""Unit tests for the golden textbook fetcher (program W0).

No network anywhere: the httpx client is substituted with a fake that
streams bytes from memory, so every status path is hermetic.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from corpus_kb.eval import golden_fetch as fetch
from corpus_kb.eval.golden_fetch import GoldenBook

CONTENT = b"golden-book-bytes"
PIN = hashlib.sha256(CONTENT).hexdigest()
OTHER = hashlib.sha256(b"different-bytes").hexdigest()


def _book(sha256: str = PIN) -> GoldenBook:
    return GoldenBook(
        title="Concepts of Biology",
        filename="ConceptsOfBiology-LR.pdf",
        source_url="https://example.invalid/book.pdf",
        sha256=sha256,
        license="CC-BY-4.0",
    )


class _FakeReply:
    """The httpx streaming reply, bytes supplied in the constructor."""

    def __init__(self, payload: bytes, error: Exception | None = None) -> None:
        self._payload = payload
        self._error = error

    def __enter__(self) -> _FakeReply:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def raise_for_status(self) -> None:
        if self._error is not None:
            raise self._error

    def iter_bytes(self) -> list[bytes]:
        return [self._payload]


class _FakeClient:
    """The httpx client seam: the reply comes from the module-level box."""

    def __init__(self, **kwargs: object) -> None:
        del kwargs

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def stream(self, method: str, url: str) -> _FakeReply:
        del method, url
        return _BOX[0]


_BOX: list[_FakeReply] = []


def _use(monkeypatch: pytest.MonkeyPatch, payload: bytes, error: Exception | None = None) -> None:
    _BOX.clear()
    _BOX.append(_FakeReply(payload, error))
    monkeypatch.setattr(fetch.httpx, "Client", _FakeClient)


def test_manifest_pins_three_open_books() -> None:
    books = fetch.load_manifest()
    assert len(books) >= 3
    assert all(book.license == "CC-BY-4.0" for book in books)
    assert len({book.sha256 for book in books}) == len(books)
    assert all(book.filename.endswith(".pdf") for book in books)


def test_manifest_rejects_incomplete_rows(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"books": [{"title": "x"}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="lacks keys"):
        fetch.load_manifest(manifest)


def test_default_dir_follows_the_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CORPUS_KB_TEXTBOOK_GOLDEN_DIR", str(tmp_path))
    assert fetch.default_dir() == tmp_path
    monkeypatch.delenv("CORPUS_KB_TEXTBOOK_GOLDEN_DIR")
    assert fetch.default_dir() == Path("~/.corpus-kb/textbook-golden").expanduser()


def test_fetch_book_cached_when_hash_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "ConceptsOfBiology-LR.pdf").write_bytes(CONTENT)
    _use(monkeypatch, b"never touched")
    status = fetch.fetch_book(_book(), tmp_path)
    assert status == "cached"


def test_fetch_book_downloads_and_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, CONTENT)
    status = fetch.fetch_book(_book(), tmp_path)
    assert status == "downloaded"
    assert (tmp_path / "ConceptsOfBiology-LR.pdf").read_bytes() == CONTENT
    assert not list(tmp_path.glob("*.part"))


def test_fetch_book_reports_a_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, b"tampered")
    status = fetch.fetch_book(_book(OTHER), tmp_path)
    assert status == "mismatch"
    assert not list(tmp_path.glob("*.part"))


def test_fetch_book_survives_a_network_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, CONTENT, httpx.ConnectError("down"))
    status = fetch.fetch_book(_book(), tmp_path)
    assert status == "failed"
    assert not list(tmp_path.glob("*.part"))


def test_main_exit_zero_only_when_every_book_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    books = [_book(), _book(OTHER)]
    monkeypatch.setattr(fetch, "load_manifest", lambda: books)
    statuses = iter(["downloaded", "failed"])

    def fake_fetch(book: GoldenBook, dest_dir: object, timeout: float = 1.0) -> str:
        del book, dest_dir, timeout
        return next(statuses)

    monkeypatch.setattr(fetch, "fetch_book", fake_fetch)
    assert fetch.main(["--dir", str(tmp_path)]) == 1
    assert "failed" in capsys.readouterr().out
    statuses = iter(["cached", "downloaded"])
    assert fetch.main(["--dir", str(tmp_path)]) == 0
