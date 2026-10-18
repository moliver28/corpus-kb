"""U48/P6: the three-level tree built over packed hierarchy windows.

Builds the committed levels from the SAME source segments:

  child  (default 128 tokens) — the retrieval unit (children retrieve)
  chunk  (default 512 tokens) — the mid level (cutover candidate for v1)
  parent (default 2048 tokens) — the context unit (returned via expansion)

The hierarchy is a SIDE-BY-SIDE SHADOW artifact: it never replaces the v1
``chunks`` rows; it is derived, deterministic and rebuildable. Links: every
child points at the chunk-level node containing its start char, every
chunk-level node at the parent-level node containing ITS start char (the
``parent_id`` column in ``shadow_chunks``). Pure stdlib, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ..ingest.hierarchy_settings import HierarchySettings
from .hierarchy import Segment, estimate_tokens, merge_groups, pack_windows

LEVEL_CHILD = "child"
LEVEL_CHUNK = "chunk"
LEVEL_PARENT = "parent"


@dataclass(frozen=True)
class HierarchyNode:
    """One shadow row of the three-level tree."""

    key: str
    level: str
    seq: int
    text: str
    token_count: int
    start_char: int
    end_char: int
    boundary: str
    parent_key: str | None
    child_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class HierarchyTree:
    """The complete deterministic tree for one document/path."""

    doc_id: str
    path: str
    nodes: tuple[HierarchyNode, ...] = field(default_factory=tuple)

    def by_level(self, level: str) -> tuple[HierarchyNode, ...]:
        """All nodes of one level, in seq order."""
        return tuple(node for node in self.nodes if node.level == level)


def _link(nodes: list[HierarchyNode], containers: list[HierarchyNode]) -> dict[str, str]:
    """Map node.key -> the latest-starting container holding its start char.

    Windows advance monotonically, so the latest container with
    ``start_char <= node.start_char`` is the one containing the node's start
    even under overlap rewind.
    """
    if not containers:
        return {}
    links: dict[str, str] = {}
    for node in nodes:
        chosen = containers[0]
        for container in containers:
            if container.start_char <= node.start_char:
                chosen = container
            else:
                break
        links[node.key] = chosen.key
    return links


def _window_nodes(
    doc_id: str,
    level: str,
    windows: list[list[Segment]],
) -> list[HierarchyNode]:
    nodes: list[HierarchyNode] = []
    for seq, window in enumerate(windows):
        text = "".join(seg.text for seg in window)
        nodes.append(
            HierarchyNode(
                key=f"{doc_id}:{level}:{seq}",
                level=level,
                seq=seq,
                text=text,
                # The node's token budget describes the JOINED text (the
                # retrievable unit), not the sum of per-segment estimates.
                token_count=estimate_tokens(text),
                start_char=window[0].start_char,
                end_char=window[-1].end_char,
                boundary=window[0].kind,
                parent_key=None,
            )
        )
    return nodes


def build_hierarchy(
    doc_id: str,
    segments: list[Segment],
    settings: HierarchySettings,
    *,
    path: str = "main",
    group_kind: str | None = None,
) -> HierarchyTree:
    """Build the deterministic child/chunk/parent tree for one document.

    ``group_kind`` names what a shared-``group_id`` run represents
    (``"heading"`` on the main path, ``"qa_pair"`` on the research path);
    group merging only happens when that kind is listed in
    ``settings.respect_boundaries``.
    """
    mid_segments = (
        merge_groups(segments, group_kind)
        if group_kind is not None and group_kind in settings.respect_boundaries
        else list(segments)
    )

    child_windows = pack_windows(segments, settings.child_tokens, settings.overlap_tokens)
    chunk_windows = pack_windows(mid_segments, settings.chunk_tokens, settings.overlap_tokens)
    parent_windows = pack_windows(mid_segments, settings.parent_tokens, settings.overlap_tokens)

    children = _window_nodes(doc_id, LEVEL_CHILD, child_windows)
    chunks = _window_nodes(doc_id, LEVEL_CHUNK, chunk_windows)
    parents = _window_nodes(doc_id, LEVEL_PARENT, parent_windows)

    child_to_chunk = _link(children, chunks)
    chunk_to_parent = _link(chunks, parents)

    def _linked(
        nodes: list[HierarchyNode],
        links: dict[str, str],
    ) -> list[HierarchyNode]:
        return [
            (replace(node, parent_key=links[node.key]) if node.key in links else node)
            for node in nodes
        ]

    linked_children = _linked(children, child_to_chunk)

    child_keys_by_chunk: dict[str, list[str]] = {}
    for child in linked_children:
        target = child.parent_key
        if target is not None:
            child_keys_by_chunk.setdefault(target, []).append(child.key)

    linked_chunks = [
        replace(
            chunk,
            parent_key=chunk_to_parent.get(chunk.key),
            child_keys=tuple(child_keys_by_chunk.get(chunk.key, ())),
        )
        for chunk in chunks
    ]

    return HierarchyTree(
        doc_id=doc_id,
        path=path,
        nodes=(*linked_children, *linked_chunks, *parents),
    )
