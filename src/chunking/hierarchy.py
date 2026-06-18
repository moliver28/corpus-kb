"""Hierarchy assignment for chunks.

After all chunkers produce their base chunks, HierarchyResolver walks the
chunk list and assigns:

- parent_chunk_id: references the enclosing parent chunk
- sibling_order: ordinal within siblings under the same parent
- heading_path: the full heading trail (for markdown)

This enables tree-structured retrieval (return parent + siblings + children)
and breadcrumb navigation.
"""

from __future__ import annotations

from typing import Optional

from utils.models import Chunk


class HierarchyResolver:
    """Assigns parent/sibling relationships across chunk lists.

    The resolver is idempotent — it uses integer chunk_index values to
    establish parentage, so it can run before UUIDs are assigned.
    """

    def resolve(self, chunks: list[Chunk]) -> list[Chunk]:
        """Assign parent/sibling relationships to a flat list of chunks.

        Heuristics:
        - **Code**: A chunk that is an entity (class, function, method) whose
          line range is *fully contained* by another entity chunk's line range
          is a child.
        - **Markdown**: If heading_path exists, a chunk is a child of the
          nearest preceding chunk with a shorter heading_path.
        - **Text (paragraph groups)**: All siblings under the same parent
          document — no hierarchy.
        """
        if not chunks:
            return chunks

        # Strategy: sort by (start_line, chunk_index) then assign parentage
        sorted_chunks = sorted(
            enumerate(chunks),
            key=lambda x: (x[1].start_line, x[1].chunk_index),
        )

        # Build parent map: chunk_index -> parent_chunk_index
        parent_map: dict[int, int] = {}

        for i, (idx_a, chunk_a) in enumerate(sorted_chunks):
            # Try heading-path based parentage first (markdown)
            if chunk_a.heading_path:
                parent = self._find_heading_parent(
                    chunk_a, sorted_chunks[:i]
                )
                if parent is not None:
                    parent_map[idx_a] = parent
                    continue

            # Try containment-based parentage (code)
            if chunk_a.source_type == "code":
                parent = self._find_container_parent(
                    chunk_a, sorted_chunks[:i]
                )
                if parent is not None:
                    parent_map[idx_a] = parent
                    continue

            # No parent found — document root
            parent_map[idx_a] = -1

        # Count siblings and assign sibling_order
        sibling_counts: dict[int, int] = {}
        for idx, parent_idx in parent_map.items():
            if parent_idx not in sibling_counts:
                sibling_counts[parent_idx] = 0
            sibling_counts[parent_idx] += 1

        current_sibling: dict[int, int] = {}

        # Apply to chunks
        for idx, parent_idx in parent_map.items():
            if parent_idx == -1:
                chunks[idx].parent_chunk_id = None
            else:
                # We store the parent's chunk_index as a string reference
                # until actual UUIDs are assigned at insert time
                chunks[idx].parent_chunk_id = f"chunk:{parent_idx}"

            if parent_idx not in current_sibling:
                current_sibling[parent_idx] = 0
            current_sibling[parent_idx] += 1
            chunks[idx].sibling_order = current_sibling[parent_idx]
            chunks[idx].sibling_count = sibling_counts[parent_idx]

        return chunks

    def _find_heading_parent(
        self, chunk: Chunk, preceding: list[tuple[int, Chunk]]
    ) -> Optional[int]:
        """Find parent by heading_path prefix match.

        A chunk's parent is the nearest preceding chunk whose heading_path
        is a prefix of this chunk's heading_path and is strictly shorter.
        """
        if not chunk.heading_path:
            return None

        candidate = None
        for idx, pc in reversed(preceding):
            if not pc.heading_path:
                continue
            path = pc.heading_path
            # Parent path must be a strict prefix
            if (len(path) < len(chunk.heading_path)
                    and chunk.heading_path[:len(path)] == path):
                candidate = idx
                break  # nearest match

        return candidate

    def _find_container_parent(
        self, chunk: Chunk, preceding: list[tuple[int, Chunk]]
    ) -> Optional[int]:
        """Find parent whose line range fully contains this chunk.

        Used for code: a method inside a class is contained by the class.
        """
        cs, ce = chunk.start_line, chunk.end_line
        if cs is None or ce is None:
            return None

        candidate = None
        for idx, pc in reversed(preceding):
            ps, pe = pc.start_line, pc.end_line
            if ps is None or pe is None:
                continue
            if ps <= cs and ce <= pe:
                candidate = idx
                break

        return candidate
