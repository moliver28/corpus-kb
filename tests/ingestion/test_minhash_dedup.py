"""Wave 4 MinHash near-duplicate dedup: opt-in, deterministic survivors.

datasketch is a lazy extra (CI never installs it): logic tests run against
a fake module; one API test runs against the real datasketch when present.
The survivor rule is lowest (document_id, start_char, chunk_id).
"""

from __future__ import annotations

import pytest

from corpus_kb.errors import ConfigError
from corpus_kb.ingestion.dedup import dedup_chunks, dedup_config
from corpus_kb.utils.models import Chunk


def _chunk(text: str, doc_id: str = "doc-1", start: int | None = None, cid: str = "c") -> Chunk:
    return Chunk(
        chunk_id=cid,
        document_id=doc_id,
        text=text,
        source_type="text",
        source_start_char=start,
        source_end_char=None if start is None else start + len(text),
    )


def test_dedup_config_gating() -> None:
    assert dedup_config({}) is None
    assert dedup_config({"ingestion": {"dedup": "minhash"}}) == {"threshold": 0.9}
    assert dedup_config({"ingestion": {"dedup": "minhash", "dedup_threshold": 0.8}}) == {
        "threshold": 0.8
    }
    with pytest.raises(ConfigError):
        dedup_config({"ingestion": {"dedup": "bloom"}})


def test_no_duplicates_keeps_everything() -> None:
    chunks = [_chunk("unique body one", cid="c1"), _chunk("different body two", cid="c2")]
    kept, dropped = dedup_chunks(chunks)
    assert len(kept) == 2
    assert dropped == []


def test_near_duplicate_drops_loser_with_survivor_rule() -> None:
    # 26 unique words: one word edit keeps the word-set Jaccard above 0.9.
    base = (
        "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo "
        "lima mike november oscar papa quebec romeo sierra tango uniform "
        "victor whiskey xray yankee zulu"
    )
    near = base.replace("zulu", "zulus")  # near-identical text
    chunks = [
        _chunk(near, start=100, cid="c-late"),
        _chunk(base, start=0, cid="c-early"),
    ]
    kept, dropped = dedup_chunks(chunks, threshold=0.9)
    assert [c.chunk_id for c in kept] == ["c-early"]
    assert dropped == ["c-late"]


def test_survivor_rule_uses_document_id_then_start_char() -> None:
    base = "shared body text for the duplicate probe " * 3
    chunks = [
        _chunk(base, doc_id="doc-2", start=0, cid="c-b"),
        _chunk(base, doc_id="doc-1", start=50, cid="c-a2"),
        _chunk(base, doc_id="doc-1", start=0, cid="c-a1"),
    ]
    kept, dropped = dedup_chunks(chunks, threshold=0.9)
    assert [c.chunk_id for c in kept] == ["c-a1"]
    assert dropped == ["c-a2", "c-b"]


def test_exact_duplicates_are_deterministic_across_runs() -> None:
    text = "repeat me exactly " * 20
    runs = [
        dedup_chunks([_chunk(text, start=0, cid="c1"), _chunk(text, start=18, cid="c2")])
        for _ in range(3)
    ]
    assert all(d == runs[0][1] for d in (r[1] for r in runs))
    assert runs[0][1] == ["c2"]


def test_real_datasketch_when_installed() -> None:
    pytest.importorskip("datasketch")
    base = (
        "research participant describes the retrieval workflow with concrete "
        "numbers from the pilot study and names every tool the team used "
        "during the march review milestone"
    )
    near = base.replace("numbers", "number")
    chunks = [
        _chunk(near, start=300, cid="c-near"),
        _chunk(base, start=0, cid="c-base"),
        _chunk("totally different content about databases and indexes " * 3, start=600, cid="c-x"),
    ]
    kept, dropped = dedup_chunks(chunks, threshold=0.9)
    assert [c.chunk_id for c in kept] == ["c-base", "c-x"]
    assert dropped == ["c-near"]
