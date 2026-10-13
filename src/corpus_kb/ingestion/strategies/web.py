"""Web structure strategy: heading-scoped section chunks from raw HTML.

The fetch happens UPSTREAM (tools.ingest_url); ``build_chunks`` receives the
raw HTML and always extracts the page metadata itself from that HTML (the
``metadata`` constructor argument overrides it — used by tests and callers
that already hold a :class:`FetchedPage`). Chunk metadata merges the web
metadata (url/final_url/title/description/site_name/author/published_time/
modified_time/language/canonical) with the element-level ``element_types``
list, so every chunk is self-describing for retrieval.
"""

from __future__ import annotations

from ...partitioning import ElementProxy
from ...web.fetch import FetchedPage
from ...web.metadata import extract_metadata
from ...web.sections import group_sections, pack_elements, partition_html_proxies
from ..base import (
    TOKEN_BUDGET_CHARS,
    ChunkPlan,
    DetectResult,
    PlannedChunk,
    SourceProfile,
)

WEB_STRATEGY_NAME = "web_structure"

_WEB_HINT_SOURCES = frozenset({"web", "url"})
_JOIN = "\n\n"


class WebStructureStrategy:
    """Section-level chunking for fetched web pages (source_type "web")."""

    name = WEB_STRATEGY_NAME

    def __init__(self, metadata: dict[str, str | None] | None = None) -> None:
        self._metadata = metadata

    def detect(self, source_type_hint: str | None, filename: str, head: str) -> DetectResult:
        """Claim web content on the source-type hint or an http(s) filename."""
        del head
        hint = (source_type_hint or "").strip().lower()
        name = (filename or "").strip().lower()
        if hint in _WEB_HINT_SOURCES or name.startswith(("http://", "https://")):
            return DetectResult(1.0, "web-source-type")
        return DetectResult(0.0, "no web signal")

    def _base_metadata(self, raw: str, profile: SourceProfile) -> dict[str, object]:
        """Page-level metadata for every chunk (self-extracted by default)."""
        if self._metadata is not None:
            meta: dict[str, object] = dict(self._metadata)
        else:
            page = FetchedPage(
                url=profile.path,
                final_url=profile.path,
                status_code=0,
                html=raw,
                content_type="text/html",
                fetched_at="",
            )
            meta = dict(extract_metadata(page))
        meta.setdefault("url", profile.path)
        meta.setdefault("final_url", profile.path)
        return meta

    def build_chunks(self, raw: str, filename: str, profile: SourceProfile) -> ChunkPlan:
        """Partition the HTML, group sections, pack to the token budget.

        ``document_text`` is the '\\n\\n'-join of every kept element text and
        each chunk is the '\\n\\n'-join of one consecutive element group — the
        same join discipline on both sides, so chunk texts are exact
        substrings of ``document_text`` and real source char spans can be
        located with the find-with-cursor pattern (mirroring
        ``chunking.unstructured_chunker.chunk_elements``). A chunk that cannot
        be located honestly keeps ``source_start_char=None`` (late chunking
        then falls back to pooled embedding for it, never silently).
        """
        del filename
        sections = group_sections(partition_html_proxies(raw))
        meta = self._base_metadata(raw, profile)
        document_text = _JOIN.join(
            element.text for section in sections for element in section.elements
        )
        planned: list[PlannedChunk] = []
        cursor = 0
        for section in sections:
            for group in pack_elements(section.elements, TOKEN_BUDGET_CHARS):
                text = _JOIN.join(element.text for element in group)
                start = document_text.find(text, cursor)
                if start == -1:
                    stripped = text.strip()
                    if stripped and stripped != text:
                        start = document_text.find(stripped, cursor)
                        if start != -1:
                            text = stripped
                end = start + len(text) if start != -1 else None
                if end is not None:
                    cursor = end
                planned.append(
                    PlannedChunk(
                        text=text,
                        index=len(planned),
                        source_type="text",
                        heading_path=list(section.heading_path),
                        metadata={**meta, "element_types": _element_types(group)},
                        source_start_char=start,
                        source_end_char=end,
                    )
                )
        return ChunkPlan(
            strategy=self.name,
            chunks=planned,
            document_text=document_text,
            empty_reason=None if planned else "no elements recovered from HTML",
        )


def _element_types(group: list[ElementProxy]) -> list[str]:
    """Order-preserving unique element categories inside one chunk."""
    seen: list[str] = []
    for element in group:
        if element.element_type not in seen:
            seen.append(element.element_type)
    return seen
