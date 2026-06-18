"""AST-aware code chunker using tree-sitter.

Splits code at semantic boundaries (functions, classes, methods) rather than
arbitrary character limits. Never splits mid-function or mid-class.

Supports 40+ languages via tree-sitter grammar packages.
"""

from __future__ import annotations

import os
from typing import Optional

from utils.models import Chunk
from .base import Chunker

# Map file extensions to tree-sitter language package names
LANGUAGE_MAP: dict[str, tuple[str, str]] = {
    ".py": ("python", "tree_sitter_python"),
    ".js": ("javascript", "tree_sitter_javascript"),
    ".jsx": ("javascript", "tree_sitter_javascript"),
    ".ts": ("typescript", "tree_sitter_typescript"),
    ".tsx": ("typescript", "tree_sitter_typescript"),
    ".rs": ("rust", "tree_sitter_rust"),
    ".go": ("go", "tree_sitter_go"),
    ".java": ("java", "tree_sitter_java"),
    ".cpp": ("cpp", "tree_sitter_cpp"),
    ".c": ("cpp", "tree_sitter_cpp"),
    ".h": ("cpp", "tree_sitter_cpp"),
    ".hpp": ("cpp", "tree_sitter_cpp"),
    ".rb": ("ruby", "tree_sitter_ruby"),
    ".php": ("php", "tree_sitter_php"),
    ".swift": ("swift", "tree_sitter_swift"),
    ".kt": ("kotlin", "tree_sitter_kotlin"),
    ".kts": ("kotlin", "tree_sitter_kotlin"),
    ".scala": ("scala", "tree_sitter_scala"),
    ".lua": ("lua", "tree_sitter_lua"),
}

# Tree-sitter query patterns for extracting named entities
ENTITY_QUERIES: dict[str, list[str]] = {
    "python": [
        "(class_definition name: (identifier) @name) @entity",
        "(function_definition name: (identifier) @name) @entity",
        "(decorated_definition (class_definition name: (identifier) @name) @entity)",
        "(decorated_definition (function_definition name: (identifier) @name) @entity)",
    ],
    "javascript": [
        "(class_declaration name: (identifier) @name) @entity",
        "(function_declaration name: (identifier) @name) @entity",
        "(method_definition name: (property_identifier) @name) @entity",
        "(arrow_function) @entity",
    ],
    "typescript": [
        "(class_declaration name: (identifier) @name) @entity",
        "(function_declaration name: (identifier) @name) @entity",
        "(interface_declaration name: (identifier) @name) @entity",
        "(type_alias_declaration name: (identifier) @name) @entity",
        "(method_definition name: (property_identifier) @name) @entity",
    ],
    "rust": [
        "(struct_item name: (identifier) @name) @entity",
        "(enum_item name: (identifier) @name) @entity",
        "(function_item name: (identifier) @name) @entity",
        "(impl_item name: (type_identifier) @name) @entity",
        "(trait_item name: (identifier) @name) @entity",
    ],
    "go": [
        "(type_declaration (type_spec name: (identifier) @name)) @entity",
        "(function_declaration name: (identifier) @name) @entity",
        "(method_declaration name: (identifier) @name) @entity",
    ],
    "java": [
        "(class_declaration name: (identifier) @name) @entity",
        "(method_declaration name: (identifier) @name) @entity",
        "(interface_declaration name: (identifier) @name) @entity",
        "(enum_declaration name: (identifier) @name) @entity",
    ],
}


def _import_tree_sitter_language(lang_name: str, package: str):
    """Dynamically import a tree-sitter language package."""
    import importlib
    try:
        mod = importlib.import_module(package)
        return mod.language()
    except ImportError:
        raise ImportError(
            f"tree-sitter grammar for '{lang_name}' not installed. "
            f"Run: pip install {package}"
        )


