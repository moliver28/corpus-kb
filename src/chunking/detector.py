"""File type detection — determines which chunker to use for a given file."""

from __future__ import annotations

import os
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .base import Chunker


# Language detection maps
CODE_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".cpp": "cpp",
    ".c": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".lua": "lua",
    ".hs": "haskell",
    ".ex": "elixir",
    ".exs": "elixir",
    ".clj": "clojure",
    ".cljs": "clojure",
    ".sql": "sql",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".fish": "bash",
    ".ps1": "powershell",
    ".r": "r",
    ".m": "matlab",
    ".zig": "zig",
}

MARKDOWN_EXTENSIONS: set[str] = {".md", ".mdx", ".rst"}

SHEBANG_MAP: dict[str, str] = {
    "python": "python",
    "node": "javascript",
    "deno": "javascript",
    "bash": "bash",
    "sh": "bash",
    "zsh": "bash",
    "ruby": "ruby",
    "perl": "perl",
    "php": "php",
    "lua": "lua",
    "go": "go",
    "rust": "rust",
}


def detect_file_type(file_path: str, content: Optional[str] = None) -> str:
    """Detect the file type for chunking purposes.

    Returns one of: "code", "markdown", "text"
    """
    ext = os.path.splitext(file_path)[1].lower()

    # Check code extensions first
    if ext in CODE_EXTENSIONS:
        return "code"

    # Check markdown extensions
    if ext in MARKDOWN_EXTENSIONS:
        return "markdown"

    # Check shebang for code files without code extensions
    if content and content.startswith("#!"):
        first_line = content.split("\n")[0].lower()
        for keyword, _ in SHEBANG_MAP.items():
            if keyword in first_line:
                return "code"

    # Default to text
    return "text"


def detect_language(file_path: str, content: Optional[str] = None) -> Optional[str]:
    """Detect the programming language of a code file."""
    ext = os.path.splitext(file_path)[1].lower()

    if ext in CODE_EXTENSIONS:
        return CODE_EXTENSIONS[ext]

    # Check shebang
    if content and content.startswith("#!"):
        first_line = content.split("\n")[0].lower()
        for keyword, lang in SHEBANG_MAP.items():
            if keyword in first_line:
                return lang

    return None


class FileTypeDetector:
    """Detects file types and returns the appropriate Chunker."""

    def __init__(self, chunkers: Optional[dict[str, "Chunker"]] = None):
        """Initialize with a dict of {type: Chunker} mappings.

        Expected keys: "code", "markdown", "text".
        If not provided, default chunkers are created automatically.
        """
        if chunkers is None:
            from .code_chunker import CodeChunker
            from .markdown_chunker import MarkdownChunker
            from .text_chunker import TextChunker
            chunkers = {
                "code": CodeChunker(),
                "markdown": MarkdownChunker(),
                "text": TextChunker(),
            }
        self.chunkers = chunkers

    def get_chunker(self, file_type: str) -> "Chunker":
        """Get the appropriate chunker for a file type."""
        chunker = self.chunkers.get(file_type) or self.chunkers.get("text")
        assert chunker is not None, "No text chunker configured — at least one chunker is required"
        return chunker

    def chunk_file(self, file_path: str, content: str) -> list:
        """Detect type and chunk a file's content in one step."""
        file_type = detect_file_type(file_path, content)
        chunker = self.get_chunker(file_type)
        return chunker.chunk(content, file_path=file_path)

    def chunk_text(self, text: str, source: str = "",
                   file_type: Optional[str] = None) -> list:
        """Chunk raw text with optional type hint."""
        if file_type:
            chunker = self.get_chunker(file_type)
        else:
            # Best guess based on content
            if text.startswith("#!"):
                chunker = self.get_chunker("code")
            elif text.strip().startswith("#") or text.strip().startswith("##"):
                chunker = self.get_chunker("markdown")
            else:
                chunker = self.get_chunker("text")
        return chunker.chunk(text, file_path=source)
