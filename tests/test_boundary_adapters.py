"""W2 boundary adapters: per-source segment grouping + strategy dispatch.

The shadow hierarchy groups segments by each source family's NATURAL
boundaries instead of headings on every document. Covered here:

* ``segments_from_slides`` — slide grouping, ordering, offsets, and the
  heading fallthrough for unnumbered chunks;
* ``segments_from_transcript_chunks`` — exchange grouping via qa_context
  (part suffixes normalized), window chunks ungrouped;
* ``select_segment_adapter`` — strategy/marker dispatch, precedence,
  determinism, and the pptx strategy-name pin;
* the ``respect_boundaries`` gate matrix for all four boundary kinds
  (speaker_turn, qa_pair, slide, heading): unlisted kinds never merge,
  listed kinds merge same-group segments at the chunk/parent levels;
* two pipeline-level ingests (pptx deck, transcript) through
  ``run_pipeline`` with a fake store, fake embedder, and a capturing
  shadow writer — no Postgres, no Ollama, no markers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest
from pptx_factory import make_research_deck

from corpus_kb.chunking.hierarchy import Segment, merge_groups, pack_windows
from corpus_kb.chunking.hierarchy_paths import (
    segments_from_chunks,
    segments_from_slides,
    segments_from_transcript_chunks,
    segments_from_turns,
)
from corpus_kb.chunking.hierarchy_tree import (
    LEVEL_CHILD,
    LEVEL_CHUNK,
    HierarchyTree,
    build_hierarchy,
)
from corpus_kb.ingest.hierarchy_settings import DEFAULT_RESPECT_BOUNDARIES, HierarchySettings
from corpus_kb.tools import shadow_adapters, shadow_ingest
from corpus_kb.tools.shadow_adapters import (
    ADAPTER_HEADING,
    ADAPTER_SLIDES,
    ADAPTER_TURNS,
    select_segment_adapter,
)
from corpus_kb.utils.models import Chunk

REPO = Path(__file__).resolve().parent.parent
TRANSCRIPT_TEXT = (REPO / "tests" / "fixtures" / "transcripts" / "sample_interview.txt").read_text(
    encoding="utf-8"
)

# Small overlap keeps the merge/no-merge difference structurally visible.
GATE_OVERLAP_TOKENS = 30


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _chunk(
    text: str,
    metadata: dict[str, object],
    heading_path: list[str] | None = None,
) -> Chunk:
    return Chunk(
        chunk_id=f"c-{len(text)}-{id(metadata) % 1000}",
        document_id="d",
        text=text,
        source_type="text",
        metadata=metadata,
        heading_path=heading_path,
    )


def _pptx_chunks(numbered: bool = True) -> list[Chunk]:
    """Deck-plan chunks: slide 1 split into two body parts, slide 2 whole."""
    chunks = [
        _chunk("S" * 240, {"slide_number": 1, "slide_title": "Intro"}, ["Deck", "Intro"]),
        _chunk("T" * 240, {"slide_number": 1, "slide_title": "Intro"}, ["Deck", "Intro"]),
        _chunk("U" * 240, {"slide_number": 2, "slide_title": "Verdicts"}, ["Deck", "Verdicts"]),
    ]
    if not numbered:
        chunks.append(_chunk("V" * 40, {"element_type": "NarrativeText"}, ["Deck", "Intro"]))
    return chunks


def _transcript_chunks() -> list[Chunk]:
    """Transcript-plan chunks: two parts of one exchange, then a window."""

    def qa(exchange_id: str) -> dict[str, object]:
        return {"qa_context": {"question": "q", "participants": ["A"], "exchange_id": exchange_id}}

    return [
        _chunk("question one", qa("exchange-0007")),
        _chunk("answer one", qa("exchange-0007-part2")),
        _chunk("question two", qa("exchange-0008")),
        _chunk("window talk", {"strategy": "transcript", "grouping": "turns"}),
    ]


def _plain_chunks() -> list[Chunk]:
    return [
        _chunk("# Head\n\nBody text.", {"element_type": "Title"}, ["# Head"]),
        _chunk("More body text.", {"element_type": "NarrativeText"}, ["# Head"]),
    ]


@dataclass
class _Turn:
    seq: int
    text: str


def _markdown_gate_chunks() -> list[Chunk]:
    """Three heading sections (head ~2 tokens + 60-token body) as chunks."""
    chunks: list[Chunk] = []
    cursor = 0
    for head in ("# One", "# Two", "# Three"):
        body = "w" * 240
        for text, element in ((head, "Title"), (body, "NarrativeText")):
            chunks.append(
                Chunk(
                    chunk_id=f"md-{cursor}",
                    document_id="md",
                    text=text,
                    source_type="markdown",
                    source_start_char=cursor,
                    source_end_char=cursor + len(text),
                    metadata={"element_type": element},
                    heading_path=[head],
                )
            )
            cursor += len(text) + 2
    return chunks


def _gate_segments() -> dict[str, tuple[list[Segment], str, int]]:
    """Per-kind (segments, group_kind, chunk budget) triples for the matrix.

    Sizes are chosen so unmerged packing breaks groups mid-way while merged
    packing yields group-aligned windows with a different window count
    (hand-checked against the ~4 chars/token estimator).
    """
    slide_segments = segments_from_slides(_pptx_chunks(), "S" * 240 + "T" * 240 + "U" * 240)
    turns = [_Turn(seq=i, text="x" * 120) for i in range(8)]
    turn_segments = segments_from_turns(turns, {seq: seq // 2 for seq in range(8)})
    markdown = _markdown_gate_chunks()
    heading_segments = segments_from_chunks(markdown, "\n\n".join(c.text for c in markdown))
    return {
        "slide": (slide_segments, "slide", 130),
        "qa_pair": (turn_segments, "qa_pair", 150),
        "speaker_turn": (turn_segments, "speaker_turn", 150),
        "heading": (heading_segments, "heading", 100),
    }


def _gate_settings(kinds: tuple[str, ...], chunk_tokens: int) -> HierarchySettings:
    return HierarchySettings(
        child_tokens=64,
        chunk_tokens=chunk_tokens,
        parent_tokens=400,
        overlap_tokens=GATE_OVERLAP_TOKENS,
        respect_boundaries=kinds,
    )


def _texts_at_level(tree: HierarchyTree, level: str) -> list[str]:
    return [node.text for node in tree.by_level(level)]


# ---------------------------------------------------------------------------
# segments_from_slides
# ---------------------------------------------------------------------------


def test_segments_from_slides_group_by_slide_number_in_order() -> None:
    segments = segments_from_slides(_pptx_chunks())
    assert [seg.kind for seg in segments] == ["slide", "slide", "slide"]
    assert [seg.group_id for seg in segments] == ["slide-1", "slide-1", "slide-2"]
    # Segment order follows chunk (slide) order; offsets stay consecutive.
    assert [seg.start_char for seg in segments] == sorted(seg.start_char for seg in segments)
    assert segments[0].end_char == 240 and segments[1].start_char == 240


def test_segments_from_slides_derive_offsets_from_document_text() -> None:
    chunks = _pptx_chunks()
    for chunk in chunks:
        chunk.source_start_char = None
        chunk.source_end_char = None
    original = "S" * 240 + "\n\n" + "T" * 240 + "\n\n" + "U" * 240
    segments = segments_from_slides(chunks, original)
    assert segments[1].start_char == 242
    assert segments[2].end_char == len(original)
    assert all(seg.text in original for seg in segments)


def test_segments_from_slides_unnumbered_chunks_fall_through_to_heading() -> None:
    segments = segments_from_slides(_pptx_chunks(numbered=False))
    kinds = [seg.kind for seg in segments]
    groups = [seg.group_id for seg in segments]
    assert kinds == ["slide", "slide", "slide", "paragraph"]
    assert groups == ["slide-1", "slide-1", "slide-2", "Deck|Intro"]


def test_segments_from_slides_boolean_slide_number_falls_through() -> None:
    # A bool survives JSON round-trips; True is not a slide number.
    segments = segments_from_slides([_chunk("text", {"slide_number": True})])
    assert [seg.kind for seg in segments] == ["paragraph"]
    assert all(seg.group_id is None for seg in segments)


# ---------------------------------------------------------------------------
# segments_from_transcript_chunks
# ---------------------------------------------------------------------------


def test_segments_from_transcript_chunks_group_by_exchange() -> None:
    segments = segments_from_transcript_chunks(_transcript_chunks())
    assert [seg.kind for seg in segments] == ["speaker_turn"] * 4
    assert [seg.group_id for seg in segments] == [
        "exchange-0007",
        "exchange-0007",
        "exchange-0008",
        None,
    ]


def test_segments_from_transcript_chunks_offsets_follow_chunk_text() -> None:
    chunks = _transcript_chunks()
    segments = segments_from_transcript_chunks(chunks)
    assert segments[0].start_char == 0
    assert segments[1].start_char == len("question one")
    assert segments[-1].end_char == sum(len(chunk.text) for chunk in chunks)


# ---------------------------------------------------------------------------
# select_segment_adapter (dispatch)
# ---------------------------------------------------------------------------


def test_pptx_strategy_name_literal_stays_pinned() -> None:
    from corpus_kb.ingestion.strategies.pptx import PPTX_STRATEGY_NAME

    assert shadow_adapters._PPTX_STRATEGY_NAME == PPTX_STRATEGY_NAME


@pytest.mark.parametrize(
    ("chunks", "strategy", "expected"),
    [
        (_pptx_chunks(), "pptx_structure", ADAPTER_SLIDES),
        (_pptx_chunks(), None, ADAPTER_SLIDES),
        (_transcript_chunks(), "transcript", ADAPTER_TURNS),
        (_transcript_chunks(), None, ADAPTER_TURNS),
        (_plain_chunks(), None, ADAPTER_HEADING),
        (_plain_chunks(), "default", ADAPTER_HEADING),
    ],
)
def test_select_segment_adapter_by_strategy_and_markers(
    chunks: list[Chunk], strategy: str | None, expected: str
) -> None:
    assert select_segment_adapter(chunks, strategy) == expected


def test_select_segment_adapter_slide_markers_win_over_transcript() -> None:
    mixed = [*_pptx_chunks()[:1], *_transcript_chunks()[:1]]
    assert select_segment_adapter(mixed, "transcript") == ADAPTER_SLIDES
    assert select_segment_adapter(mixed, None) == ADAPTER_SLIDES


def test_select_segment_adapter_is_deterministic() -> None:
    cases: list[tuple[list[Chunk], str | None]] = [
        (_pptx_chunks(), "pptx_structure"),
        (_pptx_chunks(), None),
        (_transcript_chunks(), "transcript"),
        (_transcript_chunks(), None),
        (_plain_chunks(), None),
        (_plain_chunks(), "default"),
    ]
    for chunks, strategy in cases:
        assert select_segment_adapter(chunks, strategy) == select_segment_adapter(chunks, strategy)


def test_build_main_path_tree_uses_adapter_group_kinds() -> None:
    settings = HierarchySettings()
    heading_tree = build_hierarchy(
        "d1",
        segments_from_chunks(_plain_chunks()),
        settings,
        group_kind="heading",
    )
    slide_tree = build_hierarchy(
        "d2",
        segments_from_slides(_pptx_chunks()),
        settings,
        group_kind="slide",
    )
    turn_tree = build_hierarchy(
        "d3",
        segments_from_transcript_chunks(_transcript_chunks()),
        settings,
        group_kind="qa_pair",
    )
    assert all(node.boundary == "heading" for node in heading_tree.by_level(LEVEL_CHUNK))
    assert all(node.boundary == "slide" for node in slide_tree.by_level(LEVEL_CHUNK))
    mid = turn_tree.by_level(LEVEL_CHUNK)
    assert mid and all(node.boundary == "qa_pair" for node in mid)


# ---------------------------------------------------------------------------
# respect_boundaries gate matrix (all four kinds)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["speaker_turn", "qa_pair", "slide", "heading"])
def test_respect_boundaries_gate_merges_only_listed_kinds(kind: str) -> None:
    segments, group_kind, budget = _gate_segments()[kind]
    assert group_kind == kind
    off = _gate_settings(tuple(k for k in DEFAULT_RESPECT_BOUNDARIES if k != kind), budget)
    on = _gate_settings(DEFAULT_RESPECT_BOUNDARIES, budget)
    off_tree = build_hierarchy("doc-off", segments, off, group_kind=group_kind)
    on_tree = build_hierarchy("doc-on", segments, on, group_kind=group_kind)

    raw_windows = pack_windows(segments, budget, GATE_OVERLAP_TOKENS)
    merged_windows = pack_windows(merge_groups(segments, group_kind), budget, GATE_OVERLAP_TOKENS)
    assert _texts_at_level(off_tree, LEVEL_CHUNK) == [
        "".join(seg.text for seg in window) for window in raw_windows
    ]
    assert _texts_at_level(on_tree, LEVEL_CHUNK) == [
        "".join(seg.text for seg in window) for window in merged_windows
    ]
    # The gate observably changes the mid level: merged windows join their
    # segments (group separator included), unmerged windows do not.
    off_texts = _texts_at_level(off_tree, LEVEL_CHUNK)
    on_texts = _texts_at_level(on_tree, LEVEL_CHUNK)
    assert off_texts != on_texts
    assert any("\n\n" in text for text in on_texts)
    assert all("\n\n" not in text for text in off_texts)
    # Merged mid-level windows start at group boundaries and carry the kind.
    merged_starts = {seg.start_char for seg in merge_groups(segments, group_kind)}
    for node in on_tree.by_level(LEVEL_CHUNK):
        assert node.boundary == group_kind
        assert node.start_char in merged_starts
    # Children pack RAW segments at every setting (the retrieval unit).
    child_windows = pack_windows(segments, 64, GATE_OVERLAP_TOKENS)
    assert _texts_at_level(on_tree, LEVEL_CHILD) == [
        "".join(seg.text for seg in window) for window in child_windows
    ]


# ---------------------------------------------------------------------------
# Pipeline-level: deck and transcript ingests build grouped shadow trees
# ---------------------------------------------------------------------------


class _LockConn:
    async def fetchval(self, sql: str, *args: object) -> bool:
        return True

    async def execute(self, sql: str, *args: object) -> str:
        return "OK"


class _FakePool:
    def acquire(self) -> _FakePool:
        return self

    async def __aenter__(self) -> _LockConn:
        return _LockConn()

    async def __aexit__(self, *args: object) -> bool:
        return False


class _FakeStore:
    def __init__(self, pool: object, tenant_id: str = "") -> None:
        self.pool = pool
        self.tenant_id = tenant_id

    async def unchanged_doc_id(self, path: str, file_hash: str) -> None:
        return None

    async def begin_run(self, document: object, file_hash: str) -> tuple[str, str]:
        return "doc-w2", "run-w2"

    async def store_chunks(self, chunks: list[Chunk]) -> set[str]:
        return {str(chunk.chunk_id) for chunk in chunks}

    async def store_vectors(
        self,
        chunks: list[Chunk],
        embedding_model: str,
        dimensions: int = 768,
        vectors_1024: dict[str, list[float]] | None = None,
    ) -> int:
        return len(chunks)

    async def update_document(self, document: object, file_hash: str) -> None:
        return None

    async def finish_run(
        self,
        run_id: str,
        file_hash: str,
        stages: dict[str, object],
        errors: list[str],
    ) -> None:
        return None

    async def fail_run(self, run_id: str, error: str) -> None:
        return None


class _CaptureWriter:
    """Stands in for ShadowIngestWriter and records the built trees."""

    trees: ClassVar[list[HierarchyTree]] = []

    def __init__(self, pool: object, tenant_id: str = "") -> None:
        self.pool = pool
        self.tenant_id = tenant_id

    async def store_tree(
        self,
        tree: HierarchyTree,
        vectors_1024: dict[str, list[float]] | None = None,
        embedding_model: str = "nomic-embed-text",
        metadata_by_key: dict[str, dict[str, object]] | None = None,
    ) -> tuple[int, int]:
        _CaptureWriter.trees.append(tree)
        return len(tree.nodes), len(vectors_1024 or {})


async def _fake_embed(
    chunks: list[Chunk],
    config: dict[str, object],
    pool: object = None,
    **kwargs: object,
) -> tuple[bool, None]:
    for chunk in chunks:
        chunk.embedding = [0.1] * 768
    return False, None


@pytest.fixture()
def pipeline_harness(monkeypatch: pytest.MonkeyPatch) -> list[HierarchyTree]:
    """Fake stores + embedder + capturing shadow writer; returns the trees."""
    from corpus_kb.tools import ingest_common

    _CaptureWriter.trees = []
    monkeypatch.setattr(ingest_common, "PostgresIngestStore", _FakeStore)
    monkeypatch.setattr(ingest_common, "embed_chunks", _fake_embed)
    monkeypatch.setattr(shadow_ingest, "ShadowIngestWriter", _CaptureWriter)
    return _CaptureWriter.trees


def _pipeline_config() -> dict[str, object]:
    return {
        "graph": {"extract_entities": False},
        "ingestion": {
            "strategies": {"transcript": "transcript", "pptx": "pptx_structure"},
            "default": "default",
        },
        "embedding": {
            "provider": "pgml",
            "fallback_provider": "ollama",
            "model": "nomic-embed-text",
            "base_url": "http://localhost:65432",
            "dimensions": 768,
            "batch_size": 32,
        },
        "ingest": {"chunking": {"enabled": True}},
    }


async def test_pptx_ingest_shadow_tree_groups_by_slide(
    tmp_path: Path,
    pipeline_harness: list[HierarchyTree],
) -> None:
    from corpus_kb.tools.ingest_common import run_pipeline

    deck = make_research_deck(tmp_path / "deck.pptx")
    result = await run_pipeline(
        "", "pptx", str(deck), _pipeline_config(), _FakePool(), is_file=True
    )
    assert result["status"] == "success"
    assert result["shadow_chunk_count"] > 0
    assert "boundary adapter: slides" in str(result["shadow_note"])
    tree = pipeline_harness[-1]
    children = tree.by_level(LEVEL_CHILD)
    mid = tree.by_level(LEVEL_CHUNK)
    assert children and all(node.boundary == "slide" for node in children)
    assert mid and all(node.boundary == "slide" for node in mid)
    # Slide grouping merged the mid level below the raw child count.
    assert len(mid) < len(children)


async def test_transcript_ingest_shadow_tree_groups_by_exchanges(
    pipeline_harness: list[HierarchyTree],
) -> None:
    from corpus_kb.tools.ingest_common import run_pipeline

    result = await run_pipeline(
        TRANSCRIPT_TEXT, "transcript", "w2-transcript", _pipeline_config(), _FakePool()
    )
    assert result["status"] == "success"
    assert result["shadow_chunk_count"] > 0
    assert "boundary adapter: turns" in str(result["shadow_note"])
    tree = pipeline_harness[-1]
    children = tree.by_level(LEVEL_CHILD)
    mid = tree.by_level(LEVEL_CHUNK)
    assert children and all(node.boundary == "speaker_turn" for node in children)
    # Exchange groups merge SOME mid windows; nothing foreign appears.
    assert any(node.boundary == "qa_pair" for node in mid)
    assert all(node.boundary in {"qa_pair", "speaker_turn"} for node in mid)


async def test_default_ingest_keeps_heading_adapter(
    pipeline_harness: list[HierarchyTree],
) -> None:
    from corpus_kb.tools.ingest_common import run_pipeline

    text = "# Alpha\n\nGateway routes requests.\n\n# Beta\n\nAlerts page the on-call."
    result = await run_pipeline(text, "markdown", "w2-markdown", _pipeline_config(), _FakePool())
    assert result["status"] == "success"
    tree = pipeline_harness[-1]
    children = tree.by_level(LEVEL_CHILD)
    assert children and all(node.boundary in {"heading", "paragraph"} for node in children)
