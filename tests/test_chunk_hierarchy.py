"""Offline tests for the U48/P6 multi-granularity token hierarchy.

Pure functions: determinism, token budgets, boundary snapping on a fixture
transcript (speaker_turn) and a fixture markdown (heading), overlap
correctness, parent-link completeness, oversized-segment hard splitting,
and the typed settings loader. No DB, no Ollama, no markers.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import pytest

from corpus_kb.chunking.hierarchy import (
    CHARS_PER_TOKEN,
    Segment,
    estimate_tokens,
    merge_groups,
    pack_windows,
    split_oversized,
)
from corpus_kb.chunking.hierarchy_paths import segments_from_chunks, segments_from_turns
from corpus_kb.chunking.hierarchy_settings import (
    CHUNKING_CONFIG_DEFAULTS,
    HierarchySettings,
    load_hierarchy_settings,
)
from corpus_kb.chunking.hierarchy_tree import (
    LEVEL_CHUNK,
    LEVEL_PARENT,
    build_hierarchy,
)
from corpus_kb.research.retrieval_settings import load_retrieval_settings
from corpus_kb.utils.models import Chunk

DEFAULTS = HierarchySettings()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class _Turn:
    """Minimal speaker-turn stand-in (TurnSpec shape)."""

    seq: int
    text: str


def _transcript_turns() -> list[_Turn]:
    # ~65 estimated tokens per turn: each turn is a single atomic child
    # window block (well under child_tokens=128) while the 8-turn document
    # (~530 tokens) spans several child windows and two 512-token chunk
    # windows but stays under the 2048-token parent level.
    texts = [
        "Moderator: Walk me through your very first week using the new analytics dashboard, "
        "and tell me exactly what you expected to see when you opened the billing tab for the "
        "very first time during onboarding last month, before anybody had shown you around "
        "the workspace.",
        "Participant: Honestly, it was confusing right from the start; the billing tab hid "
        "the usage chart entirely behind a collapsed accordion, so for two full days I "
        "assumed the workspace simply had no usage reporting at all, and I told our team lead "
        "exactly that in standup.",
        "Moderator: What exactly did you try first when the usage chart did not load the way "
        "you expected it to load, and how long did you spend poking at the collapsed "
        "accordion before you gave up on it completely and started looking for the data "
        "somewhere else entirely?",
        "Participant: I reloaded the page twice, then exported the raw CSV file and plotted "
        "it myself in a spreadsheet; the whole detour took maybe twenty minutes of my first "
        "workday, and I nearly filed a support ticket before a teammate spotted the accordion "
        "handle by accident.",
        "Moderator: Would you pay for the CSV export feature alone, if the dashboard chart "
        "stayed exactly as hidden and broken as it is right now in the current build that you "
        "are using every single day for your weekly reporting and monthly reviews with the "
        "growth team?",
        "Participant: Perhaps, but only if the export kept the segment filters intact; "
        "without the filtered exports the spreadsheet detour is honestly faster than clicking "
        "through all of your menus and dialogs, and my exports would still need manual "
        "cleanup afterwards.",
        "Moderator: Any last blockers that you want to flag for the team before we close out "
        "today's session and move on to the scheduling questions for the next round of "
        "interviews we have planned for sometime early next week with the rest of your group?",
        "Participant: Onboarding, onboarding, onboarding; that collapsed accordion cost us a "
        "full week of trust before anybody on our team found the usage chart hidden behind "
        "it, and nobody has reopened it voluntarily since that first Friday when we all "
        "looked together.",
    ]
    return [_Turn(seq=i, text=text) for i, text in enumerate(texts)]


def _markdown_chunks() -> list[Chunk]:
    """Three heading sections of ~180 tokens each (~540 total): two 512-token
    chunk windows, both snapping to heading-section boundaries."""
    bodies = [
        (
            "The gateway routes every incoming request through the shared auth filter before it"
            " reaches the shard pools, and the shard pools in turn write their late events to a"
            " durable backfill queue that a nightly compactor drains into the cold storage tier;"
            " when the queue grows past its high watermark the compactor signals the router to"
            " shed low-priority writes first so the interactive paths stay fast, and the whole"
            " loop is supervised by a lease manager that fails over to the standby coordinator"
            " within seconds if the primary stops heartbeating, a transition the on-call guides"
            " rehearse quarterly under live traffic so the pager stays quiet during real failovers"
            " and the shard maps republish cleanly."
        ),
        (
            "Alerts page the on-call engineer only after two consecutive probe failures across two"
            " distinct regions, which keeps single-region network flaps from waking anybody at"
            " night; the paging policy then escalates through a three-step ladder that starts"
            " with a chat message, moves to a phone call after five minutes of silence, and"
            " finally opens a predefined incident channel so the responder has context, runbooks,"
            " and the authority to roll back a suspected deploy without waiting for a manager to"
            " wake up and approve anything at all, a policy the reliability group reviews after"
            " every quarter's postmortems and tunes against the false-page rate"
            " they measured across all of the served regions."
        ),
        (
            "Capacity planning runs weekly against the trailing four weeks of shard-level"
            " utilization curves, and the planner proposes shard splits only when both the p99"
            " latency and the queue depth budgets would be violated under the forecast peak;"
            " proposals land as reviewable diffs, and an operator applies them during a"
            " maintenance window with a one-command rollback ready before the traffic season"
            " peaks and the on-call rotation doubles in size, because a split executed under"
            " load without a rehearsed rollback path has historically cost more than the"
            " saturation it was meant to prevent, as the retro on the spring incident showed"
            " everyone who was watching it closely."
        ),
    ]
    heads = ["# Architecture", "# Operations", "# Capacity Planning"]
    chunks: list[Chunk] = []
    cursor = 0
    for head, body in zip(heads, bodies, strict=True):
        for text, element_type in ((head, "Title"), (body, "NarrativeText")):
            chunks.append(
                Chunk(
                    chunk_id=f"md-{cursor}",
                    document_id="md-doc",
                    text=text,
                    source_type="markdown",
                    source_start_char=cursor,
                    source_end_char=cursor + len(text),
                    metadata={"element_type": element_type},
                    heading_path=[head],
                )
            )
            cursor += len(text) + 2  # the "\n\n" separator in the original text
    return chunks


# ---------------------------------------------------------------------------
# Token estimator
# ---------------------------------------------------------------------------


def test_estimate_tokens_matches_chars_per_token_rule() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("ab") == 1
    assert estimate_tokens("a" * (CHARS_PER_TOKEN * 10)) == 10
    assert estimate_tokens("a" * (CHARS_PER_TOKEN * 10 + 1)) == 11


# ---------------------------------------------------------------------------
# Settings loader
# ---------------------------------------------------------------------------


def test_settings_default_to_committed_upgrade_program_values() -> None:
    settings = load_hierarchy_settings({})
    assert settings == HierarchySettings()
    assert (settings.child_tokens, settings.chunk_tokens, settings.parent_tokens) == (
        128,
        512,
        2048,
    )
    assert settings.overlap_tokens == 64
    assert settings.respect_boundaries == ("speaker_turn", "qa_pair", "slide", "heading")
    assert settings.enabled is True


def test_expand_to_parent_lives_under_retrieval_now() -> None:
    """The query-time arm moved to retrieval.expand_to_parent (config v2).

    The hierarchy settings no longer carry it; the typed retrieval reader
    owns it and defaults it to TRUE.
    """
    settings = load_hierarchy_settings({"ingest": {"chunking": {"expand_to_parent": True}}})
    assert not hasattr(settings, "expand_to_parent")
    retrieval = load_retrieval_settings({})
    assert retrieval.expand_to_parent is True


def test_settings_loader_roundtrips_chunking_config_defaults() -> None:
    """CHUNKING_CONFIG_DEFAULTS is exactly the block the orchestrator wires
    into ingest.chunking.*; loading it must reproduce the default settings."""
    settings = load_hierarchy_settings({"ingest": {"chunking": dict(CHUNKING_CONFIG_DEFAULTS)}})
    assert settings == HierarchySettings()


def test_settings_loader_applies_overrides() -> None:
    settings = load_hierarchy_settings(
        {
            "ingest": {
                "chunking": {
                    "enabled": False,
                    "child_tokens": 96,
                    "chunk_tokens": 384,
                    "parent_tokens": 1024,
                    "overlap_tokens": 32,
                    "respect_boundaries": ["heading"],
                }
            }
        }
    )
    assert settings.enabled is False
    assert settings.child_tokens == 96
    assert settings.chunk_tokens == 384
    assert settings.parent_tokens == 1024
    assert settings.overlap_tokens == 32
    assert settings.respect_boundaries == ("heading",)


@pytest.mark.parametrize(
    ("block", "match"),
    [
        ({"child_tokens": 0}, "positive int"),
        ({"child_tokens": "128"}, "must be an int"),
        ({"enabled": "yes"}, "must be a bool"),
        ({"respect_boundaries": "heading"}, "must be a list"),
        ({"respect_boundaries": ["nonsense"]}, "unknown kinds"),
        ({"respect_boundaries": [1, 2]}, "must be a list of strings"),
    ],
)
def test_settings_loader_rejects_invalid_values(block: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        load_hierarchy_settings({"ingest": {"chunking": block}})


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_hierarchy_is_deterministic() -> None:
    """Same input -> the same three-level tree, field for field."""
    segments = segments_from_turns(_transcript_turns())
    first = build_hierarchy("doc-1", segments, DEFAULTS, group_kind="qa_pair")
    second = build_hierarchy("doc-1", segments, DEFAULTS, group_kind="qa_pair")
    assert first.nodes == second.nodes
    assert [node.text for node in first.by_level("child")] == [
        node.text for node in second.by_level("child")
    ]


def test_hierarchy_levels_all_present_for_multi_segment_input() -> None:
    segments = segments_from_turns(_transcript_turns())
    tree = build_hierarchy("doc-2", segments, DEFAULTS)
    assert len(tree.by_level("child")) >= 2
    assert len(tree.by_level(LEVEL_CHUNK)) >= 1
    assert len(tree.by_level(LEVEL_PARENT)) == 1


# ---------------------------------------------------------------------------
# Boundary snapping — research path (speaker turns / qa pairs)
# ---------------------------------------------------------------------------


def test_child_windows_snap_to_speaker_turn_boundaries() -> None:
    """Children never start or end mid-turn: every edge equals a turn edge."""
    turns = _transcript_turns()
    segments = segments_from_turns(turns)
    tree = build_hierarchy("doc-3", segments, DEFAULTS)
    turn_starts = {seg.start_char for seg in segments}
    turn_ends = {seg.end_char for seg in segments}
    for child in tree.by_level("child"):
        assert child.start_char in turn_starts
        assert child.end_char in turn_ends
        assert child.boundary == "speaker_turn"
        assert child.text in "".join(turn.text for turn in turns)


def _shared_prefix_tokens(prev_text: str, nxt_text: str) -> int:
    """Estimated tokens of the verbatim prev-text suffix carried at nxt's start.

    Windows join their segments verbatim, so the overlap rewind is a char
    suffix of the previous text AND a char prefix of the next text.
    """
    limit = min(len(prev_text), len(nxt_text))
    for k in range(limit, 0, -1):
        if nxt_text.startswith(prev_text[-k:]):
            return estimate_tokens(prev_text[-k:])
    return 0


def test_adjacent_children_carry_at_least_overlap_tokens() -> None:
    """The overlap rewind is carried verbatim: each next child STARTS with a
    tail of the previous child worth at least overlap_tokens (or the whole
    previous child when it is smaller than the overlap)."""
    turns = _transcript_turns()
    segments = segments_from_turns(turns)
    tree = build_hierarchy("doc-4", segments, DEFAULTS)
    children = tree.by_level("child")
    assert len(children) >= 2
    for prev, nxt in pairwise(children):
        shared = _shared_prefix_tokens(prev.text, nxt.text)
        assert shared >= min(DEFAULTS.overlap_tokens, prev.token_count)


def test_qa_pair_grouping_snaps_mid_levels_to_exchanges() -> None:
    turns = _transcript_turns()
    exchange_of = {turn.seq: turn.seq // 2 for turn in turns}
    segments = segments_from_turns(turns, exchange_of)
    tree = build_hierarchy("doc-5", segments, DEFAULTS, group_kind="qa_pair")
    exchange_starts = {seg.start_char for seg in merge_groups(segments, "qa_pair")}
    for chunk_node in tree.by_level(LEVEL_CHUNK):
        assert chunk_node.boundary == "qa_pair"
        assert chunk_node.start_char in exchange_starts


def test_qa_pair_not_in_respect_boundaries_packs_raw_turns() -> None:
    turns = _transcript_turns()
    exchange_of = {turn.seq: turn.seq // 2 for turn in turns}
    segments = segments_from_turns(turns, exchange_of)
    settings = load_hierarchy_settings(
        {"ingest": {"chunking": {"respect_boundaries": ["speaker_turn"]}}}
    )
    tree = build_hierarchy("doc-6", segments, settings, group_kind="qa_pair")
    for chunk_node in tree.by_level(LEVEL_CHUNK):
        assert chunk_node.boundary == "speaker_turn"


# ---------------------------------------------------------------------------
# Boundary snapping — main path (headings)
# ---------------------------------------------------------------------------


def test_chunk_level_snaps_to_headings_on_markdown() -> None:
    original = "\n\n".join(chunk.text for chunk in _markdown_chunks())
    segments = segments_from_chunks(_markdown_chunks(), original)
    assert [seg.kind for seg in segments] == [
        "heading",
        "paragraph",
        "heading",
        "paragraph",
        "heading",
        "paragraph",
    ]
    tree = build_hierarchy("md-doc", segments, DEFAULTS, group_kind="heading")
    chunk_nodes = tree.by_level(LEVEL_CHUNK)
    # ~540 tokens over three ~180-token heading sections -> two 512-token
    # chunk windows, each starting exactly at a heading-section boundary
    # (the overlap rewind carries whole sections, so both start on "#").
    assert len(chunk_nodes) == 2
    for chunk_node in chunk_nodes:
        assert chunk_node.boundary == "heading"
        assert chunk_node.text.startswith("#")


def test_segments_from_chunks_derives_missing_offsets() -> None:
    chunks = [
        Chunk(chunk_id="c1", document_id="d", text="alpha beta", source_type="text"),
        Chunk(chunk_id="c2", document_id="d", text="gamma delta", source_type="text"),
    ]
    original = "alpha beta...gamma delta"
    segments = segments_from_chunks(chunks, original)
    assert [(seg.start_char, seg.end_char) for seg in segments] == [(0, 10), (13, 24)]
    assert [seg.text for seg in segments] == ["alpha beta", "gamma delta"]


def test_code_symbol_chunks_tag_symbol_boundary_kind() -> None:
    """AST-path chunks carrying an entity_type are atomic symbol units (the
    'symbol' boundary vocabulary of the main path); they are never grouped."""
    chunks = [
        Chunk(
            chunk_id="sym-1",
            document_id="code-doc",
            text="def load(): ...",
            source_type="code",
            entity_type="function",
            metadata={},
        ),
        Chunk(
            chunk_id="sym-2",
            document_id="code-doc",
            text="class Repo: ...",
            source_type="code",
            entity_type="class",
            metadata={},
        ),
    ]
    segments = segments_from_chunks(chunks)
    assert [seg.kind for seg in segments] == ["symbol", "symbol"]
    assert all(seg.group_id is None for seg in segments)
    # 'symbol' is a recognized ingest.chunking.respect_boundaries kind.
    settings = load_hierarchy_settings(
        {"ingest": {"chunking": {"respect_boundaries": ["symbol", "heading"]}}}
    )
    assert settings.respect_boundaries == ("symbol", "heading")


# ---------------------------------------------------------------------------
# Parent links
# ---------------------------------------------------------------------------


def test_parent_links_complete_with_backlinks() -> None:
    segments = segments_from_turns(_transcript_turns())
    tree = build_hierarchy("doc-7", segments, DEFAULTS)
    children = tree.by_level("child")
    chunks = tree.by_level(LEVEL_CHUNK)
    parents = tree.by_level(LEVEL_PARENT)
    chunk_keys = {node.key for node in chunks}
    parent_keys = {node.key for node in parents}

    linked_children = [child for child in children if child.parent_key is not None]
    assert linked_children, "every child must link to its chunk-level parent"
    for child in linked_children:
        assert child.parent_key in chunk_keys
    for chunk_node in chunks:
        if chunk_node.parent_key is not None:
            assert chunk_node.parent_key in parent_keys
        else:
            assert len(parents) == 0, "dangling chunk without a parent level"
    for parent in parents:
        assert parent.parent_key is None

    # Backlinks mirror the forward links exactly.
    for chunk_node in chunks:
        expected = tuple(c.key for c in linked_children if c.parent_key == chunk_node.key)
        assert chunk_node.child_keys == expected


# ---------------------------------------------------------------------------
# Token budgets (with the documented atomic-segment slack)
# ---------------------------------------------------------------------------


def test_all_levels_respect_token_budget_with_documented_slack() -> None:
    segments = segments_from_turns(_transcript_turns())
    tree = build_hierarchy("doc-8", segments, DEFAULTS)
    max_atomic = max(estimate_tokens(seg.text) for seg in segments)
    for level, target in (
        ("child", DEFAULTS.child_tokens),
        (LEVEL_CHUNK, DEFAULTS.chunk_tokens),
        (LEVEL_PARENT, DEFAULTS.parent_tokens),
    ):
        for node in tree.by_level(level):
            assert node.token_count <= target + DEFAULTS.overlap_tokens + max_atomic
            assert node.token_count == estimate_tokens(node.text)


# ---------------------------------------------------------------------------
# Oversized segments (hard split)
# ---------------------------------------------------------------------------


def test_oversized_segment_hard_splits_at_whitespace_with_overlap() -> None:
    long_text = " ".join(f"token{i}x" for i in range(400))  # ~4000 chars ~ 1000 tokens
    segment = Segment(text=long_text, start_char=0, end_char=len(long_text), kind="slide")
    pieces = split_oversized(segment, DEFAULTS.child_tokens, DEFAULTS.overlap_tokens)
    assert len(pieces) >= 4
    for piece in pieces:
        assert piece.kind == "split"
        assert estimate_tokens(piece.text) <= DEFAULTS.child_tokens
        assert piece.text.strip().startswith("token")
    # Consecutive pieces overlap: the next piece starts inside the previous.
    for prev, nxt in pairwise(pieces):
        assert nxt.text[:20] in prev.text
    # Char offsets stay aligned and ordered.
    assert pieces[0].start_char == 0
    assert [p.start_char for p in pieces] == sorted(p.start_char for p in pieces)
    assert pieces[-1].end_char == len(long_text)


def test_oversized_segment_becomes_one_window_per_piece() -> None:
    long_text = " ".join(f"w{i}" for i in range(600))
    segments = [Segment(text=long_text, start_char=0, end_char=len(long_text), kind="slide")]
    windows = pack_windows(segments, DEFAULTS.child_tokens, DEFAULTS.overlap_tokens)
    assert len(windows) >= 3
    for window in windows:
        assert len(window) == 1
        assert estimate_tokens(window[0].text) <= DEFAULTS.child_tokens


def test_split_oversized_single_unbroken_run_terminates_and_covers() -> None:
    # Regression: a single whitespace-free run longer than the target used to
    # rewind the cut back to the piece's first word forever — this call never
    # returned. Completeness assertions bound the check instead of a timeout.
    giant = "A" * 600  # ~150 estimated tokens in ONE unbreakable word
    long_text = f"{giant} followed by perfectly normal prose words"
    segment = Segment(text=long_text, start_char=0, end_char=len(long_text), kind="slide")
    pieces = split_oversized(segment, DEFAULTS.child_tokens, DEFAULTS.overlap_tokens)
    assert pieces, "hard split must yield at least one piece"
    # Pieces jointly cover the full segment text.
    assert pieces[0].start_char == 0
    assert pieces[-1].end_char == len(long_text)
    for prev, nxt in pairwise(pieces):
        assert nxt.start_char <= prev.end_char, "no gap between consecutive pieces"
    # The unbreakable run is emitted whole (documented hard-split semantics).
    assert any(giant in piece.text for piece in pieces)


def test_split_oversized_long_url_with_prose_terminates_and_covers() -> None:
    url = "https://example.com/" + "a" * 300  # one unbreakable ~80-token run
    long_text = f"See {url} for details, plus some additional prose that follows the link."
    segment = Segment(text=long_text, start_char=0, end_char=len(long_text), kind="slide")
    pieces = split_oversized(segment, DEFAULTS.child_tokens, DEFAULTS.overlap_tokens)
    assert pieces
    assert pieces[0].start_char == 0
    assert pieces[-1].end_char == len(long_text)
    for prev, nxt in pairwise(pieces):
        assert nxt.start_char <= prev.end_char
    assert any(url in piece.text for piece in pieces)


def test_pack_windows_with_unbroken_run_terminates_and_covers() -> None:
    # Production entry point (hierarchy_tree.pack_windows) on the same input.
    text = "A" * 600 + " " + " ".join(f"w{i}" for i in range(50))
    segments = [Segment(text=text, start_char=0, end_char=len(text), kind="slide")]
    windows = pack_windows(segments, DEFAULTS.child_tokens, DEFAULTS.overlap_tokens)
    assert windows
    pieces = [window[0] for window in windows]  # hard-split windows: one piece each
    assert pieces[0].start_char == 0
    assert pieces[-1].end_char == len(text)
    for prev, nxt in pairwise(pieces):
        assert nxt.start_char <= prev.end_char


# ---------------------------------------------------------------------------
# Group merge helper
# ---------------------------------------------------------------------------


def test_merge_groups_concatenates_consecutive_same_group_segments() -> None:
    segments = [
        Segment("q1", 0, 2, "speaker_turn", "0"),
        Segment("a1", 2, 4, "speaker_turn", "0"),
        Segment("q2", 4, 6, "speaker_turn", "1"),
    ]
    merged = merge_groups(segments, "qa_pair")
    assert [seg.text for seg in merged] == ["q1\n\na1", "q2"]
    assert merged[0].kind == "qa_pair"
    assert merged[0].start_char == 0 and merged[0].end_char == 4


def test_merge_groups_leaves_ungrouped_segments_alone() -> None:
    segments = [
        Segment("p1", 0, 2, "paragraph", None),
        Segment("p2", 2, 4, "paragraph", None),
    ]
    merged = merge_groups(segments, "qa_pair")
    assert merged == segments
