"""Convert Unstructured element proxies into typed Chunk records."""

from __future__ import annotations

from ..partitioning import ElementProxy
from ..utils.models import Chunk

_CATEGORY_TO_SOURCE_TYPE = {
    "CodeSnippet": "code",
    "Title": "markdown",
    "Header": "markdown",
    "Footer": "markdown",
    "List": "markdown",
    "ListItem": "markdown",
    "NarrativeText": "text",
    "UncategorizedText": "text",
    "Quote": "text",
}

# One element over this width cannot be a chunk on its own: validate_plan
# rejects any chunk past the plan budget (0.8 x 2048 tokens at chars/4).
# ingestion.base.TOKEN_BUDGET_CHARS is DERIVED from this constant, so the
# two limits cannot drift.
MAX_CHUNK_CHARS = int(0.8 * 2048 * 4)


def _split_oversized(text: str, budget: int) -> list[str]:
    """Cut one oversized element into exact slices, each within the budget.

    Cuts prefer the last blank line in the window, then the last line break,
    then the last space, then a hard cut. Slices are exact substrings (the
    concatenation equals the input) so char offsets stay truthful.
    """
    pieces: list[str] = []
    rest = text
    while len(rest) > budget:
        window = rest[:budget]
        floor = budget // 2
        cut = window.rfind("\n\n")
        if cut <= floor:
            cut = window.rfind("\n")
        if cut <= floor:
            cut = window.rfind(" ")
        if cut <= floor:
            cut = budget
        pieces.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        pieces.append(rest)
    return pieces


def chunk_elements(
    elements: list[ElementProxy],
    original_text: str,
    document_id: str,
) -> list[Chunk]:
    """Map Unstructured element proxies to chunks with source character offsets."""
    chunks: list[Chunk] = []
    cursor = 0
    heading_path: list[str] = []

    for element in elements:
        text = element.text
        if not text:
            continue

        start = original_text.find(text, cursor)
        if start == -1:
            stripped = text.strip()
            if stripped and stripped != text:
                start = original_text.find(stripped, cursor)
                if start != -1:
                    text = stripped
        if start == -1:
            start = cursor
        cursor = start

        source_type = _CATEGORY_TO_SOURCE_TYPE.get(element.element_type, "text")
        heading_path = _update_heading_path(heading_path, element, text)
        for piece in (
            _split_oversized(text, MAX_CHUNK_CHARS) if len(text) > MAX_CHUNK_CHARS else [text]
        ):
            piece_start = original_text.find(piece, cursor)
            if piece_start == -1:
                piece_start = cursor
            piece_end = piece_start + len(piece)
            cursor = piece_end
            start_line, end_line = _line_numbers(original_text, piece_start, piece_end)
            chunks.append(
                Chunk(
                    document_id=document_id,
                    text=piece,
                    source_type=source_type,
                    start_line=start_line,
                    end_line=end_line,
                    source_start_char=piece_start,
                    source_end_char=piece_end,
                    metadata={
                        "element_type": element.element_type,
                        "parent_id": element.parent_id,
                        "heading_path": list(heading_path),
                    },
                )
            )

    return chunks


def _update_heading_path(heading_path: list[str], element: ElementProxy, text: str) -> list[str]:
    depth = element.metadata.get("category_depth")
    if element.element_type == "Title" and isinstance(depth, int):
        return [*heading_path[:depth], text]
    return heading_path


def _line_numbers(text: str, start: int, end: int) -> tuple[int, int]:
    start_line = text.count("\n", 0, start) + 1
    end_line = text.count("\n", 0, end) + 1
    return start_line, end_line
