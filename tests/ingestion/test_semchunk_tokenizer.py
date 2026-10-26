"""Wave 4 semchunk tokenizer seam: native offsets replace the chars/4 slice.

The package is a lazy extra (CI never installs it): logic tests run against
a fake module; one API test runs against the real semchunk when present.
Without the config key the split is byte-identical to the old slicing.
"""

from __future__ import annotations

from typing import Any

import pytest

from corpus_kb.errors import ConfigError
from corpus_kb.ingestion.base import TOKEN_BUDGET_CHARS
from corpus_kb.ingestion.tokenizer import hard_split_text, tokenizer_name


class _FakeSemchunk:
    """Minimal stand-in for semchunk.chunk with the offsets=True contract."""

    @staticmethod
    def chunk(
        text: str, chunk_size: int, token_counter: Any, *, offsets: bool = False, **kwargs: Any
    ) -> Any:
        assert offsets is True
        pieces: list[str] = []
        spans: list[tuple[int, int]] = []
        start = 0
        for word in text.split(" "):
            piece = word + " "
            if pieces and token_counter("".join(pieces[-1]) + piece) > chunk_size:
                end = start + len(pieces[-1])
                spans.append((start, end))
                start = end
                pieces.append(piece)
            elif pieces:
                pieces[-1] = pieces[-1] + piece
            else:
                pieces.append(piece)
        if pieces:
            spans.append((start, start + len(pieces[-1])))
        texts = [text[s:e] for s, e in spans]
        return texts, spans


@pytest.fixture
def fake_semchunk(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_kb.ingestion.tokenizer as tokenizer_module

    monkeypatch.setattr(tokenizer_module, "_load_semchunk", lambda: _FakeSemchunk)


def test_no_config_keeps_char_slice_behavior() -> None:
    text = "a" * 100
    budget = 30
    pieces = hard_split_text(text, budget_chars=budget, config=None)
    assert pieces == [text[i : i + budget] for i in range(0, len(text), budget)]
    assert tokenizer_name(None) is None


def test_unknown_tokenizer_value_is_config_error() -> None:
    with pytest.raises(ConfigError):
        tokenizer_name({"ingestion": {"tokenizer": "chonkie"}})


def test_semchunk_config_without_package_degrades_with_note(fake_semchunk: None) -> None:
    import corpus_kb.ingestion.tokenizer as tokenizer_module

    def _missing() -> Any:
        raise ImportError("semchunk not installed")

    tokenizer_module._load_semchunk = _missing  # type: ignore[assignment]
    warnings: list[str] = []
    text = "word " * 4000
    pieces = hard_split_text(
        text,
        budget_chars=TOKEN_BUDGET_CHARS,
        config={"ingestion": {"tokenizer": "semchunk"}},
        warnings=warnings,
    )
    assert "".join(pieces) == text
    assert warnings and "semchunk" in warnings[0]


def test_semchunk_pieces_keep_offset_invariant_and_budget(fake_semchunk: None) -> None:
    text = ("alpha beta gamma delta epsilon. " * 400).strip()
    pieces = hard_split_text(
        text, budget_chars=TOKEN_BUDGET_CHARS, config={"ingestion": {"tokenizer": "semchunk"}}
    )
    rebuilt = []
    pos = 0
    for piece in pieces:
        start = text.find(piece, pos)
        assert start >= 0
        assert text[start : start + len(piece)] == piece
        rebuilt.append(text[start : start + len(piece)] + (" " if start else ""))
        pos = start + len(piece)
    assert len(rebuilt) == len(pieces)
    assert all(len(p) <= TOKEN_BUDGET_CHARS for p in pieces)


def test_real_semchunk_when_installed() -> None:
    pytest.importorskip("semchunk")
    text = ("The quick brown fox jumps over the lazy dog. " * 300).strip()
    pieces = hard_split_text(
        text, budget_chars=TOKEN_BUDGET_CHARS, config={"ingestion": {"tokenizer": "semchunk"}}
    )
    assert len(pieces) > 1
    assert all(piece and len(piece) <= TOKEN_BUDGET_CHARS for piece in pieces)
    # Native offsets: every piece is an exact slice of the canonical text.
    cursor = 0
    for piece in pieces:
        start = text.index(piece, cursor)
        assert text[start : start + len(piece)] == piece
        cursor = start + len(piece)
