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
        end = start + len(text)
        cursor = end

        source_type = _CATEGORY_TO_SOURCE_TYPE.get(element.element_type, "text")
        heading_path = _update_heading_path(heading_path, element, text)
        start_line, end_line = _line_numbers(original_text, start, end)

        chunks.append(
            Chunk(
                document_id=document_id,
                text=text,
                source_type=source_type,
                start_line=start_line,
                end_line=end_line,
                source_start_char=start,
                source_end_char=end,
                metadata={
                    "element_type": element.element_type,
                    "parent_id": element.parent_id,
                    "heading_path": list(heading_path),
                },
            )
        )

    return chunks


def _update_heading_path(
    heading_path: list[str], element: ElementProxy, text: str
) -> list[str]:
    depth = element.metadata.get("category_depth")
    if element.element_type == "Title" and isinstance(depth, int):
        return heading_path[:depth] + [text]
    return heading_path


def _line_numbers(text: str, start: int, end: int) -> tuple[int, int]:
    start_line = text.count("\n", 0, start) + 1
    end_line = text.count("\n", 0, end) + 1
    return start_line, end_line