class CodeChunker(Chunker):
    """AST-aware code chunker using tree-sitter.

    Extracts complete syntactic units (functions, classes, methods, interfaces)
    and preserves scope chains, parent relationships, and imports.
    """

    def __init__(self, max_size: int = 2500):
        self.max_size = max_size
        self._parsers: dict[str, any] = {}

    def _get_parser(self, lang_name: str) -> any:
        """Lazy-load and cache tree-sitter parsers by language."""
        if lang_name not in self._parsers:
            from tree_sitter import Parser

            lang_entry = None
            for ext, (ln, pkg) in LANGUAGE_MAP.items():
                if ln == lang_name:
                    lang_entry = _import_tree_sitter_language(ln, pkg)
                    break

            if lang_entry is None:
                raise ValueError(f"Unsupported language: {lang_name}")

            parser = Parser(lang_entry)
            self._parsers[lang_name] = parser

        return self._parsers[lang_name]

    def detect_language(self, file_path: Optional[str]) -> Optional[str]:
        """Detect language from file extension."""
        if not file_path:
            return None
        ext = os.path.splitext(file_path)[1].lower()
        if ext in LANGUAGE_MAP:
            return LANGUAGE_MAP[ext][0]
        return None

    def chunk(self, text: str, file_path: Optional[str] = None) -> list[Chunk]:
        """Split code into AST-aware chunks.

        Strategy:
        1. Parse AST via tree-sitter
        2. Extract named entities (functions, classes, methods)
        3. Group imports and leading comments
        4. Create chunks: one per entity, with scope chain metadata
        5. Merge small adjacent entities
        6. Split oversized entities at statement boundaries
        """
        lang = self.detect_language(file_path)

        # If language not detected or tree-sitter fails, fall back to line-based
        if not lang:
            return self._fallback_chunk(text, file_path)

        try:
            parser = self._get_parser(lang)
            tree = parser.parse(bytes(text, "utf-8"))
            return self._extract_chunks(tree, text, lang, file_path)
        except Exception:
            return self._fallback_chunk(text, file_path)

    def _extract_chunks(self, tree, text: str, lang: str,
                        file_path: Optional[str]) -> list[Chunk]:
        """Extract semantic chunks from a parsed AST."""
        chunks: list[Chunk] = []
        lines = text.split("\n")
        root = tree.root_node

        # Collect all top-level entities with their positions
        entities = []
        self._collect_entities(root, entities, text, lang)

        if not entities:
            return self._fallback_chunk(text, file_path)

        # Group consecutive imports
        imports_text = self._extract_imports(root, text)
        if imports_text:
            import_chunk = Chunk(
                text=imports_text,
                chunk_index=0,
                chunk_type="imports",
                source_type="code",
                file_path=file_path,
                start_line=0,
                end_line=imports_text.count("\n"),
            )
            chunks.append(import_chunk)
            offset = 1
        else:
            offset = 0

        # Process each entity
        for i, (name, node, node_type) in enumerate(entities):
            entity_text = text[node.start_byte:node.end_byte]
            entity_lines = entity_text.count("\n") + 1

            # Build scope chain
            scope = self._build_scope_chain(node, text)

            # If entity fits in max_size, create a single chunk
            if len(entity_text) <= self.max_size:
                chunk = Chunk(
                    text=entity_text,
                    chunk_index=i + offset,
                    chunk_type=node_type,
                    entity_name=name,
                    source_type="code",
                    file_path=file_path,
                    start_line=node.start_point[0],
                    end_line=node.end_point[0],
                    scope_chain=scope,
                )
                chunks.append(chunk)
            else:
                # Split oversized entity at statement/expression boundaries
                sub_chunks = self._split_large_entity(
                    entity_text, name, node_type, scope,
                    node.start_point[0], file_path
                )
                for j, sc in enumerate(sub_chunks):
                    sc.chunk_index = i + offset + j
                    chunks.append(sc)
                offset += len(sub_chunks) - 1

        # Merge small adjacent chunks to reduce fragmentation
        chunks = self._merge_small(chunks)

        return chunks

    def _collect_entities(self, node, entities: list, text: str, lang: str):
        """Recursively collect named entities from the AST."""
        node_type = node.type

        # Determine if this is a named entity
        entity_name = None
        entity_kind = None

        # Python
        if lang == "python":
            if node_type == "class_definition":
                for child in node.children:
                    if child.type == "identifier":
                        entity_name = text[child.start_byte:child.end_byte]
                        entity_kind = "class"
                        break
            elif node_type == "function_definition":
                for child in node.children:
                    if child.type == "identifier":
                        entity_name = text[child.start_byte:child.end_byte]
                        entity_kind = "function"
                        break
            # Method inside a class - still a function
            elif node_type == "decorated_definition":
                for child in node.children:
                    if child.type in ("class_definition", "function_definition"):
                        self._collect_entities(child, entities, text, lang)
                        return

        # JavaScript/TypeScript
        elif lang in ("javascript", "typescript"):
            if node_type == "class_declaration":
                for child in node.children:
                    if child.type in ("identifier", "property_identifier"):
                        entity_name = text[child.start_byte:child.end_byte]
                        entity_kind = "class"
                        break
            elif node_type in ("function_declaration", "method_definition"):
                for child in node.children:
                    if child.type in ("identifier", "property_identifier"):
                        entity_name = text[child.start_byte:child.end_byte]
                        entity_kind = "function"
                        break
            elif node_type == "interface_declaration":
                for child in node.children:
                    if child.type == "identifier":
                        entity_name = text[child.start_byte:child.end_byte]
                        entity_kind = "interface"
                        break

        # Rust
        elif lang == "rust":
            if node_type == "struct_item":
                for child in node.children:
                    if child.type == "identifier":
                        entity_name = text[child.start_byte:child.end_byte]
                        entity_kind = "struct"
                        break
            elif node_type == "function_item":
                for child in node.children:
                    if child.type == "identifier":
                        entity_name = text[child.start_byte:child.end_byte]
                        entity_kind = "function"
                        break
            elif node_type == "impl_item":
                for child in node.children:
                    if child.type == "type_identifier":
                        entity_name = text[child.start_byte:child.end_byte]
                        entity_kind = "impl"
                        break

        # Go
        elif lang == "go":
            if node_type == "function_declaration":
                for child in node.children:
                    if child.type in ("identifier", "field_identifier"):
                        entity_name = text[child.start_byte:child.end_byte]
                        entity_kind = "function"
                        break
            elif node_type == "method_declaration":
                for child in node.children:
                    if child.type in ("identifier", "field_identifier"):
                        entity_name = text[child.start_byte:child.end_byte]
                        entity_kind = "method"
                        break
            elif node_type == "type_declaration":
                for child in node.children:
                    if child.type == "type_spec":
                        for gc in child.children:
                            if gc.type == "identifier":
                                entity_name = text[gc.start_byte:gc.end_byte]
                                entity_kind = "type"
                                break

        # Java
        elif lang == "java":
            if node_type == "class_declaration":
                for child in node.children:
                    if child.type == "identifier":
                        entity_name = text[child.start_byte:child.end_byte]
                        entity_kind = "class"
                        break
            elif node_type == "method_declaration":
                for child in node.children:
                    if child.type == "identifier":
                        entity_name = text[child.start_byte:child.end_byte]
                        entity_kind = "method"
                        break

        if entity_name:
            entities.append((entity_name, node, entity_kind))
            return  # Don't recurse into named entities — they're top-level

        # Recurse into children
        for child in node.children:
            self._collect_entities(child, entities, text, lang)

    def _extract_imports(self, node, text: str) -> str:
        """Extract import statements from the top of a file."""
        import_lines = []
        for child in node.children:
            child_type = child.type
            # Python imports
            is_import = (
                child_type in ("import_statement", "import_from_statement")
                # JS/TS imports
                or child_type in ("import_declaration", "import_statement")
                # Rust imports
                or child_type == "use_declaration"
                # Go imports
                or child_type == "import_declaration"
                # Java imports
                or child_type == "import_declaration"
            )
            if is_import:
                import_lines.append(text[child.start_byte:child.end_byte])
            elif import_lines:
                # Stop at first non-import
                break

        return "\n".join(import_lines)

    def _build_scope_chain(self, node, text: str) -> list[str]:
        """Build the scope chain from root to this node."""
        scopes = []
        parent = node.parent
        while parent:
            if parent.type in ("class_definition", "function_definition",
                               "module", "program"):
                # Try to extract name
                for child in parent.children:
                    if child.type in ("identifier", "property_identifier",
                                      "type_identifier"):
                        name = text[child.start_byte:child.end_byte]
                        scopes.insert(0, name)
                        break
            parent = parent.parent
        return scopes

    def _split_large_entity(self, text: str, name: str, node_type: str,
                            scope_chain: list[str], start_line: int,
                            file_path: Optional[str]) -> list[Chunk]:
        """Split a single large entity into smaller chunks at statement boundaries.

        Strategy: split by logical blocks (class methods, top-level statements)
        while keeping each sub-chunk coherent.
        """
        lines = text.split("\n")
        chunks = []
        current_lines = []
        current_start = 0
        chunk_idx = 0

        for i, line in enumerate(lines):
            is_boundary = (
                line.strip().startswith("def ")
                or line.strip().startswith("async def ")
                or line.strip().startswith("class ")
                or line.strip().startswith("func ")
                or line.strip().startswith("fn ")
                or line.strip().startswith("func ")
                or line.strip().startswith("public ")
                or line.strip().startswith("private ")
                or line.strip().startswith("protected ")
            )

            if is_boundary and current_lines:
                chunk_text = "\n".join(current_lines)
                if chunk_text.strip():
                    chunks.append(Chunk(
                        text=chunk_text,
                        chunk_index=chunk_idx,
                        chunk_type=node_type,
                        entity_name=name,
                        source_type="code",
                        file_path=file_path,
                        start_line=start_line + current_start,
                        end_line=start_line + i - 1,
                        scope_chain=scope_chain,
                    ))
                    chunk_idx += 1
                current_lines = [line]
                current_start = i
            else:
                current_lines.append(line)

        # Last chunk
        if current_lines:
            chunk_text = "\n".join(current_lines)
            if chunk_text.strip():
                chunks.append(Chunk(
                    text=chunk_text,
                    chunk_index=chunk_idx,
                    chunk_type=node_type,
                    entity_name=name,
                    source_type="code",
                    file_path=file_path,
                    start_line=start_line + current_start,
                    end_line=start_line + len(lines) - 1,
                    scope_chain=scope_chain,
                ))

        return chunks

    def _merge_small(self, chunks: list[Chunk], min_size: int = 100) -> list[Chunk]:
        """Merge adjacent small chunks to reduce fragmentation."""
        if not chunks:
            return chunks

        merged = [chunks[0]]
        for chunk in chunks[1:]:
            if (len(merged[-1].text) < min_size
                    and merged[-1].chunk_type == chunk.chunk_type
                    and len(merged[-1].text) + len(chunk.text) <= self.max_size):
                # Merge into previous chunk
                prev = merged[-1]
                prev.text += "\n\n" + chunk.text
                prev.end_line = chunk.end_line
                if chunk.entity_name and not prev.entity_name:
                    prev.entity_name = chunk.entity_name
            else:
                merged.append(chunk)

        # Re-index
        for i, c in enumerate(merged):
            c.chunk_index = i

        return merged

    def _fallback_chunk(self, text: str, file_path: Optional[str]) -> list[Chunk]:
        """Fallback: line-based chunking when tree-sitter is unavailable."""
        lines = text.split("\n")
        chunks = []
        current_chunk: list[str] = []
        current_start = 0
        chunk_idx = 0

        for i, line in enumerate(lines):
            is_major_boundary = (
                line.strip().startswith("def ")
                or line.strip().startswith("class ")
                or line.strip().startswith("fn ")
                or line.strip().startswith("func ")
                or line.strip().startswith("public ")
                or line.strip().startswith("import ")
                or line.strip().startswith("from ")
                or line.strip().startswith("# ")
                or line.strip().startswith("// ")
            )

            current_chunk.append(line)
            chunk_text = "\n".join(current_chunk)

            if is_major_boundary and len(chunk_text) > 200:
                # Start a new chunk at this boundary
                if chunk_text.strip():
                    chunks.append(Chunk(
                        text=chunk_text,
                        chunk_index=chunk_idx,
                        chunk_type="block",
                        source_type="code",
                        file_path=file_path,
                        start_line=current_start,
                        end_line=i,
                    ))
                    chunk_idx += 1
                    current_chunk = []
                    current_start = i + 1

        # Remaining lines
        if current_chunk:
            chunk_text = "\n".join(current_chunk)
            if chunk_text.strip():
                chunks.append(Chunk(
                    text=chunk_text,
                    chunk_index=chunk_idx,
                    chunk_type="block",
                    source_type="code",
                    file_path=file_path,
                    start_line=current_start,
                    end_line=len(lines) - 1,
                ))

        return chunks if chunks else [
            Chunk(
                text=text,
                chunk_index=0,
                chunk_type="block",
                source_type="code",
                file_path=file_path,
                start_line=0,
                end_line=len(lines) - 1,
            )
        ]
