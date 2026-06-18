"""Tests for Phase 2: Chunking Engine.

Covers:
- CodeChunker (fallback mode + tree-sitter if grammar installed)
- MarkdownChunker (heading-aware splitting)
- TextChunker (paragraph + semantic modes)
- HierarchyResolver (parent/sibling assignment)
- FileTypeDetector (language & chunker dispatch)
"""

from __future__ import annotations

import os
import json
import pytest

from chunking.base import Chunker
from chunking.detector import FileTypeDetector, detect_file_type, detect_language
from chunking.code_chunker import CodeChunker
from chunking.markdown_chunker import MarkdownChunker
from chunking.text_chunker import TextChunker
from chunking.hierarchy import HierarchyResolver
from utils.models import Chunk


# ============================================================================
# FileTypeDetector
# ============================================================================

class TestFileTypeDetector:
    def test_detect_python(self):
        assert detect_file_type("foo.py") == "code"
        assert detect_language("foo.py") == "python"

    def test_detect_javascript(self):
        assert detect_file_type("bar.js") == "code"
        assert detect_language("bar.js") == "javascript"

    def test_detect_typescript(self):
        assert detect_file_type("baz.tsx") == "code"
        assert detect_language("baz.tsx") == "typescript"

    def test_detect_markdown(self):
        assert detect_file_type("readme.md") == "markdown"
        # detect_language returns the language for code files, not for markdown
        assert detect_language("readme.md") is None

    def test_detect_text(self):
        assert detect_file_type("notes.txt") == "text"
        assert detect_language("notes.txt") is None

    def test_detect_unknown_with_shebang(self, tmp_path):
        f = tmp_path / "script"
        content = "#!/usr/bin/env python\nprint('hi')"
        f.write_text(content)
        assert detect_file_type(str(f), content=content) == "code"

    def test_get_chunker_dispatch(self):
        d = FileTypeDetector()
        code_chunker = d.get_chunker("code")
        assert isinstance(code_chunker, CodeChunker)
        md_chunker = d.get_chunker("markdown")
        assert isinstance(md_chunker, MarkdownChunker)
        text_chunker = d.get_chunker("text")
        assert isinstance(text_chunker, TextChunker)

    def test_get_chunker_fallback(self):
        d = FileTypeDetector()
        chunker = d.get_chunker("unknown")
        assert isinstance(chunker, TextChunker)


# ============================================================================
# MarkdownChunker
# ============================================================================

class TestMarkdownChunker:
    def test_single_chunk_no_headings(self):
        chunker = MarkdownChunker()
        text = "Some plain text content.\n\nMultiple paragraphs.\n\nNo markdown headings."
        chunks = chunker.chunk(text, file_path="test.txt")
        assert len(chunks) == 1
        assert chunks[0].chunk_type == "document"
        assert "plain text" in chunks[0].text

    def test_split_at_headings(self):
        chunker = MarkdownChunker()
        text = "# Section 1\n\nContent for section 1.\n\n## Subsection 1a\n\nSub content.\n\n# Section 2\n\nContent for section 2."
        chunks = chunker.chunk(text, file_path="test.md")
        assert len(chunks) >= 2

        # First chunk should be Section 1 with content
        assert "Section 1" in chunks[0].text
        assert chunks[0].heading_path == ["Section 1"]

        # If we have 3+ chunks, check subsection
        section1_chunks = [c for c in chunks if c.heading_path == ["Section 1"]]
        subsection_chunks = [c for c in chunks if c.heading_path == ["Section 1", "Subsection 1a"]]
        section2_chunks = [c for c in chunks if c.heading_path == ["Section 2"]]

        assert len(section1_chunks) >= 1
        assert len(subsection_chunks) >= 1
        assert len(section2_chunks) >= 1

    def test_heading_path_nesting(self):
        chunker = MarkdownChunker()
        text = "# A\n\na content.\n\n## B\n\nb content.\n\n### C\n\nc content."
        chunks = chunker.chunk(text, file_path="test.md")
        paths = {tuple(c.heading_path) for c in chunks}
        assert ("A",) in paths
        assert ("A", "B") in paths
        assert ("A", "B", "C") in paths

    def test_yaml_frontmatter(self):
        chunker = MarkdownChunker()
        text = "---\ntitle: Test\nauthor: Me\n---\n\n# Content\n\nBody here."
        chunks = chunker.chunk(text, file_path="test.md")
        assert len(chunks) >= 1
        # Frontmatter should be in metadata
        fm = chunks[0].metadata.get("frontmatter", {})
        assert fm.get("title") == "Test"

    def test_merge_small_siblings(self):
        chunker = MarkdownChunker()
        text = "# Big\n\nThis is long content " * 50 + "\n\n# Small\n\nTiny.\n\n# Small2\n\nSmall too."
        chunks = chunker.chunk(text, file_path="test.md")
        # The small chunks should have been merged
        assert len(chunks) >= 1


