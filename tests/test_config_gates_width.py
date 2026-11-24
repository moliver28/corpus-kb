"""The store-width refusal message (issue #559 / G28; plan rulings R4/R5).

One function owns the bytes, and both engines' boot guard imports it, so
a store reopened with a different ``embedding.dimensions`` answers the
same message on Postgres and DuckDB — the (e) identical-UX requirement.
"""

from __future__ import annotations

import re
from pathlib import Path

from corpus_kb.config_gates import store_width_mismatch_error

REPO = Path(__file__).resolve().parents[1]
GUARD_SOURCE = REPO / "src" / "corpus_kb" / "_setup" / "version_guard.py"


def test_width_mismatch_message_identical_engines() -> None:
    """One producer returns deterministic bytes; the guard imports it."""
    message = store_width_mismatch_error(1024, 2560)
    assert message is not None
    assert message == store_width_mismatch_error(1024, 2560)
    assert "1024" in message and "2560" in message
    # Matching widths are not an error.
    assert store_width_mismatch_error(2560, 2560) is None
    # Grep-level half: the boot guard sources the message from this gate
    # (the lazy import inside assert_store_supported), never a local copy.
    guard_text = GUARD_SOURCE.read_text(encoding="utf-8")
    assert "from corpus_kb.config_gates import store_width_mismatch_error" in guard_text


def test_width_message_under_20_words() -> None:
    """Every sentence stays within the 20-word plain-language ceiling."""
    message = store_width_mismatch_error(1024, 2560)
    assert message is not None
    # Sentence breaks are period + whitespace, so the dotted config key
    # (embedding.dimensions) stays inside one sentence.
    sentences = [part.strip() for part in re.split(r"(?<=\.)\s+", message) if part.strip()]
    assert len(sentences) == 2
    for sentence in sentences:
        assert len(sentence.split()) <= 20, sentence
