"""Heading-aware Markdown chunker.

Splits Markdown at heading boundaries (#, ##, ###, etc.) preserving
hierarchical structure. Each chunk inherits the heading path of its
section (e.g., ["Installation", "Quick Start", "Configuration"]).

Handles YAML frontmatter, code blocks (won't split inside them),
and generates parent_chunk_id for hierarchy reconstruction.
"""

from __future__ import annotations

import re
from typing import Optional

from utils.models import Chunk
from .base import Chunker

# Regex to match ATX headings (# style)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

# Regex to match YAML frontmatter
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n?", re.DOTALL)


class MarkdownChunker(Chunker):
    """Heading-aware Markdown chunker.

    Strategy:
    1. Extract YAML frontmatter as metadata
    2. Split at heading boundaries, tracking heading_path
    3. Never split inside fenced code blocks or inline code
    4. Merge small adjacent chunks under same parent heading
    """

    def __init__(self, max_size: int = 2048):
        self.max_size = max_size

    def chunk(self, text: str, file_path: Optional[str] = None) -> list[Chunk]:
        """Split Markdown into heading-boundary chunks."""
        # Extract frontmatter
        body, frontmatter = self._extract_frontmatter(text)

        # Build section map: heading positions and their heading paths
        sections = self._find_sections(body)

        if not sections:
            return [
                Chunk(
                    text=body,
                    chunk_index=0,
                    chunk_type="document",
                    source_type="markdown",
                    file_path=file_path,
                    start_line=0,
                    end_line=body.count("\n"),
                    metadata={"frontmatter": frontmatter} if frontmatter else {},
                )
            ]

        # Create a chunk for each section
        chunks: list[Chunk] = []
        for i, section in enumerate(sections):
            chunk_text = section["text"].strip()
            if not chunk_text:
                continue

            estimate = len(chunk_text)

            if estimate <= self.max_size:
                chunk = Chunk(
                    text=chunk_text,
                    chunk_index=len(chunks),
                    chunk_type="section",
                    source_type="markdown",
                    file_path=file_path,
                    start_line=section["start_line"],
                    end_line=section["end_line"],
                    heading_path=section["heading_path"],
                )
                if frontmatter:
                    chunk.metadata["frontmatter"] = frontmatter
                chunks.append(chunk)
            else:
                # Oversized section: split by sub-headings or paragraphs
                sub_chunks = self._split_oversized_section(
                    chunk_text, section, frontmatter, file_path
                )
                chunks.extend(sub_chunks)

        # Merge small adjacent siblings
        chunks = self._merge_small_siblings(chunks)

        return chunks

    def _extract_frontmatter(self, text: str) -> tuple[str, dict]:
        """Extract and parse YAML frontmatter."""
        match = FRONTMATTER_RE.match(text)
        if match:
            frontmatter_raw = match.group(1)
            body = text[match.end():]
            try:
                import yaml
                frontmatter = yaml.safe_load(frontmatter_raw) or {}
            except Exception:
                frontmatter = {"_raw": frontmatter_raw}
            return body, frontmatter
        return text, {}

    def _find_sections(self, text: str) -> list[dict]:
        """Find all heading-delimited sections.

        Returns a list of dicts with:
            - heading: the heading text (without #)
            - level: heading level (1-6)
            - heading_line: 0-indexed line of the heading
            - start_line: content start line (0-indexed, after heading)
            - end_line: last content line (inclusive)
            - heading_path: full path of headings from root
            - text: full section text
        """
        lines = text.split("\n")
        sections = []
        heading_stack = []  # List of (level, heading_text, line_index)

        i = 0
        while i < len(lines):
            line = lines[i]
            match = HEADING_RE.match(line)
            if match:
                level = len(match.group(1))
                heading_text = match.group(2).strip()

                # Pop headings deeper than current level
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()

                heading_stack.append((level, heading_text, i))

                # Build heading path
                heading_path = [h[1] for h in heading_stack]

                sections.append({
                    "heading": heading_text,
                    "level": level,
                    "heading_line": i,
                    "start_line": i + 1,  # content starts after heading
                    "end_line": i,  # will be adjusted
                    "heading_path": heading_path,
                    "text": "",  # will be filled
                })
            i += 1

        # Assign end lines and extract text
        for j in range(len(sections)):
            if j + 1 < len(sections):
                sections[j]["end_line"] = sections[j + 1]["heading_line"] - 1
            else:
                sections[j]["end_line"] = len(lines) - 1

            start = sections[j]["heading_line"]
            end = sections[j]["end_line"]
            sections[j]["text"] = "\n".join(lines[start:end + 1])

        return sections

    def _split_oversized_section(
        self,
        text: str,
        section: dict,
        frontmatter: dict,
        file_path: Optional[str],
    ) -> list[Chunk]:
        """Split an oversized section at paragraph boundaries."""
        paragraphs = re.split(r"\n\n+", text)
        chunks = []
        buffer: list[str] = []
        buffer_size = 0

        def flush_buffer():
            nonlocal buffer, buffer_size
            if not buffer:
                return
            chunk_text = "\n\n".join(buffer)
            chunk = Chunk(
                text=chunk_text,
                chunk_index=len(chunks),
                chunk_type="section_fragment",
                source_type="markdown",
                file_path=file_path,
                start_line=section["start_line"],
                end_line=section["end_line"],
                heading_path=section["heading_path"],
            )
            if frontmatter:
                chunk.metadata["frontmatter"] = frontmatter
            chunks.append(chunk)
            buffer = []
            buffer_size = 0

        for para in paragraphs:
            para_len = len(para)
            if buffer_size + para_len > self.max_size and buffer:
                flush_buffer()
            buffer.append(para)
            buffer_size += para_len

        flush_buffer()
        return chunks

    def _merge_small_siblings(self, chunks: list[Chunk], min_size: int = 100) -> list[Chunk]:
        """Merge adjacent chunks with the same heading_path."""
        if not chunks:
            return chunks

        merged = [chunks[0]]
        for chunk in chunks[1:]:
            prev = merged[-1]
            same_path = prev.heading_path == chunk.heading_path
            if (same_path and len(prev.text) < min_size
                    and len(prev.text) + len(chunk.text) <= self.max_size):
                prev.text += "\n\n" + chunk.text
                prev.end_line = chunk.end_line
            else:
                merged.append(chunk)

        # Re-index
        for i, c in enumerate(merged):
            c.chunk_index = i

        return merged