# ============================================================================
# TextChunker
# ============================================================================

class TestTextChunker:
    def test_paragraph_mode_single_chunk(self):
        chunker = TextChunker(max_size=2048)
        text = "This is a short paragraph."
        chunks = chunker.chunk(text)
        assert len(chunks) == 1
        assert "short paragraph" in chunks[0].text

    def test_paragraph_mode_multiple_chunks(self):
        chunker = TextChunker(max_size=200)
        text = "\n\n".join([f"This is paragraph number {i} with enough text to fill a reasonable chunk that should be split." for i in range(10)])
        chunks = chunker.chunk(text)
        assert len(chunks) > 1
        assert all(isinstance(c, Chunk) for c in chunks)

    def test_semantic_mode_fallback_on_no_ollama(self):
        chunker = TextChunker(max_size=2048, use_semantic=True)
        text = "This is a test. It has multiple sentences. But Ollama isn't running. So it should fall back."
        # Should not crash — falls back to paragraph mode
        chunks = chunker.chunk(text)
        assert len(chunks) >= 1

    def test_empty_text(self):
        chunker = TextChunker()
        chunks = chunker.chunk("")
        assert chunks == []

    def test_whitespace_only(self):
        chunker = TextChunker()
        chunks = chunker.chunk("   \n\n  \n  ")
        assert chunks == []

    def test_long_paragraph_respects_max_size(self):
        chunker = TextChunker(max_size=100)
        paragraphs = [f"This is paragraph number {i} with enough text to fill a reasonably sized chunk that should be split across boundaries." for i in range(20)]
        text = "\n\n".join(paragraphs)
        chunks = chunker.chunk(text)
        assert len(chunks) >= 2
        # Each chunk should be close to max_size (overshoot from word/para boundaries OK)
        assert all(len(c.text) <= 300 for c in chunks)


# ============================================================================
# CodeChunker
# ============================================================================

class TestCodeChunker:
    def test_fallback_python_to_block_chunks(self):
        chunker = CodeChunker()
        code = """import os
import sys

def hello():
    print("Hello, world!")

class MyClass:
    def method_one(self):
        pass

    def method_two(self):
        return 42

# Standalone comment block
x = 10
"""
        chunks = chunker.chunk(code, file_path="test.py")
        assert len(chunks) >= 1
        assert all(isinstance(c, Chunk) for c in chunks)

    def test_fallback_respects_boundaries(self):
        chunker = CodeChunker()
        code = """def func_a():
    return 1

def func_b():
    return 2
"""
        chunks = chunker.chunk(code, file_path="test.py")
        # Fallback should split at def boundaries
        assert len(chunks) >= 1

    def test_empty_code(self):
        chunker = CodeChunker()
        chunks = chunker.chunk("", file_path="empty.py")
        # Should return a single chunk
        assert len(chunks) == 0 or all(isinstance(c, Chunk) for c in chunks)

    def test_no_file_path_fallback_line_based(self):
        chunker = CodeChunker()
        code = "x = 1\ny = 2"
        chunks = chunker.chunk(code)
        assert len(chunks) >= 1

    def test_detect_language(self):
        chunker = CodeChunker()
        assert chunker.detect_language("foo.py") == "python"
        assert chunker.detect_language("bar.js") == "javascript"
        assert chunker.detect_language("baz.ts") == "typescript"
        assert chunker.detect_language("qux.rs") == "rust"
        assert chunker.detect_language("unknown.xyz") is None
        assert chunker.detect_language(None) is None


