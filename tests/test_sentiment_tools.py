"""The sentiment screen: preset lexicon -> labeling functions -> aggregation.

The lexicon composition runs through the EXISTING labeling engine
(``score_matrix``) and the existing Dawid-Skene vote aggregator
(``aggregate_signals``). Real lexicon file, real engine, fake storage
at the one read seam. Covered: the honest disabled/unavailable/empty
statuses, the vote semantics (tie routes to review, no signal abstains,
non-membership is honest), the chunk-id filter and row cap, the
evidence note (this is a preset composition, not a trained model), and
byte-identical lexicon pair.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from corpus_kb.tools import sentiment_tools

TENANT = "00000000-0000-0000-0000-000000000001"

REPO_LEXICON = Path("config/sentiment_lexicon.yaml")
PACKAGE_LEXICON = Path("src/corpus_kb/config/sentiment_lexicon.yaml")

ON = {"analysis": {"sentiment": {"enabled": True, "lexicon_path": str(REPO_LEXICON)}}}


class _Chunk:
    def __init__(self, chunk_id: str, text: str) -> None:
        self.chunk_id = chunk_id
        self.text = text
        self.metadata: dict[str, object] = {}


def _patch_chunks(monkeypatch: pytest.MonkeyPatch, chunks: list[_Chunk]) -> None:
    async def fake_read(pool: Any, tenant_id: str, limit: int = 100000) -> list[_Chunk]:
        return chunks

    monkeypatch.setattr(sentiment_tools.engine, "read_chunks", fake_read)


CORPUS = [
    _Chunk("c1", "I am happy with the great result."),
    _Chunk("c2", "This is a terrible failure and a waste of money."),
    _Chunk("c3", "The team reports great progress but a terrible delay."),  # tie -> review
    _Chunk("c4", "The meeting is on Tuesday in room four."),  # no signal -> abstain
]


def test_lexicon_pair_is_byte_identical() -> None:
    assert REPO_LEXICON.read_bytes() == PACKAGE_LEXICON.read_bytes()


def test_lexicon_lists_are_disjoint_common_words() -> None:
    import yaml

    data = yaml.safe_load(REPO_LEXICON.read_text(encoding="utf-8"))
    positive = data["positive"]
    negative = data["negative"]
    assert set(positive).isdisjoint(negative)
    assert len(positive) + len(negative) >= 150
    assert all(word == word.lower() for word in positive + negative)


async def test_disabled_by_default() -> None:
    result = await sentiment_tools.sentiment_screen(None, config=None, tenant_id=TENANT)
    assert result["status"] == "disabled"
    assert "analysis.sentiment.enabled" in str(result["reason"])
    assert result["predictions"] == []
    assert result["screened_count"] == 0


async def test_missing_lexicon_file_answers_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {"analysis": {"sentiment": {"enabled": True, "lexicon_path": "nope/missing.yaml"}}}
    result = await sentiment_tools.sentiment_screen(None, config=config, tenant_id=TENANT)
    assert result["status"] == "unavailable"
    assert "lexicon" in str(result["reason"])


async def test_malformed_lexicon_raises_loudly(tmp_path: Path) -> None:
    bad = tmp_path / "lexicon.yaml"
    bad.write_text("positive: [good]\nnegative: 'not a list'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="lexicon"):
        sentiment_tools.load_lexicon(str(bad))


async def test_overlapping_lists_are_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "lexicon.yaml"
    bad.write_text("positive: [good]\nnegative: [good, bad]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="disjoint"):
        sentiment_tools.load_lexicon(str(bad))


async def test_success_labels_review_and_abstain_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_chunks(monkeypatch, CORPUS)
    result = await sentiment_tools.sentiment_screen(None, config=ON, tenant_id=TENANT)
    assert result["status"] == "success"
    assert result["screened_count"] == 4
    by_id = {row["chunk_id"]: row for row in result["predictions"]}
    assert by_id["c1"]["label"] == "positive"
    assert by_id["c2"]["label"] == "negative"
    # The tie routes to review; the label still names the majority side.
    assert by_id["c3"]["review"] is True
    # No signal means no row: non-membership is honest.
    assert "c4" not in by_id
    assert by_id["c1"]["review"] is False
    for row in result["predictions"]:
        assert 0.0 <= row["probability"] <= 1.0
        assert row["probability"] == round(row["probability"], 6)


async def test_evidence_note_names_the_preset_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_chunks(monkeypatch, CORPUS)
    result = await sentiment_tools.sentiment_screen(None, config=ON, tenant_id=TENANT)
    note = str(result["evidence_note"]).lower()
    assert "preset" in note
    assert "not a trained" in note
    assert result["params"]["positive_terms"] > 0
    assert result["params"]["negative_terms"] > 0
    assert result["params"]["lexicon_path"] == str(REPO_LEXICON)


async def test_chunk_id_filter_shrinks_the_denominator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_chunks(monkeypatch, CORPUS)
    result = await sentiment_tools.sentiment_screen(
        None, config=ON, tenant_id=TENANT, chunk_ids=["c1", "c2"]
    )
    assert result["screened_count"] == 2
    assert {row["chunk_id"] for row in result["predictions"]} == {"c1", "c2"}


async def test_limit_caps_rows_but_not_the_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_chunks(monkeypatch, CORPUS)
    result = await sentiment_tools.sentiment_screen(None, config=ON, tenant_id=TENANT, limit=1)
    assert len(result["predictions"]) == 1
    assert result["screened_count"] == 4


async def test_no_chunks_answer_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_chunks(monkeypatch, [])
    result = await sentiment_tools.sentiment_screen(None, config=ON, tenant_id=TENANT)
    assert result["status"] == "empty"
    assert str(result["remedy"])