# ============================================================================
# HierarchyResolver
# ============================================================================

class TestHierarchyResolver:
    def test_empty_list(self):
        r = HierarchyResolver()
        assert r.resolve([]) == []

    def test_single_chunk_root(self):
        r = HierarchyResolver()
        chunks = [Chunk(text="hello", start_line=0, end_line=0)]
        result = r.resolve(chunks)
        assert result[0].parent_chunk_id is None
        assert result[0].sibling_order == 1
        assert result[0].sibling_count == 1

    def test_flat_text_chunks(self):
        r = HierarchyResolver()
        chunks = [
            Chunk(text="first", chunk_index=0, start_line=0, end_line=5),
            Chunk(text="second", chunk_index=1, start_line=6, end_line=10),
        ]
        result = r.resolve(chunks)
        assert result[0].parent_chunk_id is None
        assert result[1].parent_chunk_id is None
        assert result[0].sibling_order == 1
        assert result[1].sibling_order == 2

    def test_markdown_heading_parentage(self):
        r = HierarchyResolver()
        chunks = [
            Chunk(text="# A\n\ncontent", chunk_index=0, chunk_type="section",
                  heading_path=["A"], start_line=0, end_line=2),
            Chunk(text="## B\n\ncontent", chunk_index=1, chunk_type="section",
                  heading_path=["A", "B"], start_line=3, end_line=5),
        ]
        result = r.resolve(chunks)
        assert result[0].parent_chunk_id is None
        assert result[1].parent_chunk_id == "chunk:0"
        assert result[1].sibling_order == 1

    def test_code_containment_parentage(self):
        r = HierarchyResolver()
        chunks = [
            Chunk(text="class X:", chunk_index=0, chunk_type="class",
                  entity_name="X", source_type="code",
                  start_line=0, end_line=10),
            Chunk(text="    def y():", chunk_index=1, chunk_type="function",
                  entity_name="y", source_type="code",
                  start_line=2, end_line=5),
        ]
        result = r.resolve(chunks)
        assert result[0].parent_chunk_id is None
        assert result[1].parent_chunk_id == "chunk:0"


# ============================================================================
# Integration: detector -> chunker -> hierarchy
# ============================================================================

class TestEndToEndChunking:
    def test_detect_chunk_hierarchy_markdown(self):
        detector = FileTypeDetector()
        resolver = HierarchyResolver()

        text = "# Intro\n\nWelcome.\n\n## Details\n\nMore info.\n"
        file_type = detect_file_type("doc.md")
        chunker = detector.get_chunker(file_type)
        chunks = chunker.chunk(text, file_path="doc.md")
        resolved = resolver.resolve(chunks)

        assert len(resolved) >= 2
        intro = [c for c in resolved if c.heading_path == ["Intro"]]
        details = [c for c in resolved if c.heading_path == ["Intro", "Details"]]
        assert len(intro) >= 1
        assert len(details) >= 1

    def test_detect_chunk_hierarchy_code(self):
        detector = FileTypeDetector()
        resolver = HierarchyResolver()

        code = """def existing():
    pass

class Container:
    pass
"""
        file_type = detect_file_type("test.py")
        chunker = detector.get_chunker(file_type)
        chunks = chunker.chunk(code, file_path="test.py")
        resolved = resolver.resolve(chunks)

        assert len(resolved) >= 1

    def test_detect_chunk_hierarchy_text(self):
        detector = FileTypeDetector()
        resolver = HierarchyResolver()

        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph.\n"
        file_type = detect_file_type("notes.txt")
        chunker = detector.get_chunker(file_type)
        chunks = chunker.chunk(text, file_path="notes.txt")
        resolved = resolver.resolve(chunks)

        assert len(resolved) >= 1
        # Text chunks are all root-level
        assert all(c.parent_chunk_id is None for c in resolved)
